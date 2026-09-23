"""Scarica gli indicatori World Bank (inclusi quelli ITU) per i paesi configurati.

Uso: python etl/fetch_worldbank.py
Scrive data/auto/worldbank.csv. Un indicatore che fallisce mantiene i valori precedenti.
"""
import datetime
import json
import sys
import time
import urllib.request

from common import DATA, load_json, merge_keep_last_good, read_csv, write_csv

API = "https://api.worldbank.org/v2/country/{countries}/indicator/{indicator}?format=json&per_page=2000&date={start}:{end}"
FIELDS = ["country", "indicator", "year", "value", "source_id", "retrieved", "via"]
OUT = DATA / "auto" / "worldbank.csv"
# Indicatori usati come fallback nei calcoli anche se non sono KPI visibili.
EXTRA_INDICATORS = ["NY.GDP.MKTP.CD"]


def parse_response(payload, iso3_to_id, indicator, retrieved):
    """Converte la risposta JSON dell'API v2 in righe CSV. Ignora i valori nulli."""
    if not isinstance(payload, list) or len(payload) < 2 or payload[1] is None:
        message = payload[0].get("message") if isinstance(payload, list) and payload and isinstance(payload[0], dict) else payload
        raise ValueError(f"Risposta inattesa per {indicator}: {message}")
    rows = []
    for obs in payload[1]:
        cid = iso3_to_id.get(obs.get("countryiso3code"))
        if cid is None or obs.get("value") is None:
            continue
        rows.append({
            "country": cid, "indicator": indicator, "year": str(obs["date"]),
            "value": repr(float(obs["value"])), "source_id": "worldbank",
            "retrieved": retrieved, "via": "api.worldbank.org v2",
        })
    return rows


def fetch(url, attempts=3):
    for i in range(attempts):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "telco-benchmark-etl"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001 - rete instabile, riprova
            if i == attempts - 1:
                raise
            print(f"  tentativo {i + 1} fallito ({exc}), riprovo", file=sys.stderr)
            time.sleep(5 * (i + 1))


def main():
    countries = load_json("countries.json")["countries"]
    kpis = load_json("kpis.json")["kpis"]
    iso3_to_id = {c["iso3"]: c["id"] for c in countries}
    indicators = [k["wb"] for k in kpis if k.get("wb")] + EXTRA_INDICATORS
    today = datetime.date.today()
    fresh, ok, failed = [], set(), []
    for ind in indicators:
        url = API.format(countries=";".join(iso3_to_id), indicator=ind, start=today.year - 12, end=today.year)
        try:
            rows = parse_response(fetch(url), iso3_to_id, ind, today.isoformat())
            if not rows:
                raise ValueError("nessun valore restituito")
            fresh += rows
            ok.add(ind)
            print(f"ok  {ind}: {len(rows)} valori")
        except Exception as exc:  # noqa: BLE001
            failed.append(ind)
            print(f"ERR {ind}: {exc} - mantengo i valori precedenti", file=sys.stderr)
    merged = merge_keep_last_good(read_csv(OUT), fresh, ["country", "indicator", "year"], ok, "indicator")
    write_csv(OUT, merged, FIELDS)
    print(f"Scritte {len(merged)} righe in {OUT}")
    if failed and not ok:
        sys.exit(1)  # tutto fallito: segnala al workflow


if __name__ == "__main__":
    main()
