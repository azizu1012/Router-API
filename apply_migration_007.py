#!/usr/bin/env python3
"""
Apply migration 007 - Model Aliases table
"""
import sqlite3
import sys
from pathlib import Path

def apply_migration():
    # Get project root
    project_root = Path(__file__).parent
    db_path = project_root / "usage.db"
    migration_path = project_root / "migrations" / "007_model_aliases.sql"

    if not db_path.exists():
        print(f"❌ Database not found: {db_path}")
        print("Please ensure usage.db exists in project root.")
        sys.exit(1)

    if not migration_path.exists():
        print(f"❌ Migration file not found: {migration_path}")
        sys.exit(1)

    print(f"📂 Database: {db_path}")
    print(f"📄 Migration: {migration_path}")
    print()

    # Read migration SQL
    with open(migration_path, 'r', encoding='utf-8') as f:
        migration_sql = f.read()

    # Connect to database
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()

    try:
        # Check if table already exists
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='model_aliases'")
        if cursor.fetchone():
            print("⚠️  Table 'model_aliases' already exists.")
            print("Migration may have been applied before.")

            # Ask user if they want to continue
            response = input("\nContinue anyway? (y/N): ").strip().lower()
            if response != 'y':
                print("❌ Migration cancelled.")
                conn.close()
                sys.exit(0)

        # Execute migration
        print("🔄 Applying migration...")
        cursor.executescript(migration_sql)
        conn.commit()

        # Verify table was created
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='model_aliases'")
        if cursor.fetchone():
            print("✅ Migration applied successfully!")
            print()
            print("Created:")
            print("  - Table: model_aliases")
            print("  - Index: idx_model_aliases_lookup")
            print("  - Index: idx_model_aliases_account")
            print("  - Index: idx_custom_endpoints_account")
        else:
            print("❌ Migration failed - table not created")
            sys.exit(1)

        # Show table schema
        print("\n📋 Table schema:")
        cursor.execute("PRAGMA table_info(model_aliases)")
        for row in cursor.fetchall():
            col_id, name, type_, notnull, default, pk = row
            print(f"  {name:20} {type_:12} {'NOT NULL' if notnull else ''} {'PRIMARY KEY' if pk else ''}")

    except Exception as e:
        print(f"❌ Error applying migration: {e}")
        conn.rollback()
        sys.exit(1)
    finally:
        conn.close()

if __name__ == "__main__":
    apply_migration()
