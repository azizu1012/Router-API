-- Migration 007: Model Aliases and Self-Managed Custom Endpoints
-- Allows users to:
-- 1. Create model name aliases (e.g., "claude-3-5-sonnet" → "gemini-flash")
-- 2. Spoof model names to bypass client regex validation
-- 3. Self-manage custom endpoints without admin approval

-- Model Aliases table
CREATE TABLE IF NOT EXISTS model_aliases (
    alias_id TEXT PRIMARY KEY,              -- UUID
    account_id TEXT NOT NULL,               -- Owner of this alias
    account_key_id TEXT DEFAULT NULL,       -- NULL = account-wide, non-NULL = key-specific

    -- Alias definition
    alias_name TEXT NOT NULL,               -- Name user will call (e.g., "claude-3-5-sonnet")
    target_model TEXT NOT NULL,             -- Actual model (e.g., "gemini-flash")
    target_endpoint TEXT DEFAULT NULL,      -- NULL = use pool, non-NULL = custom endpoint name

    -- Metadata
    enabled INTEGER DEFAULT 1,              -- 1 = active, 0 = disabled
    label TEXT DEFAULT '',                  -- User description
    created_at INTEGER NOT NULL,            -- Unix timestamp
    updated_at INTEGER NOT NULL,            -- Unix timestamp

    -- Constraints
    UNIQUE(account_id, account_key_id, alias_name),
    FOREIGN KEY(account_id) REFERENCES accounts(account_id) ON DELETE CASCADE
);

-- Indexes for fast lookup
CREATE INDEX IF NOT EXISTS idx_model_aliases_lookup
    ON model_aliases(account_id, account_key_id, alias_name, enabled);

CREATE INDEX IF NOT EXISTS idx_model_aliases_account
    ON model_aliases(account_id, enabled);

-- Update custom_endpoints table to support member ownership
-- (account_id column already exists from previous migrations)

-- Add index for member-owned endpoints lookup
CREATE INDEX IF NOT EXISTS idx_custom_endpoints_account
    ON custom_endpoints(account_id, enabled);

-- Migration complete
-- To rollback:
-- DROP TABLE IF EXISTS model_aliases;
-- DROP INDEX IF EXISTS idx_model_aliases_lookup;
-- DROP INDEX IF EXISTS idx_model_aliases_account;
-- DROP INDEX IF EXISTS idx_custom_endpoints_account;
