import subprocess
import json
import os
import re
import sys
import shutil

# Helper to resolve local gcloud.cmd path if present in workspace
def get_gcloud_cmd():
    local_gcloud = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "gcloud_sdk", "google-cloud-sdk", "bin", "gcloud.cmd"))
    if os.path.exists(local_gcloud):
        return f'"{local_gcloud}"'
    return "gcloud"

# 1. Test key status using gemini-2.5-flash-lite
async def check_key_status(key: str) -> str:
    if not key:
        return "empty"
    try:
        from google.genai import Client
        client = Client(api_key=key)
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

def get_gcloud_accounts():
    try:
        result = subprocess.run(
            f"{get_gcloud_cmd()} auth list --format=json",
            shell=True, capture_output=True, text=True, check=True
        )
        accounts_data = json.loads(result.stdout)
        all_accounts = [item.get("account") for item in accounts_data if item.get("account")]
        active_account = next((item.get("account") for item in accounts_data if item.get("status") == "ACTIVE"), None)
        return all_accounts, active_account
    except Exception:
        return [], None

def get_gcp_projects(account):
    try:
        # Switch to account to fetch projects
        subprocess.run(f'{get_gcloud_cmd()} config set account "{account}"', shell=True, stdout=subprocess.DEVNULL, check=True)
        result = subprocess.run(
            f"{get_gcloud_cmd()} projects list --format=json",
            shell=True, capture_output=True, text=True, check=True
        )
        projects = json.loads(result.stdout)
        return [p["projectId"] for p in projects]
    except Exception:
        return []

def create_api_key(project_id, key_display_name):
    try:
        result = subprocess.run(
            f'{get_gcloud_cmd()} services api-keys create --display-name="{key_display_name}" --project="{project_id}" --api-target="service=generativelanguage.googleapis.com" --format=json',
            shell=True, capture_output=True, text=True, check=True
        )
        stdout = result.stdout
        start_idx = stdout.find('{')
        end_idx = stdout.rfind('}')
        key_str = None
        if start_idx != -1 and end_idx != -1:
            try:
                key_str = json.loads(stdout[start_idx:end_idx+1]).get("keyString")
            except Exception:
                pass
        if key_str:
            return key_str
            
        # Fallback if long-running operation
        import time
        time.sleep(3)
        list_res = subprocess.run(
            f'{get_gcloud_cmd()} services api-keys list --project="{project_id}" --format=json',
            shell=True, capture_output=True, text=True, check=True
        )
        keys = json.loads(list_res.stdout)
        for k in keys:
            if k.get("displayName") == key_display_name:
                return k.get("keyString")
    except Exception:
        pass
    return None

async def main():
    # Check gcloud CLI
    try:
        subprocess.run(f"{get_gcloud_cmd()} --version", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    except Exception:
        print("Error: 'gcloud' CLI tool is not installed or not in PATH.")
        return
        
    env_path = ".env"
    bak_path = ".env.bak"
    if not os.path.exists(env_path):
        print("Error: .env file not found!")
        return
        
    # 1. Backup original .env if not done
    if not os.path.exists(bak_path):
        shutil.copy(env_path, bak_path)
        print(f"Backed up original keys to {bak_path}")
        
    # Read env lines
    with open(env_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
        
    key_pattern = re.compile(r'^(?:#\s*(?:BANNED|LEAKED_BANNED|LEAKED)?[^:]*:\s*|#\s*)?(GEMINI_API_KEY_\d+)\s*=\s*(.*)$')
    
def is_gemini_key(k: dict, project_id: str) -> bool:
    pid_lower = project_id.lower()
    if pid_lower.startswith("gen-lang-client-") or pid_lower in ("fire-house-api", "backfire-api"):
        return True
    display_name = k.get("displayName", "").lower()
    if "gemini" in display_name or "generative" in display_name:
        return True
    restrictions = k.get("restrictions", {})
    api_targets = restrictions.get("apiTargets", [])
    for target in api_targets:
        if target.get("service") == "generativelanguage.googleapis.com":
            return True
    annotations = k.get("annotations", {})
    if annotations.get("generative-language") == "enabled":
        return True
    return False

def save_key_to_env(key_name, key_val):
    env_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".env"))
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

async def main():
    # Check gcloud CLI
    try:
        subprocess.run(f"{get_gcloud_cmd()} --version", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    except Exception:
        print("Error: 'gcloud' CLI tool is not installed or not in PATH.")
        return
        
    env_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".env"))
    bak_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".env.bak"))
    if not os.path.exists(env_path):
        print("Error: .env file not found!")
        return
        
    # 1. Backup original .env if not done
    if not os.path.exists(bak_path):
        shutil.copy(env_path, bak_path)
        print(f"Backed up original keys to {bak_path}")
        
    # Read env lines
    with open(env_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
        
    key_pattern = re.compile(r'^(?:#\s*(?:BANNED|LEAKED_BANNED|LEAKED)?[^:]*:\s*|#\s*)?(GEMINI_API_KEY_(\d+))\s*=\s*(.*)$')
    
    # --- PHASE 1: Find Empty Slots ---
    print("\n--- PHASE 1: Finding empty slots in .env ---")
    active_lines = []
    empty_keys = []
    existing_values = set()
    highest_idx = 0
    
    for idx, line in enumerate(lines):
        match = key_pattern.match(line.strip())
        if match:
            key_name = match.group(1)
            idx_num = int(match.group(2))
            key_val = match.group(3).strip().strip('"').strip("'")
            highest_idx = max(highest_idx, idx_num)
            if line.strip().startswith("#") or not key_val:
                print(f"Empty/commented key slot: {key_name}")
                empty_keys.append(key_name)
            else:
                existing_values.add(key_val)
            active_lines.append(line)
        else:
            active_lines.append(line)
            
    print(f"\nFound {len(empty_keys)} empty/commented slots to fill. Highest index: {highest_idx}")
    
    def get_next_key_name():
        nonlocal highest_idx
        if empty_keys:
            return empty_keys.pop(0)
        highest_idx += 1
        return f"GEMINI_API_KEY_{highest_idx}"
    
    # --- PHASE 2: Fetch Accounts & Regenerate ---
    print("\n--- PHASE 2: Fetching Google Accounts ---")
    accounts, original_active = get_gcloud_accounts()
    if not accounts:
        print("No accounts logged in. Running 'gcloud auth login'...")
        subprocess.run(f"{get_gcloud_cmd()} auth login", shell=True, check=True)
        accounts, original_active = get_gcloud_accounts()
        if not accounts:
            print("Failed to authenticate. Exiting.")
            return
            
    print(f"Logged-in accounts: {', '.join(accounts)}")
    new_keys_map = {}
    used_projects = set()
    
    for acc in accounts:
        print(f"\nChecking projects for account: {acc}")
        projects = get_gcp_projects(acc)
        if not projects:
            print(f"No projects found or API disabled for {acc}")
            continue
            
        for project in projects:
            if project in used_projects:
                print(f"  [SKIP] Project '{project}' already has a key in .env")
                continue
                
            print(f"Checking existing keys in project: {project}")
            got_key_from_project = False
            try:
                list_res = subprocess.run(
                    f'{get_gcloud_cmd()} services api-keys list --project="{project}" --format=json',
                    shell=True, capture_output=True, text=True
                )
                if list_res.returncode == 0:
                    existing_keys = json.loads(list_res.stdout)
                    for k in existing_keys:
                        if not is_gemini_key(k, project):
                            print(f"    [SKIP] Key '{k.get('displayName')}' is not a Gemini/AI Studio key.")
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
                                        print(f"  [SKIP] Key already in .env, skipping")
                                        used_projects.add(project)
                                        got_key_from_project = True
                                        break
                                    key_name = get_next_key_name()
                                    save_key_to_env(key_name, k_str)
                                    new_keys_map[key_name] = k_str
                                    existing_values.add(k_str)
                                    used_projects.add(project)
                                    got_key_from_project = True
                                    print(f"  [SAVED EXISTING] {key_name} -> {k_str[:8]}...")
                                    break
            except Exception:
                pass
                
            if got_key_from_project:
                continue
                
            pid_lower = project.lower()
            is_valid_ai_studio_project = pid_lower.startswith("gen-lang-client-") or pid_lower in ("fire-house-api", "backfire-api")
            if not is_valid_ai_studio_project:
                print(f"  [SKIP] Project '{project}' is not an AI Studio project. Skipping generation.")
                continue
                
            print(f"Generating new keys in project: {project}")
            keys_created = 0
            while keys_created < 1:
                key_name = get_next_key_name()
                new_key = create_api_key(project, key_name)
                if new_key:
                    save_key_to_env(key_name, new_key)
                    new_keys_map[key_name] = new_key
                    existing_values.add(new_key)
                    used_projects.add(project)
                    keys_created += 1
                    print(f"  [SUCCESS & SAVED] Created new key for {key_name}")
                else:
                    break
                    
        # If we still have empty slots OR we want to support dynamic addition for this account,
        # we can dynamically create a new AI Studio project for this account to help fill empty slots/add new keys!
        # Enforce that the total number of projects in the account (existing + newly created) must not exceed 10.
        new_projects_created = 0
        while (len(projects) + new_projects_created) < 10:
            valid_unused_projects = [
                p for p in projects 
                if p not in used_projects and (p.lower().startswith("gen-lang-client-") or p.lower() in ("fire-house-api", "backfire-api"))
            ]
            if not projects or not valid_unused_projects:
                import random
                import string
                rand_id = "".join(random.choices(string.digits, k=9))
                new_project_id = f"gen-lang-client-{rand_id}"
                key_name = get_next_key_name()
                
                print(f"  Account '{acc}' (projects count: {len(projects) + new_projects_created}) needs projects to fill empty key slots. Auto-creating GCP Project: {new_project_id}...")
                
                try:
                    # 1. Create project
                    p_create = subprocess.run(
                        f'{get_gcloud_cmd()} projects create "{new_project_id}" --name="AI Studio Project" --format=json',
                        shell=True, capture_output=True, text=True
                    )
                    if p_create.returncode != 0:
                        print(f"    Failed to create project: {p_create.stderr.strip()}")
                        break
                        
                    # 2. Enable API
                    print(f"    Project created. Enabling Generative Language API in {new_project_id}...")
                    api_enable = subprocess.run(
                        f'{get_gcloud_cmd()} services enable generativelanguage.googleapis.com --project="{new_project_id}"',
                        shell=True, capture_output=True, text=True
                    )
                    if api_enable.returncode != 0:
                        print(f"    Failed to enable API: {api_enable.stderr.strip()}")
                        break
                        
                    # 3. Create Key
                    print(f"    API enabled. Creating key '{key_name}' in {new_project_id}...")
                    new_key = create_api_key(new_project_id, key_name)
                    if new_key:
                        save_key_to_env(key_name, new_key)
                        new_keys_map[key_name] = new_key
                        existing_values.add(new_key)
                        used_projects.add(new_project_id)
                        projects.append(new_project_id)  # count towards projects limit
                        new_projects_created += 1
                        print(f"    [CREATED & SAVED NEW PROJECT KEY] {key_name} -> {new_key[:8]}...")
                    else:
                        break
                except Exception as e:
                    print(f"    Error during dynamic project/key creation: {e}")
                    break
            else:
                break
                    
    # Restore active account
    if original_active:
        subprocess.run(f'{get_gcloud_cmd()} config set account "{original_active}"', shell=True, stdout=subprocess.DEVNULL, check=True)
        
    print(f"\n--- ONE-CLICK RUN COMPLETE ---")
    print(f"Total keys cleaned and updated: {len(new_keys_map)}")
    print(f"Updated .env saved successfully.")

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
