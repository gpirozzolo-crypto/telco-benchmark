"""Apre (o aggiorna) una issue GitHub con l'elenco delle fonti curate scadute.

Legge dist/stale.json prodotto da build.py. Richiede la CLI `gh` (presente sui runner GitHub).
"""
import json
import subprocess

from common import ROOT

LABEL = "dati-da-aggiornare"
TITLE = "Benchmark: dati da controllare"


def gh(*args):
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout


def main():
    report = json.loads((ROOT / "dist" / "stale.json").read_text(encoding="utf-8"))
    stale, anomalies, log = report.get("stale", []), report.get("anomalies", []), report.get("log", [])
    if not (stale or anomalies or log):
        print("Nessuna segnalazione")
        return
    lines = []
    if stale:
        lines += ["### Fonti scadute", "Aggiorna i valori in `data/curated/kpi_values.csv` (e `market_shares.csv` se serve), compresa la colonna `retrieved`.", ""]
        for s in stale:
            lines.append(f"- **{s['name']}**: ultima raccolta {s['last_retrieved']} ({s['age_days']} giorni fa), cadenza {s['cadence']}. {s['url']}")
        lines.append("")
    if anomalies:
        lines += ["### Valori automatici scartati", "Il sito mostra il valore curato. Controlla se la fonte ha cambiato definizione o formato.", ""]
        lines += [f"- {a}" for a in anomalies] + [""]
    if log:
        lines += ["### Note dei connettori", ""] + [f"- {l}" for l in log]
    body = "\n".join(lines)
    gh("label", "create", LABEL, "--color", "B26B00", "--force")
    existing = json.loads(gh("issue", "list", "--label", LABEL, "--state", "open", "--json", "number"))
    if existing:
        gh("issue", "edit", str(existing[0]["number"]), "--body", body)
        print(f"Aggiornata issue #{existing[0]['number']}")
    else:
        gh("issue", "create", "--title", TITLE, "--label", LABEL, "--body", body)
        print("Creata nuova issue")


if __name__ == "__main__":
    main()
