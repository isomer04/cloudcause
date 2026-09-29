-- Uploaded datasets, shared by every process that has to rebuild one from its id.
--
-- dataset_json holds the normalized contract payload; the columns beside it exist
-- for TTL eviction, the store-wide byte cap, and refusing an unsealed dataset
-- without deserializing it first. Nothing raw from the upload is stored here.

CREATE TABLE IF NOT EXISTS cloudcause_datasets (
    dataset_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    sealed INTEGER NOT NULL,
    byte_size INTEGER NOT NULL,
    record_count INTEGER NOT NULL,
    dataset_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS cloudcause_datasets_expires_at_idx ON cloudcause_datasets (expires_at);
