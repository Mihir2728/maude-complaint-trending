"""Shared paths and config loading."""
from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw"
MART_DIR = ROOT / "data" / "marts"
DB_PATH = ROOT / "data" / "maude.duckdb"
SQL_DIR = ROOT / "src" / "sql"
DOCS_DIR = ROOT / "docs"
REPORTS_DIR = ROOT / "reports"


def load_config(path: Path | None = None) -> dict:
    with open(path or ROOT / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)
