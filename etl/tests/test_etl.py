"""Test dei parser e dei calcoli. Eseguire con: python -m pytest etl/tests"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from common import merge_keep_last_good  # noqa: E402
from fetch_ecb_fx import parse_csv  # noqa: E402
from fetch_worldbank import parse_response  # noqa: E402

WB_SAMPLE = [
    {"page": 1, "pages": 1, "per_page": 2000, "total": 3},
    [
        {"indicator": {"id": "SP.POP.TOTL", "value": "Population, total"}, "country": {"id": "IT", "value": "Italy"},
         "countryiso3code": "ITA", "date": "2024", "value": 58952704, "unit": "", "obs_status": "", "decimal": 0},
        {"indicator": {"id": "SP.POP.TOTL", "value": "Population, total"}, "country": {"id": "IT", "value": "Italy"},
         "countryiso3code": "ITA", "date": "2025", "value": None, "unit": "", "obs_status": "", "decimal": 0},
        {"indicator": {"id": "SP.POP.TOTL", "value": "Population, total"}, "country": {"id": "XX", "value": "Other"},
         "countryiso3code": "XXX", "date": "2024", "value": 1, "unit": "", "obs_status": "", "decimal": 0},
    ],
]


def test_worldbank_parse_skips_nulls_and_unknown_countries():
    rows = parse_response(WB_SAMPLE, {"ITA": "IT"}, "SP.POP.TOTL", "2026-01-01")
    assert len(rows) == 1
    assert rows[0]["country"] == "IT" and rows[0]["year"] == "2024"
    assert float(rows[0]["value"]) == 58952704


def test_worldbank_error_payload_raises():
    error = [{"message": [{"id": "120", "key": "Invalid value"}]}]
    try:
        parse_response(error, {"ITA": "IT"}, "BAD", "2026-01-01")
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def test_ecb_parse_inverts_rate():
    text = "KEY,FREQ,CURRENCY,TIME_PERIOD,OBS_VALUE\nEXR.A.USD.EUR.SP00.A,A,USD,2024,1.0824\n"
    rows = parse_csv(text, "USD", "2026-01-01")
    assert rows[0]["year"] == "2024"
    assert abs(float(rows[0]["eur_per_unit"]) - 1 / 1.0824) < 1e-6


def test_merge_keeps_failed_groups():
    existing = [{"g": "A", "k": "1"}, {"g": "B", "k": "1"}]
    fresh = [{"g": "A", "k": "2"}]
    merged = merge_keep_last_good(existing, fresh, ["g", "k"], {"A"}, "g")
    assert {"g": "B", "k": "1"} in merged      # B fallito: resta
    assert {"g": "A", "k": "1"} not in merged  # A aggiornato: sostituito
