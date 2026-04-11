-- =============================================================================
-- Fix ranking_snapshots unique constraint on the partitioned table
-- =============================================================================
-- The 20260409 partition migration created:
--   UNIQUE (platform_listing_id, chart_type, region, snapshot_date, id)
-- but dedup.process_ranking_entries does:
--   INSERT ... ON CONFLICT (platform_listing_id, chart_type, region, snapshot_date)
-- which fails with "no unique or exclusion constraint matching the ON CONFLICT
-- specification" because the existing constraint has the extra `id` column.
--
-- Fix: drop the over-broad constraint and recreate it without `id`. The
-- resulting constraint still includes snapshot_date (the partition key), which
-- is what PostgreSQL requires for partitioned tables.
--
-- Idempotent — safe to re-run.
-- =============================================================================

DO $$
DECLARE
    cons RECORD;
BEGIN
    -- Drop any UNIQUE constraint on ranking_snapshots that doesn't match the
    -- canonical 4-column tuple. We iterate defensively — constraint names are
    -- auto-generated and could vary across environments.
    FOR cons IN
        SELECT conname
        FROM pg_constraint c
        JOIN pg_class t ON c.conrelid = t.oid
        WHERE t.relname = 'ranking_snapshots'
          AND c.contype = 'u'
          AND conname <> 'ranking_snapshots_unique_key'
    LOOP
        EXECUTE format('ALTER TABLE ranking_snapshots DROP CONSTRAINT IF EXISTS %I', cons.conname);
        RAISE NOTICE 'dropped stale unique constraint %', cons.conname;
    END LOOP;

    -- Check whether the canonical constraint already exists
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint c
        JOIN pg_class t ON c.conrelid = t.oid
        WHERE t.relname = 'ranking_snapshots'
          AND c.conname = 'ranking_snapshots_unique_key'
    ) THEN
        EXECUTE '
            ALTER TABLE ranking_snapshots
            ADD CONSTRAINT ranking_snapshots_unique_key
            UNIQUE (platform_listing_id, chart_type, region, snapshot_date)
        ';
        RAISE NOTICE 'added ranking_snapshots_unique_key';
    ELSE
        RAISE NOTICE 'ranking_snapshots_unique_key already present, skipping';
    END IF;
END$$;
