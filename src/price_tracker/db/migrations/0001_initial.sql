CREATE TABLE chain (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    country_code TEXT NOT NULL DEFAULT 'DE',
    parser_profile TEXT NOT NULL DEFAULT 'generic'
);

CREATE TABLE store (
    id INTEGER PRIMARY KEY,
    chain_id INTEGER NOT NULL REFERENCES chain (id),
    name TEXT,
    street TEXT,
    postal_code TEXT,
    city TEXT,
    country_code TEXT NOT NULL DEFAULT 'DE',
    UNIQUE (chain_id, street, postal_code)
);

CREATE TABLE source_file (
    id INTEGER PRIMARY KEY,
    sha256 TEXT NOT NULL UNIQUE,
    original_name TEXT NOT NULL,
    archive_path TEXT NOT NULL,
    media_type TEXT NOT NULL CHECK (media_type IN ('pdf', 'image')),
    page_count INTEGER,
    extraction_method TEXT NOT NULL CHECK (extraction_method IN ('text_layer', 'ocr')),
    ocr_confidence REAL,
    raw_text TEXT NOT NULL,
    ingested_at TEXT NOT NULL
);

CREATE TABLE receipt (
    id INTEGER PRIMARY KEY,
    source_file_id INTEGER NOT NULL UNIQUE REFERENCES source_file (id),
    chain_id INTEGER REFERENCES chain (id),
    store_id INTEGER REFERENCES store (id),
    purchased_at TEXT,
    currency TEXT NOT NULL DEFAULT 'EUR',
    total_cents INTEGER,
    lines_sum_cents INTEGER,
    payment_method TEXT,
    parser_profile TEXT NOT NULL,
    parse_confidence REAL NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('needs_review', 'parsed', 'confirmed', 'failed')),
    review_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE category (
    id INTEGER PRIMARY KEY,
    parent_id INTEGER REFERENCES category (id),
    slug TEXT NOT NULL UNIQUE,
    name_de TEXT NOT NULL,
    name_en TEXT NOT NULL
);

CREATE TABLE canonical_product (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    category_id INTEGER REFERENCES category (id),
    base_unit TEXT NOT NULL CHECK (base_unit IN ('kg', 'l', 'pc')),
    brand TEXT,
    notes TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE chain_product (
    id INTEGER PRIMARY KEY,
    chain_id INTEGER NOT NULL REFERENCES chain (id),
    raw_name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    article_no TEXT,
    ean TEXT,
    category_id INTEGER REFERENCES category (id),
    canonical_product_id INTEGER REFERENCES canonical_product (id),
    pack_size_milli INTEGER,
    pack_unit TEXT CHECK (pack_unit IN ('kg', 'l', 'pc')),
    is_weighed INTEGER NOT NULL DEFAULT 0,
    match_source TEXT CHECK (match_source IN ('rule', 'fuzzy', 'manual', 'ean')),
    UNIQUE (chain_id, normalized_name)
);

CREATE INDEX ix_chain_product_ean ON chain_product (ean);
CREATE INDEX ix_chain_product_canonical ON chain_product (canonical_product_id);

CREATE TABLE receipt_line (
    id INTEGER PRIMARY KEY,
    receipt_id INTEGER NOT NULL REFERENCES receipt (id) ON DELETE CASCADE,
    line_no INTEGER NOT NULL,
    raw_text TEXT NOT NULL,
    line_type TEXT NOT NULL
        CHECK (line_type IN ('item', 'discount', 'deposit', 'deposit_return', 'other')),
    chain_product_id INTEGER REFERENCES chain_product (id),
    quantity_milli INTEGER NOT NULL DEFAULT 1000,
    quantity_unit TEXT NOT NULL DEFAULT 'pc' CHECK (quantity_unit IN ('pc', 'kg', 'l')),
    unit_price_cents INTEGER,
    line_total_cents INTEGER NOT NULL,
    vat_code TEXT,
    applies_to_line_no INTEGER,
    base_quantity_milli INTEGER,
    UNIQUE (receipt_id, line_no)
);

CREATE TABLE alias_rule (
    id INTEGER PRIMARY KEY,
    chain_id INTEGER REFERENCES chain (id),
    pattern TEXT NOT NULL,
    match_type TEXT NOT NULL CHECK (match_type IN ('exact', 'prefix', 'regex')),
    canonical_product_id INTEGER REFERENCES canonical_product (id),
    category_id INTEGER REFERENCES category (id),
    priority INTEGER NOT NULL DEFAULT 100,
    created_at TEXT NOT NULL,
    UNIQUE (chain_id, pattern, match_type)
);

CREATE TABLE match_rejection (
    chain_product_id INTEGER NOT NULL REFERENCES chain_product (id),
    canonical_product_id INTEGER NOT NULL REFERENCES canonical_product (id),
    created_at TEXT NOT NULL,
    PRIMARY KEY (chain_product_id, canonical_product_id)
);
