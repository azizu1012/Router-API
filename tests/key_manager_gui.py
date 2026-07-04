import os
import re
import sys
import json
import asyncio
import subprocess
import webbrowser
import threading
from typing import Dict, List
from fastapi import FastAPI, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn

# Resolve .env path once at module level (tests/ -> project root)
ENV_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".env"))
BAK_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".env.bak"))

app = FastAPI(title="Gemini Key Manager GUI")

# Helper to resolve local gcloud.cmd path if present in workspace
def get_gcloud_cmd():
    local_gcloud = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "gcloud_sdk", "google-cloud-sdk", "bin", "gcloud.cmd"))
    if os.path.exists(local_gcloud):
        return f'"{local_gcloud}"'
    return "gcloud"

def is_gemini_key(k: dict, project_id: str) -> bool:
    # 1. Project ID patterns for AI Studio (auto-created) or user custom AI Studio projects
    pid_lower = project_id.lower()
    if pid_lower.startswith("gen-lang-client-") or pid_lower in ("fire-house-api", "backfire-api"):
        return True
        
    # 2. Check displayName of the API key
    display_name = k.get("displayName", "").lower()
    if "gemini" in display_name or "generative" in display_name:
        return True
        
    # 3. Check restrictions API targets (should have generativelanguage.googleapis.com)
    restrictions = k.get("restrictions", {})
    api_targets = restrictions.get("apiTargets", [])
    for target in api_targets:
        if target.get("service") == "generativelanguage.googleapis.com":
            return True
            
    # 4. Check annotations
    annotations = k.get("annotations", {})
    if annotations.get("generative-language") == "enabled":
        return True
        
    return False

# Global log buffer to stream back to the UI
logs_list: List[str] = []

def log_message(msg: str):
    print(msg)
    logs_list.append(msg)
    if len(logs_list) > 1000:
        logs_list.pop(0)

def save_key_to_env(key_name, key_val):
    env_path = ENV_PATH
    if not os.path.exists(env_path):
        return
    with open(env_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    
    key_pattern = re.compile(r'^(?:#\s*(?:BANNED|LEAKED_BANNED|LEAKED)?[^:]*:\s*|#\s*)?(' + re.escape(key_name) + r')\s*=\s*(.*)$')
    new_lines = []
    found = False
    for line in lines:
        match = key_pattern.match(line.strip())
        if match:
            new_lines.append(f"{key_name}={key_val}\n")
            found = True
        else:
            new_lines.append(line)
            
    if not found:
        # Key name was not found in .env, so we append it after the last GEMINI_API_KEY line
        last_key_idx = -1
        for idx, line in enumerate(new_lines):
            if "GEMINI_API_KEY_" in line:
                last_key_idx = idx
        if last_key_idx != -1:
            new_lines.insert(last_key_idx + 1, f"{key_name}={key_val}\n")
        else:
            new_lines.append(f"{key_name}={key_val}\n")
            
    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)

# Helper function to run check_key_status using google-genai
async def check_key_status(key: str) -> str:
    if not key:
        return "empty"
    try:
        from google.genai import Client
        from google.genai import types
        # Set 5-second timeout and attempts=1 to prevent SDK retries from hanging on quota errors
        client = Client(
            api_key=key,
            http_options=types.HttpOptions(
                timeout=5000,
                retry_options=types.HttpRetryOptions(attempts=1)
            )
        )
        response = await client.aio.models.generate_content(
            model="gemini-2.5-flash-lite",
            contents="Hi"
        )
        if response.text:
            return "working"
    except Exception as e:
        err_str = str(e)
        if "leaked" in err_str:
            return "leaked"
        elif "quota" in err_str.lower() or "exhausted" in err_str.lower() or "429" in err_str:
            return "quota"
    return "failed"

def run_gcloud_login():
    log_message(">>> Launching 'gcloud auth login' in a new terminal window...")
    try:
        import sys
        if sys.platform == "win32":
            subprocess.run(f'start "Google Cloud Login" cmd /k {get_gcloud_cmd()} auth login', shell=True)
        else:
            subprocess.run(f"{get_gcloud_cmd()} auth login", shell=True, check=True)
        log_message(">>> Login window opened. Please complete the login in the opened terminal/browser.")
    except Exception as e:
        log_message(f"Error launching login: {e}")

def run_gcloud_login_no_browser():
    log_message(">>> Launching 'gcloud auth login --no-launch-browser'...")
    log_message(">>> Please COPY the link shown in the terminal window, paste it into an INCOGNITO browser window to log in, and paste the authorization code back in the terminal.")
    try:
        import sys
        if sys.platform == "win32":
            subprocess.run(f'start "Google Cloud Login (No Browser)" cmd /k {get_gcloud_cmd()} auth login --no-launch-browser', shell=True)
        else:
            subprocess.run(f"{get_gcloud_cmd()} auth login --no-launch-browser", shell=True)
        log_message(">>> Login window opened. Please copy the link and paste it into Edge's InPrivate window.")
    except Exception as e:
        log_message(f"Error launching no-browser login: {e}")

def run_gcloud_logout():
    log_message(">>> Revoking credentials for all logged-in accounts...")
    try:
        # List accounts first to show what will be revoked
        res = subprocess.run(f"{get_gcloud_cmd()} auth list --format=json", shell=True, capture_output=True, text=True)
        if res.returncode == 0:
            accounts = [a.get("account") for a in json.loads(res.stdout) if a.get("account")]
            if not accounts:
                log_message(">>> No accounts to logout.")
                return
            log_message(f">>> Revoking {len(accounts)} account(s): {', '.join(accounts)}")
        
        # Revoke all with --quiet to skip confirmation prompts
        for acc in accounts:
            subprocess.run(f'{get_gcloud_cmd()} auth revoke "{acc}" --quiet', shell=True, capture_output=True)
            log_message(f"  Revoked: {acc}")
        log_message(">>> Logout successful. All accounts revoked.")
    except Exception as e:
        log_message(f"Error during logout: {e}")

async def run_clean_leaked():
    log_message("\n--- PHASE 1: Scanning & Cleaning Leaked Keys ---")
    env_path = ENV_PATH
    bak_path = BAK_PATH
    if not os.path.exists(env_path):
        log_message("Error: .env file not found!")
        return
        
    # Backup
    if not os.path.exists(bak_path):
        import shutil
        shutil.copy(env_path, bak_path)
        log_message(f"Created backup .env.bak")
        
    read_path = bak_path if os.path.exists(bak_path) else env_path
    with open(read_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
        
    key_pattern = re.compile(r'^(?:#\s*(?:BANNED|LEAKED_BANNED|LEAKED)?[^:]*:\s*|#\s*)?(GEMINI_API_KEY_\d+)\s*=\s*(.*)$')
    new_lines = []
    cleaned_count = 0
    emptied_vals = set()
    
    for idx, line in enumerate(lines):
        match = key_pattern.match(line.strip())
        if match:
            key_name = match.group(1)
            key_val = match.group(2).strip().strip('"').strip("'")
            if not key_val:
                new_lines.append(f"{key_name}=\n")
                continue
                
            log_message(f"Testing {key_name} on gemini-2.5-flash-lite...")
            status = await check_key_status(key_val)
            log_message(f"  Result: {status.upper()}")
            
            if status in ("leaked", "failed"):
                new_lines.append(f"{key_name}=\n")
                log_message(f"  [EMPTIED] {key_name} (Status: {status.upper()})")
                cleaned_count += 1
                emptied_vals.add(key_val)
            else:
                new_lines.append(line)
        else:
            new_lines.append(line)
            
    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)
    log_message(f">>> Cleanup complete! Emptied {cleaned_count} leaked keys in .env.")
    
    # After writing updated env, if any keys were emptied, delete them from Google Cloud Console!
    if cleaned_count > 0:
        log_message(">>> Initiating Google Cloud Console deletion of cleaned keys...")
        try:
            res = subprocess.run(f"{get_gcloud_cmd()} auth list --format=json", shell=True, capture_output=True, text=True)
            if res.returncode == 0:
                accounts_data = json.loads(res.stdout)
                accounts = [item.get("account") for item in accounts_data if item.get("account")]
                active_acc = next((item.get("account") for item in accounts_data if item.get("status") == "ACTIVE"), None)
                
                if accounts:
                    gcp_deleted = 0
                    for acc in accounts:
                        subprocess.run(f'{get_gcloud_cmd()} config set account "{acc}"', shell=True, stdout=subprocess.DEVNULL)
                        # Get projects
                        p_res = subprocess.run(f"{get_gcloud_cmd()} projects list --format=json", shell=True, capture_output=True, text=True)
                        if p_res.returncode == 0:
                            projects = [p["projectId"] for p in json.loads(p_res.stdout)]
                            for project in projects:
                                k_res = subprocess.run(f'{get_gcloud_cmd()} services api-keys list --project="{project}" --format=json', shell=True, capture_output=True, text=True)
                                if k_res.returncode == 0:
                                    keys_data = json.loads(k_res.stdout)
                                    for key_info in keys_data:
                                        key_str = key_info.get("keyString")
                                        resource_name = key_info.get("name")
                                        if key_str in emptied_vals:
                                            log_message(f"  Deleting leaked/CSE key on Google Cloud: {key_info.get('displayName')} in project {project}...")
                                            del_res = subprocess.run(
                                                f'{get_gcloud_cmd()} services api-keys delete "{resource_name}" --location=global --project="{project}" --quiet',
                                                shell=True, capture_output=True
                                            )
                                            if del_res.returncode == 0:
                                                gcp_deleted += 1
                                                log_message("    Deleted successfully!")
                    
                    # Restore original active
                    if active_acc:
                        subprocess.run(f'{get_gcloud_cmd()} config set account "{active_acc}"', shell=True, stdout=subprocess.DEVNULL)
                    log_message(f">>> Google Cloud Console cleanup complete! Deleted {gcp_deleted} keys from Cloud.")
        except Exception as e:
            log_message(f"Error during Google Cloud Console key deletion: {e}")

async def run_sync_existing_keys():
    log_message("\n--- Multi-Account Key Sync (Existing Keys Only) ---")
    env_path = ENV_PATH
    if not os.path.exists(env_path):
        log_message("Error: .env file not found!")
        return
        
    try:
        res = subprocess.run(f"{get_gcloud_cmd()} auth list --format=json", shell=True, capture_output=True, text=True, check=True)
        accounts_data = json.loads(res.stdout)
    except Exception as e:
        log_message(f"Error fetching accounts: {e}")
        return
        
    accounts = [item.get("account") for item in accounts_data if item.get("account")]
    active_acc = next((item.get("account") for item in accounts_data if item.get("status") == "ACTIVE"), None)
    
    if not accounts:
        log_message("No authenticated accounts found. Please login first.")
        return
        
    log_message(f"Found accounts: {', '.join(accounts)}")
    
    with open(env_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
        
    key_pattern = re.compile(r'^(?:#\s*(?:BANNED|LEAKED_BANNED|LEAKED)?[^:]*:\s*|#\s*)?(GEMINI_API_KEY_(\d+))\s*=\s*(.*)$')
    empty_keys = []
    existing_values = set()  # Track key values already in .env
    highest_idx = 0
    for line in lines:
        match = key_pattern.match(line.strip())
        if match:
            key_name = match.group(1)
            idx_num = int(match.group(2))
            key_val = match.group(3).strip().strip('"').strip("'")
            highest_idx = max(highest_idx, idx_num)
            if line.strip().startswith("#") or not key_val:
                empty_keys.append(key_name)
            else:
                existing_values.add(key_val)
                
    log_message(f"Found {len(empty_keys)} empty/commented key slots. Highest index: {highest_idx}")
    new_keys_map = {}
    used_projects = set()  # Track projects already represented in .env
    
    def get_next_key_name():
        nonlocal highest_idx
        if empty_keys:
            return empty_keys.pop(0)
        highest_idx += 1
        return f"GEMINI_API_KEY_{highest_idx}"
    
    for acc in accounts:
        log_message(f"Switching active account to {acc}...")
        subprocess.run(f'{get_gcloud_cmd()} config set account "{acc}"', shell=True, check=True)
        
        try:
            p_res = subprocess.run(f"{get_gcloud_cmd()} projects list --format=json", shell=True, capture_output=True, text=True, check=True)
            projects = [p["projectId"] for p in json.loads(p_res.stdout)]
        except Exception:
            projects = []
            
        log_message(f"Projects in {acc}: {', '.join(projects)}")
        
        for project in projects:
            if project in used_projects:
                log_message(f"  [SKIP] Project '{project}' already has a key in .env")
                continue
                
            log_message(f"Checking existing keys in project: {project}")
            try:
                list_res = subprocess.run(f'{get_gcloud_cmd()} services api-keys list --project="{project}" --format=json', shell=True, capture_output=True, text=True)
                if list_res.returncode == 0:
                    existing_keys = json.loads(list_res.stdout)
                    for k in existing_keys:
                        if not is_gemini_key(k, project):
                            log_message(f"  [SKIP] Key '{k.get('displayName')}' in project '{project}' is not a Gemini/AI Studio key.")
                            continue
                        resource_name = k.get("name")
                        if resource_name:
                            string_res = subprocess.run(
                                f'{get_gcloud_cmd()} services api-keys get-key-string "{resource_name}" --project="{project}" --format=json',
                                shell=True, capture_output=True, text=True
                            )
                            if string_res.returncode == 0:
                                k_str = json.loads(string_res.stdout).get("keyString")
                                if k_str:
                                    if k_str in existing_values:
                                        log_message(f"  [SKIP] Key already in .env, skipping")
                                        used_projects.add(project)
                                        break
                                    key_name = get_next_key_name()
                                    save_key_to_env(key_name, k_str)
                                    new_keys_map[key_name] = k_str
                                    existing_values.add(k_str)
                                    used_projects.add(project)
                                    log_message(f"  [SAVED EXISTING] {key_name} -> {k_str[:8]}...")
                                    break
            except Exception:
                pass
                
    if active_acc:
        subprocess.run(f'{get_gcloud_cmd()} config set account "{active_acc}"', shell=True, stdout=subprocess.DEVNULL, check=True)
        
    log_message(">>> Key sync complete!")

async def run_regenerate_new_keys():
    log_message("\n--- PHASE 2: Multi-Account Key Sync & Generation ---")
    env_path = ENV_PATH
    if not os.path.exists(env_path):
        log_message("Error: .env file not found!")
        return
        
    try:
        res = subprocess.run(f"{get_gcloud_cmd()} auth list --format=json", shell=True, capture_output=True, text=True, check=True)
        accounts_data = json.loads(res.stdout)
    except Exception as e:
        log_message(f"Error fetching accounts: {e}")
        return
        
    accounts = [item.get("account") for item in accounts_data if item.get("account")]
    active_acc = next((item.get("account") for item in accounts_data if item.get("status") == "ACTIVE"), None)
    
    if not accounts:
        log_message("No authenticated accounts found. Please login first.")
        return
        
    log_message(f"Found accounts: {', '.join(accounts)}")
    
    with open(env_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
        
    key_pattern = re.compile(r'^(?:#\s*(?:BANNED|LEAKED_BANNED|LEAKED)?[^:]*:\s*|#\s*)?(GEMINI_API_KEY_(\d+))\s*=\s*(.*)$')
    empty_keys = []
    existing_values = set()
    highest_idx = 0
    for line in lines:
        match = key_pattern.match(line.strip())
        if match:
            key_name = match.group(1)
            idx_num = int(match.group(2))
            key_val = match.group(3).strip().strip('"').strip("'")
            highest_idx = max(highest_idx, idx_num)
            if line.strip().startswith("#") or not key_val:
                empty_keys.append(key_name)
            else:
                existing_values.add(key_val)
                
    log_message(f"Found {len(empty_keys)} empty/commented key slots. Highest index: {highest_idx}")
    new_keys_map = {}
    used_projects = set()
    
    def get_next_key_name():
        nonlocal highest_idx
        if empty_keys:
            return empty_keys.pop(0)
        highest_idx += 1
        return f"GEMINI_API_KEY_{highest_idx}"
    
    for acc in accounts:
        log_message(f"Switching active account to {acc}...")
        subprocess.run(f'{get_gcloud_cmd()} config set account "{acc}"', shell=True, check=True)
        
        try:
            p_res = subprocess.run(f"{get_gcloud_cmd()} projects list --format=json", shell=True, capture_output=True, text=True, check=True)
            projects = [p["projectId"] for p in json.loads(p_res.stdout)]
        except Exception:
            projects = []
            
        log_message(f"Projects in {acc}: {', '.join(projects)}")
        
        for project in projects:
            if project in used_projects:
                log_message(f"  [SKIP] Project '{project}' already has a key in .env")
                continue
                
            log_message(f"Checking existing keys in project: {project}")
            got_key_from_project = False
            try:
                list_res = subprocess.run(f'{get_gcloud_cmd()} services api-keys list --project="{project}" --format=json', shell=True, capture_output=True, text=True)
                if list_res.returncode == 0:
                    existing_keys = json.loads(list_res.stdout)
                    for k in existing_keys:
                        if not is_gemini_key(k, project):
                            log_message(f"  [SKIP] Key '{k.get('displayName')}' in project '{project}' is not a Gemini/AI Studio key.")
                            continue
                        resource_name = k.get("name")
                        if resource_name:
                            string_res = subprocess.run(
                                f'{get_gcloud_cmd()} services api-keys get-key-string "{resource_name}" --project="{project}" --format=json',
                                shell=True, capture_output=True, text=True
                            )
                            if string_res.returncode == 0:
                                k_str = json.loads(string_res.stdout).get("keyString")
                                if k_str:
                                    if k_str in existing_values:
                                        log_message(f"  [SKIP] Key already in .env, skipping")
                                        used_projects.add(project)
                                        got_key_from_project = True
                                        break
                                    key_name = get_next_key_name()
                                    save_key_to_env(key_name, k_str)
                                    new_keys_map[key_name] = k_str
                                    existing_values.add(k_str)
                                    used_projects.add(project)
                                    got_key_from_project = True
                                    log_message(f"  [SAVED EXISTING] {key_name} -> {k_str[:8]}...")
                                    break
            except Exception:
                pass
                
            if got_key_from_project:
                continue
                
            # Only generate new keys in projects that are actually meant for Gemini/AI Studio
            # Auto-generated AI Studio projects start with gen-lang-client-
            # User manually declared: fire-house-api, backfire-api
            pid_lower = project.lower()
            is_valid_ai_studio_project = pid_lower.startswith("gen-lang-client-") or pid_lower in ("fire-house-api", "backfire-api")
            
            if not is_valid_ai_studio_project:
                log_message(f"  [SKIP] Project '{project}' is not an AI Studio project. Skipping generation.")
                continue
                
            log_message(f"Generating new keys in project: {project}")
            keys_created = 0
            while keys_created < 1:
                key_name = get_next_key_name()
                try:
                    create_res = subprocess.run(
                        f'{get_gcloud_cmd()} services api-keys create --display-name="{key_name}" --project="{project}" --api-target="service=generativelanguage.googleapis.com" --format=json',
                        shell=True, capture_output=True, text=True, check=True
                    )
                    stdout = create_res.stdout
                    start_idx = stdout.find('{')
                    end_idx = stdout.rfind('}')
                    k_str = None
                    if start_idx != -1 and end_idx != -1:
                        try:
                            k_str = json.loads(stdout[start_idx:end_idx+1]).get("keyString")
                        except Exception:
                            pass
                            
                    if k_str:
                        save_key_to_env(key_name, k_str)
                        new_keys_map[key_name] = k_str
                        existing_values.add(k_str)
                        used_projects.add(project)
                        keys_created += 1
                        log_message(f"  [CREATED & SAVED] {key_name} -> {k_str[:8]}...")
                    else:
                        break
                except Exception as e:
                    log_message(f"  Failed to create key: {e}")
                    break
                    
        # If we still have empty slots OR we want to support dynamic addition for this account,
        # we can dynamically create a new AI Studio project for this account to help fill empty slots/add new keys!
        # Enforce that the total number of projects in the account (existing + newly created) must not exceed 10.
        new_projects_created = 0
        while (len(projects) + new_projects_created) < 10:
            # Check if there are any unused valid AI Studio projects in this account
            valid_unused_projects = [
                p for p in projects 
                if p not in used_projects and (p.lower().startswith("gen-lang-client-") or p.lower() in ("fire-house-api", "backfire-api"))
            ]
            if not projects or not valid_unused_projects:
                # We need a new project! Generate a random project ID
                import random
                import string
                rand_id = "".join(random.choices(string.digits, k=9))
                new_project_id = f"gen-lang-client-{rand_id}"
                key_name = get_next_key_name()
                
                log_message(f"  Account '{acc}' (projects count: {len(projects) + new_projects_created}) needs projects to fill empty key slots. Auto-creating GCP Project: {new_project_id}...")
                
                try:
                    # 1. Create project
                    p_create = subprocess.run(
                        f'{get_gcloud_cmd()} projects create "{new_project_id}" --name="AI Studio Project" --format=json',
                        shell=True, capture_output=True, text=True
                    )
                    if p_create.returncode != 0:
                        log_message(f"    Failed to create project: {p_create.stderr.strip()}")
                        break
                        
                    # 2. Enable API
                    log_message(f"    Project created. Enabling Generative Language API in {new_project_id}...")
                    api_enable = subprocess.run(
                        f'{get_gcloud_cmd()} services enable generativelanguage.googleapis.com --project="{new_project_id}"',
                        shell=True, capture_output=True, text=True
                    )
                    if api_enable.returncode != 0:
                        log_message(f"    Failed to enable API: {api_enable.stderr.strip()}")
                        break
                        
                    # 3. Create Key
                    log_message(f"    API enabled. Creating key '{key_name}' in {new_project_id}...")
                    key_create = subprocess.run(
                        f'{get_gcloud_cmd()} services api-keys create --display-name="{key_name}" --project="{new_project_id}" --api-target="service=generativelanguage.googleapis.com" --format=json',
                        shell=True, capture_output=True, text=True
                    )
                    stdout = key_create.stdout
                    start_idx = stdout.find('{')
                    end_idx = stdout.rfind('}')
                    k_str = None
                    if start_idx != -1 and end_idx != -1:
                        try:
                            k_str = json.loads(stdout[start_idx:end_idx+1]).get("keyString")
                        except Exception:
                            pass
                            
                    if k_str:
                        save_key_to_env(key_name, k_str)
                        new_keys_map[key_name] = k_str
                        existing_values.add(k_str)
                        used_projects.add(new_project_id)
                        projects.append(new_project_id)  # count towards projects limit
                        new_projects_created += 1
                        log_message(f"    [CREATED & SAVED NEW PROJECT KEY] {key_name} -> {k_str[:8]}...")
                    else:
                        # Fallback list check
                        import time
                        time.sleep(2)
                        list_res = subprocess.run(
                            f'{get_gcloud_cmd()} services api-keys list --project="{new_project_id}" --format=json',
                            shell=True, capture_output=True, text=True
                        )
                        found_fallback = False
                        if list_res.returncode == 0:
                            for key_item in json.loads(list_res.stdout):
                                if key_item.get("displayName") == key_name:
                                    res_name = key_item.get("name")
                                    str_res = subprocess.run(
                                        f'{get_gcloud_cmd()} services api-keys get-key-string "{res_name}" --project="{new_project_id}" --format=json',
                                        shell=True, capture_output=True, text=True
                                    )
                                    if str_res.returncode == 0:
                                        k_str = json.loads(str_res.stdout).get("keyString")
                                        if k_str:
                                            save_key_to_env(key_name, k_str)
                                            new_keys_map[key_name] = k_str
                                            existing_values.add(k_str)
                                            used_projects.add(new_project_id)
                                            projects.append(new_project_id)
                                            new_projects_created += 1
                                            log_message(f"    [CREATED & SAVED NEW PROJECT KEY (FALLBACK)] {key_name} -> {k_str[:8]}...")
                                            found_fallback = True
                                            break
                        if not found_fallback:
                            break
                except Exception as e:
                    log_message(f"    Error during dynamic project/key creation: {e}")
                    break
            else:
                break
                    
    if active_acc:
        subprocess.run(f'{get_gcloud_cmd()} config set account "{active_acc}"', shell=True, stdout=subprocess.DEVNULL, check=True)
        
    log_message(">>> Key generation complete!")

async def run_one_click():
    log_message(">>> Starting One-Click Auto-Fill Process (Creates New Keys)...")
    await run_regenerate_new_keys()
    log_message(">>> ONE-CLICK PROCESS COMPLETED SUCCESSFULLY!")

# API endpoints
@app.get("/api/status")
async def get_status():
    env_path = ENV_PATH
    accounts = []
    projects = []
    env_stats = {"total": 0, "active": 0, "empty": 0}
    
    # 1. Get authenticated accounts
    try:
        res = subprocess.run(f"{get_gcloud_cmd()} auth list --format=json", shell=True, capture_output=True, text=True)
        if res.returncode == 0:
            accounts_data = json.loads(res.stdout)
            accounts = [{"email": item.get("account"), "active": item.get("status") == "ACTIVE"} for item in accounts_data if item.get("account")]
    except Exception:
        pass
        
    # 2. Get projects for active account
    try:
        p_res = subprocess.run(f"{get_gcloud_cmd()} projects list --format=json", shell=True, capture_output=True, text=True)
        if p_res.returncode == 0:
            projects = [p["projectId"] for p in json.loads(p_res.stdout)]
    except Exception:
        pass
        
    # 3. Read .env stats
    if os.path.exists(env_path):
        key_pattern = re.compile(r'^(GEMINI_API_KEY_\d+)\s*=\s*(.*)$')
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                match = key_pattern.match(line.strip())
                if match:
                    env_stats["total"] += 1
                    val = match.group(2).strip().strip('"').strip("'")
                    if val:
                        env_stats["active"] += 1
                    else:
                        env_stats["empty"] += 1
                        
    return {
        "accounts": accounts,
        "projects": projects,
        "env_stats": env_stats
    }

@app.get("/api/logs")
async def get_logs():
    return {"logs": logs_list}

@app.post("/api/login")
async def start_login(background_tasks: BackgroundTasks):
    background_tasks.add_task(run_gcloud_login)
    return {"status": "success", "message": "Login initiated"}

@app.post("/api/login_no_browser")
async def start_login_no_browser(background_tasks: BackgroundTasks):
    background_tasks.add_task(run_gcloud_login_no_browser)
    return {"status": "success", "message": "No-browser login initiated"}

@app.post("/api/clean")
async def start_clean(background_tasks: BackgroundTasks):
    background_tasks.add_task(run_clean_leaked)
    return {"status": "success", "message": "Cleanup initiated"}

@app.post("/api/generate")
async def start_generate(background_tasks: BackgroundTasks):
    background_tasks.add_task(run_sync_existing_keys)
    return {"status": "success", "message": "Key sync initiated"}

@app.post("/api/oneclick")
async def start_oneclick(background_tasks: BackgroundTasks):
    background_tasks.add_task(run_one_click)
    return {"status": "success", "message": "One-click initiated"}

@app.post("/api/logout")
async def start_logout(background_tasks: BackgroundTasks):
    background_tasks.add_task(run_gcloud_logout)
    return {"status": "success", "message": "Logout initiated"}

# Serve Frontend HTML
@app.get("/", response_class=HTMLResponse)
async def serve_index():
    html_content = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Gemini API Key Manager Dashboard</title>
        <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;700&display=swap" rel="stylesheet">
        <style>
            :root {
                --bg-primary: #0a0c10;
                --bg-secondary: #141722;
                --accent-blue: #2563eb;
                --accent-blue-hover: #3b82f6;
                --accent-purple: #8b5cf6;
                --text-main: #f3f4f6;
                --text-muted: #9ca3af;
                --success: #10b981;
                --danger: #ef4444;
            }
            * {
                box-sizing: border-box;
                margin: 0;
                padding: 0;
                font-family: 'Outfit', sans-serif;
            }
            body {
                background-color: var(--bg-primary);
                color: var(--text-main);
                min-height: 100vh;
                display: flex;
                flex-direction: column;
                justify-content: flex-start;
                align-items: center;
                padding: 2rem;
            }
            .container {
                max-width: 1000px;
                width: 100%;
                display: flex;
                flex-direction: column;
                gap: 1.5rem;
            }
            header {
                display: flex;
                justify-content: space-between;
                align-items: center;
                padding: 1.5rem;
                background: linear-gradient(135deg, rgba(20, 23, 34, 0.8), rgba(26, 31, 46, 0.8));
                backdrop-filter: blur(10px);
                border: 1px solid rgba(255, 255, 255, 0.05);
                border-radius: 16px;
                box-shadow: 0 8px 32px rgba(0, 0, 0, 0.5);
            }
            h1 {
                font-size: 1.8rem;
                font-weight: 700;
                background: linear-gradient(to right, #60a5fa, #a78bfa);
                -webkit-background-clip: text;
                -webkit-text-fill-color: transparent;
            }
            .grid {
                display: grid;
                grid-template-columns: 1fr 1fr;
                gap: 1.5rem;
            }
            @media (max-width: 768px) {
                .grid {
                    grid-template-columns: 1fr;
                }
            }
            .card {
                background: rgba(20, 23, 34, 0.8);
                backdrop-filter: blur(10px);
                border: 1px solid rgba(255, 255, 255, 0.05);
                border-radius: 16px;
                padding: 1.5rem;
                display: flex;
                flex-direction: column;
                gap: 1rem;
                box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
            }
            .card h2 {
                font-size: 1.2rem;
                font-weight: 600;
                color: var(--text-main);
                border-bottom: 1px solid rgba(255, 255, 255, 0.05);
                padding-bottom: 0.5rem;
            }
            .stat-box {
                display: flex;
                justify-content: space-around;
                align-items: center;
                padding: 1rem;
                background: rgba(255, 255, 255, 0.02);
                border-radius: 12px;
                border: 1px solid rgba(255, 255, 255, 0.02);
            }
            .stat-item {
                text-align: center;
            }
            .stat-value {
                font-size: 1.8rem;
                font-weight: 700;
            }
            .stat-value.total { color: #60a5fa; }
            .stat-value.active { color: var(--success); }
            .stat-value.empty { color: var(--danger); }
            .stat-label {
                font-size: 0.8rem;
                color: var(--text-muted);
                margin-top: 0.2rem;
            }
            .btn {
                padding: 0.8rem 1.5rem;
                border: none;
                border-radius: 10px;
                font-size: 0.95rem;
                font-weight: 600;
                cursor: pointer;
                transition: all 0.2s ease;
                display: flex;
                align-items: center;
                justify-content: center;
                gap: 0.5rem;
            }
            .btn-blue {
                background: linear-gradient(135deg, var(--accent-blue), #1d4ed8);
                color: white;
            }
            .btn-blue:hover {
                background: linear-gradient(135deg, var(--accent-blue-hover), #2563eb);
                transform: translateY(-1px);
            }
            .btn-purple {
                background: linear-gradient(135deg, var(--accent-purple), #6d28d9);
                color: white;
            }
            .btn-purple:hover {
                background: linear-gradient(135deg, #a78bfa, #7c3aed);
                transform: translateY(-1px);
            }
            .btn-danger {
                background: linear-gradient(135deg, var(--danger), #b91c1c);
                color: white;
            }
            .btn-danger:hover {
                background: linear-gradient(135deg, #f87171, #dc2626);
                transform: translateY(-1px);
            }
            .btn-success {
                background: linear-gradient(135deg, var(--success), #047857);
                color: white;
            }
            .btn-success:hover {
                background: linear-gradient(135deg, #34d399, #059669);
                transform: translateY(-1px);
                box-shadow: 0 0 15px rgba(16, 185, 129, 0.4);
            }
            .actions-grid {
                display: grid;
                grid-template-columns: 1fr 1fr;
                gap: 1rem;
            }
            .list-container {
                max-height: 150px;
                overflow-y: auto;
                display: flex;
                flex-direction: column;
                gap: 0.5rem;
                padding-right: 0.5rem;
            }
            .list-item {
                display: flex;
                justify-content: space-between;
                align-items: center;
                padding: 0.6rem 0.8rem;
                background: rgba(255, 255, 255, 0.02);
                border-radius: 8px;
                border: 1px solid rgba(255, 255, 255, 0.02);
                font-size: 0.9rem;
            }
            .badge {
                padding: 0.2rem 0.6rem;
                border-radius: 20px;
                font-size: 0.75rem;
                font-weight: 600;
            }
            .badge-active {
                background: rgba(16, 185, 129, 0.1);
                color: var(--success);
                border: 1px solid rgba(16, 185, 129, 0.2);
            }
            .badge-inactive {
                background: rgba(255, 255, 255, 0.05);
                color: var(--text-muted);
            }
            .console-card {
                grid-column: 1 / -1;
            }
            .console {
                background: #05070a;
                border: 1px solid rgba(255, 255, 255, 0.05);
                border-radius: 12px;
                padding: 1rem;
                font-family: monospace;
                font-size: 0.85rem;
                color: #34d399;
                height: 300px;
                overflow-y: auto;
                white-space: pre-wrap;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <header>
                <div>
                    <h1>Gemini API Key Manager</h1>
                    <p style="font-size: 0.85rem; color: var(--text-muted); margin-top: 0.2rem;">Local Management Console</p>
                </div>
                <button class="btn btn-blue" onclick="refreshStatus()">🔄 Refresh Status</button>
            </header>

            <div class="grid">
                <div class="card">
                    <h2>🔑 Environment Key Stats (.env)</h2>
                    <div class="stat-box">
                        <div class="stat-item">
                            <div class="stat-value total" id="stat-total">0</div>
                            <div class="stat-label">Total Slots</div>
                        </div>
                        <div class="stat-item">
                            <div class="stat-value active" id="stat-active">0</div>
                            <div class="stat-label">Active Keys</div>
                        </div>
                        <div class="stat-item">
                            <div class="stat-value empty" id="stat-empty">0</div>
                            <div class="stat-label">Empty Slots</div>
                        </div>
                    </div>
                    <h2>⚙️ Actions</h2>
                    <div class="actions-grid">
                        <button class="btn btn-success" style="grid-column: 1 / -1; font-size: 1.05rem;" onclick="triggerAction('/api/oneclick')">🚀 One-Click Auto-Fill (Creates New Keys)</button>
                        <button class="btn btn-purple" onclick="triggerAction('/api/login')">👤 Login Google</button>
                        <button class="btn btn-purple" onclick="triggerAction('/api/login_no_browser')">👤 Login Google (Incognito Mode)</button>
                        <button class="btn btn-danger" style="grid-column: 1 / -1;" onclick="triggerAction('/api/logout')">🚪 Logout All</button>
                        <button class="btn btn-danger" style="grid-column: 1 / -1;" onclick="triggerAction('/api/clean')">🧹 Clean Leaked</button>
                        <button class="btn btn-purple" style="grid-column: 1 / -1;" onclick="triggerAction('/api/generate')">⚡ Sync Existing Keys (No Creation)</button>
                    </div>
                </div>

                <div class="card">
                    <h2>🌐 Google Accounts & Projects</h2>
                    <div>
                        <h3 style="font-size: 0.95rem; color: var(--text-muted); margin-bottom: 0.5rem;">Google Accounts:</h3>
                        <div class="list-container" id="accounts-list">
                            <!-- Accounts will be loaded here -->
                        </div>
                    </div>
                    <div>
                        <h3 style="font-size: 0.95rem; color: var(--text-muted); margin-bottom: 0.5rem;">Active Account Projects:</h3>
                        <div class="list-container" id="projects-list">
                            <!-- Projects will be loaded here -->
                        </div>
                    </div>
                </div>

                <div class="card console-card">
                    <h2>📋 Real-time Activity Logs</h2>
                    <div class="console" id="console-logs">Waiting for actions...</div>
                </div>
            </div>
        </div>

        <script>
            async function refreshStatus() {
                try {
                    const response = await fetch('/api/status');
                    const data = await response.json();
                    
                    document.getElementById('stat-total').innerText = data.env_stats.total;
                    document.getElementById('stat-active').innerText = data.env_stats.active;
                    document.getElementById('stat-empty').innerText = data.env_stats.empty;
                    
                    const accList = document.getElementById('accounts-list');
                    accList.innerHTML = '';
                    if (data.accounts.length === 0) {
                        accList.innerHTML = '<div class="list-item">No accounts logged in</div>';
                    } else {
                        data.accounts.forEach(acc => {
                            accList.innerHTML += `
                                <div class="list-item">
                                    <span>${acc.email}</span>
                                    <span class="badge ${acc.active ? 'badge-active' : 'badge-inactive'}">${acc.active ? 'Active' : 'OAuth'}</span>
                                </div>
                            `;
                        });
                    }
                    
                    const projList = document.getElementById('projects-list');
                    projList.innerHTML = '';
                    if (data.projects.length === 0) {
                        projList.innerHTML = '<div class="list-item">No projects found</div>';
                    } else {
                        data.projects.forEach(p => {
                            projList.innerHTML += `<div class="list-item">${p}</div>`;
                        });
                    }
                } catch (e) {
                    console.error("Error refreshing status", e);
                }
            }

            async function refreshLogs() {
                try {
                    const response = await fetch('/api/logs');
                    const data = await response.json();
                    const consoleEl = document.getElementById('console-logs');
                    if (data.logs.length > 0) {
                        consoleEl.innerText = data.logs.join('\\n');
                        consoleEl.scrollTop = consoleEl.scrollHeight;
                    }
                } catch (e) {
                    console.error("Error refreshing logs", e);
                }
            }

            async function triggerAction(endpoint) {
                try {
                    const response = await fetch(endpoint, { method: 'POST' });
                    const data = await response.json();
                    refreshStatus();
                } catch (e) {
                    console.error("Error triggering action", e);
                }
            }

            // Polling loops
            refreshStatus();
            setInterval(refreshStatus, 3000);
            setInterval(refreshLogs, 1000);
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

def start_server():
    # Run uvicorn server on localhost:8080
    config = uvicorn.Config(app, host="127.0.0.1", port=8080, log_level="info")
    server = uvicorn.Server(config)
    server.run()

if __name__ == "__main__":
    log_message("Starting Local Web Console for Gemini API Key Manager...")
    # Open browser in a separate thread after 1.5 second delay
    def open_browser():
        import time
        time.sleep(1.5)
        webbrowser.open("http://127.0.0.1:8080")
        
    threading.Thread(target=open_browser, daemon=True).start()
    start_server()
