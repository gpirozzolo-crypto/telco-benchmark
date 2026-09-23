"""Scarica dalla BCE i cambi medi annui (EUR per unità di valuta) usati per convertire GBP e USD.

Uso: python etl/fetch_ecb_fx.py  ->  data/auto/ecb_fx.csv
"""
import csv
import datetime
import io
import sys
import urllib.request

from common import DATA, merge_keep_last_good, read_csv, write_csv

URL = "https://data-api.ecb.europa.eu/service/data/EXR/A.{cur}.EUR.SP00.A?format=csvdata&startPeriod=2014"
FIELDS = ["currency", "year", "eur_per_unit", "source_id", "retrieved", "via"]
OUT = DATA / "auto" / "ecb_fx.csv"
CURRENCIES = ["USD", "GBP"]


def parse_csv(text, currency, retrieved):
    """OBS_VALUE = unità di valuta per 1 EUR; salviamo l'inverso (EUR per unità)."""
    rows = []
    for r in csv.DictReader(io.StringIO(text)):
        value = r.get("OBS_VALUE")
        if not value:
            continue
        rows.append({
            "currency": currency, "year": r["TIME_PERIOD"][:4],
            "eur_per_unit": f"{1 / float(value):.6f}", "source_id": "ecb_fx",
            "retrieved": retrieved, "via": "data-api.ecb.europa.eu EXR annual",
        })
    return rows


def main():
    today = datetime.date.today().isoformat()
    fresh, ok = [], set()
    for cur in CURRENCIES:
        try:
            req = urllib.request.Request(URL.format(cur=cur), headers={"User-Agent": "telco-benchmark-etl"})
            with urllib.request.urlopen(req, timeout=60) as r:
                rows = parse_csv(r.read().decode("utf-8"), cur, today)
            if not rows:
                raise ValueError("nessun dato")
            fresh += rows
            ok.add(cur)
            print(f"ok  {cur}: {len(rows)} anni")
        except Exception as exc:  # noqa: BLE001
            print(f"ERR {cur}: {exc} - mantengo i valori precedenti", file=sys.stderr)
    merged = merge_keep_last_good(read_csv(OUT), fresh, ["currency", "year"], ok, "currency")
    write_csv(OUT, merged, FIELDS)
    print(f"Scritte {len(merged)} righe in {OUT}")


if __name__ == "__main__":
    main()
