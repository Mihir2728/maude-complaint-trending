"""Statistical process control on monthly MAUDE report counts.

Method
------
* Phase I (baseline): a stable window, set in config.yaml by
  `baseline_start` / `baseline_end`, fixes the centre line and control limits.
  (If those keys are absent, the first `baseline_months` months are used.)
* Phase II (monitoring): months after the baseline are judged against those
  fixed limits, so a sustained change cannot widen its own limits and hide.
* Months before the baseline are labelled "Pre-baseline". They are plotted
  for context but not tested, because they were not part of the period the
  limits describe.
* Default chart is an individuals (I-MR) chart: sigma = mean moving range / 1.128.
  Complaint counts are usually over-dispersed relative to Poisson, which makes a
  c-chart (sigma = sqrt(mean)) flag too many months; the c-chart is kept as an
  option for comparison.
* Rules (Western Electric), applied in both directions:
    rule 1  a point above the UCL / below the LCL;
    rule 2  `run_length` consecutive points above / below the centre line.
  Rises are the quality signals that trigger a driver breakdown; falls are
  reported too, because a drop in reporting can be as informative.
"""
from __future__ import annotations

import math
import os
import sys

import duckdb
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from common import DB_PATH, MART_DIR, load_config  # noqa: E402

D2 = 1.128  # bias-correction constant for moving ranges of size 2


def baseline_mask(months: pd.Series, cc: dict) -> pd.Series:
    """Boolean mask of the baseline months (months must be sorted)."""
    months = pd.to_datetime(months)
    if cc.get("baseline_start") and cc.get("baseline_end"):
        start, end = pd.Timestamp(cc["baseline_start"]), pd.Timestamp(cc["baseline_end"])
        return (months >= start) & (months <= end)
    n = int(cc.get("baseline_months", 24))
    return pd.Series(np.arange(len(months)) < n, index=months.index)


def control_limits(counts: pd.Series, mask, method: str = "imr", sigma: float = 3.0):
    """Centre line and limits from the baseline months selected by `mask`.

    `mask` may also be an int N, meaning the first N months.
    """
    if isinstance(mask, (int, np.integer)):
        mask = pd.Series(np.arange(len(counts)) < mask, index=counts.index)
    mask = pd.Series(mask, index=counts.index).astype(bool)
    base = counts[mask].astype(float)
    if len(base) < 2:
        raise ValueError("Need at least 2 baseline months.")
    centre = base.mean()
    if method == "imr":
        consecutive = mask & mask.shift(1, fill_value=False)  # both months in baseline
        sd = counts.astype(float).diff().abs()[consecutive].mean() / D2
    elif method == "c":
        sd = math.sqrt(centre)
    else:
        raise ValueError(f"Unknown method: {method}")
    ucl = centre + sigma * sd
    lcl = max(0.0, centre - sigma * sd)
    return centre, ucl, lcl, sd


def _runs(flag: pd.Series, run_length: int) -> pd.Series:
    """Mark every point that belongs to a run of >= run_length consecutive True."""
    f = flag.astype(int)
    run = f.groupby((f != f.shift()).cumsum()).cumsum() * f
    out = pd.Series(False, index=flag.index)
    for i in np.where(run.values >= run_length)[0]:
        out.iloc[i - run_length + 1: i + 1] = True
    return out


def apply_rules(counts: pd.Series, centre: float, ucl: float, lcl: float,
                run_length: int = 8, tested=None):
    """Return (above_ucl, below_lcl, upward_shift, downward_shift) boolean Series."""
    tested = pd.Series(True, index=counts.index) if tested is None else tested.astype(bool)
    above = (counts > ucl) & tested
    below = (counts < lcl) & tested
    up = _runs((counts > centre) & tested, run_length)
    down = _runs((counts < centre) & tested, run_length)
    return above, below, up, down


def build_control_table(monthly: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    cc = cfg.get("control_chart", {})
    df = monthly.sort_values("month").reset_index(drop=True).copy()
    mask = baseline_mask(df["month"], cc)
    if mask.sum() < 2:
        raise SystemExit("Baseline window has fewer than 2 months of data; check "
                         "baseline_start/baseline_end in config.yaml.")
    first_base = mask.values.argmax()
    phase = np.where(mask, "Baseline", np.where(df.index < first_base, "Pre-baseline", "Monitoring"))
    tested = pd.Series(phase != "Pre-baseline", index=df.index)

    centre, ucl, lcl, sd = control_limits(
        df["reports"], mask, cc.get("method", "imr"), float(cc.get("sigma", 3)))
    above, below, up, down = apply_rules(
        df["reports"], centre, ucl, lcl, int(cc.get("run_length", 8)), tested)

    df["phase"] = phase
    df["centre"], df["ucl"], df["lcl"], df["sigma_hat"] = centre, ucl, lcl, sd
    df["moving_range"] = df["reports"].diff().abs()
    df["rule1_above_ucl"], df["rule1_below_lcl"] = above, below
    df["rule2_upward_shift"], df["rule2_downward_shift"] = up, down
    df["signal_up"] = above | up
    df["signal_down"] = below | down
    labels = []
    for a, b, u, d, p in zip(above, below, up, down, phase):
        parts = [t for t, on in [("Above UCL", a), ("Upward shift", u),
                                 ("Below LCL", b), ("Downward shift", d)] if on]
        labels.append(" + ".join(parts) if parts else
                      ("Pre-baseline (not tested)" if p == "Pre-baseline" else ""))
    df["signal_reason"] = labels
    return df


def signal_drivers(con: duckdb.DuckDBPyConnection, ctrl: pd.DataFrame, top_k: int = 3):
    """Excess over the baseline mean in each above-UCL month, broken down by dimension."""
    cols = ["month", "dimension", "value", "observed", "baseline_mean", "excess", "share_of_excess_pct"]
    base_months = pd.to_datetime(ctrl.loc[ctrl.phase == "Baseline", "month"])
    n_base = len(base_months)
    sig_months = pd.to_datetime(ctrl.loc[ctrl["rule1_above_ucl"], "month"])
    if sig_months.empty:
        return pd.DataFrame(columns=cols)
    dims = {
        "Manufacturer": "SELECT month, manufacturer AS value, report_number FROM fct_reports",
        "Product problem": "SELECT month, problem AS value, report_number FROM fct_product_problems",
        "Event type": "SELECT month, event_type AS value, report_number FROM fct_reports",
        "Report source": "SELECT month, report_source AS value, report_number FROM fct_reports",
        "Report origin": "SELECT month, report_origin AS value, report_number FROM fct_reports",
    }
    out = []
    for dim, sql in dims.items():
        d = con.execute(sql).df()
        d["month"] = pd.to_datetime(d["month"])
        counts = d.groupby(["month", "value"]).report_number.nunique().rename("n").reset_index()
        base = (counts[counts.month.isin(base_months)].groupby("value").n.sum() / n_base
                ).rename("baseline_mean")
        for m in sig_months:
            obs = counts[counts.month == m].set_index("value").n.rename("observed")
            t = pd.concat([obs, base], axis=1).fillna(0)
            t["excess"] = t["observed"] - t["baseline_mean"]
            total_excess = t.loc[t.excess > 0, "excess"].sum()
            t = t[t.excess > 0].sort_values("excess", ascending=False).head(top_k)
            t["share_of_excess_pct"] = (100 * t.excess / total_excess).round(1) if total_excess else 0.0
            t = t.reset_index().rename(columns={"index": "value"})
            t.insert(0, "dimension", dim)
            t.insert(0, "month", m.date())
            out.append(t)
    res = pd.concat(out, ignore_index=True)
    order = {d: i for i, d in enumerate(dims)}
    res = (res.assign(_d=res.dimension.map(order))
              .sort_values(["month", "_d", "excess"], ascending=[True, True, False])
              .drop(columns="_d").reset_index(drop=True))
    res["baseline_mean"] = res["baseline_mean"].round(1)
    res["excess"] = res["excess"].round(1)
    return res[cols]


def run(con: duckdb.DuckDBPyConnection, cfg: dict, mart_dir=MART_DIR):
    monthly = con.execute("SELECT month, reports FROM mart_monthly_total ORDER BY month").df()
    monthly["month"] = pd.to_datetime(monthly["month"])
    ctrl = build_control_table(monthly, cfg)
    if not (ctrl.phase == "Monitoring").any():
        print("  WARNING: no months after the baseline window, so nothing is monitored. "
              "Extend date_to or move the baseline earlier.")
    drivers = signal_drivers(con, ctrl)
    out = ctrl.copy()
    out["month"] = out["month"].dt.date
    out.to_csv(mart_dir / "mart_control_chart.csv", index=False)
    drivers.to_csv(mart_dir / "mart_signal_drivers.csv", index=False)
    tested = ctrl[ctrl.phase != "Pre-baseline"]
    print(f"  control chart: baseline {ctrl[ctrl.phase == 'Baseline'].month.min():%b %Y}–"
          f"{ctrl[ctrl.phase == 'Baseline'].month.max():%b %Y}; centre={ctrl.centre[0]:.1f} "
          f"UCL={ctrl.ucl[0]:.1f} LCL={ctrl.lcl[0]:.1f}")
    print(f"  tested months: {int(tested.rule1_above_ucl.sum())} above UCL, "
          f"{int(tested.rule1_below_lcl.sum())} below LCL, "
          f"{int(tested.rule2_upward_shift.sum())} in an upward shift, "
          f"{int(tested.rule2_downward_shift.sum())} in a downward shift")
    return ctrl, drivers


if __name__ == "__main__":
    c = duckdb.connect(str(DB_PATH))
    run(c, load_config())
    c.close()
