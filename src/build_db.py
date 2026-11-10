"""Flatten raw openFDA JSON into DuckDB tables, then run the SQL models.

raw_events            one row per MDR report
raw_devices           one row per device listed on a report
raw_product_problems  one row per (report, product problem)
raw_patient_problems  one row per (report, patient problem)
raw_narratives        event description text (first 2,000 characters)

The SQL files in src/sql/ (run in name order) build the cleaned staging tables
and the analysis marts. Every mart is also exported to data/marts/*.csv so it
can be loaded straight into Power BI or Excel.
"""
from __future__ import annotations

import gzip
import json
import os
import sys
from pathlib import Path

import duckdb
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from common import DB_PATH, MART_DIR, RAW_DIR, ROOT, SQL_DIR, load_config  # noqa: E402


def _first(x):
    """openFDA wraps many scalars in lists; return the first element or None."""
    if isinstance(x, list):
        return x[0] if x else None
    return x


def flatten(records):
    events, devices, pprobs, patprobs, texts = [], [], [], [], []
    for r in records:
        rn = r.get("report_number")
        if not rn:
            continue
        events.append(
            {
                "report_number": rn,
                "mdr_report_key": r.get("mdr_report_key"),
                "date_received": r.get("date_received"),
                "date_of_event": r.get("date_of_event"),
                "event_type": r.get("event_type"),
                "report_source_code": r.get("report_source_code"),
                "type_of_report": _first(r.get("type_of_report")),
                "adverse_event_flag": r.get("adverse_event_flag"),
                "product_problem_flag": r.get("product_problem_flag"),
            }
        )
        for d in r.get("device", []) or []:
            ofda = d.get("openfda", {}) or {}
            devices.append(
                {
                    "report_number": rn,
                    "device_sequence_number": d.get("device_sequence_number"),
                    "product_code": d.get("device_report_product_code"),
                    "brand_name": d.get("brand_name"),
                    "generic_name": d.get("generic_name"),
                    "manufacturer_name": d.get("manufacturer_d_name"),
                    "model_number": d.get("model_number"),
                    "openfda_device_name": _first(ofda.get("device_name")),
                }
            )
        for p in r.get("product_problems", []) or []:
            pprobs.append({"report_number": rn, "problem": p})
        for pat in r.get("patient", []) or []:
            for p in pat.get("patient_problems", []) or []:
                patprobs.append({"report_number": rn, "problem": p})
        for t in r.get("mdr_text", []) or []:
            if "Description of Event" in (t.get("text_type_code") or ""):
                texts.append({"report_number": rn, "text": (t.get("text") or "")[:2000]})
    return events, devices, pprobs, patprobs, texts


def read_raw(raw_dir: Path):
    files = sorted(raw_dir.glob("*.jsonl.gz"))
    if not files:
        raise SystemExit(f"No raw files in {raw_dir}. Run src/fetch_openfda.py first.")
    for fp in files:
        with gzip.open(fp, "rt", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    yield json.loads(line)


RAW_COLUMNS = {
    "raw_events": ["report_number", "mdr_report_key", "date_received", "date_of_event",
                   "event_type", "report_source_code", "type_of_report",
                   "adverse_event_flag", "product_problem_flag"],
    "raw_devices": ["report_number", "device_sequence_number", "product_code", "brand_name",
                    "generic_name", "manufacturer_name", "model_number", "openfda_device_name"],
    "raw_product_problems": ["report_number", "problem"],
    "raw_patient_problems": ["report_number", "problem"],
    "raw_narratives": ["report_number", "text"],
}


def build(raw_dir: Path = RAW_DIR, db_path: Path = DB_PATH, mart_dir: Path = MART_DIR,
          cfg: dict | None = None) -> duckdb.DuckDBPyConnection:
    cfg = cfg or load_config()
    tables = dict(zip(RAW_COLUMNS, flatten(read_raw(raw_dir))))

    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    con = duckdb.connect(str(db_path))

    for name, rows in tables.items():
        df = pd.DataFrame(rows, columns=RAW_COLUMNS[name]).astype("string")
        con.register("tmp_df", df)
        con.execute(f"CREATE TABLE {name} AS SELECT * FROM tmp_df")
        con.unregister("tmp_df")
        print(f"  {name:<22} {len(df):>9,} rows")

    # Parameters used by the SQL models
    codes = pd.DataFrame({"product_code": [c.upper() for c in cfg["product_codes"]]})
    con.register("tmp_codes", codes)
    con.execute("CREATE TABLE param_product_codes AS SELECT * FROM tmp_codes")
    con.execute(
        "CREATE TABLE param_window AS SELECT CAST(? AS DATE) AS date_from, CAST(? AS DATE) AS date_to",
        [cfg["date_from"], cfg["date_to"]],
    )
    aliases_path = ROOT / "reference" / "manufacturer_aliases.csv"
    aliases = (pd.read_csv(aliases_path, dtype=str) if aliases_path.exists()
               else pd.DataFrame(columns=["prefix", "parent"]))
    aliases = aliases.apply(lambda c: c.str.strip().str.upper())
    con.register("tmp_alias", aliases)
    con.execute("CREATE TABLE ref_manufacturer_aliases AS SELECT * FROM tmp_alias")
    con.unregister("tmp_alias")
    con.execute(
        "CREATE TABLE param_top_n AS SELECT CAST(? AS INTEGER) AS top_n_manufacturers",
        [int(cfg.get("top_n_manufacturers", 6))],
    )

    for sql_file in sorted(SQL_DIR.glob("*.sql")):
        print(f"  running {sql_file.name}")
        con.execute(sql_file.read_text(encoding="utf-8"))

    mart_dir.mkdir(parents=True, exist_ok=True)
    marts = [r[0] for r in con.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_name LIKE 'mart_%' ORDER BY 1"
    ).fetchall()]
    for m in marts:
        con.execute(f"SELECT * FROM {m}").df().to_csv(mart_dir / f"{m}.csv", index=False)
    print(f"  exported {len(marts)} marts to {mart_dir}")
    return con


if __name__ == "__main__":
    build().close()
