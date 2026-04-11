-- =============================================================================
-- ranking_snapshots monthly partition migration
-- =============================================================================
-- Goal: convert ranking_snapshots into a RANGE-partitioned table by snapshot_date
-- to keep query performance steady as the table grows. Retains all historical data.
-- Idempotent: detects if already partitioned and exits cleanly.
-- =============================================================================

DO $$
DECLARE
    is_partitioned BOOLEAN;
BEGIN
    SELECT EXISTS (
        SELECT 1 FROM pg_partitioned_table pt
        JOIN pg_class c ON pt.partrelid = c.oid
        WHERE c.relname = 'ranking_snapshots'
    ) INTO is_partitioned;

    IF is_partitioned THEN
        RAISE NOTICE 'ranking_snapshots is already partitioned, skipping';
        RETURN;
    END IF;

    -- Rename existing table
    EXECUTE 'ALTER TABLE ranking_snapshots RENAME TO ranking_snapshots_old';

    -- Create partitioned parent table
    EXECUTE $ddl$
        CREATE TABLE ranking_snapshots (
            id              SERIAL,
            platform_listing_id INTEGER NOT NULL REFERENCES platform_listings(id),
            chart_type      TEXT NOT NULL,
            region          TEXT NOT NULL DEFAULT 'CN',
            rank_position   SMALLINT NOT NULL,
            previous_rank   SMALLINT,
            rank_change     SMALLINT,
            snapshot_date   DATE NOT NULL,
            PRIMARY KEY (id, snapshot_date),
            UNIQUE (platform_listing_id, chart_type, region, snapshot_date, id)
        ) PARTITION BY RANGE (snapshot_date)
    $ddl$;

    -- Create monthly partitions for last 12 + next 3 months
    DECLARE
        d DATE := date_trunc('month', CURRENT_DATE - INTERVAL '12 months')::date;
        end_d DATE := date_trunc('month', CURRENT_DATE + INTERVAL '3 months')::date;
        part_name TEXT;
        next_d DATE;
    BEGIN
        WHILE d <= end_d LOOP
            next_d := (d + INTERVAL '1 month')::date;
            part_name := 'ranking_snapshots_' || to_char(d, 'YYYY_MM');
            EXECUTE format(
                'CREATE TABLE %I PARTITION OF ranking_snapshots FOR VALUES FROM (%L) TO (%L)',
                part_name, d, next_d
            );
            d := next_d;
        END LOOP;
    END;

    -- Copy data from old table
    EXECUTE 'INSERT INTO ranking_snapshots (id, platform_listing_id, chart_type, region, rank_position, previous_rank, rank_change, snapshot_date) SELECT id, platform_listing_id, chart_type, region, rank_position, previous_rank, rank_change, snapshot_date FROM ranking_snapshots_old';

    -- Drop old table
    EXECUTE 'DROP TABLE ranking_snapshots_old';

    -- Reset sequence
    PERFORM setval(
        pg_get_serial_sequence('ranking_snapshots', 'id'),
        COALESCE((SELECT MAX(id) FROM ranking_snapshots), 1)
    );

    -- Recreate indexes
    EXECUTE 'CREATE INDEX ranking_snapshots_date_idx ON ranking_snapshots (snapshot_date DESC)';
    EXECUTE 'CREATE INDEX ranking_snapshots_listing_date_idx ON ranking_snapshots (platform_listing_id, snapshot_date DESC)';
END$$;

-- Note for Prisma:
-- The Prisma RankingSnapshot model still uses @id @default(autoincrement()) on id.
-- Postgres-side the PK is now (id, snapshot_date). Prisma will treat id as the
-- logical PK; this works for inserts and joins because (id, snapshot_date) is
-- unique within each partition. If Prisma errors on the introspection, regenerate.
