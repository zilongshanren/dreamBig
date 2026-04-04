-- Enable pg_trgm extension for fuzzy text matching (game deduplication)
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Enable btree_gin for combined index support
CREATE EXTENSION IF NOT EXISTS btree_gin;
