-- Bring-your-own-data: remember which uploaded dataset an investigation used.
--
-- The id is an opaque token minted by the gateway, not user data, so unlike the
-- account identifiers beside it there is nothing to hash. It outlives the
-- dataset on purpose: history is permanent, a dataset lives two hours, and a
-- stored investigation whose dataset has expired must be able to say so.
--
-- Portable DDL: ALTER TABLE ... ADD COLUMN with no constraint runs identically
-- on SQLite and PostgreSQL. The column is nullable because every investigation
-- recorded before this migration ran on fixtures or a seeded scenario.

ALTER TABLE investigations ADD COLUMN dataset_id TEXT;
