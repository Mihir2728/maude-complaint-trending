# Reference tables

## manufacturer_aliases.csv

MAUDE records the manufacturing *site* as free text, so one company appears under
several names (e.g. `MEDTRONIC IRELAND` and `MEDTRONIC`; `BIOTRONIK BUELACH SWITZERLAND`
and `BIOTRONIK`). After basic cleaning (upper case, punctuation and legal suffixes removed),
any name that **starts with** `prefix` is grouped under `parent`. The longest matching prefix
wins. The original site name is kept in the `manufacturer_site` column.

Grouping sites under the parent company is an analysis choice: it is right for
company-level trending, wrong if you want to compare sites. Edit this file to change it,
then run `python run_pipeline.py --skip-fetch`.
