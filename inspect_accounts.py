import sqlite3
import os

db_path = r"D:\AI_Projects\router_api\usage.db"

if not os.path.exists(db_path):
    print("Database not found!")
    exit(1)

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# Lấy cột của bảng accounts
cursor.execute("PRAGMA table_info(accounts);")
cols = [col[1] for col in cursor.fetchall()]
print(f"Columns: {cols}")

# Lấy dữ liệu
cursor.execute("SELECT * FROM accounts")
rows = cursor.fetchall()
print("Accounts rows:")
for r in rows:
    print(r)

conn.close()
