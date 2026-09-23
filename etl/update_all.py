"""Esegue tutti i connettori automatici. Un errore in una fonte non ferma le altre."""
import runpy
import sys
import traceback

STEPS = ["fetch_worldbank.py", "fetch_ecb_fx.py", "fetch_regulators.py"]

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
