#!/usr/bin/env python3
"""
Test script for Model Aliases API endpoints
"""
import requests
import json
import sys

import os

BASE_URL = os.getenv("ROUTER_API_BASE", f"http://localhost:{os.getenv('ROUTER_API_PORT', 58100)}")
USERNAME = os.getenv("ROUTER_API_ADMIN_USER", "admin")
PASSWORD = os.getenv("ROUTER_API_ADMIN_PASS", "1234")

def login():
    """Get auth token"""
    response = requests.post(f"{BASE_URL}/dashboard/login", json={
        "username": USERNAME,
        "password": PASSWORD
    })
    response.raise_for_status()
    data = response.json()
    return data.get("session_token") or data.get("token")

def _headers(token):
    return {"Authorization": f"Bearer {token}", "X-Dashboard-Token": token}

def test_admin_list_aliases(token):
    """Test GET /dashboard/admin/aliases"""
    print("\n[TEST] GET /dashboard/admin/aliases")
    response = requests.get(
        f"{BASE_URL}/dashboard/admin/aliases",
        headers=_headers(token)
    )
    print(f"Status: {response.status_code}")
    print(f"Response: {json.dumps(response.json(), indent=2)}")
    return response.json()

def test_admin_create_alias(token):
    """Test POST /dashboard/admin/aliases"""
    print("\n[TEST] POST /dashboard/admin/aliases")
    response = requests.post(
        f"{BASE_URL}/dashboard/admin/aliases",
        headers=_headers(token),
        json={
            "alias_name": "gpt-4-test",
            "target_model": "gemini-flash",
            "target_endpoint": None,
            "label": "Test alias for Cursor AI",
            "account_key_id": None
        }
    )
    print(f"Status: {response.status_code}")
    print(f"Response: {json.dumps(response.json(), indent=2)}")
    return response.json()

def test_admin_update_alias(token, alias_id):
    """Test PATCH /dashboard/admin/aliases/{alias_id}"""
    print(f"\n[TEST] PATCH /dashboard/admin/aliases/{alias_id}")
    response = requests.patch(
        f"{BASE_URL}/dashboard/admin/aliases/{alias_id}",
        headers=_headers(token),
        json={
            "target_model": "gemini-pro",
            "label": "Updated label",
            "enabled": True
        }
    )
    print(f"Status: {response.status_code}")
    print(f"Response: {json.dumps(response.json(), indent=2)}")
    return response.json()

def test_admin_delete_alias(token, alias_id):
    """Test DELETE /dashboard/admin/aliases/{alias_id}"""
    print(f"\n[TEST] DELETE /dashboard/admin/aliases/{alias_id}")
    response = requests.delete(
        f"{BASE_URL}/dashboard/admin/aliases/{alias_id}",
        headers=_headers(token)
    )
    print(f"Status: {response.status_code}")
    print(f"Response: {json.dumps(response.json(), indent=2)}")
    return response.json()

def test_member_list_aliases(token):
    """Test GET /api/me/aliases"""
    print("\n[TEST] GET /api/me/aliases")
    response = requests.get(
        f"{BASE_URL}/api/me/aliases",
        headers=_headers(token)
    )
    print(f"Status: {response.status_code}")
    print(f"Response: {json.dumps(response.json(), indent=2)}")
    return response.json()

def test_member_create_alias(token):
    """Test POST /api/me/aliases"""
    print("\n[TEST] POST /api/me/aliases")
    response = requests.post(
        f"{BASE_URL}/api/me/aliases",
        headers=_headers(token),
        json={
            "alias_name": "claude-3-5-sonnet-test",
            "target_model": "gemini-flash",
            "target_endpoint": None,
            "label": "Member self-service alias",
            "account_key_id": None
        }
    )
    print(f"Status: {response.status_code}")
    print(f"Response: {json.dumps(response.json(), indent=2)}")
    return response.json()

def main():
    print("=== Model Aliases API Test ===")

    try:
        # Login
        print("\n[LOGIN]")
        token = login()
        print(f"✅ Logged in successfully")

        # Admin tests
        print("\n--- ADMIN TESTS ---")

        # List (should be empty initially)
        list_result = test_admin_list_aliases(token)

        # Create
        create_result = test_admin_create_alias(token)
        alias_id = create_result.get("alias_id")

        # List again (should have 1)
        test_admin_list_aliases(token)

        # Update
        if alias_id:
            test_admin_update_alias(token, alias_id)

        # List after update
        test_admin_list_aliases(token)

        # Delete
        if alias_id:
            test_admin_delete_alias(token, alias_id)

        # List after delete (should be empty)
        test_admin_list_aliases(token)

        # Member tests
        print("\n--- MEMBER TESTS ---")
        test_member_list_aliases(token)
        member_result = test_member_create_alias(token)
        test_member_list_aliases(token)

        # Cleanup member alias
        if member_result.get("alias_id"):
            test_admin_delete_alias(token, member_result["alias_id"])

        print("\n✅ All tests completed!")

    except requests.exceptions.HTTPError as e:
        print(f"\n❌ HTTP Error: {e}")
        print(f"Response: {e.response.text}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
