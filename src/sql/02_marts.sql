-- =============================================================================
-- 02_marts.sql : analysis-ready tables (exported to data/marts/*.csv)
-- =============================================================================

-- Complete month spine, so months with zero reports still appear.
CREATE OR REPLACE TABLE dim_month AS
SELECT CAST(m AS DATE) AS month
FROM param_window w,
     generate_series(DATE_TRUNC('month', w.date_from), DATE_TRUNC('month', w.date_to), INTERVAL 1 MONTH) AS t(m);

-- Monthly totals (input to the control chart).
CREATE OR REPLACE TABLE mart_monthly_total AS
SELECT d.month, COUNT(f.report_number) AS reports
FROM dim_month d
LEFT JOIN fct_reports f USING (month)
GROUP BY d.month
ORDER BY d.month;

-- Monthly counts by event type (long format: easy for Power BI and Plotly).
CREATE OR REPLACE TABLE mart_monthly_event_type AS
SELECT d.month, t.event_type, COUNT(f.report_number) AS reports
FROM dim_month d
CROSS JOIN (VALUES ('Malfunction'), ('Injury'), ('Death'), ('Other / not provided')) AS t(event_type)
LEFT JOIN fct_reports f ON f.month = d.month AND f.event_type = t.event_type
GROUP BY d.month, t.event_type
ORDER BY d.month, t.event_type;

-- Monthly counts by report origin (literature / clinical study vs other).
CREATE OR REPLACE TABLE mart_monthly_origin AS
SELECT d.month, t.report_origin, COUNT(f.report_number) AS reports
FROM dim_month d
CROSS JOIN (VALUES ('Other reports'), ('Literature / clinical study')) AS t(report_origin)
LEFT JOIN fct_reports f ON f.month = d.month AND f.report_origin = t.report_origin
GROUP BY d.month, t.report_origin
ORDER BY d.month, t.report_origin;

-- Yearly counts per manufacturer (parent company) and per manufacturing site.
CREATE OR REPLACE TABLE mart_yearly_manufacturer AS
SELECT YEAR(month) AS year, manufacturer, COUNT(*) AS reports
FROM fct_reports
GROUP BY ALL
ORDER BY year, reports DESC;

CREATE OR REPLACE TABLE mart_manufacturer_sites AS
SELECT manufacturer, manufacturer_site, COUNT(*) AS reports,
       MIN(month) AS first_month, MAX(month) AS last_month
FROM fct_reports
GROUP BY ALL
ORDER BY manufacturer, reports DESC;

-- Top-N manufacturers by total volume; everyone else grouped as 'Other'.
CREATE OR REPLACE TABLE mart_manufacturer_rank AS
SELECT manufacturer, COUNT(*) AS reports,
       ROW_NUMBER() OVER (ORDER BY COUNT(*) DESC, manufacturer) AS rank
FROM fct_reports
GROUP BY manufacturer;

CREATE OR REPLACE TABLE mart_monthly_manufacturer AS
WITH grouped AS (
    SELECT f.month,
           CASE WHEN r.rank <= (SELECT top_n_manufacturers FROM param_top_n)
                THEN f.manufacturer ELSE 'Other' END AS manufacturer_group,
           f.report_number
    FROM fct_reports f
    JOIN mart_manufacturer_rank r USING (manufacturer)
),
groups AS (SELECT DISTINCT manufacturer_group FROM grouped)
SELECT d.month, g.manufacturer_group, COUNT(x.report_number) AS reports
FROM dim_month d
CROSS JOIN groups g
LEFT JOIN grouped x ON x.month = d.month AND x.manufacturer_group = g.manufacturer_group
GROUP BY d.month, g.manufacturer_group
ORDER BY d.month, g.manufacturer_group;

-- Product (device) problems: overall frequency and share of reports.
CREATE OR REPLACE TABLE mart_product_problems AS
SELECT problem,
       COUNT(DISTINCT report_number) AS reports,
       ROUND(100.0 * COUNT(DISTINCT report_number) / (SELECT COUNT(*) FROM fct_reports), 1) AS pct_of_reports
FROM fct_product_problems
GROUP BY problem
ORDER BY reports DESC;

CREATE OR REPLACE TABLE mart_monthly_product_problems AS
SELECT month, problem, COUNT(DISTINCT report_number) AS reports
FROM fct_product_problems
GROUP BY month, problem
ORDER BY month, reports DESC;

CREATE OR REPLACE TABLE mart_patient_problems AS
SELECT problem,
       COUNT(DISTINCT report_number) AS reports,
       ROUND(100.0 * COUNT(DISTINCT report_number) / (SELECT COUNT(*) FROM fct_reports), 1) AS pct_of_reports
FROM fct_patient_problems
GROUP BY problem
ORDER BY reports DESC;

CREATE OR REPLACE TABLE mart_monthly_report_source AS
SELECT d.month, s.report_source, COUNT(f.report_number) AS reports
FROM dim_month d
CROSS JOIN (SELECT DISTINCT report_source FROM fct_reports) s
LEFT JOIN fct_reports f ON f.month = d.month AND f.report_source = s.report_source
GROUP BY d.month, s.report_source
ORDER BY d.month, s.report_source;

-- Lag between the event and FDA receipt (reporting-delay check).
CREATE OR REPLACE TABLE mart_reporting_lag AS
SELECT event_type,
       COUNT(*) AS reports_with_event_date,
       MEDIAN(date_received - date_of_event) AS median_lag_days,
       QUANTILE_CONT(date_received - date_of_event, 0.9) AS p90_lag_days
FROM fct_reports
WHERE date_of_event IS NOT NULL AND date_of_event <= date_received
GROUP BY event_type
ORDER BY event_type;
