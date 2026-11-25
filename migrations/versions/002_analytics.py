"""Versioned analytical views."""

from alembic import op

revision = "002"
down_revision = "001"
branch_labels = depends_on = None


def upgrade():
    op.execute(
        "CREATE SCHEMA analytics;\nCREATE VIEW analytics.purchase_lines AS\nSELECT i.id AS purchase_item_id, o.id AS order_id, o.retailer, o.delivery_date, o.status,\n       p.id AS product_id, p.name AS product_name, p.brand, i.source_product_name,\n       COALESCE(parent.name, c.name, 'Uncategorised') AS category,\n       CASE WHEN parent.id IS NOT NULL THEN c.name END AS subcategory,\n       c.slug AS category_slug, i.quantity,\n       i.line_total_pence::numeric / 100 AS line_spend_gbp,\n       i.line_total_pence::numeric / 100 / i.quantity AS effective_unit_price_gbp\nFROM purchase_items i JOIN orders o ON o.id = i.order_id\nJOIN products p ON p.id = i.product_id\nLEFT JOIN categories c ON c.id = p.category_id\nLEFT JOIN categories parent ON parent.id = c.parent_id;\nCREATE VIEW analytics.order_totals AS\nSELECT id AS order_id, retailer, delivery_date, status, currency,\n       gross_subtotal_pence::numeric / 100 AS gross_subtotal_gbp,\n       offer_savings_pence::numeric / 100 AS savings_gbp,\n       product_total_pence::numeric / 100 AS product_spend_gbp,\n       delivery_charge_pence::numeric / 100 AS delivery_charge_gbp,\n       bag_charge_pence::numeric / 100 AS bag_charge_gbp,\n       small_order_charge_pence::numeric / 100 AS small_order_charge_gbp,\n       other_adjustments_pence::numeric / 100 AS other_adjustments_gbp,\n       total_pence::numeric / 100 AS order_total_gbp\nFROM orders;\nCREATE VIEW analytics.monthly_category_spend AS\nSELECT date_trunc('month', delivery_date::timestamp)::date AS month,\n       retailer, status, category, subcategory, SUM(line_spend_gbp) AS spend_gbp\nFROM analytics.purchase_lines GROUP BY 1,2,3,4,5;\nCREATE VIEW analytics.product_price_history AS\nSELECT product_id, product_name, retailer, delivery_date, status, purchase_item_id,\n       quantity, line_spend_gbp, effective_unit_price_gbp\nFROM analytics.purchase_lines;\n"
    )


def downgrade():
    op.execute("DROP SCHEMA analytics CASCADE")
