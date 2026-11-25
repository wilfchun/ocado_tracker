-- Save each SELECT as a Metabase question. Add date-range, retailer and status
-- filters in the question builder, or use mapped field filters in native SQL.
-- All charts describe provisional delivery summaries until final receipts exist.

-- Spending overview: weekly/monthly totals, order count and average basket.
SELECT date_trunc('month', delivery_date::timestamp)::date AS month, retailer, status,
       count(*) AS orders, sum(order_total_gbp) AS spend_gbp,
       avg(order_total_gbp) AS average_basket_gbp
FROM analytics.order_totals GROUP BY 1,2,3 ORDER BY 1;

-- Rolling expenditure: 28 calendar days, with missing days filled.
WITH dates AS (
  SELECT generate_series(min(delivery_date), max(delivery_date), interval '1 day')::date AS day
  FROM analytics.order_totals
), daily AS (
  SELECT dates.day, coalesce(sum(o.order_total_gbp), 0) AS spend_gbp
  FROM dates LEFT JOIN analytics.order_totals o ON o.delivery_date = dates.day GROUP BY 1
)
SELECT day, spend_gbp, sum(spend_gbp) OVER (ORDER BY day ROWS BETWEEN 27 PRECEDING AND CURRENT ROW) AS rolling_28_day_gbp
FROM daily ORDER BY day;

-- Category analysis, including unknown spend.
SELECT * FROM analytics.monthly_category_spend ORDER BY month, spend_gbp DESC;

-- Highest spend and frequency. Weighted price uses reported purchased units.
SELECT product_id, product_name, sum(quantity) AS purchased_units,
       count(DISTINCT order_id) AS orders, sum(line_spend_gbp) AS spend_gbp,
       sum(line_spend_gbp) / nullif(sum(quantity), 0) AS weighted_unit_price_gbp
FROM analytics.purchase_lines GROUP BY 1,2 ORDER BY spend_gbp DESC LIMIT 20;

-- Price history; promotions and pack-size changes affect this effective price.
SELECT * FROM analytics.product_price_history ORDER BY product_id, delivery_date;

-- Offers and charges; use the order view to avoid multiplying basket totals.
SELECT date_trunc('month', delivery_date::timestamp)::date AS month, status,
       sum(gross_subtotal_gbp) AS gross_gbp, sum(savings_gbp) AS savings_gbp,
       sum(product_spend_gbp) AS net_products_gbp, sum(delivery_charge_gbp) AS delivery_gbp,
       sum(bag_charge_gbp) AS bags_gbp, sum(small_order_charge_gbp) AS small_order_gbp
FROM analytics.order_totals GROUP BY 1,2 ORDER BY 1;

-- Data quality available to analytics reader; aggregate operational health is
-- exposed separately without exposing individual sources or their metadata.
SELECT status, count(*) AS order_count FROM analytics.order_totals GROUP BY status;
SELECT count(*) AS unclassified_lines, sum(line_spend_gbp) AS unclassified_gbp
FROM analytics.purchase_lines WHERE category = 'Uncategorised';
SELECT * FROM analytics.import_health;
