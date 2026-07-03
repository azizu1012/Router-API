import sys
import os
from pathlib import Path

# Thêm thư mục hiện tại vào sys.path để import được src
sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.core.accounts.account_manager import account_manager

# Tạo account mới
try:
    acc = account_manager.create_account(
        name="azuree-admin",
        rpm=999999,
        tpm=999999999,
        rpd=999999,
        tier="admin"
    )
    print(f"Created account: {acc}")
except Exception as e:
    print(f"Error creating account: {e}")

# Cập nhật auth_key và lưu vào DB
import sqlite3
db_path = r"D:\AI_Projects\router_api\usage.db"
conn = sqlite3.connect(db_path)
cursor = conn.cursor()
cursor.execute(
    "UPDATE accounts SET auth_key = ?, tier = 'admin' WHERE name = ?",
    ("sk-iiVUNH2k3QedJAroueymIo0q9qL5TimQ95vJpbNTOK4", "azuree-admin")
)
conn.commit()
conn.close()

print("Admin account elevated with custom key successfully!")
