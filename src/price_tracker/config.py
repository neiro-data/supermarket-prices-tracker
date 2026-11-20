"""Runtime configuration from environment / .env."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True, slots=True)
class Settings:
    """Resolved application settings."""

    db_path: Path
    data_dir: Path
    tesseract_cmd: str | None


def load_settings() -> Settings:
    """Load settings from the environment (after reading a local .env if present)."""
    load_dotenv()
    return Settings(
        db_path=Path(os.getenv("PTRACK_DB_PATH", "data/prices.db")),
        data_dir=Path(os.getenv("PTRACK_DATA_DIR", "data")),
        tesseract_cmd=os.getenv("PTRACK_TESSERACT_CMD") or None,
    )
