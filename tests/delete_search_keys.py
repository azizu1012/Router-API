import subprocess
import json
import os
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

def delete_key(project_id, key_name):
    try:
        print(f"Deleting key {key_name} in project {project_id}...")
        subprocess.run(
            f'{get_gcloud_cmd()} services api-keys delete "{key_name}" --location=global --project="{project_id}" --quiet',
            shell=True, check=True
        )
        print("Deleted successfully!")
        return True
    except Exception as e:
        print(f"Failed to delete key: {e}")
        return False

def main():
    if not check_gcloud():
        print("Error: 'gcloud' CLI tool is not installed.")
        sys.exit(1)
        
    accounts, original_active = get_gcloud_accounts()
    if not accounts:
        print("No authenticated Google accounts found.")
        return
        
    deleted_total = 0
    
    for acc in accounts:
        print(f"\n>>> Checking account: {acc}")
        if not set_active_account(acc):
            continue
            
        projects = get_gcp_projects()
        for project in projects:
            print(f"Scanning keys in project: {project}...")
            try:
                # List keys in JSON format
                result = subprocess.run(
                    f'{get_gcloud_cmd()} services api-keys list --project="{project}" --format=json',
                    shell=True, capture_output=True, text=True, check=True
                )
                keys_data = json.loads(result.stdout)
                
                for key_info in keys_data:
                    display_name = key_info.get("displayName", "").lower()
                    resource_name = key_info.get("name") # projects/.../keys/...
                    restrictions = key_info.get("restrictions", {})
                    api_targets = restrictions.get("apiTargets", [])
                    
                    # Check if the key has "search" in its display name
                    is_search_key = "search" in display_name
                    
                    # Also check if it is restricted to search services (e.g. customsearch.googleapis.com)
                    for target in api_targets:
                        service = target.get("service", "").lower()
                        if "search" in service:
                            is_search_key = True
                            
                    if is_search_key:
                        print(f"-> Found search-related API key: '{key_info.get('displayName')}'")
                        if delete_key(project, resource_name):
                            deleted_total += 1
                            
            except Exception:
                # API Keys API might not be enabled in this project
                pass
                
    # Restore original active account
    if original_active:
        set_active_account(original_active)
        
    print(f"\n=== Search API Keys Cleanup Complete ===")
    print(f"Total search API keys deleted from your projects: {deleted_total}")

if __name__ == "__main__":
    main()
