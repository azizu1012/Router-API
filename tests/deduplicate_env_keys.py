import subprocess
import json
import os
import re
import sys

# Helper to resolve local gcloud.cmd path if present in workspace
def get_gcloud_cmd():
    local_gcloud = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "gcloud_sdk", "google-cloud-sdk", "bin", "gcloud.cmd"))
    if os.path.exists(local_gcloud):
        return f'"{local_gcloud}"'
    return "gcloud"

def check_gcloud():
    try:
        subprocess.run(f"{get_gcloud_cmd()} --version", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        return True
    except Exception:
        return False

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

def set_active_account(account):
    try:
        subprocess.run(
            f'{get_gcloud_cmd()} config set account "{account}"',
            shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True
        )
        return True
    except subprocess.CalledProcessError:
        return False

def get_gcp_projects():
    try:
        result = subprocess.run(
            f"{get_gcloud_cmd()} projects list --format=json",
            shell=True, capture_output=True, text=True, check=True
        )
        projects = json.loads(result.stdout)
        return [p["projectId"] for p in projects]
    except Exception:
        return []

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

def main():
    if not check_gcloud():
        print("Error: 'gcloud' CLI tool is not installed.")
        sys.exit(1)
        
    accounts, original_active = get_gcloud_accounts()
    if not accounts:
        print("No authenticated Google accounts found.")
        return
        
    print("Scanning all accounts and projects to map API keys to their projects...")
    
    # Map to store: keyString -> project_id
    key_to_project = {}
    
    for acc in accounts:
        print(f"\nScanning account: {acc}")
        if not set_active_account(acc):
            continue
            
        projects = get_gcp_projects()
        for project in projects:
            print(f"  Project: {project}")
            try:
                # List API keys in this project
                result = subprocess.run(
                    f'{get_gcloud_cmd()} services api-keys list --project="{project}" --format=json',
                    shell=True, capture_output=True, text=True, check=True
                )
                keys_data = json.loads(result.stdout)
                
                for k in keys_data:
                    if not is_gemini_key(k, project):
                        print(f"    [SKIP] Key '{k.get('displayName')}' is not a Gemini/AI Studio key.")
                        continue
                    res_name = k.get("name")
                    if res_name:
                        # Fetch the actual key string
                        str_res = subprocess.run(
                            f'{get_gcloud_cmd()} services api-keys get-key-string "{res_name}" --project="{project}" --format=json',
                            shell=True, capture_output=True, text=True
                        )
                        if str_res.returncode == 0:
                            k_str = json.loads(str_res.stdout).get("keyString")
                            if k_str:
                                key_to_project[k_str] = project
                                
            except Exception:
                pass
                
    # Restore original active account
    if original_active:
        set_active_account(original_active)
        
    # Read local .env
    env_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".env"))
    if not os.path.exists(env_path):
        print(f"\nError: .env file not found at {env_path}")
        return
        
    with open(env_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
        
    key_pattern = re.compile(r'^(?:#\s*(?:BANNED|LEAKED_BANNED|LEAKED)?[^:]*:\s*|#\s*)?(GEMINI_API_KEY_\d+)\s*=\s*(.*)$')
    
    new_lines = []
    used_projects = set()
    seen_values = set()
    cleaned_count = 0
    kept_count = 0
    
    print("\nProcessing local .env file...")
    
    for line in lines:
        match = key_pattern.match(line.strip())
        if match:
            key_name = match.group(1)
            key_val = match.group(2).strip().strip('"').strip("'")
            
            if not key_val:
                new_lines.append(f"{key_name}=\n")
                continue
                
            # 1. Check for exact duplicate values in the file
            if key_val in seen_values:
                print(f"-> [CLEARED] {key_name} (Duplicate value of a previously seen key)")
                new_lines.append(f"{key_name}=\n")
                cleaned_count += 1
                continue
                
            seen_values.add(key_val)
            
            # 2. Check if this key belongs to one of our scanned projects
            project_id = key_to_project.get(key_val)
            
            if project_id:
                # Key belongs to scanned project. Deduplicate by project
                if project_id in used_projects:
                    print(f"-> [CLEARED] {key_name} (Duplicate key for project '{project_id}')")
                    new_lines.append(f"{key_name}=\n")
                    cleaned_count += 1
                else:
                    print(f"-> [KEEPING] {key_name} (Project: '{project_id}')")
                    new_lines.append(f"{key_name}={key_val}\n")
                    used_projects.add(project_id)
                    kept_count += 1
            else:
                # Key is not in scanned projects (e.g. friend's key, manual session key starting with AQ.). Keep it!
                print(f"-> [KEEPING] {key_name} (External/Manual key - preserved)")
                new_lines.append(f"{key_name}={key_val}\n")
                kept_count += 1
        else:
            new_lines.append(line)
            
    # Write back to .env
    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)
        
    print(f"\n=== Local Deduplication Complete ===")
    print(f"Kept keys: {kept_count}")
    print(f"Cleared keys in .env: {cleaned_count}")

if __name__ == "__main__":
    main()
