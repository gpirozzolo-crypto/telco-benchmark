"""Test dei connettori CNMC/Arcep e dei controlli della build, con dati di esempio."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import sources_arcep  # noqa: E402
import sources_cnmc  # noqa: E402


def rec(trim, servicio, concepto, field, value, unidades, **dims):
    r = {"trimestre": trim, "servicio": servicio, "concepto": concepto, "operador": None, "unidades": unidades, field: value}
    r.update(dims)
    return r


def cnmc_sample():
    rows = []
    for i, t in enumerate(["2025T1", "2025T2", "2025T3", "2025T4"]):
        rows.append(rec(t, "Telefonía móvil", "Líneas", "lineas_o_accesos", 63_000_000 + i * 400_000, "Líneas"))
        rows.append(rec(t, "Telefonía móvil", "Ingresos", "ingresos", 1_950 + i * 10, "Millones de euros"))
        rows.append(rec(t, "Banda Ancha móvil", "Tráfico - datos", "trafico_de_datos", 2_700_000 + i * 50_000, "TB"))
        # linee fisse divise per segmento: il totale va ricostruito sommando
        rows.append(rec(t, "Banda ancha fija minorista", "Líneas", "lineas_o_accesos", 17_000_000, "Líneas", segmento="Residencial"))
        rows.append(rec(t, "Banda ancha fija minorista", "Líneas", "lineas_o_accesos", 2_900_000, "Líneas", segmento="Negocios"))
        # FTTH solo per operatore: fallback operatore+segmento
        for op, v in [("A", 9_000_000), ("B", 8_000_000)]:
            rows.append(rec(t, "Banda ancha fija minorista", "Líneas", "lineas_o_accesos", v, "Líneas",
                            tecnologia_de_acceso="FTTH", operador=op, segmento="Residencial"))
    return rows


def test_cnmc_extract_totals_ttm_and_traffic():
    rows, log = sources_cnmc.extract(cnmc_sample(), "2026-01-01")
    by = {(r["kpi"], r["period"]): float(r["value"]) for r in rows}
    assert abs(by[("mobile_subs", "2025-Q4")] - 64.2) < 1e-6
    assert abs(by[("fbb_subs", "2025-Q4")] - 19.9) < 1e-6
    assert abs(by[("ftth_subs", "2025-Q4")] - 17.0) < 1e-6
    assert abs(by[("mobile_rev", "12 mesi a 2025-Q4")] - 7.86) < 1e-6   # 1950+1960+1970+1980 mln
    assert ("mobile_rev", "12 mesi a 2025-Q3") not in by                 # servono 4 trimestri
    gb = 2_850_000 * 1e3 / 64.2e6 / 3
    assert abs(by[("mobile_data", "2025-Q4")] - gb) < 1e-3
    assert not log


def test_cnmc_unknown_unit_is_rejected():
    rows = [rec("2025T4", "Telefonía móvil", "Líneas", "lineas_o_accesos", 5, "Paquetes raros")]
    out, log = sources_cnmc.extract(rows, "2026-01-01")
    assert not out and "unità non riconosciuta" in log[0]


def test_cnmc_ambiguous_total_is_rejected():
    rows = [rec("2025T4", "Telefonía móvil", "Líneas", "lineas_o_accesos", 5, "Líneas", segmento="X"),
            rec("2025T4", "Telefonía móvil", "Líneas", "lineas_o_accesos", 6, "Líneas", segmento="X")]
    out, _ = sources_cnmc.extract(rows, "2026-01-01")
    assert not out


def test_arcep_workbook_parsing():
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Open Data"
    ws.append(["Observatoire"])
    ws.append(["label", "unit", "libellé", "unité", "Q3 2025", "Q4 2025"])
    ws.append(["Total number of SIM cards (MtoM cards excluded)", "million units", "", "", 84.5, 84.9])
    ws.append(["of which fiber to the home (FTTH)", "million", "", "", 25.1, 25.8])
    ws.append(["Something else", "million", "", "", 1, 2])
    rows, log = sources_arcep.extract_workbook(wb, "trimestrale", "http://x", "2026-01-01")
    by = {(r["kpi"], r["period"]): float(r["value"]) for r in rows}
    assert by[("mobile_subs", "2025-Q4")] == 84.9
    assert by[("ftth_subs", "2025-Q3")] == 25.1
    assert len(rows) == 4 and not log


def test_arcep_capex_millions_to_billions():
    assert sources_arcep.scale("€ million", "money") == 1e-3
    assert sources_arcep.scale("strange", "money") is None


def test_cnmc_describe_reports_structure():
    lines = sources_cnmc.describe([{"trimestre": "2025T4", "servicio": "X", "concepto": "Y", "operador": "Z", "unidades": "U"}])
    assert "1 record" in lines[0] and "2025T4" in lines[1]
    assert sources_cnmc.describe([])[0].endswith("0 record")
