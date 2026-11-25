-- Run in the purchases DB as its owner/admin AFTER every migration.
-- PUBLIC has no application privileges. Owner keeps schema and view ownership.
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC;
REVOKE ALL ON SCHEMA analytics FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO purchases_importer;
GRANT SELECT ON categories TO purchases_importer;
GRANT SELECT, INSERT, UPDATE, DELETE ON products, orders, purchase_items, source_messages, scan_state TO purchases_importer;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO purchases_importer;
GRANT USAGE ON SCHEMA analytics TO purchases_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA analytics TO purchases_reader;
DO $$ BEGIN
  EXECUTE format('GRANT CONNECT ON DATABASE %I TO purchases_importer, purchases_reader', current_database());
END $$;
