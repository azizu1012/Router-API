"""Migration: Clear pool_assignments for custom endpoints.

This migration clears the deprecated `pool_assignments` field for custom endpoints
that already have an `account_id` or `account_key_id` set, since the new architecture
routes based on account/key assignment rather than pool membership.

Run this after deploying the passthrough architecture.
"""

from src.backend._db import _LOCK, conn as _conn
from src.core.config_n_logg.logger import logger_system as logger


def migrate_clear_pool_assignments():
    """Clear pool_assignments for endpoints with account/key assignments."""
    with _LOCK:
        c = _conn()
        try:
            # First, log which endpoints will be affected
            affected = c.execute("""
                SELECT name, account_id, account_key_id, pool_assignments
                FROM custom_endpoints
                WHERE pool_assignments != '{}'
                  AND pool_assignments IS NOT NULL
                  AND (account_id != '' OR account_key_id IS NOT NULL)
            """).fetchall()

            if not affected:
                logger.info("[Migration] No custom endpoints with pool_assignments to clear")
                return

            logger.info("[Migration] Clearing pool_assignments for %d custom endpoints:", len(affected))
            for row in affected:
                logger.info("  - %s (account: %s, key: %s, old pool_assignments: %s)",
                           row["name"], row["account_id"], row["account_key_id"], row["pool_assignments"])

            # Clear pool_assignments for endpoints with account/key assignments
            c.execute("""
                UPDATE custom_endpoints
                SET pool_assignments = '{}',
                    updated_at = datetime('now')
                WHERE (account_id != '' OR account_key_id IS NOT NULL)
                  AND pool_assignments != '{}'
            """)

            rows_updated = c.rowcount
            c.commit()

            logger.info("[Migration] Successfully cleared pool_assignments for %d endpoints", rows_updated)

            # Log warning for endpoints that still have pool_assignments (no account/key)
            orphaned = c.execute("""
                SELECT name, pool_assignments
                FROM custom_endpoints
                WHERE pool_assignments != '{}'
                  AND pool_assignments IS NOT NULL
                  AND account_id = ''
                  AND account_key_id IS NULL
            """).fetchall()

            if orphaned:
                logger.warning("[Migration] ⚠️ %d custom endpoints have pool_assignments but no account/key assignment:", len(orphaned))
                for row in orphaned:
                    logger.warning("  - %s: %s", row["name"], row["pool_assignments"])
                logger.warning("[Migration] These endpoints need manual review - assign to account or leave as fallback")

        except Exception as e:
            logger.error("[Migration] Failed to clear pool_assignments: %s", e)
            raise
        finally:
            c.close()


def check_migration_status():
    """Check which endpoints still have pool_assignments."""
    with _LOCK:
        c = _conn()
        try:
            rows = c.execute("""
                SELECT name, account_id, account_key_id, pool_assignments
                FROM custom_endpoints
                WHERE pool_assignments != '{}' AND pool_assignments IS NOT NULL
            """).fetchall()

            if not rows:
                logger.info("[Migration] ✅ All custom endpoints migrated (no pool_assignments)")
                return True

            logger.info("[Migration] ⚠️ %d custom endpoints still have pool_assignments:", len(rows))
            for row in rows:
                logger.info("  - %s (account: %s, key: %s, pool_assignments: %s)",
                           row["name"], row["account_id"] or "(none)",
                           row["account_key_id"] or "(none)", row["pool_assignments"])
            return False

        finally:
            c.close()


if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("Custom Endpoint Pool Assignments Migration")
    logger.info("=" * 60)

    logger.info("\n[Step 1] Checking current state...")
    check_migration_status()

    logger.info("\n[Step 2] Running migration...")
    migrate_clear_pool_assignments()

    logger.info("\n[Step 3] Verifying migration...")
    success = check_migration_status()

    if success:
        logger.info("\n✅ Migration completed successfully!")
    else:
        logger.warning("\n⚠️ Migration completed with warnings - manual review needed")
