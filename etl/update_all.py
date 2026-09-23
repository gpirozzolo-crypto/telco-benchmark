"""Esegue tutti i connettori automatici. Un errore in una fonte non ferma le altre."""
import pathlib
import runpy
import subprocess
import sys
import traceback

STEPS = ["fetch_worldbank.py", "fetch_ecb_fx.py", "fetch_regulators.py"]

# File rimasti nella cartella principale da caricamenti manuali precedenti.
# Solo questi nomi esatti e solo nella radice del repository; le copie buone stanno in config/, data/ e site/.
OBSOLETE_ROOT_FILES = ["kpi_values.csv", "market_shares.csv", "template.html", "update.yml",
                       "countries.json", "kpis.json", "sources.json", "ecb_fx.csv", "worldbank.csv",
                       "regulators.csv", "regulators_log.txt", "build.py", "common.py", "fetch_ecb_fx.py",
                       "fetch_regulators.py", "fetch_worldbank.py", "open_stale_issue.py", "sources_arcep.py",
                       "sources_cnmc.py", "test_etl.py", "test_regulators.py", "update_all.py"]


def clean_root():
    """Rimuove dal repository i file obsoleti; la rimozione entra nel commit dei dati automatici."""
    root = pathlib.Path(__file__).resolve().parent.parent
    found = [f for f in OBSOLETE_ROOT_FILES if (root / f).is_file()]
    if not found or not (root / ".git").exists():
        return
    subprocess.run(["git", "rm", "-q", "--ignore-unmatch", "--", *found], cwd=root, check=False)
    print("Rimossi file obsoleti dalla radice:", ", ".join(found))


clean_root()

failed = []
for step in STEPS:
    print(f"== {step}")
    try:
        runpy.run_path(step, run_name="__main__")
    except SystemExit as e:
        if e.code not in (0, None):
            failed.append(step)
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        failed.append(step)
print("Fonti con errori:", ", ".join(failed) if failed else "nessuna")
sys.exit(1 if len(failed) == len(STEPS) else 0)
