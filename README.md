# price-tracker

Supermarket receipt ingestion, parsing, product matching and price analytics.
Everything runs locally: receipts (PDF or photo) go in, a SQLite database comes out,
and DuckDB, reports and a Streamlit dashboard read from it.

## Resume

- **Stack:** Python 3.12, uv, typer, SQLite (storage) + DuckDB (analytics), pypdfium2,
  Tesseract via pytesseract, OpenCV, rapidfuzz, polars, plotly, Streamlit, httpx (optional
  Open Food Facts lookups).
- **Entry point:** `src/price_tracker/cli.py` → the `ptrack` command.
- **Data:** under `data/` (git-ignored): `inbox/`, `archive/`, `prices.db`.
- **Rules:** money is stored as integer cents; SQL is ANSI with `?` parameters only;
  tests run offline.

## Setup

```bash
brew install tesseract tesseract-lang   # OCR for photos and scanned PDFs
uv sync
cp .env.example .env                    # optional: DB path, data dir, tesseract path
uv run ptrack init                      # create data dirs, run migrations, seed chains and categories
```

## Workflow

1. Drop receipts into `data/inbox/`.
2. `ptrack ingest` — extract text and store each file once.
3. `ptrack review list` / `review show` / `review accept` — check and correct parsed receipts.
4. `ptrack match run`, then `ptrack match review` — map chain products to canonical products.
5. `ptrack report …` or `ptrack dashboard` — spending and price analytics.
6. Optional: `ptrack enrich run` — add Open Food Facts data for products that have an EAN.

## Commands

| Command | Purpose |
| --- | --- |
| `init` | Create data dirs, apply migrations, seed categories and chains |
| `ingest [PATHS] [--force] [--dry-run]` | Extract text from receipts and store each new file once |
| `dashboard [--port]` | Serve the read-only Streamlit dashboard |
| `db status` | List applied migrations |
| `review list/show/set/line/add-line/delete-line/accept/reparse` | Review and correct parsed receipts |
| `categories list/add/reseed` | Manage the category taxonomy |
| `canonical list/add/rename/set-category/merge` | Manage canonical products |
| `products list/set-ean/set-category/set-pack` | Inspect and edit chain products |
| `alias list/add/delete` | Manage alias rules |
| `match run/review` | Auto-match by alias rules and EAN; interactive review |
| `report spend/habits/top/prices/cheapest` | Spending and price reports (table, CSV or HTML) |
| `enrich run [--limit]` / `enrich show EAN` | Open Food Facts lookup (network); fills missing pack size, suggests a name |

Run `uv run ptrack <command> --help` for all options.

## Enrichment (Open Food Facts)

`enrich run` sends only the EAN (EAN-8/13, checksum validated) with the generic User-Agent
`price-tracker/0.1`. Results are cached in `product_ean` for 30 days. OFF quantity text
fills `pack_size` only where it is missing. Name suggestions are shown, never applied
automatically. Most receipts print article numbers, not EANs, so set EANs with
`ptrack products set-ean` where you know them.

## Adding a chain or country

1. Add a `[[chains]]` row in `src/price_tracker/data/chains.toml` (`code`, `name`,
   `country_code`, `parser_profile`, `detect_keywords`).
2. If the receipt layout is new, add a parser profile in `src/price_tracker/parsing/profiles/`
   (see `rewe.py`, `lidl.py`, `aldi.py`; `generic.py` is the fallback) and reference it
   by `parser_profile`.
3. Run `uv run ptrack init` to seed the chain, then add fixtures and tests.

## Testing

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest -q
```

Markers: `tesseract` (auto-skipped when the binary is missing) and `network`
(deselected by default; run with `uv run pytest -m network`).
