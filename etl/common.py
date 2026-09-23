"""Utility condivise dagli script ETL."""
import csv
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config"
DATA = ROOT / "data"


def load_json(name):
    return json.loads((CONFIG / name).read_text(encoding="utf-8"))


def read_csv(path):
    path = pathlib.Path(path)
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_csv(path, rows, fieldnames):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


def merge_keep_last_good(existing, fresh, key_fields, replaced_groups, group_field):
    """Sostituisce solo i gruppi (es. indicatori) scaricati con successo.

    Se una chiamata fallisce, le righe già presenti per quel gruppo restano:
    il sito continua a mostrare l'ultimo dato valido invece di un buco.
    """
    kept = [r for r in existing if r[group_field] not in replaced_groups]
    merged = kept + fresh
    merged.sort(key=lambda r: tuple(r[k] for k in key_fields))
    return merged
