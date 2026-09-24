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


def monthly_sample():
    rows = []
    for mes in ["2026-06", "2026-07"]:
        base = dict(mes=mes, pais="España", unidades="Unidades")
        rows.append(dict(base, servicio="Telefonía móvil", concepto="Líneas", operador="N/A", lineas=64_800_000))
        for op, v in [("Orange", 26_700_000), ("Movistar", 17_000_000), ("Vodafone", 12_000_000), ("DIGI", 7_600_000), ("OMV", 1_500_000)]:
            # ogni operatore diviso per segmento: il totale va ricostruito
            rows.append(dict(base, servicio="Telefonía móvil", concepto="Líneas", operador=op, segmento="Residencial", lineas=v * 0.8))
            rows.append(dict(base, servicio="Telefonía móvil", concepto="Líneas", operador=op, segmento="Negocios", lineas=v * 0.2))
        rows.append(dict(base, servicio="Banda ancha fija minorista", concepto="Líneas", operador="N/A", lineas=20_100_000))
        rows.append(dict(base, servicio="Banda ancha fija minorista", concepto="Líneas", operador="N/A", tecnologia_de_acceso="FTTH", lineas=18_300_000))
        rows.append(dict(base, servicio="Telefonía móvil", concepto="Portabilidades", operador="N/A", lineas=500_000))
    return rows


def test_cnmc_monthly_lines_ftth_and_shares():
    rows, log = sources_cnmc.extract_monthly(monthly_sample(), "2026-09-23", "http://x")
    by = {(r["kpi"], r["period"]): float(r["value"]) for r in rows}
    assert abs(by[("mobile_subs", "2026-07")] - 64.8) < 1e-6
    assert abs(by[("fbb_subs", "2026-07")] - 20.1) < 1e-6        # la riga FTTH non deve sommarsi al totale
    assert abs(by[("ftth_subs", "2026-07")] - 18.3) < 1e-6
    shares = {k.split("|")[1]: v for (k, p), v in by.items() if k.startswith("share|") and p == "2026-07"}
    assert abs(sum(shares.values()) - 100) < 1e-3
    assert abs(shares["Orange"] - 26.7 / 64.8 * 100) < 1e-3
    assert rows[0]["period_end"] in ("2026-06-30", "2026-07-31")
    assert not any("nessun totale" in l or "non riconosciuta" in l for l in log)


def test_month_parser_formats():
    for s in ["2026-07", "202607", "2026-07-01", "07/2026", "2026M07", "julio 2026", "2026-07-01T00:00:00"]:
        assert sources_cnmc.month(s)[0] == "2026-07", s
    assert sources_cnmc.month("2026-13") is None


def test_level_total_multi_dimension_split():
    base = dict(servicio="Telefonía móvil", concepto="Líneas", operador="N/A", pais="España", unidades="Unidades")
    rows = []
    for seg in ["Residencial", "Negocios"]:
        for con in ["Pospago", "Prepago"]:
            rows.append(dict(base, segmento=seg, contrato=con, lineas=10))
    v, _, m = sources_cnmc.level_total(rows, "lineas", (), sources_cnmc.MONTHLY_DIMS)
    assert v == 40 and "segmento" in m and "contrato" in m
    # subtotali per segmento coerenti con il dettaglio: vince il livello più aggregato
    rows += [dict(base, segmento="Residencial", lineas=20), dict(base, segmento="Negocios", lineas=20)]
    v, _, m = sources_cnmc.level_total(rows, "lineas", (), sources_cnmc.MONTHLY_DIMS)
    assert v == 40 and m == "somma per segmento"
    # due ricostruzioni allo stesso livello in disaccordo: scartato
    rows += [dict(base, contrato="Pospago", lineas=30), dict(base, contrato="Prepago", lineas=30)]
    assert sources_cnmc.level_total(rows, "lineas", (), sources_cnmc.MONTHLY_DIMS)[0] is None


def test_general_mobile_revenue_ttm():
    recs = []
    for i, t in enumerate(["2025T1", "2025T2", "2025T3", "2025T4"]):
        base = dict(servicio="Datos generales", concepto="Ingresos", operador="N/A", trimestre=t, unidades="Millones de euros")
        recs.append(dict(base, tipo_de_ingreso="Telefonía móvil", tipo_de_mercado="Servicio minorista", ingresos=1500))
        recs.append(dict(base, tipo_de_ingreso="Banda Ancha móvil", tipo_de_mercado="Servicio minorista", ingresos=470))
        recs.append(dict(base, tipo_de_ingreso="Telefonía móvil", tipo_de_mercado="Servicio mayorista", ingresos=300))
        recs.append(dict(base, tipo_de_ingreso="Telefonía fija", ingresos=500))
    out, log = sources_cnmc.extract_general(recs, "2026-09-23", "http://x")
    out = [r for r in out if r["kpi"] == "mobile_rev"]
    assert len(out) == 1 and abs(float(out[0]["value"]) - 7.88) < 1e-6


def test_cnmc_monthly_fbb_from_operator_sum():
    rows = []
    base = dict(mes="2026-07", pais="España", unidades="Unidades", servicio="Banda ancha fija minorista", concepto="Líneas")
    for op, v in [("Movistar", 6e6), ("MASORANGE", 7e6), ("Vodafone", 3e6), ("DIGI", 2.5e6), ("Resto", 1.6e6)]:
        rows.append(dict(base, operador=op, tecnologia_de_acceso="FTTH", lineas=v * 0.9))
        rows.append(dict(base, operador=op, tecnologia_de_acceso="Otros", lineas=v * 0.1))
    out, log = sources_cnmc.extract_monthly(rows, "2026-09-23", "http://x")
    by = {r["kpi"]: float(r["value"]) for r in out}
    assert abs(by["fbb_subs"] - 20.1) < 1e-6
    assert abs(by["ftth_subs"] - 18.09) < 1e-6


def test_general_wholesale_ttm():
    recs = []
    for t in ["2025T1", "2025T2", "2025T3", "2025T4"]:
        base = dict(servicio="Datos generales", concepto="Ingresos", operador="N/A", trimestre=t, unidades="Millones de euros")
        recs.append(dict(base, tipo_de_ingreso="Telefonía móvil", tipo_de_mercado="Servicio minorista", ingresos=1500))
        recs.append(dict(base, tipo_de_ingreso="Banda Ancha móvil", tipo_de_mercado="Servicio minorista", ingresos=470))
        recs.append(dict(base, tipo_de_ingreso="Interconexión móvil", tipo_de_mercado="Servicio mayorista", ingresos=300))
        recs.append(dict(base, tipo_de_ingreso="Acceso fijo", tipo_de_mercado="Servicio mayorista", ingresos=450))
    out, _ = sources_cnmc.extract_general(recs, "2026-09-24", "http://x")
    w = [r for r in out if r["kpi"] == "rev_wholesale"]
    assert len(w) == 1 and abs(float(w[0]["value"]) - 3.0) < 1e-9
