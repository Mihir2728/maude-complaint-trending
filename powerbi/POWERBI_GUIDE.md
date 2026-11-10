# Building the Power BI version

The Python pipeline produces clean, analysis-ready CSVs in `data/marts/`. This guide
rebuilds the same dashboard in **Power BI Desktop** (free, Windows only; on a Mac use
a Windows VM, or upload the CSVs to the Power BI web service, which has fewer features).

Allow about 2–3 hours the first time. Save the result as `powerbi/maude_trending.pbix`
and export a screenshot to `docs/powerbi_dashboard.png` for the README.

## 1. Load the data

1. Run `python run_pipeline.py` first, so `data/marts/*.csv` exist.
2. **Home → Get data → Text/CSV** and load each of these:
   - `mart_control_chart.csv`
   - `mart_monthly_event_type.csv`
   - `mart_monthly_manufacturer.csv`
   - `mart_product_problems.csv`
   - `mart_signal_drivers.csv`
3. In **Transform data** (Power Query), check the column types: `month` = Date;
   `reports`, `centre`, `ucl`, `lcl`, `moving_range` = Decimal/Whole number;
   `rule1_above_ucl`, `rule1_below_lcl`, `rule2_upward_shift`, `rule2_downward_shift`, `signal_up`, `signal_down` = True/False. Close & Apply.

## 2. Model

1. **Modeling → New table** and paste the `Months` table from `measures.dax`.
2. In **Model view**, drag `Months[Month]` onto the `month` column of each mart table that has
   one (one-to-many, single direction).
3. Mark `Months` as a date table if Power BI offers it, or just use `Months[Month]` on every x-axis.

## 3. Measures

**Modeling → New measure**, one per measure in `measures.dax`. After creating
`Baseline Centre` and `UCL`, put them in a card and check they match the centre and UCL
values printed by `run_pipeline.py`. That check shows the DAX reproduces the Python method.

## 4. Visuals (one page, 16:9)

| Visual | Fields | Notes |
|---|---|---|
| 4 × **Card** | `Total Reports`, `Injury or Death %`, `Months Above UCL`, `Months Below LCL` | Top row |
| **Line chart**: control chart | X: `Months[Month]`; Y: `Total Reports`, `Baseline Centre`, `UCL`, `LCL`, `Signal Reports`, `Low Signal Reports` | UCL/LCL dashed grey; centre solid grey; signal series line width 0 with large markers (red up, orange down). Shade the baseline window with an X-axis constant line or a background shape |
| **Stacked column**: event type | X: `Months[Month]`; Y: `mart_monthly_event_type[reports]`; Legend: `event_type` | |
| **Line chart**: manufacturer | X: `Months[Month]`; Y: `mart_monthly_manufacturer[reports]`; Legend: `manufacturer_group` | |
| **Bar chart**: product problems | Y: `problem`; X: `reports` | Filter pane → Top N = 12 by `reports` |
| **Table**: signal drivers | all columns of `mart_signal_drivers` | |
| **Slicer** | `Months[Month]` (Between) | Date-range filter for the whole page |

Keep one colour per meaning: one blue for report volume, grey for limits, red only for signals.

## 5. Publish to GitHub

- Save `powerbi/maude_trending.pbix` (usually a few MB, fine for GitHub).
- **File → Export → Export to PDF**, or take a screenshot, and save it as `docs/powerbi_dashboard.png`.
- Uncomment the Power BI screenshot line in `README.md`.
