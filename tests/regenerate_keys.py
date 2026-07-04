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
        
        active_account = None
        all_accounts = []
        
        for item in accounts_data:
            acc = item.get("account")
            if acc:
                all_accounts.append(acc)
                if item.get("status") == "ACTIVE":
                    active_account = acc
                    
        return all_accounts, active_account
    except Exception as e:
        print(f"Error listing gcloud accounts: {e}")
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

def create_api_key(project_id, key_display_name):
    try:
        print(f"Creating key '{key_display_name}' in project '{project_id}'...")
        result = subprocess.run(
            f'{get_gcloud_cmd()} services api-keys create --display-name="{key_display_name}" --project="{project_id}" --api-target="service=generativelanguage.googleapis.com" --format=json',
            shell=True, capture_output=True, text=True, check=True
        )
        key_data = json.loads(result.stdout)
        key_str = key_data.get("keyString")
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
                    
    except Exception as e:
        print(f"Failed to create key: {e}")
    return None

def main():
    if not check_gcloud():
        print("Error: 'gcloud' CLI tool is not installed or not in PATH.")
        print("Please install Google Cloud SDK and authenticate with 'gcloud auth login' first.")
        sys.exit(1)
        
    env_path = ".env"
    if not os.path.exists(env_path):
        print("Error: No .env file found.")
        return
        
    accounts, original_active = get_gcloud_accounts()
    if not accounts:
        print("No authenticated Google accounts found. Run 'gcloud auth login' for your accounts first.")
        return
        
    print(f"Found authenticated accounts: {', '.join(accounts)}")
    if original_active:
        print(f"Current active account: {original_active}")
        
    # Read current .env
    with open(env_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
        
    key_pattern = re.compile(r'^(GEMINI_API_KEY_\d+)\s*=\s*(.*)$')
    
    # Extract empty keys to regenerate
    empty_keys = []
    for line in lines:
        match = key_pattern.match(line.strip())
        if match:
            key_name = match.group(1)
            key_val = match.group(2).strip().strip('"').strip("'")
            if not key_val:
                empty_keys.append(key_name)
                
    if not empty_keys:
        print("No empty key slots in .env. Nothing to do!")
        return
        
    print(f"Found {len(empty_keys)} empty key slots to fill.")
    new_keys_map = {}
    
    # Loop accounts to fill slots
    for acc in accounts:
        if not empty_keys:
            break
            
        print(f"\n>>> Switching to account: {acc}")
        if not set_active_account(acc):
            print(f"Failed to switch to account: {acc}")
            continue
            
        projects = get_gcp_projects()
        if not projects:
            print(f"No GCP projects found or accessible for account: {acc}")
            continue
            
        print(f"Found {len(projects)} projects for {acc}: {', '.join(projects)}")
        
        # Distribute empty keys across available projects of this account
        for project in projects:
            if not empty_keys:
                break
                
            print(f"Generating keys in project '{project}'...")
            keys_created = 0
            
            while empty_keys and keys_created < 5:
                key_name = empty_keys.pop(0)
                new_key = create_api_key(project, key_name)
                if new_key:
                    new_keys_map[key_name] = new_key
                    keys_created += 1
                else:
                    # Put it back at the end of the queue to try another project/account
                    empty_keys.append(key_name)
                    break
                    
    # Restore original active account configuration
    if original_active:
        print(f"\nRestoring active account back to: {original_active}")
        set_active_account(original_active)
        
    # Write new keys back to .env
    new_lines = []
    regenerated_count = 0
    for line in lines:
        match = key_pattern.match(line.strip())
        if match:
            key_name = match.group(1)
            if key_name in new_keys_map:
                new_lines.append(f"{key_name}={new_keys_map[key_name]}\n")
                regenerated_count += 1
            else:
                new_lines.append(line)
        else:
            new_lines.append(line)
            
    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)
        
    print(f"\n=== Multi-Account Regeneration Complete ===")
    print(f"Total new keys created and written to .env: {regenerated_count}")
    if empty_keys:
        print(f"Note: {len(empty_keys)} slots could not be filled. Please log in to more accounts or create more projects.")
    print(f"Updated .env saved successfully.")

if __name__ == "__main__":
    main()
