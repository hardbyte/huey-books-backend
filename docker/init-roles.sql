-- Create application roles for least-privilege access.
-- This runs once on first postgres container startup via /docker-entrypoint-initdb.d/.
-- Grants are managed declaratively by pgroles (see pgroles.yaml).

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'cloudrun') THEN
        CREATE ROLE cloudrun LOGIN PASSWORD 'cloudrun';
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'readonly') THEN
        CREATE ROLE readonly LOGIN PASSWORD 'readonly';
    END IF;
END
$$;
