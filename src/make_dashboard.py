"""Build a self-contained interactive dashboard: docs/index.html.

docs/ is the GitHub Pages folder, so after pushing, enable Pages
(Settings -> Pages -> Deploy from branch -> main /docs) and the dashboard is
live at https://<user>.github.io/<repo>/.
"""
from __future__ import annotations

import html
import json
import os
import sys
from datetime import datetime, timezone

import pandas as pd
import plotly.graph_objects as go

sys.path.insert(0, os.path.dirname(__file__))
from common import DOCS_DIR, MART_DIR, load_config  # noqa: E402

# Validated categorical palette (fixed order, light / dark steps) + chart chrome.
SERIES = {
    "light": ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"],
    "dark": ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"],
}
CHROME = {
    "light": {"ink": "#0b0b0b", "ink2": "#52514e", "muted": "#898781", "grid": "#e1e0d9",
              "axis": "#c3c2b7", "band": "rgba(137,135,129,0.10)", "other": "#898781"},
    "dark": {"ink": "#f0efec", "ink2": "#c3c2b7", "muted": "#898781", "grid": "#2c2c2a",
             "axis": "#383835", "band": "rgba(137,135,129,0.08)", "other": "#898781"},
}
CRITICAL = "#d03b3b"
SERIOUS = "#ec835a"
EVENT_ORDER = ["Malfunction", "Injury", "Death", "Other / not provided"]


def _layout(fig: go.Figure, height=360, y_title="Reports per month"):
    c = CHROME["light"]
    fig.update_layout(
        height=height, margin=dict(l=56, r=16, t=8, b=40),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="system-ui, -apple-system, Segoe UI, sans-serif", size=13, color=c["ink2"]),
        hovermode="x unified", hoverlabel=dict(font_size=13),
        legend=dict(orientation="h", y=1.02, yanchor="bottom", x=0, title_text=""),
    )
    fig.update_xaxes(showgrid=False, linecolor=c["axis"], tickcolor=c["axis"], ticks="outside")
    fig.update_yaxes(gridcolor=c["grid"], zeroline=False, linecolor=c["axis"], title_text=y_title,
                     rangemode="tozero")
    return fig


def fig_control(ctrl: pd.DataFrame, method: str, sigma: float) -> go.Figure:
    c = CHROME["light"]
    x = pd.to_datetime(ctrl["month"])
    base = ctrl.phase == "Baseline"
    pre = ctrl.phase == "Pre-baseline"
    b0, b1 = x[base].min(), x[base].max()
    fig = go.Figure()
    fig.add_vrect(x0=b0 - pd.Timedelta(days=15), x1=b1 + pd.Timedelta(days=15),
                  fillcolor=c["band"], line_width=0, layer="below")
    fig.add_annotation(x=b0, y=0, yref="paper", xanchor="left", yanchor="bottom",
                       text=f"Baseline {b0:%b %Y}–{b1:%b %Y} sets the limits", showarrow=False,
                       font=dict(size=12, color=c["muted"]))
    if pre.any():
        fig.add_annotation(x=x[pre].min(), y=0, yref="paper", xanchor="left", yanchor="bottom",
                           text="Pre-baseline: context, not tested", showarrow=False,
                           font=dict(size=12, color=c["muted"]))
    reason = ctrl["signal_reason"].replace("", "In control")
    fig.add_trace(go.Scatter(
        x=x, y=ctrl["reports"], name="Reports", mode="lines+markers",
        line=dict(width=2, color=SERIES["light"][0]), marker=dict(size=8, color=SERIES["light"][0]),
        customdata=reason, meta={"role": "series", "slot": 0},
        hovertemplate="%{y:,} reports<br>%{customdata}<extra></extra>"))
    for col, label, dash in [("ucl", f"UCL (+{sigma:g}σ)", "dash"), ("centre", "Centre line", "solid"),
                             ("lcl", f"LCL (−{sigma:g}σ)", "dash")]:
        y = ctrl[col].where(~pre)  # limits only drawn where months are tested
        fig.add_trace(go.Scatter(
            x=x, y=y, name=label, mode="lines", meta={"role": "chrome-ink2"},
            line=dict(width=1.5, dash=dash, color=c["ink2"] if col != "centre" else c["muted"]),
            hovertemplate=f"{label}: %{{y:.1f}}<extra></extra>"))
    up = ctrl[ctrl["rule1_above_ucl"]]
    if not up.empty:
        fig.add_trace(go.Scatter(
            x=pd.to_datetime(up["month"]), y=up["reports"], name="Above UCL",
            mode="markers+text", text=["▲ " + pd.to_datetime(m).strftime("%b %Y") for m in up["month"]],
            textposition="top center", textfont=dict(size=12, color=c["ink"]),
            marker=dict(size=13, color=CRITICAL, line=dict(width=2, color="#fcfcfb")),
            meta={"role": "signal"}, hoverinfo="skip"))
    down = ctrl[ctrl["rule1_below_lcl"]]
    if not down.empty:
        fig.add_trace(go.Scatter(
            x=pd.to_datetime(down["month"]), y=down["reports"], name="▼ Below LCL",
            mode="markers", marker=dict(size=12, symbol="triangle-down", color=SERIOUS,
                                        line=dict(width=1.5, color="#fcfcfb")),
            meta={"role": "signal"}, hoverinfo="skip"))
    _layout(fig, 420)
    fig.update_layout(legend=dict(traceorder="normal"))
    fig.update_yaxes(title_text=f"Reports per month ({'I-MR' if method == 'imr' else 'c-chart'})")
    return fig


def fig_event_type(df: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    for i, et in enumerate(EVENT_ORDER):
        d = df[df.event_type == et]
        fig.add_trace(go.Bar(x=pd.to_datetime(d.month), y=d.reports, name=et,
                             marker=dict(color=SERIES["light"][i], line=dict(width=1, color="#fcfcfb")),
                             meta={"role": "series", "slot": i},
                             hovertemplate=f"{et}: %{{y:,}}<extra></extra>"))
    _layout(fig, 340)
    fig.update_layout(barmode="stack", bargap=0.15, legend=dict(traceorder="normal"))
    return fig


def fig_origin(df: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    for i, o in enumerate(["Other reports", "Literature / clinical study"]):
        d = df[df.report_origin == o]
        fig.add_trace(go.Bar(x=pd.to_datetime(d.month), y=d.reports, name=o,
                             marker=dict(color=SERIES["light"][i], line=dict(width=1, color="#fcfcfb")),
                             meta={"role": "series", "slot": i},
                             hovertemplate=f"{o}: %{{y:,}}<extra></extra>"))
    _layout(fig, 340)
    fig.update_layout(barmode="stack", bargap=0.15, legend=dict(traceorder="normal"))
    return fig


def fig_manufacturer(df: pd.DataFrame, rank: pd.DataFrame) -> go.Figure:
    order = rank.sort_values("rank")["manufacturer"].tolist()
    groups = [g for g in order if g in set(df.manufacturer_group)] + (
        ["Other"] if "Other" in set(df.manufacturer_group) else [])
    fig = go.Figure()
    for i, g in enumerate(groups):
        d = df[df.manufacturer_group == g]
        is_other = g == "Other"
        color = CHROME["light"]["other"] if is_other else SERIES["light"][i]
        fig.add_trace(go.Scatter(
            x=pd.to_datetime(d.month), y=d.reports, name=g.title(), mode="lines",
            line=dict(width=2, color=color, dash="dot" if is_other else "solid"),
            meta={"role": "other" if is_other else "series", "slot": i},
            hovertemplate=f"{g.title()}: %{{y:,}}<extra></extra>"))
    _layout(fig, 360)
    return fig


def fig_problems(df: pd.DataFrame, top_n: int) -> go.Figure:
    d = df.head(top_n).iloc[::-1]
    fig = go.Figure(go.Bar(
        y=d.problem, x=d.reports, orientation="h", text=[f"{p:.1f}%" for p in d.pct_of_reports],
        textposition="outside", cliponaxis=False,
        marker=dict(color=SERIES["light"][0]), meta={"role": "series", "slot": 0},
        hovertemplate="%{y}<br>%{x:,} reports (%{text} of all)<extra></extra>"))
    _layout(fig, max(300, 34 * len(d) + 60), y_title="")
    fig.update_layout(hovermode="closest", margin=dict(l=8, r=60))
    fig.update_xaxes(showgrid=True, gridcolor=CHROME["light"]["grid"], title_text="Reports")
    fig.update_yaxes(showgrid=False, automargin=True)
    return fig


def _table(df: pd.DataFrame, cls="tbl") -> str:
    head = "".join(f"<th>{html.escape(str(c))}</th>" for c in df.columns)
    rows = "".join("<tr>" + "".join(f"<td>{html.escape(str(v))}</td>" for v in r) + "</tr>"
                   for r in df.itertuples(index=False))
    return f'<table class="{cls}"><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table>'


def build(mart_dir=MART_DIR, docs_dir=DOCS_DIR, cfg: dict | None = None):
    cfg = cfg or load_config()
    cc = cfg.get("control_chart", {})
    method, sigma = cc.get("method", "imr"), float(cc.get("sigma", 3))
    rd = lambda n: pd.read_csv(mart_dir / f"{n}.csv")  # noqa: E731
    ctrl, et, mfr, rank, prob, drivers, origin = (
        rd("mart_control_chart"), rd("mart_monthly_event_type"), rd("mart_monthly_manufacturer"),
        rd("mart_manufacturer_rank"), rd("mart_product_problems"), rd("mart_signal_drivers"),
        rd("mart_monthly_origin"))

    total = int(ctrl.reports.sum())
    serious = int(et[et.event_type.isin(["Injury", "Death"])].reports.sum())
    mon = ctrl[ctrl.phase == "Monitoring"]
    n_ucl = int(mon.rule1_above_ucl.sum())
    n_low = int((mon.rule1_below_lcl | mon.rule2_downward_shift).sum())
    bm = pd.to_datetime(ctrl.loc[ctrl.phase == "Baseline", "month"])
    base_lbl = f"{bm.min():%b %Y}–{bm.max():%b %Y}"
    pre_rows = ctrl[ctrl.phase == "Pre-baseline"]
    b_mean = ctrl.loc[ctrl.phase == "Baseline", "reports"].mean()
    pre_txt = (f"Earlier months (mean {pre_rows.reports.mean():.0f}/month vs {b_mean:.0f} in the baseline) "
               "are shown for context but not tested." if len(pre_rows) else "")
    period = f"{pd.to_datetime(ctrl.month.iloc[0]):%b %Y} – {pd.to_datetime(ctrl.month.iloc[-1]):%b %Y}"

    figs = {
        "control": fig_control(ctrl, method, sigma),
        "event": fig_event_type(et),
        "mfr": fig_manufacturer(mfr, rank),
        "origin": fig_origin(origin),
        "prob": fig_problems(prob, int(cfg.get("top_n_problems", 12))),
    }
    fig_json = {k: json.loads(f.to_json()) for k, f in figs.items()}

    if drivers.empty:
        drivers_html = "<p class='note'>No tested month exceeded the upper control limit.</p>"
    else:
        d = drivers.copy()
        d.columns = ["Month", "Dimension", "Value", "Observed", "Baseline mean", "Excess", "% of excess"]
        drivers_html = _table(d)
    monthly_tbl = ctrl[["month", "phase", "reports", "centre", "ucl", "lcl", "signal_reason"]].copy()
    monthly_tbl[["centre", "ucl", "lcl"]] = monthly_tbl[["centre", "ucl", "lcl"]].round(1)
    monthly_tbl.columns = ["Month", "Phase", "Reports", "Centre", "UCL", "LCL", "Signal"]

    tiles = [
        (f"{total:,}", "MDR reports analysed", period),
        (f"{100 * serious / total:.1f}%" if total else "–", "Injury or death reports", f"{serious:,} reports"),
        (str(n_ucl), "Monitored months above UCL", f"of {len(mon)} months after the baseline"),
        (str(n_low), "Monitored months unusually low",
         f"below LCL or in a run of {cc.get('run_length', 8)}+ below centre"),
    ]
    tiles_html = "".join(f'<div class="tile"><div class="kpi">{v}</div><div class="lbl">{l}</div>'
                         f'<div class="sub">{s}</div></div>' for v, l, s in tiles)
    codes = ", ".join(cfg["product_codes"])
    built = datetime.now(timezone.utc).strftime("%d %b %Y")

    page = TEMPLATE.format(
        title=html.escape(cfg["project_title"]), codes=html.escape(codes), period=period, built=built,
        tiles=tiles_html, drivers=drivers_html, monthly=_table(monthly_tbl),
        base_lbl=base_lbl, pre_txt=pre_txt, sigma=f"{sigma:g}",
        method_txt=("individuals (I-MR) chart; σ estimated from the mean moving range ÷ 1.128"
                    if method == "imr" else "c-chart; σ = √(centre line), assumes Poisson counts"),
        figs=json.dumps(fig_json), series=json.dumps(SERIES), chrome=json.dumps(CHROME),
    )
    docs_dir.mkdir(parents=True, exist_ok=True)
    out = docs_dir / "index.html"
    out.write_text(page, encoding="utf-8")
    print(f"  dashboard written to {out}")
    return out


TEMPLATE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js" charset="utf-8"></script>
<style>
:root {{ color-scheme: light; --page:#f9f9f7; --surface:#fcfcfb; --ink:#0b0b0b; --ink2:#52514e;
  --muted:#898781; --grid:#e1e0d9; --axis:#c3c2b7; --crit:#d03b3b; }}
@media (prefers-color-scheme: dark) {{ :root {{ color-scheme: dark; --page:#0d0d0d; --surface:#1a1a19;
  --ink:#f0efec; --ink2:#c3c2b7; --grid:#2c2c2a; --axis:#383835; }} }}
* {{ box-sizing: border-box; }}
body {{ margin:0; background:var(--page); color:var(--ink);
  font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif; }}
main {{ max-width:1180px; margin:0 auto; padding:32px 20px 64px; }}
h1 {{ font-size:26px; margin:0 0 4px; letter-spacing:-0.01em; }}
h2 {{ font-size:17px; margin:0 0 2px; }}
.meta, .note, .sub {{ color:var(--ink2); }}
.meta {{ margin:0 0 24px; }}
.tiles {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(200px,1fr)); gap:12px; margin-bottom:16px; }}
.tile, .card {{ background:var(--surface); border:1px solid var(--grid); border-radius:10px; padding:16px 18px; }}
.kpi {{ font-size:30px; font-weight:650; font-variant-numeric:tabular-nums; }}
.lbl {{ font-weight:600; }} .sub {{ font-size:13px; }}
.card {{ margin-bottom:16px; }}
.card > p.note {{ margin:0 0 8px; font-size:14px; }}
.grid2 {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; }}
@media (max-width: 900px) {{ .grid2 {{ grid-template-columns:1fr; }} }}
.tbl {{ border-collapse:collapse; width:100%; font-size:13px; font-variant-numeric:tabular-nums; }}
.tbl th, .tbl td {{ text-align:left; padding:6px 10px; border-bottom:1px solid var(--grid); }}
.tbl th {{ color:var(--ink2); font-weight:600; position:sticky; top:0; background:var(--surface); }}
.scroll {{ max-height:420px; overflow:auto; }}
details summary {{ cursor:pointer; color:var(--ink2); margin-top:8px; }}
ul.caveats li {{ margin-bottom:6px; }}
footer {{ color:var(--muted); font-size:13px; margin-top:24px; }}
a {{ color:inherit; }}
</style></head>
<body><main>
<h1>{title}</h1>
<p class="meta">FDA MAUDE adverse-event reports via openFDA · product code(s) {codes} · received {period} · built {built}</p>

<section class="tiles">{tiles}</section>

<section class="card">
  <h2>Monthly report volume: control chart</h2>
  <p class="note">The baseline ({base_lbl}) sets the centre line and ±{sigma}σ limits; later months are judged against those fixed limits. {pre_txt}
  Method: {method_txt}. ▲ red = above the UCL; ▼ orange = below the LCL.</p>
  <div id="control"></div>
  <details><summary>Show the monthly data as a table</summary><div class="scroll">{monthly}</div></details>
</section>

<section class="card">
  <h2>What drove each signal month?</h2>
  <p class="note">Excess reports over the baseline monthly mean, split by manufacturer, product problem, event type, report source and report origin (top 3 contributors each).
  One manufacturer or one problem taking most of the excess points to a specific issue (or a backlog submission) rather than a category-wide trend.</p>
  <div class="scroll">{drivers}</div>
</section>

<div class="grid2">
  <section class="card"><h2>Reports by event type</h2><p class="note">Monthly, stacked.</p><div id="event"></div></section>
  <section class="card"><h2>Reports from published studies or trials</h2><p class="note">Narrative mentions an article, journal, study, trial or registry (keyword rule). These arrive in batches and rarely identify a device problem.</p><div id="origin"></div></section>
</div>

<section class="card"><h2>Reports by manufacturer</h2><p class="note">Top manufacturers by total volume, with site names grouped under the parent company (see <code>reference/manufacturer_aliases.csv</code>); the rest grouped as Other.</p><div id="mfr"></div></section>

<section class="card">
  <h2>Most frequent device (product) problems</h2>
  <p class="note">Share of all reports that list each problem; one report can list several.</p>
  <div id="prob"></div>
</section>

<section class="card">
  <h2>How to read this (MAUDE limitations)</h2>
  <ul class="caveats">
    <li>MAUDE is a passive surveillance system. Counts reflect <b>reporting</b>, not incidence: without units sold or procedures performed there is no denominator, so a manufacturer with more reports may simply have more devices on the market.</li>
    <li>Reports can be incomplete, unverified, duplicated or delayed, and a single submission can include backlogged events. A spike is a prompt to investigate, not evidence that a device caused harm.</li>
    <li>Months are by <b>date FDA received</b> the report, not the event date, so reporting delays shift events later.</li>
    <li>Manufacturer names are standardised with text rules and grouped by parent company (<code>reference/manufacturer_aliases.csv</code>). Literature/study reports are identified by keywords in the narrative, so some will be missed or mislabelled.</li>
  </ul>
</section>

<footer>Data: U.S. FDA MAUDE via <a href="https://open.fda.gov/apis/device/event/">openFDA</a>. openFDA data is not for clinical use and has not been validated by FDA.
Built with Python, DuckDB (SQL) and Plotly.</footer>
</main>
<script>
const FIGS = {figs}, SERIES = {series}, CHROME = {chrome};
const dark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
const mode = dark ? 'dark' : 'light', C = CHROME[mode];
for (const [id, fig] of Object.entries(FIGS)) {{
  for (const t of fig.data) {{
    const m = t.meta || {{}};
    if (m.role === 'series') {{
      const col = SERIES[mode][m.slot];
      if (t.line) t.line.color = col;
      if (t.marker) t.marker.color = col;
    }} else if (m.role === 'other') {{ t.line.color = C.other; }}
    else if (m.role === 'chrome-ink2') {{ t.line.color = t.name === 'Centre line' ? C.muted : C.ink2; }}
    else if (m.role === 'signal') {{ if (t.textfont) t.textfont.color = C.ink; t.marker.line.color = dark ? '#1a1a19' : '#fcfcfb'; }}
    if (t.type === 'bar' && t.marker && t.marker.line) t.marker.line.color = dark ? '#1a1a19' : '#fcfcfb';
  }}
  const L = fig.layout;
  L.font.color = C.ink2;
  for (const ax of ['xaxis', 'yaxis']) {{ if (L[ax]) {{ L[ax].gridcolor = C.grid; L[ax].linecolor = C.axis; L[ax].tickcolor = C.axis; }} }}
  (L.shapes || []).forEach(s => s.fillcolor = C.band);
  (L.annotations || []).forEach(a => a.font && (a.font.color = C.muted));
  Plotly.newPlot(id, fig.data, L, {{displayModeBar: false, responsive: true}});
}}
</script>
</body></html>
"""

if __name__ == "__main__":
    build()
