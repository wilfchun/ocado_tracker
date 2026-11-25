CREATE SCHEMA analytics;
CREATE VIEW analytics.purchase_lines AS
SELECT i.id AS purchase_item_id, o.id AS order_id, o.retailer, o.delivery_date, o.status,
       p.id AS product_id, p.name AS product_name, p.brand, i.source_product_name,
       COALESCE(parent.name, c.name, 'Uncategorised') AS category,
       CASE WHEN parent.id IS NOT NULL THEN c.name END AS subcategory,
       c.slug AS category_slug, i.quantity,
       i.line_total_pence::numeric / 100 AS line_spend_gbp,
       i.line_total_pence::numeric / 100 / i.quantity AS effective_unit_price_gbp
FROM purchase_items i JOIN orders o ON o.id = i.order_id
JOIN products p ON p.id = i.product_id
LEFT JOIN categories c ON c.id = p.category_id
LEFT JOIN categories parent ON parent.id = c.parent_id;
CREATE VIEW analytics.order_totals AS
SELECT id AS order_id, retailer, delivery_date, status, currency,
       gross_subtotal_pence::numeric / 100 AS gross_subtotal_gbp,
       offer_savings_pence::numeric / 100 AS savings_gbp,
       product_total_pence::numeric / 100 AS product_spend_gbp,
       delivery_charge_pence::numeric / 100 AS delivery_charge_gbp,
       bag_charge_pence::numeric / 100 AS bag_charge_gbp,
       small_order_charge_pence::numeric / 100 AS small_order_charge_gbp,
       other_adjustments_pence::numeric / 100 AS other_adjustments_gbp,
       total_pence::numeric / 100 AS order_total_gbp
FROM orders;
CREATE VIEW analytics.monthly_category_spend AS
SELECT date_trunc('month', delivery_date::timestamp)::date AS month,
       retailer, status, category, subcategory, SUM(line_spend_gbp) AS spend_gbp
FROM analytics.purchase_lines GROUP BY 1,2,3,4,5;
CREATE VIEW analytics.product_price_history AS
SELECT product_id, product_name, retailer, delivery_date, status, purchase_item_id,
       quantity, line_spend_gbp, effective_unit_price_gbp
FROM analytics.purchase_lines;
-- Added by migration 004: safe aggregate operational health for Metabase.
CREATE VIEW analytics.import_health AS SELECT
  (SELECT max(completed_at) FROM scan_state) AS last_complete_scan,
  (SELECT max(created_at) FROM orders) AS last_imported_order,
  (SELECT count(*) FROM source_messages WHERE status = 'failed') AS failed_sources,
  (SELECT count(*) FROM source_messages WHERE status = 'pending') AS pending_sources,
  (SELECT count(*) FROM orders WHERE status = 'provisional') AS provisional_orders,
  (SELECT count(*) FROM products WHERE category_id IS NULL) AS uncategorised_products;
