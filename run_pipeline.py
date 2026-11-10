"""Run the whole pipeline: fetch -> SQL build -> control chart -> dashboard -> brief.

    python run_pipeline.py              # fetch fresh data from openFDA, then build
    python run_pipeline.py --skip-fetch # rebuild from data/raw/ already on disk
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

import build_db  # noqa: E402
import control_chart  # noqa: E402
import fetch_openfda  # noqa: E402
import make_brief  # noqa: E402
import make_dashboard  # noqa: E402
from common import load_config  # noqa: E402

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-fetch", action="store_true", help="use files already in data/raw/")
    args = ap.parse_args()
    cfg = load_config()
    if not args.skip_fetch:
        print("1/5 Fetching from openFDA ...")
        fetch_openfda.main()
    print("2/5 Building DuckDB + SQL marts ...")
    con = build_db.build(cfg=cfg)
    print("3/5 Control chart ...")
    control_chart.run(con, cfg)
    print("4/5 Dashboard ...")
    make_dashboard.build(cfg=cfg)
    print("5/5 Insight brief ...")
    make_brief.build(con, cfg)
    con.close()
    print("Done. Open docs/index.html and reports/insight_brief.md")
