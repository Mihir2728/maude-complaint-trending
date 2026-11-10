"""End-to-end tests on a synthetic openFDA-shaped fixture (no network needed)."""
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import build_db  # noqa: E402
import control_chart  # noqa: E402
import make_brief  # noqa: E402
import make_dashboard  # noqa: E402
from common import load_config  # noqa: E402
from make_fixture import write_fixture  # noqa: E402


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("pipe")
    planted = write_fixture(tmp / "raw")
    cfg = load_config()
    cfg.update(product_codes=["NIQ"], date_from="2020-01-01", date_to="2025-12-31")
    cfg["control_chart"] = dict(cfg["control_chart"], baseline_start="2020-07-01",
                                baseline_end="2022-06-30", method="imr")
    con = build_db.build(tmp / "raw", tmp / "t.duckdb", tmp / "marts", cfg)
    ctrl, drivers = control_chart.run(con, cfg, tmp / "marts")
    make_dashboard.build(tmp / "marts", tmp / "docs", cfg)
    make_brief.build(con, cfg, tmp / "marts", tmp / "reports")
    yield con, ctrl, drivers, planted, tmp
    con.close()


def test_out_of_scope_product_codes_excluded(run):
    con, *_ = run
    assert con.execute("SELECT COUNT(*) FROM fct_reports WHERE product_code <> 'NIQ'").fetchone()[0] == 0


def test_duplicate_reports_removed(run):
    con, *_ = run
    n, distinct = con.execute("SELECT COUNT(*), COUNT(DISTINCT report_number) FROM fct_reports").fetchone()
    assert n == distinct


def test_manufacturer_names_standardised(run):
    con, *_ = run
    names = {r[0] for r in con.execute("SELECT DISTINCT manufacturer FROM fct_reports").fetchall()}
    assert "ACME VASCULAR" in names
    assert not any("INC" in n.split() or "LLC" in n.split() for n in names)
    assert len(names) == 7  # 6 fictional companies + MEDTRONIC (two site names merged)


def test_site_names_grouped_under_parent(run):
    con, *_ = run
    sites = con.execute("""SELECT manufacturer_site FROM mart_manufacturer_sites
                           WHERE manufacturer = 'MEDTRONIC' ORDER BY 1""").fetchall()
    assert [r[0] for r in sites] == ["MEDTRONIC", "MEDTRONIC IRELAND"]


def test_literature_reports_flagged(run):
    con, _, drivers, planted, _ = run
    lit = con.execute("""SELECT COUNT(*) FROM fct_reports
                         WHERE report_origin = 'Literature / clinical study'""").fetchone()[0]
    assert lit == 70  # only the planted spike reports cite an article
    top = drivers[(pd.to_datetime(drivers.month).dt.date == planted["spike_month"])
                  & (drivers.dimension == "Report origin")].iloc[0]
    assert top.value == "Literature / clinical study"


def test_month_spine_is_complete(run):
    con, *_ = run
    assert con.execute("SELECT COUNT(*) FROM mart_monthly_total").fetchone()[0] == 72


def test_planted_spike_is_flagged_and_attributed(run):
    _, ctrl, drivers, planted, _ = run
    flagged = set(pd.to_datetime(ctrl.loc[ctrl.rule1_above_ucl, "month"]).dt.date)
    assert planted["spike_month"] in flagged
    top = drivers[(pd.to_datetime(drivers.month).dt.date == planted["spike_month"])
                  & (drivers.dimension == "Manufacturer")].iloc[0]
    assert top.value == planted["spike_mfr"]
    assert top.share_of_excess_pct > 50


def test_limits_fixed_from_baseline(run):
    _, ctrl, *_ = run
    assert ctrl.ucl.nunique() == 1 and ctrl.centre.nunique() == 1
    assert (ctrl.ucl > ctrl.centre).all() and (ctrl.lcl >= 0).all()


def test_run_rules_both_directions():
    s = pd.Series([0, 0] + [5] * 8 + [0])
    _, _, up, down = control_chart.apply_rules(s, centre=1, ucl=100, lcl=0, run_length=8)
    assert up.tolist() == [False, False] + [True] * 8 + [False]
    assert not down.any()
    _, _, up, down = control_chart.apply_rules(-s, centre=-1, ucl=100, lcl=-100, run_length=8)
    assert down.tolist() == [False, False] + [True] * 8 + [False] and not up.any()


def test_pre_baseline_months_are_not_tested(run):
    _, ctrl, *_ = run
    pre = ctrl[ctrl.phase == "Pre-baseline"]
    assert len(pre) == 6
    assert not pre[["rule1_above_ucl", "rule1_below_lcl", "rule2_upward_shift",
                    "rule2_downward_shift"]].any().any()
    assert (ctrl.phase == "Baseline").sum() == 24


def test_report_source_text_values_mapped(run):
    con, *_ = run
    srcs = {r[0] for r in con.execute("SELECT DISTINCT report_source FROM fct_reports").fetchall()}
    assert srcs <= {"Manufacturer", "User facility", "Voluntary", "Distributor"}
    assert "Manufacturer" in srcs


def test_c_chart_option():
    s = pd.Series([100] * 24)
    centre, ucl, lcl, sd = control_chart.control_limits(s, 24, method="c", sigma=3)  # first 24 months
    assert centre == 100 and sd == 10 and ucl == 130 and lcl == 70


def test_outputs_written(run):
    *_, tmp = run
    html = (tmp / "docs" / "index.html").read_text()
    assert "Monthly report volume" in html and "Plotly.newPlot" in html
    brief = (tmp / "reports" / "insight_brief.md").read_text()
    assert "March 2024" in brief and "Baseline choice" in brief
    nar = pd.read_csv(tmp / "reports" / "signal_month_narratives.csv")
    assert nar.report_number.is_unique and len(nar) > 0
