"""Generate SYNTHETIC openFDA-shaped records for testing the pipeline offline.

Not real FDA data: manufacturer names are fictional. The fixture plants known
features so the tests can check the pipeline finds them:
  * a one-month spike (2024-03) driven by one manufacturer + one problem;
  * messy manufacturer spellings that should collapse to one name;
  * duplicate report numbers;
  * out-of-scope devices (product code LOX) that must be excluded.
"""
from __future__ import annotations

import gzip
import json
import random
from datetime import date
from pathlib import Path

SPIKE_MONTH = date(2024, 3, 1)
SPIKE_MFR = "ACME VASCULAR"

MFRS = {  # canonical name -> (weight, spellings)
    "ACME VASCULAR": (0.30, ["Acme Vascular, Inc.", "ACME VASCULAR INC", "acme vascular corp"]),
    "NORTHWIND MEDICAL": (0.25, ["Northwind Medical LLC", "NORTHWIND MEDICAL"]),
    "CONTOSO CARDIO": (0.20, ["Contoso Cardio Ltd", "CONTOSO CARDIO LIMITED"]),
    "FABRIKAM DEVICES": (0.12, ["Fabrikam Devices GmbH"]),
    "TAILSPIN HEALTH": (0.08, ["Tailspin Health Co."]),
    "WINGTIP LIFESCIENCES": (0.05, ["Wingtip Lifesciences"]),
}
# Site-name variants of one company, to test the alias table (prefix MEDTRONIC).
SITE_VARIANTS = ["Medtronic Ireland", "MEDTRONIC, INC."]
PROBLEMS = ["Material Deformation", "Failure to Advance", "Difficult to Remove",
            "Detachment of Device or Device Component", "Activation Failure",
            "Adverse Event Without Identified Device or Use Problem", "Material Separation"]
PAT_PROBLEMS = ["Thrombosis", "Myocardial Infarction", "No Clinical Signs, Symptoms or Conditions",
                "Restenosis", "Vascular Dissection"]


def months(start=date(2020, 1, 1), end=date(2025, 12, 1)):
    d = start
    while d <= end:
        yield d
        d = date(d.year + (d.month == 12), d.month % 12 + 1, 1)


def record(rng, rn, month, mfr_canon, code="NIQ", problem=None, literature=False):
    spelling = rng.choice(MFRS[mfr_canon][1]) if mfr_canon in MFRS else mfr_canon
    text = ("IT WAS REPORTED THROUGH A RESEARCH ARTICLE THAT MAY BE RELATED TO DEATH OR INJURY."
            if literature else "SYNTHETIC TEST NARRATIVE")
    received = date(month.year, month.month, rng.randint(1, 28))
    event_day = date.fromordinal(received.toordinal() - rng.randint(5, 120))
    etype = rng.choices(["Malfunction", "Injury", "Death", ""], [0.62, 0.32, 0.03, 0.03])[0]
    return {
        "report_number": f"SYN-{rn:07d}",
        "mdr_report_key": str(10_000_000 + rn),
        "date_received": received.strftime("%Y%m%d"),
        "date_of_event": event_day.strftime("%Y%m%d"),
        "event_type": etype,
        "report_source_code": rng.choices(["Manufacturer report", "User facility report", "Voluntary report"],
                                          [0.85, 0.1, 0.05])[0],
        "type_of_report": ["Initial submission"],
        "adverse_event_flag": "Y" if etype in ("Injury", "Death") else "N",
        "product_problem_flag": "Y",
        "product_problems": [problem or rng.choice(PROBLEMS)],
        "patient": [{"patient_problems": [rng.choice(PAT_PROBLEMS)]}],
        "device": [{
            "device_sequence_number": "1",
            "device_report_product_code": code,
            "manufacturer_d_name": spelling,
            "brand_name": f"{mfr_canon.title().split()[0]} DES",
            "generic_name": "CORONARY DRUG-ELUTING STENT",
            "model_number": f"M{rng.randint(100, 999)}",
            "openfda": {"device_name": ["Coronary Drug-Eluting Stent"]},
        }],
        "mdr_text": [{"text_type_code": "Description of Event or Problem",
                      "text": text},
                     {"text_type_code": "Description of Event or Problem",
                      "text": "SYNTHETIC TEST NARRATIVE (follow-up)"}],
    }


def write_fixture(out_dir: Path, seed: int = 7) -> dict:
    rng = random.Random(seed)
    out_dir.mkdir(parents=True, exist_ok=True)
    names, weights = list(MFRS), [w for w, _ in MFRS.values()]
    by_year: dict[int, list] = {}
    rn = 0
    for m in months():
        # over-dispersed monthly volume with a gentle upward drift
        n = max(0, int(rng.gauss(80 + 0.3 * (m.year - 2020) * 12, 9)))
        recs = [record(rng, (rn := rn + 1), m, rng.choices(names, weights)[0]) for _ in range(n)]
        if m == SPIKE_MONTH:  # planted signal
            recs += [record(rng, (rn := rn + 1), m, SPIKE_MFR, problem="Difficult to Remove",
                            literature=True) for _ in range(70)]
        recs += [record(rng, (rn := rn + 1), m, v) for v in SITE_VARIANTS]
        recs += [record(rng, (rn := rn + 1), m, "OTHER BALLOON CO", code="LOX") for _ in range(10)]
        recs += [dict(recs[0])]  # duplicate report_number
        by_year.setdefault(m.year, []).extend(recs)
    for y, recs in by_year.items():
        with gzip.open(out_dir / f"events_{y}.jsonl.gz", "wt", encoding="utf-8") as f:
            for r in recs:
                f.write(json.dumps(r) + "\n")
    return {"spike_month": SPIKE_MONTH, "spike_mfr": SPIKE_MFR}


if __name__ == "__main__":
    import sys
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/raw")
    print(write_fixture(target))
