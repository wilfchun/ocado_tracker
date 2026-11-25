"""Aggregate data quality without exposing source metadata."""

from alembic import op

revision = "004"
down_revision = "003"
branch_labels = depends_on = None


def upgrade():
    op.execute("""CREATE VIEW analytics.import_health AS SELECT
      (SELECT max(completed_at) FROM scan_state) AS last_complete_scan,
      (SELECT max(created_at) FROM orders) AS last_imported_order,
      (SELECT count(*) FROM source_messages WHERE status = 'failed') AS failed_sources,
      (SELECT count(*) FROM source_messages WHERE status = 'pending') AS pending_sources,
      (SELECT count(*) FROM orders WHERE status = 'provisional') AS provisional_orders,
      (SELECT count(*) FROM products WHERE category_id IS NULL) AS uncategorised_products""")


def downgrade():
    op.execute("DROP VIEW analytics.import_health")
