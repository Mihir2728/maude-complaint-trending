"""Generate reports/insight_brief.md (one page) and a narrative sample per signal month.

The numbers are filled in automatically. The "Interpretation" section is left
for a human to write after reading the narratives in
reports/signal_month_narratives.csv: that judgement is the part of trending
work a script cannot do.
"""
from __future__ import annotations

import os
import sys

import duckdb
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from common import DB_PATH, MART_DIR, REPORTS_DIR, load_config  # noqa: E402


def _pct(a, b):
    return f"{100 * a / b:.1f}%" if b else "n/a"


def build(con: duckdb.DuckDBPyConnection, cfg: dict | None = None, mart_dir=MART_DIR,
          reports_dir=REPORTS_DIR, narratives_per_month: int = 10):
    cfg = cfg or load_config()
    cc = cfg.get("control_chart", {})
    rd = lambda n: pd.read_csv(mart_dir / f"{n}.csv")  # noqa: E731
    ctrl, et, rank, prob, drivers, lag = (rd("mart_control_chart"), rd("mart_monthly_event_type"),
                                          rd("mart_manufacturer_rank"), rd("mart_product_problems"),
                                          rd("mart_signal_drivers"), rd("mart_reporting_lag"))
    ymfr, origin, sites = rd("mart_yearly_manufacturer"), rd("mart_monthly_origin"), rd("mart_manufacturer_sites")
    total = int(ctrl.reports.sum())
    ctrl["month"] = pd.to_datetime(ctrl["month"])
    base, mon = ctrl[ctrl.phase == "Baseline"], ctrl[ctrl.phase == "Monitoring"]
    first, last = ctrl.month.iloc[0], ctrl.month.iloc[-1]
    ev = et.groupby("event_type").reports.sum()
    up = mon[mon.rule1_above_ucl | mon.rule2_upward_shift]
    low_lcl, low_run = int(mon.rule1_below_lcl.sum()), int(mon.rule2_downward_shift.sum())
    sig = ctrl[ctrl.rule1_above_ucl]
    top_m = rank.sort_values("rank").head(3)
    yearly = ctrl.groupby(ctrl.month.dt.year).reports.agg(["sum", "mean"])
    b_mean = base.reports.mean()
    m_mean = mon.reports.mean() if not mon.empty else float("nan")
    run_n = cc.get("run_length", 8)

    L = [f"# Insight brief: {cfg['project_title']}", "",
         f"*Source: FDA MAUDE via openFDA · product code(s) {', '.join(cfg['product_codes'])} · "
         f"reports received {first:%b %Y} – {last:%b %Y} · {total:,} reports*", "",
         "## Key numbers", "",
         f"- **{total:,}** MDR reports over {len(ctrl)} months.",
         "- Reports per year (monthly mean): " + "; ".join(
             f"{y} {int(r['sum']):,} ({r['mean']:.0f})" for y, r in yearly.iterrows()) + ".",
         f"- Event mix: Malfunction {_pct(ev.get('Malfunction', 0), total)}, Injury {_pct(ev.get('Injury', 0), total)}, "
         f"Death {_pct(ev.get('Death', 0), total)}, Other/not provided {_pct(ev.get('Other / not provided', 0), total)}.",
         "- Top manufacturers by volume: " + "; ".join(
             f"{r.manufacturer.title()} ({_pct(r.reports, total)})" for r in top_m.itertuples()) + ".",
         "- Most frequent device problems: " + "; ".join(
             f"{r.problem} ({r.pct_of_reports}%)" for r in prob.head(3).itertuples()) + ".",
         f"- Control chart ({'I-MR' if cc.get('method', 'imr') == 'imr' else 'c-chart'}, ±{cc.get('sigma', 3)}σ): "
         f"baseline {base.month.min():%b %Y}–{base.month.max():%b %Y} (mean {b_mean:.1f}/month); "
         f"centre {ctrl.centre.iloc[0]:.1f}, UCL {ctrl.ucl.iloc[0]:.1f}, LCL {ctrl.lcl.iloc[0]:.1f}.",
         f"- Monitoring period ({len(mon)} months, mean {m_mean:.1f}/month, {(m_mean - b_mean) / b_mean:+.0%} vs baseline): "
         f"**{int(mon.rule1_above_ucl.sum())}** month(s) above the UCL, "
         f"**{int(mon.rule2_upward_shift.sum())}** in an upward run of {run_n}+; "
         f"**{low_lcl}** below the LCL, **{low_run}** in a downward run of {run_n}+.",
         ]
    if not lag.empty:
        L.append("- Median event-to-receipt lag: " + "; ".join(
            f"{r.event_type} {r.median_lag_days:.0f} days" for r in lag.itertuples()) + ".")
    # Manufacturer by year (top N + Other), with change first -> last year
    top_names = rank.sort_values("rank").head(int(cfg.get("top_n_manufacturers", 6))).manufacturer
    ymfr["group"] = ymfr.manufacturer.where(ymfr.manufacturer.isin(top_names), "Other")
    pv = ymfr.pivot_table(index="group", columns="year", values="reports", aggfunc="sum", fill_value=0)
    pv = pv.loc[sorted(pv.index, key=lambda g: (g == "Other", -pv.loc[g].sum()))]
    pv.loc["All"] = pv.sum()
    y0, y1 = pv.columns[0], pv.columns[-1]
    drop_total = pv.loc["All", y0] - pv.loc["All", y1]
    hdr = "| Manufacturer | " + " | ".join(str(c) for c in pv.columns) + f" | Change {y0}→{y1} | Share of change |"
    L += ["", "## Reports by manufacturer and year", "", hdr, "|" + "---|" * (len(pv.columns) + 3)]
    for g, r in pv.iterrows():
        chg = (r[y1] - r[y0]) / r[y0] if r[y0] else float("nan")
        share = (r[y0] - r[y1]) / drop_total + 0.0 if drop_total and g != "All" else float("nan")
        name = g if g in ("Other", "All") else g.title()
        L.append(f"| {name} | " + " | ".join(f"{int(v):,}" for v in r) +
                 f" | {chg + 0.0:+.0%} | " + ("" if share != share else f"{abs(share) if abs(share) < 0.005 else share:.0%}") + " |")
    merged = sites.groupby("manufacturer").manufacturer_site.nunique()
    merged = merged[merged > 1]
    if len(merged):
        L += ["", "Site names grouped under a parent company (`reference/manufacturer_aliases.csv`): " +
              "; ".join(f"{m.title()} ← " + ", ".join(sites[sites.manufacturer == m].manufacturer_site.str.title())
                        for m in merged.index) + "."]

    # Literature / clinical-study reports
    origin["month"] = pd.to_datetime(origin["month"])
    o = origin.pivot_table(index="month", columns="report_origin", values="reports", aggfunc="sum").fillna(0)
    lit_col = "Literature / clinical study"
    if lit_col in o:
        o["share"] = o[lit_col] / o.sum(axis=1).where(o.sum(axis=1) > 0)
        oy = o.groupby(o.index.year)[[lit_col]].sum()
        oy["total"] = o.drop(columns="share").groupby(o.index.year).sum().sum(axis=1)
        b_share = o.loc[o.index.isin(base.month), lit_col].sum() / max(1, base.reports.sum())
        L += ["", "## Reports from published studies or trials", "",
              "Narratives mentioning an article, journal, study, trial or registry (keyword rule, approximate).", "",
              "- Share by year: " + "; ".join(f"{y} {r[lit_col] / r.total:.0%} ({int(r[lit_col]):,})"
                                              for y, r in oy.iterrows() if r.total) + ".",
              f"- Baseline share: {b_share:.0%}. Highest-volume months: " + "; ".join(
                  f"{m:%b %Y} {o.loc[m, 'share']:.0%} of {int(o.loc[m].drop('share').sum()):,}"
                  for m in ctrl.nlargest(3, "reports").month) + "."]

    pre_years = [(y, r["mean"]) for y, r in yearly.iterrows()
                 if pd.Timestamp(year=int(y), month=12, day=1) < base.month.min()]
    L += ["", "## Baseline choice", "",
          f"Limits are set on {base.month.min():%b %Y}–{base.month.max():%b %Y} (mean {b_mean:.0f}/month)"
          + ((". Monthly means before that: " + ", ".join(f"{y}: {v:.0f}" for y, v in pre_years) + ".")
             if pre_years else ".")
          + " The baseline should be a stable stretch: limits estimated while the process is changing "
          "come out too wide and hide later signals. Months before the baseline are shown for context "
          "but not tested. (Set in `config.yaml`.)", "",
          "## Signal months (above UCL)", ""]
    if sig.empty:
        L.append("No tested month exceeded the upper control limit.")
    for m in sig.itertuples():
        md = pd.to_datetime(m.month)
        L.append(f"### {md:%B %Y}: {m.reports} reports (UCL {m.ucl:.1f}, excess {m.reports - m.centre:.0f} over centre)")
        d = drivers[pd.to_datetime(drivers.month) == md]
        for dim in ["Manufacturer", "Product problem", "Event type", "Report source"]:
            top = d[d.dimension == dim].head(2)
            if not top.empty:
                L.append(f"- **{dim}:** " + "; ".join(
                    f"{str(r.value).title() if dim == 'Manufacturer' else r.value} "
                    f"(+{r.excess:.0f}, {r.share_of_excess_pct:.0f}% of excess)" for r in top.itertuples()))
        L.append("")
    if not up.empty and sig.empty:
        L.append(f"{len(up)} month(s) sit in an upward run; see the dashboard.")
    L.append("")
    L += ["## Interpretation (write this after reading the narratives)", "",
          "> Use `reports/signal_month_narratives.csv`. Questions to answer:",
          "> 1. Is each signal driven by one manufacturer / one problem code, or spread across the category?",
          "> 2. Do the narratives describe a single failure mode, or is it a backlog / summary submission?",
          "> 3. What explains the long-term decline in reports (fewer procedures, better devices, or changed",
          ">    reporting, e.g. manufacturers grouping malfunctions into summary reports)? What evidence would tell them apart?",
          "> 4. What would you escalate, and what extra data (units sold, lot numbers) would you request?", "",
          "_Your 3–5 sentence summary here._", "",
          "## Limitations", "",
          "MAUDE is passive surveillance: counts reflect reporting, not incidence, and there is no "
          "denominator (units sold or procedures). Reports may be incomplete, duplicated or delayed. "
          "Months use the date FDA received the report. A signal is a prompt to investigate, not "
          "evidence that a device caused harm.", ""]
    reports_dir.mkdir(parents=True, exist_ok=True)
    (reports_dir / "insight_brief.md").write_text("\n".join(L), encoding="utf-8")

    # Reproducible narrative sample: every above-UCL month plus the 3 highest-volume
    # months overall (so large pre-baseline spikes can be examined too).
    picks = {m: "Above UCL" for m in sig.month}
    for m in ctrl.nlargest(3, "reports").month:
        picks.setdefault(m, "Highest-volume month")
    why = pd.DataFrame({"month": [m.date() for m in picks], "why_sampled": list(picks.values())})
    con.register("tmp_why", why)
    nar = con.execute(f"""
        SELECT w.why_sampled, f.month, f.manufacturer, f.event_type, f.report_number, n.text
        FROM fct_reports f
        JOIN tmp_why w ON f.month = w.month
        JOIN (SELECT report_number, arg_max(text, length(text)) AS text
              FROM raw_narratives GROUP BY report_number) n USING (report_number)
        QUALIFY ROW_NUMBER() OVER (PARTITION BY f.month ORDER BY hash(f.report_number)) <= {narratives_per_month}
        ORDER BY f.month, f.manufacturer""").df()
    con.unregister("tmp_why")
    nar.to_csv(reports_dir / "signal_month_narratives.csv", index=False)
    L_months = ", ".join(f"{m:%b %Y}" for m in picks)
    (reports_dir / "insight_brief.md").write_text(
        "\n".join(L).replace("Use `reports/signal_month_narratives.csv`.",
                             f"Use `reports/signal_month_narratives.csv` ({L_months})."),
        encoding="utf-8")
    print(f"  brief written to {reports_dir / 'insight_brief.md'}")


if __name__ == "__main__":
    c = duckdb.connect(str(DB_PATH))
    build(c)
    c.close()
