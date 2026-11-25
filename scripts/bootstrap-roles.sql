-- Run as cluster administrator, before creating the purchases database.
-- Set passwords separately with psql \password or configure local peer auth.
DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'purchases_owner') THEN
    CREATE ROLE purchases_owner LOGIN;
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'purchases_importer') THEN
    CREATE ROLE purchases_importer LOGIN;
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'purchases_reader') THEN
    CREATE ROLE purchases_reader LOGIN;
  END IF;
END $$;
