from src.core.providers import _custom_endpoint_manager
eps = _custom_endpoint_manager.list_endpoints()
if not eps:
    print("NO custom endpoints configured")
else:
    for e in eps:
        print(f"Name: {e.get('name')}")
        print(f"  pool_assignments: {e.get('pool_assignments')}")
        print(f"  fallback: {e.get('fallback', 0)}")
        print(f"  enabled: {e.get('enabled', True)}")
        print(f"  base_url: {e.get('base_url', 'N/A')}")
        print()
