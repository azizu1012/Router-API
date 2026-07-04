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

def get_leaked_and_failed_keys():
    bak_path = ".env.bak"
    env_path = ".env"
    
    if not os.path.exists(env_path):
        print("Error: No .env file found.")
        sys.exit(1)
        
    invalid_keys = set()
    key_pattern = re.compile(r'^(?:#\s*(?:BANNED|LEAKED_BANNED|LEAKED)?[^:]*:\s*|#\s*)?(GEMINI_API_KEY_\d+)\s*=\s*(.*)$')
    
    # We compare .env and .env.bak
    # Any key that is empty in .env but has a value in .env.bak is invalid (either leaked or failed/old CSE key!)
    if os.path.exists(bak_path):
        env_keys = {}
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                match = key_pattern.match(line.strip())
                if match:
                    env_keys[match.group(1)] = match.group(2).strip().strip('"').strip("'")
                    
        with open(bak_path, "r", encoding="utf-8") as f:
            for line in f:
                match = key_pattern.match(line.strip())
                if match:
                    name = match.group(1)
                    bak_val = match.group(2).strip().strip('"').strip("'")
                    # If it is empty in .env but had a value in .env.bak -> it was cleared out as invalid!
                    if bak_val and not env_keys.get(name):
                        invalid_keys.add(bak_val)
    else:
        # Fallback: read .env and collect keys that are commented out with LEAKED or BANNED
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                if "LEAKED" in line or "BANNED" in line:
                    match = key_pattern.match(line.strip())
                    if match and match.group(2).strip().strip('"').strip("'"):
                        invalid_keys.add(match.group(2).strip().strip('"').strip("'"))
                        
    return invalid_keys

def get_gcp_projects():
    try:
        result = subprocess.run(
            f"{get_gcloud_cmd()} projects list --format=json",
            shell=True, capture_output=True, text=True, check=True
        )
        projects = json.loads(result.stdout)
        return [p["projectId"] for p in projects]
    except Exception as e:
        print(f"Error listing projects: {e}")
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
    except subprocess.CalledProcessError as e:
        print(f"Failed to delete key: {e}")
        return False

def main():
    if not check_gcloud():
        print("Error: 'gcloud' CLI tool is not installed or not in PATH.")
        print("Please install Google Cloud SDK and authenticate with 'gcloud auth login' first.")
        sys.exit(1)
        
    invalid_vals = get_leaked_and_failed_keys()
    if not invalid_vals:
        print("No leaked or failed keys identified to delete. Make sure your .env and .env.bak are present.")
        return
        
    print(f"Loaded {len(invalid_vals)} invalid (leaked/failed/CSE) key strings for deletion.")
    
    projects = get_gcp_projects()
    if not projects:
        print("No active GCP projects found or you are not logged in. Run 'gcloud auth login' first.")
        return
        
    print(f"Scanning {len(projects)} projects: {', '.join(projects)}")
    deleted_total = 0
    
    for project in projects:
        print(f"\nScanning keys in project: {project}...")
        try:
            # List keys in JSON format
            result = subprocess.run(
                f'{get_gcloud_cmd()} services api-keys list --project="{project}" --format=json',
                shell=True, capture_output=True, text=True, check=True
            )
            keys_data = json.loads(result.stdout)
            
            for key_info in keys_data:
                key_str = key_info.get("keyString")
                resource_name = key_info.get("name") # e.g. projects/.../keys/...
                display_name = key_info.get("displayName", "Unnamed Key")
                
                if key_str in invalid_vals:
                    print(f"-> Found matching invalid key '{display_name}' ({key_str[:6]}...)")
                    if delete_key(project, resource_name):
                        deleted_total += 1
                        
        except subprocess.CalledProcessError:
            pass
            
    print(f"\n=== Deletion Complete ===")
    print(f"Total invalid keys deleted from Google Cloud Console: {deleted_total}")

if __name__ == "__main__":
    main()
