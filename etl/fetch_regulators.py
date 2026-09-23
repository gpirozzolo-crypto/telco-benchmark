"""Scarica i dati trimestrali dei regolatori con API stabili (CNMC, Arcep) in data/auto/regulators.csv.

Ogni fonte è indipendente: se una fallisce, restano le sue righe precedenti.
I messaggi di scarto finiscono in data/auto/regulators_log.txt e nella issue automatica.
"""
import datetime
import sys

import requests

import sources_arcep
import sources_cnmc
from common import DATA, merge_keep_last_good, read_csv, write_csv

OUT = DATA / "auto" / "regulators.csv"
LOG = DATA / "auto" / "regulators_log.txt"
FIELDS = ["country", "kpi", "value", "period", "period_end", "frequency", "source_id", "source_url", "retrieved", "method"]


def main():
    today = datetime.date.today().isoformat()
    session = requests.Session()
    session.headers.update({"User-Agent": "telco-benchmark-etl (+github)"})
    fresh, ok, log = [], set(), []
    jobs = {
        "cnmc_api": lambda: sources_cnmc.extract(sources_cnmc.fetch_records(session), today),
        "arcep_api": lambda: sources_arcep.fetch(session, today),
    }
    for sid, job in jobs.items():
        try:
            rows, l = job()
            log += l
            if not rows:
                raise ValueError("nessun valore estratto")
            fresh += rows
            ok.add(sid)
            print(f"ok  {sid}: {len(rows)} valori")
        except Exception as exc:  # noqa: BLE001
            log.append(f"{sid}: errore {exc} - mantengo i valori precedenti")
            print(f"ERR {sid}: {exc}", file=sys.stderr)
    merged = merge_keep_last_good(read_csv(OUT), fresh, ["source_id", "kpi", "period_end"], ok, "source_id")
    write_csv(OUT, merged, FIELDS)
    LOG.write_text("\n".join(log) + ("\n" if log else ""), encoding="utf-8")
    print(f"Scritte {len(merged)} righe; {len(log)} note nel log")


if __name__ == "__main__":
    main()
