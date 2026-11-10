-- =============================================================================
-- 01_staging.sql : clean, de-duplicate and join the raw MAUDE tables
-- =============================================================================

-- Manufacturer name standardisation: MAUDE free-text names vary
-- ("BOSTON SCIENTIFIC CORPORATION", "Boston Scientific Corp.", ...).
-- Upper-case, strip punctuation and common legal suffixes, collapse spaces.
CREATE OR REPLACE MACRO clean_mfr(name) AS
    NULLIF(TRIM(regexp_replace(
        regexp_replace(
            regexp_replace(UPPER(COALESCE(name, '')), '[^A-Z0-9 ]', ' ', 'g'),
            '\b(INCORPORATED|INC|CORPORATION|CORP|CO|LLC|LTD|LIMITED|GMBH|AG|SA|BV|PLC|USA|US|THE)\b', ' ', 'g'),
        '\s+', ' ', 'g')), '');

-- One row per report (openFDA can return supplemental duplicates).
CREATE OR REPLACE TABLE stg_events AS
SELECT
    report_number,
    mdr_report_key,
    TRY_STRPTIME(date_received, '%Y%m%d')::DATE                     AS date_received,
    TRY_STRPTIME(date_of_event, '%Y%m%d')::DATE                     AS date_of_event,
    CASE
        WHEN event_type IN ('Malfunction', 'Injury', 'Death') THEN event_type
        ELSE 'Other / not provided'
    END                                                             AS event_type,
    CASE
        WHEN report_source_code ILIKE 'manufacturer%'  OR report_source_code = 'M' THEN 'Manufacturer'
        WHEN report_source_code ILIKE 'user facility%' OR report_source_code = 'U' THEN 'User facility'
        WHEN report_source_code ILIKE 'distributor%'   OR report_source_code = 'D' THEN 'Distributor'
        WHEN report_source_code ILIKE 'voluntary%'     OR report_source_code = 'P' THEN 'Voluntary'
        WHEN report_source_code IS NULL OR TRIM(report_source_code) = '' THEN 'Not provided'
        ELSE report_source_code
    END                                                             AS report_source,
    type_of_report
FROM (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY report_number ORDER BY mdr_report_key DESC) AS rn
    FROM raw_events
)
WHERE rn = 1;

-- Devices on each report that match the configured product codes.
-- `manufacturer_site` is the cleaned free-text name; `manufacturer` groups site
-- names under a parent company using reference/manufacturer_aliases.csv
-- (longest matching prefix wins; unmatched names are kept as they are).
CREATE OR REPLACE TABLE stg_devices AS
WITH cleaned AS (
    SELECT
        d.report_number,
        UPPER(TRIM(d.product_code))                                 AS product_code,
        COALESCE(clean_mfr(d.manufacturer_name), 'UNKNOWN')         AS manufacturer_site,
        NULLIF(TRIM(d.brand_name), '')                              AS brand_name,
        NULLIF(TRIM(d.openfda_device_name), '')                     AS device_name
    FROM raw_devices d
    JOIN param_product_codes p ON UPPER(TRIM(d.product_code)) = p.product_code
),
site_map AS (
    SELECT site, parent
    FROM (
        SELECT c.manufacturer_site AS site, a.parent,
               ROW_NUMBER() OVER (PARTITION BY c.manufacturer_site ORDER BY length(a.prefix) DESC) AS rn
        FROM (SELECT DISTINCT manufacturer_site FROM cleaned) c
        JOIN ref_manufacturer_aliases a ON starts_with(c.manufacturer_site, a.prefix)
    )
    WHERE rn = 1
)
SELECT DISTINCT
    c.report_number,
    c.product_code,
    COALESCE(m.parent, c.manufacturer_site)                         AS manufacturer,
    c.manufacturer_site,
    c.brand_name,
    c.device_name
FROM cleaned c
LEFT JOIN site_map m ON m.site = c.manufacturer_site;

-- Report origin: manufacturers also file MDRs for events found in published
-- articles and clinical studies. These arrive in batches and are usually coded
-- "Adverse Event Without Identified Device or Use Problem". Keyword heuristic on
-- the event narrative; check a sample before relying on it.
CREATE OR REPLACE TABLE stg_report_origin AS
SELECT
    report_number,
    bool_or(regexp_matches(UPPER(text),
        '\b(ARTICLE|JOURNAL|LITERATURE|PUBLICATION|PUBLISHED|STUDY|TRIAL|REGISTRY|INVESTIGATOR|ADJUDICATED)\b'))
        AS is_literature
FROM raw_narratives
GROUP BY report_number;

-- Analysis fact table: one row per in-scope report, inside the date window,
-- with the (first) in-scope device's manufacturer attached.
CREATE OR REPLACE TABLE fct_reports AS
WITH primary_device AS (
    SELECT report_number, manufacturer, manufacturer_site, product_code, brand_name
    FROM (
        SELECT *, ROW_NUMBER() OVER (
                   PARTITION BY report_number ORDER BY product_code, manufacturer, brand_name) AS rn
        FROM stg_devices
    )
    WHERE rn = 1
)
SELECT
    e.*,
    DATE_TRUNC('month', e.date_received)::DATE AS month,
    pd.manufacturer,
    pd.manufacturer_site,
    pd.product_code,
    pd.brand_name,
    CASE WHEN o.is_literature THEN 'Literature / clinical study'
         ELSE 'Other reports' END                AS report_origin
FROM stg_events e
JOIN primary_device pd USING (report_number)
LEFT JOIN stg_report_origin o USING (report_number)
CROSS JOIN param_window w
WHERE e.date_received BETWEEN w.date_from AND w.date_to;

CREATE OR REPLACE TABLE fct_product_problems AS
SELECT DISTINCT f.report_number, f.month, f.manufacturer, TRIM(p.problem) AS problem
FROM fct_reports f
JOIN raw_product_problems p USING (report_number)
WHERE TRIM(p.problem) <> '';

CREATE OR REPLACE TABLE fct_patient_problems AS
SELECT DISTINCT f.report_number, f.month, f.manufacturer, TRIM(p.problem) AS problem
FROM fct_reports f
JOIN raw_patient_problems p USING (report_number)
WHERE TRIM(p.problem) <> '';
