"""Connettore CNMC (Spagna): dati trimestrali via API CKAN pubblica.

Logica di aggregazione derivata dal connettore del progetto global-telco-intelligence
(che ha girato con successo), riscritta per produrre righe CSV invece di scrivere su Supabase.
Principio: se un totale nazionale non è ricostruibile in modo univoco, il valore viene scartato.
"""
import re
from datetime import date

DATASTORE = "https://catalogodatos.cnmc.es/api/3/action/datastore_search"
MARKETS_RESOURCE = "8ea25e53-b955-4a42-bca4-0a7183237844"
MARKETS_URL = "https://data.cnmc.es/telecomunicaciones-y-sector-audiovisual/datos-trimestrales/datos-de-mercados/telecomunicaciones-3"

# kpi, servizio, concetto, campo valore, filtri
RULES = [
    ("mobile_subs", "Telefonía móvil", "Líneas", "lineas_o_accesos", None),
    ("fbb_subs", "Banda ancha fija minorista", "Líneas", "lineas_o_accesos", None),
    ("ftth_subs", "Banda ancha fija minorista", "Líneas", "lineas_o_accesos", {"tecnologia_de_acceso": "FTTH"}),
    ("_mobile_rev_q", "Telefonía móvil", "Ingresos", "ingresos", None),
    ("_mobile_traffic_q", "Banda Ancha móvil", "Tráfico - datos", "trafico_de_datos", None),
]

DIMENSIONS = ["tipo_de_mercado", "tipo_de_cliente", "segmento", "tipo_de_trafico", "tipo_de_contrato", "tipo_de_linea",
              "tipo_de_mensaje", "tipo_de_trafico_de_mensaje", "tecnologia_de_acceso", "velocidad_baf", "tipo_de_oferta",
              "tipo_de_tarifa", "tipo_de_ce_minorista", "tipo_de_circuito", "tipo_de_emision", "tipo_de_operador", "tipo_de_medio",
              "tipo_de_publicidad", "tipo_de_contratacion", "tipo_servicio_audiovisual_mayorista", "tipo_de_ba_may",
              "tipo_de_interconexion", "tipo_de_tarificacion_en_interconexion", "tipo_de_ambito", "tipo_de_acceso_de_infraestructuras",
              "tipo_de_ingreso", "tipo_de_paquete"]


def _na(v):
    return v in (None, "", "N/A")


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def quarter(s):
    """'2025T4' -> ('2025-Q4', date(2025,12,31))."""
    m = re.fullmatch(r"(20\d{2})T([1-4])", str(s or ""))
    if not m:
        return None
    y, q = int(m.group(1)), int(m.group(2))
    return f"{y}-Q{q}", date(y, q * 3, (31, 30, 30, 31)[q - 1])


def scale(unit, kind):
    """Fattore per portare il valore nell'unità del benchmark. None = unità non riconosciuta (si scarta)."""
    u = (unit or "").strip().lower()
    if kind == "count":      # -> milioni
        if u.startswith("miles"):
            return 1e-3
        if u.startswith("millones"):
            return 1.0
        if u in ("", "líneas", "lineas", "número", "numero", "accesos", "unidades", "clientes"):
            return 1e-6
    if kind == "money":      # -> miliardi di euro
        if "millones de euros" in u:
            return 1e-3
        if "miles de euros" in u:
            return 1e-6
        if u in ("euros", "€", "eur"):
            return 1e-9
    if kind == "traffic":    # -> GB
        table = {"gb": 1, "gigabytes": 1, "tb": 1e3, "terabytes": 1e3, "pb": 1e6, "petabytes": 1e6,
                 "mb": 1e-3, "megabytes": 1e-3, "miles de gb": 1e3, "millones de gb": 1e6}
        return table.get(u)
    return None


def _matches(r, service, concept, filters, national=True):
    if r.get("servicio") != service or r.get("concepto") != concept:
        return False
    if national and not _na(r.get("operador")):
        return False
    return all(r.get(k) == v for k, v in (filters or {}).items())


def country_total(rows, field, filters):
    fixed = set((filters or {}).keys())
    dims = [d for d in DIMENSIONS if d not in fixed]
    direct = [r for r in rows if all(_na(r.get(d)) for d in dims) and _num(r.get(field)) is not None]
    if len(direct) == 1:
        return _num(direct[0][field]), direct, "totale diretto"
    relaxed = [r for r in rows if all(_na(r.get(d)) for d in dims if d != "tipo_de_mercado") and _num(r.get(field)) is not None]
    if len(relaxed) == 1:
        return _num(relaxed[0][field]), relaxed, "totale di mercato unico"
    for dim in dims:
        cand = [r for r in rows if not _na(r.get(dim)) and all(_na(r.get(d)) for d in dims if d != dim) and _num(r.get(field)) is not None]
        labels = [str(r.get(dim)) for r in cand]
        if len(cand) >= 2 and len(labels) == len(set(labels)):
            return sum(_num(r[field]) for r in cand), cand, f"somma per {dim}"
    return None, [], None


def operator_total(rows, field):
    """Fallback FTTH: somma operatore+segmento, solo se non ci sono coppie duplicate."""
    named = [(r, _num(r.get(field))) for r in rows if not _na(r.get("operador")) and _num(r.get(field)) is not None]
    keys = [(str(r.get("operador")), str(r.get("segmento"))) for r, _ in named]
    if named and all(not _na(r.get("segmento")) for r, _ in named) and len(keys) == len(set(keys)):
        return sum(v for _, v in named), [r for r, _ in named], "somma operatori per segmento"
    return None, [], None


def extract(records, retrieved):
    """Dai record CNMC alle righe del benchmark. Restituisce (righe, log)."""
    out, log = [], []
    periods = sorted({quarter(r.get("trimestre")) for r in records if quarter(r.get("trimestre"))}, key=lambda p: p[1])
    raw = {}  # (kpi, period_label) -> (value_in_target_unit, end, method)
    for kpi, service, concept, field, filters in RULES:
        kind = "money" if field == "ingresos" else "traffic" if field == "trafico_de_datos" else "count"
        for label, end in periods:
            rows = [r for r in records if quarter(r.get("trimestre")) == (label, end) and _matches(r, service, concept, filters)]
            value, used, method = country_total(rows, field, filters)
            if value is None and kpi == "ftth_subs":
                op_rows = [r for r in records if quarter(r.get("trimestre")) == (label, end) and _matches(r, service, concept, filters, national=False)]
                value, used, method = operator_total(op_rows, field)
            if value is None:
                continue
            f = scale(used[0].get("unidades"), kind)
            if f is None:
                log.append(f"CNMC {kpi} {label}: unità non riconosciuta '{used[0].get('unidades')}', valore scartato")
                continue
            raw[(kpi, label)] = (value * f, end, method)

    def row(kpi, label, end, value, method, freq="trimestrale"):
        return {"country": "ES", "kpi": kpi, "value": f"{value:.6g}", "period": label, "period_end": end.isoformat(),
                "frequency": freq, "source_id": "cnmc_api", "source_url": MARKETS_URL, "retrieved": retrieved, "method": method}

    for (kpi, label), (v, end, method) in raw.items():
        if not kpi.startswith("_"):
            out.append(row(kpi, label, end, v, f"CNMC dati trimestrali, {method}"))
    # ricavi mobili: somma degli ultimi 4 trimestri consecutivi (anno mobile)
    q_labels = [p[0] for p in periods]
    for i in range(3, len(q_labels)):
        window = q_labels[i - 3:i + 1]
        if all(("_mobile_rev_q", w) in raw for w in window) and _consecutive(window):
            total = sum(raw[("_mobile_rev_q", w)][0] for w in window)
            end = raw[("_mobile_rev_q", window[-1])][1]
            out.append(row("mobile_rev", f"12 mesi a {window[-1]}", end, total, "CNMC, somma di 4 trimestri di ricavi telefonia mobile", "annuale mobile"))
    # traffico dati per linea al mese
    for label in q_labels:
        t, s = raw.get(("_mobile_traffic_q", label)), raw.get(("mobile_subs", label))
        if t and s and s[0] > 0:
            out.append(row("mobile_data", label, t[1], t[0] / (s[0] * 1e6) / 3, "CNMC, traffico dati trimestrale / linee mobili / 3"))
    return out, log


def _consecutive(labels):
    idx = [int(l[:4]) * 4 + int(l[-1]) for l in labels]
    return all(b - a == 1 for a, b in zip(idx, idx[1:]))


def fetch_records(session):
    out, offset = [], 0
    while True:
        r = session.get(DATASTORE, params={"resource_id": MARKETS_RESOURCE, "limit": 1000, "offset": offset}, timeout=90)
        r.raise_for_status()
        result = r.json()["result"]
        batch = result.get("records", [])
        out.extend(batch)
        if len(out) >= result.get("total", 0) or not batch:
            return out
        offset += len(batch)
