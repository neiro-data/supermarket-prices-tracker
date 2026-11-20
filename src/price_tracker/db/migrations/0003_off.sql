CREATE TABLE product_ean (
    ean TEXT PRIMARY KEY,
    product_name TEXT,
    brand TEXT,
    quantity TEXT,
    categories TEXT,
    fetched_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('found', 'not_found', 'error'))
);
