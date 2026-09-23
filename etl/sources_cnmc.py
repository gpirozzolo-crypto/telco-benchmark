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


def country_total(rows, field, filters, dims_all=None):
    fixed = set((filters or {}).keys())
    dims = [d for d in (dims_all or DIMENSIONS) if d not in fixed]
    # "pais" con un unico valore in tutte le righe (España) non scompone nulla: non va trattato come dimensione
    if len({str(r.get("pais")) for r in rows}) == 1:
        dims = [d for d in dims if d != "pais"]
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



def level_total(rows, field, fixed=(), dims=None):
    """Totale nazionale robusto a scomposizioni su più dimensioni.

    Raggruppa le righe per insieme di dimensioni valorizzate ("firma"). Ogni firma senza combinazioni duplicate
    è una possibile ricostruzione del totale. Si usa la firma più aggregata; se più firme dello stesso livello
    danno totali diversi di oltre l'1%, il dato è ambiguo e si scarta.
    """
    dims = [d for d in (dims or []) if d not in set(fixed)]
    if len({str(r.get("pais")) for r in rows}) == 1:
        dims = [d for d in dims if d != "pais"]
    groups = {}
    for r in rows:
        if _num(r.get(field)) is None:
            continue
        sig = tuple(d for d in dims if not _na(r.get(d)))
        groups.setdefault(sig, []).append(r)
    cands = []
    for sig, rs in groups.items():
        combos = [tuple(str(r.get(d)) for d in sig) for r in rs]
        if len(combos) != len(set(combos)) or (not sig and len(rs) != 1):
            continue
        cands.append((len(sig), sum(_num(r[field]) for r in rs), sig, rs))
    level_total.last_candidates = sorted((c[0], "+".join(c[2]) or "totale", round(c[1] / 1e6, 3), len(c[3])) for c in cands)
    if not cands:
        return None, [], None
    level = min(c[0] for c in cands)
    same = [c for c in cands if c[0] == level]
    values = [c[1] for c in same]
    if max(values) - min(values) > 0.01 * max(values):
        return None, [], None
    _, total, sig, rs = same[0]
    return total, rs, ("totale diretto" if not sig else "somma per " + " x ".join(sig))


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


# ---------- dataset mensile "Telecomunicaciones Mensual" ----------
MONTHLY_DIMS = ["segmento", "contrato", "tecnologia_de_acceso", "linea_ba_movil", "terminal_de_telefonia_movil",
                "tipo_de_ba_mayorista", "pais", "tipo_de_portabilidad"]
MONTHLY_RULES = [
    ("mobile_subs", "Telefonía móvil", "Líneas", None),
    ("fbb_subs", "Banda ancha fija minorista", "Líneas", None),
    ("ftth_subs", "Banda ancha fija minorista", "Líneas", {"tecnologia_de_acceso": "FTTH"}),
]
MONTHS_ES = {m: i + 1 for i, m in enumerate(["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
                                               "septiembre", "octubre", "noviembre", "diciembre"])}
KEEP_MONTHS = 60


def month(s):
    """Accetta 2025-07, 202507, 2025-07-01, 07/2025, 2025M07, julio 2025. Restituisce ('2025-07', fine mese)."""
    t = str(s or "").strip().lower()
    y = m = None
    for pat, yi, mi in [(r"(20\d{2})[-/m]?(\d{1,2})(?:[-/]\d{1,2})?(?:t.*)?", 1, 2), (r"(\d{1,2})[-/](20\d{2})", 2, 1)]:
        g = re.fullmatch(pat, t)
        if g:
            y, m = int(g.group(yi)), int(g.group(mi))
            break
    if y is None:
        g = re.fullmatch(r"([a-záéíóú]+)\s+(?:de\s+)?(20\d{2})", t)
        if g and g.group(1) in MONTHS_ES:
            y, m = int(g.group(2)), MONTHS_ES[g.group(1)]
    if not y or not 1 <= m <= 12:
        return None
    end = date(y + (m == 12), m % 12 + 1, 1).toordinal() - 1
    return f"{y}-{m:02d}", date.fromordinal(end)


def extract_monthly(records, retrieved, url):
    out, log = [], []
    periods = sorted({month(r.get("mes")) for r in records if month(r.get("mes"))}, key=lambda p: p[1])[-KEEP_MONTHS:]
    by_period = {p: [] for p in periods}
    for r in records:
        p = month(r.get("mes"))
        if p in by_period:
            by_period[p].append(r)

    def row(kpi, label, end, value, method):
        return {"country": "ES", "kpi": kpi, "value": f"{value:.6g}", "period": label, "period_end": end.isoformat(),
                "frequency": "mensile", "source_id": "cnmc_api", "source_url": url, "retrieved": retrieved, "method": method}

    for (label, end), rows in by_period.items():
        for kpi, service, concept, filters in MONTHLY_RULES:
            sel = [r for r in rows if _matches(r, service, concept, filters)]
            value, used, method = level_total(sel, "lineas", (filters or {}).keys(), MONTHLY_DIMS)
            if value is None:
                # nessun totale nazionale pubblicato: somma degli operatori (serve che ce ne siano almeno 4)
                op_sel = [r for r in rows if _matches(r, service, concept, filters, national=False) and not _na(r.get("operador"))]
                parts = {}
                for op in sorted({str(r["operador"]) for r in op_sel}):
                    v, u, _ = level_total([r for r in op_sel if str(r["operador"]) == op], "lineas", (filters or {}).keys(), MONTHLY_DIMS)
                    if v is not None:
                        parts[op] = (v, u)
                if len(parts) >= 4:
                    value = sum(v for v, _ in parts.values())
                    used = next(iter(parts.values()))[1]
                    method = "somma degli operatori " + ", ".join(parts)
                elif (label, end) == periods[-1]:
                    log.append(f"CNMC mensile {kpi} {label}: somma operatori non possibile, operatori ricostruiti {sorted(parts)}")
                    for r in op_sel[:6]:
                        log.append("CNMC esempio " + str({k: v for k, v in r.items() if not _na(v) and k != "_id"}))
            if (label, end) == periods[-1]:
                log.append(f"CNMC mensile {kpi} {label}: {'ok ' + method if value is not None else 'nessun totale univoco'}; "
                           f"ricostruzioni (livello, dimensioni, milioni, righe) {level_total.last_candidates[:8]}")
                if value is None:
                    for r in sel[:6]:
                        log.append("CNMC esempio " + str({k: v for k, v in r.items() if not _na(v) and k != "_id"}))
            if value is None:
                continue
            f = scale(used[0].get("unidades"), "count")
            if f is None:
                log.append(f"CNMC mensile {kpi} {label}: unità non riconosciuta '{used[0].get('unidades')}'")
                continue
            out.append(row(kpi, label, end, value * f, f"CNMC dati mensili, {method}"))
    # quote di mercato mobile per operatore: ultimi 13 mesi
    for (label, end), rows in list(by_period.items())[-13:]:
        sel = [r for r in rows if _matches(r, "Telefonía móvil", "Líneas", None, national=False) and not _na(r.get("operador"))]
        totals = {}
        for op in sorted({str(r["operador"]) for r in sel}):
            v, _, _ = level_total([r for r in sel if str(r["operador"]) == op], "lineas", (), MONTHLY_DIMS)
            if v is not None:
                totals[op] = v
        total = sum(totals.values())
        if len(totals) >= 3 and total > 0:
            for op, v in totals.items():
                out.append(row(f"share|{op}", label, end, v / total * 100, "CNMC dati mensili, linee mobili per operatore"))
    if not any(r["kpi"] == "mobile_subs" for r in out) and periods:
        last = periods[-1]
        sample = [r for r in by_period[last] if r.get("servicio") == "Telefonía móvil" and r.get("concepto") == "Líneas"][:10]
        for r in sample:
            log.append("CNMC esempio " + str({k: v for k, v in r.items() if not _na(v) and k != "_id"}))
    return out, log



def extract_general(records, retrieved, url):
    """Dataset 'Datos Generales': ricavi (milioni di euro) per tipo di ricavo. Ricavi mobili = somma 4 trimestri."""
    out, log = [], []
    rev = [r for r in records if r.get("servicio") == "Datos generales" and r.get("concepto") == "Ingresos" and _na(r.get("operador"))]
    labels = sorted({str(r.get("tipo_de_ingreso")) for r in rev})
    markets = sorted({str(r.get("tipo_de_mercado")) for r in rev})
    log.append(f"CNMC ricavi: tipi di ricavo {labels[:25]}; mercati {markets[:10]}")
    wanted = ["Telefonía móvil", "Banda Ancha móvil"]
    missing = [w for w in wanted if w not in labels]
    if missing:
        log.append(f"CNMC ricavi: etichette {missing} assenti, nessun valore prodotto")
        return out, log
    parts = {}
    for r in rev:
        if r.get("tipo_de_ingreso") not in wanted or r.get("tipo_de_mercado") != "Servicio minorista":
            continue
        q = quarter(r.get("trimestre"))
        v = _num(r.get("ingresos"))
        f = scale(r.get("unidades"), "money")
        if q and v is not None and f is not None:
            parts.setdefault(q, {}).setdefault(r["tipo_de_ingreso"], []).append(v * f)
    # un solo valore per etichetta e trimestre, altrimenti ambiguo
    per_q = {q: [sum(v[0] for v in d.values())] for q, d in parts.items() if set(d) == set(wanted) and all(len(v) == 1 for v in d.values())}
    mobile = [" + ".join(wanted)]
    clean = {q: v[0] for q, v in per_q.items() if len(v) == 1}
    qs = sorted(clean, key=lambda q: q[1])
    for i in range(3, len(qs)):
        w = qs[i - 3:i + 1]
        if _consecutive([x[0] for x in w]):
            out.append({"country": "ES", "kpi": "mobile_rev", "value": f"{sum(clean[x] for x in w):.6g}", "period": f"12 mesi a {w[-1][0]}",
                        "period_end": w[-1][1].isoformat(), "frequency": "annuale mobile", "source_id": "cnmc_api", "source_url": url,
                        "retrieved": retrieved, "method": f"CNMC Datos generales, '{mobile[0]}', somma di 4 trimestri"})
    return out, log


PACKAGE_SEARCH = "https://catalogodatos.cnmc.es/api/3/action/package_search"


def find_resource(session, log, prefer):
    """Cerca la risorsa più recente di un dataset telecom CNMC il cui titolo contiene `prefer`.

    La CNMC pubblica nuove versioni come nuove risorse, quindi l'identificativo non va fissato nel codice.
    """
    try:
        r = session.get(PACKAGE_SEARCH, params={"q": f"telecomunicaciones {prefer}", "rows": 50}, timeout=60)
        r.raise_for_status()
        best = None
        for pkg in r.json()["result"]["results"]:
            title = (pkg.get("title") or "").lower()
            if "telecomunicaciones" not in title or prefer not in title or "geogr" in title:
                continue
            for res in pkg.get("resources", []):
                if not res.get("datastore_active"):
                    continue
                stamp = res.get("last_modified") or res.get("created") or ""
                if best is None or stamp > best[0]:
                    best = (stamp, res["id"], pkg.get("title"), pkg.get("name"))
        if best:
            log.append(f"CNMC: per '{prefer}' uso la risorsa {best[1]} ('{best[2]}', aggiornata {best[0][:10]})")
            return best[1], f"https://catalogodatos.cnmc.es/dataset/{best[3]}"
        log.append(f"CNMC: nessun dataset '{prefer}' trovato")
    except Exception as exc:  # noqa: BLE001
        log.append(f"CNMC: ricerca '{prefer}' fallita ({exc})")
    return None, None


def describe(records):
    """Riassunto della struttura dei record, scritto nel log quando l'estrazione non produce nulla."""
    from collections import Counter
    if not records:
        return ["CNMC diagnosi: l'API ha restituito 0 record"]
    keys = sorted({k for r in records[:200] for k in r})
    trims = sorted({str(r.get("trimestre") or r.get("mes")) for r in records})
    pairs = Counter((r.get("servicio"), r.get("concepto")) for r in records).most_common(12)
    ops = Counter(str(r.get("operador")) for r in records).most_common(6)
    units = Counter(str(r.get("unidades")) for r in records).most_common(8)
    return [f"CNMC diagnosi: {len(records)} record; campi: {', '.join(keys)}",
            f"CNMC diagnosi: trimestri {trims[:3]} ... {trims[-3:]}",
            f"CNMC diagnosi: servizio/concetto più frequenti {pairs}",
            f"CNMC diagnosi: operatori più frequenti {ops}",
            f"CNMC diagnosi: unità {units}"]


def run(session, retrieved):
    """Mensile: linee mobili, fisse, FTTH, quote. Trimestrale: ricavi e traffico (se il dataset si trova)."""
    log, rows = [], []
    res, url = find_resource(session, log, "mensual")
    if res:
        records = fetch_records(session, res)
        got, l = extract_monthly(records, retrieved, url)
        log += l
        if not got:
            log += describe(records)
        rows += got
    res, url = find_resource(session, log, "trimestral")
    if res:
        try:
            records = fetch_records(session, res)
            got, l = extract_general(records, retrieved, url)
            log += l
            rows += got
        except Exception as exc:  # noqa: BLE001
            log.append(f"CNMC trimestrale: errore {exc}")
    return rows, log


def fetch_records(session, resource=MARKETS_RESOURCE):
    out, offset = [], 0
    while True:
        r = session.get(DATASTORE, params={"resource_id": resource, "limit": 5000, "offset": offset}, timeout=120)
        r.raise_for_status()
        result = r.json()["result"]
        batch = result.get("records", [])
        out.extend(batch)
        if len(out) >= result.get("total", 0) or not batch:
            return out
        offset += len(batch)
