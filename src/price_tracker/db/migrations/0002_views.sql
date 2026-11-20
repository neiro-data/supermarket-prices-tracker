CREATE VIEW v_line_effective AS
WITH discounts AS (
    SELECT
        receipt_id,
        applies_to_line_no,
        SUM(line_total_cents) AS discount_cents
    FROM receipt_line
    WHERE line_type = 'discount' AND applies_to_line_no IS NOT NULL
    GROUP BY receipt_id, applies_to_line_no
),
effective AS (
    SELECT
        l.id AS line_id,
        l.receipt_id,
        l.line_no,
        l.raw_text,
        l.chain_product_id,
        l.quantity_milli,
        l.quantity_unit,
        l.unit_price_cents,
        l.line_total_cents,
        l.base_quantity_milli,
        l.line_total_cents + COALESCE(d.discount_cents, 0) AS effective_total_cents
    FROM receipt_line AS l
    LEFT JOIN discounts AS d
        ON d.receipt_id = l.receipt_id AND d.applies_to_line_no = l.line_no
    WHERE l.line_type = 'item'
)
SELECT
    e.line_id,
    e.receipt_id,
    e.line_no,
    e.raw_text,
    e.quantity_milli,
    e.quantity_unit,
    e.unit_price_cents,
    e.line_total_cents,
    e.effective_total_cents,
    e.base_quantity_milli,
    CASE
        WHEN e.base_quantity_milli > 0
            THEN (e.effective_total_cents * 1000 + e.base_quantity_milli / 2)
                / e.base_quantity_milli
    END AS price_per_base_cents,
    r.purchased_at,
    r.currency,
    r.status AS receipt_status,
    ch.id AS chain_id,
    ch.code AS chain_code,
    ch.name AS chain_name,
    s.id AS store_id,
    s.city AS store_city,
    cp.id AS chain_product_id,
    cp.raw_name AS chain_product_name,
    cp.ean AS ean,
    cpr.id AS canonical_product_id,
    cpr.name AS canonical_product_name,
    cpr.base_unit AS base_unit,
    cat.id AS category_id,
    cat.slug AS category_slug,
    cat.name_de AS category_name_de,
    cat.name_en AS category_name_en,
    par.slug AS parent_category_slug,
    par.name_de AS parent_category_name_de,
    par.name_en AS parent_category_name_en
FROM effective AS e
INNER JOIN receipt AS r ON r.id = e.receipt_id
LEFT JOIN chain AS ch ON ch.id = r.chain_id
LEFT JOIN store AS s ON s.id = r.store_id
LEFT JOIN chain_product AS cp ON cp.id = e.chain_product_id
LEFT JOIN canonical_product AS cpr ON cpr.id = cp.canonical_product_id
LEFT JOIN category AS cat ON cat.id = COALESCE(cp.category_id, cpr.category_id)
LEFT JOIN category AS par ON par.id = cat.parent_id;
