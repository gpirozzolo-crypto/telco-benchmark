"""Unisce dati automatici e curati, calcola i KPI derivati e genera il sito statico.

Uso: python etl/build.py
Output:
  dist/index.html     sito completo, un solo file con i dati incorporati
  dist/data.json      stessi dati in JSON (per riuso in altri strumenti)
  dist/data.csv       tabella lunga di tutti i valori
  dist/stale.json     fonti curate da aggiornare (usato dal workflow per aprire una issue)
"""
import csv
import datetime
import io
import json
import re
import sys
from collections import defaultdict

from common import DATA, ROOT, load_json, read_csv

DIST = ROOT / "dist"
TEMPLATE = ROOT / "site" / "template.html"
PLACEHOLDER = "/*__DATA__*/null"


def to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class Build:
    def __init__(self):
        self.countries = load_json("countries.json")["countries"]
        cat = load_json("kpis.json")
        self.groups = cat["groups"]
        self.kpis = cat["kpis"]
        self.definitions = cat.get("definitions", {})
        self.kpi_by_id = {k["id"]: k for k in self.kpis}
        self.sources = {s["id"]: s for s in load_json("sources.json")["sources"]}
        self.values = defaultdict(dict)   # country -> kpi -> record
        self.series = defaultdict(dict)   # country -> kpi -> [[year, value]]
        self.shares = {}
        self.fx = defaultdict(dict)       # currency -> year -> eur_per_unit
        self.warnings = []
        self.anomalies = []
        self.tolerance = cat.get("auto_tolerance_pct", 35) / 100

    # ---------- caricamento ----------
    def load_fx(self):
        for r in read_csv(DATA / "auto" / "ecb_fx.csv"):
            self.fx[r["currency"]][int(r["year"])] = (float(r["eur_per_unit"]), r)

    def eur_rate(self, currency, year):
        """EUR per unità di valuta nell'anno richiesto, o nell'anno più vicino disponibile."""
        if currency in ("EUR", "", None):
            return 1.0, None
        if currency == "SAR":  # riyal agganciato al dollaro: 3,75 SAR = 1 USD
            usd, note = self.eur_rate("USD", year)
            return usd / 3.75, (note or "").replace("USD", "SAR (via USD, cambio fisso 3,75)")
        table = self.fx.get(currency)
        if not table:
            raise ValueError(f"Cambio {currency} mancante: esegui etl/fetch_ecb_fx.py")
        best = min(table, key=lambda y: (abs(y - year), -y))
        rate, row = table[best]
        note = f"convertito da {currency} al cambio BCE {best} ({rate:.4f} €)"
        if "seed" in row.get("via", ""):
            note += ", cambio provvisorio"
        return rate, note

    def load_worldbank(self):
        rows = read_csv(DATA / "auto" / "worldbank.csv")
        by_ind = defaultdict(lambda: defaultdict(list))
        meta = {}
        for r in rows:
            v = to_float(r["value"])
            if v is None:
                continue
            by_ind[r["indicator"]][r["country"]].append((int(r["year"]), v))
            meta[(r["indicator"], r["country"])] = r
        self.wb = by_ind
        self.wb_meta = meta
        for k in self.kpis:
            if not k.get("wb"):
                continue
            scale = k.get("scale", 1)
            for cid, pts in by_ind.get(k["wb"], {}).items():
                pts.sort()
                self.series[cid][k["id"]] = [[y, round(v * scale, 6)] for y, v in pts]
                y, v = pts[-1]
                m = meta[(k["wb"], cid)]
                self.values[cid][k["id"]] = {
                    "value": v * scale, "period": str(y), "year": y, "source_id": "worldbank",
                    "source_url": f"https://data.worldbank.org/indicator/{k['wb']}?locations={cid}",
                    "retrieved": m["retrieved"], "confidence": "ufficiale",
                    "note": f"Indicatore World Bank {k['wb']}" + (" (seed da mirror, in attesa del primo aggiornamento)" if "seed" in m.get("via", "") else ""),
                }

    def load_curated(self):
        for r in read_csv(DATA / "curated" / "kpi_values.csv"):
            kpi = self.kpi_by_id.get(r["kpi"])
            if kpi is None:
                self.warnings.append(f"KPI sconosciuto nel CSV: {r['kpi']}")
                continue
            v = to_float(r["value"])
            if v is None:
                continue
            note = r["note"]
            if kpi.get("money") and r.get("currency") not in ("", "EUR"):
                rate, fx_note = self.eur_rate(r["currency"], int(r["year"]))
                note = f"{note}. Valore originale {v:g} {r['currency']}, {fx_note}"
                v = v * rate
            self.values[r["country"]][r["kpi"]] = {
                "value": v, "period": r["period"], "year": int(r["year"]), "source_id": r["source_id"],
                "source_url": r["source_url"], "retrieved": r["retrieved"], "confidence": r["confidence"], "note": note,
            }

    def load_shares(self):
        grouped = defaultdict(list)
        for r in read_csv(DATA / "curated" / "market_shares.csv"):
            grouped[r["country"]].append(r)
        for cid, rows in grouped.items():
            items = sorted(({"operator": r["operator"], "share": float(r["share"])} for r in rows), key=lambda x: -x["share"])
            first = rows[0]
            self.shares[cid] = {
                "basis": first["basis"], "period": first["period"], "source_id": first["source_id"],
                "source_url": first["source_url"], "retrieved": first["retrieved"], "confidence": first["confidence"],
                "items": items, "covered": round(sum(i["share"] for i in items), 1),
            }

    @staticmethod
    def period_end(rec):
        """Data di fine del periodo di un valore curato, per confrontarlo con quelli automatici."""
        p = str(rec.get("period", ""))
        m = re.search(r"Q([1-4])\s*(20\d{2})|(20\d{2})\s*-?\s*Q([1-4])", p)
        if m:
            q, y = (m.group(1), m.group(2)) if m.group(1) else (m.group(4), m.group(3))
            q, y = int(q), int(y)
            return datetime.date(y, q * 3, (31, 30, 30, 31)[q - 1])
        m = re.fullmatch(r"(20\d{2})-(\d{2})", p)
        if m:
            y, mo = int(m.group(1)), int(m.group(2))
            nxt = datetime.date(y + (mo == 12), mo % 12 + 1, 1)
            return nxt - datetime.timedelta(days=1)
        return datetime.date(int(rec["year"]), 12, 31)

    def load_regulators(self):
        """Dati trimestrali CNMC/Arcep: sostituiscono il valore curato solo se più recenti e plausibili."""
        rows = read_csv(DATA / "auto" / "regulators.csv")
        self.reg_rows = rows
        grouped = defaultdict(list)
        for r in rows:
            if r["kpi"] in self.kpi_by_id and to_float(r["value"]) is not None:
                grouped[(r["country"], r["kpi"])].append(r)
        for (cid, kid), pts in grouped.items():
            pts.sort(key=lambda r: r["period_end"])
            kpi = self.kpi_by_id[kid]
            lo, hi = kpi.get("range", [float("-inf"), float("inf")])
            good = [r for r in pts if lo <= float(r["value"]) <= hi]
            for r in pts:
                if r not in good:
                    self.anomalies.append(f"{cid} {kpi['label']} {r['period']}: {float(r['value']):g} fuori dall'intervallo plausibile {lo}-{hi}, scartato")
            if not good:
                continue
            self.series[cid][kid] = [[r["period"], round(float(r["value"]), 4)] for r in good]
            last = good[-1]
            value = float(last["value"])
            cur = self.values[cid].get(kid)
            if cur:
                if datetime.date.fromisoformat(last["period_end"]) < self.period_end(cur):
                    continue
                dev = abs(value - cur["value"]) / abs(cur["value"]) if cur["value"] else 0
                tol = kpi.get("tolerance_pct", self.tolerance * 100) / 100
                if dev > tol:
                    self.anomalies.append(
                        f"{cid} {kpi['label']} {last['period']}: valore automatico {value:g} differisce del {dev:.0%} "
                        f"dal valore curato {cur['value']:g} ({cur['period']}); tenuto il curato, verificare la definizione")
                    continue
            self.values[cid][kid] = {
                "value": value, "period": last["period"], "year": int(last["period_end"][:4]), "source_id": last["source_id"],
                "source_url": last["source_url"], "retrieved": last["retrieved"], "confidence": "ufficiale",
                "note": f"Aggiornato automaticamente: {last['method']}",
            }

    def apply_auto_shares(self):
        """Quote per operatore dai dati CNMC mensili: sostituiscono le curate se più recenti e coerenti."""
        grouped = defaultdict(lambda: defaultdict(list))
        for r in getattr(self, "reg_rows", []):
            if r["kpi"].startswith("share|") and to_float(r["value"]) is not None:
                grouped[r["country"]][r["period_end"]].append(r)
        for cid, periods in grouped.items():
            end = max(periods)
            rows = periods[end]
            aliases = next((c.get("operator_aliases", {}) for c in self.countries if c["id"] == cid), {})
            items = sorted(({"operator": aliases.get(r["kpi"].split("|", 1)[1], r["kpi"].split("|", 1)[1]), "share": round(float(r["value"]), 2)}
                            for r in rows), key=lambda x: -x["share"])
            total = sum(i["share"] for i in items)
            label = rows[0]["period"]
            if not 97 <= total <= 103 or len(items) < 3:
                self.anomalies.append(f"{cid} quote di mercato {label}: somma {total:.1f}% o operatori insufficienti, tenute le curate")
                continue
            cur = self.shares.get(cid)
            if cur:
                if datetime.date.fromisoformat(end) < self.period_end({"period": cur["period"], "year": re.search(r"\d{4}", cur["period"]).group()}):
                    continue
                lead_dev = abs(items[0]["share"] - cur["items"][0]["share"]) / cur["items"][0]["share"]
                if lead_dev > self.tolerance:
                    self.anomalies.append(f"{cid} quote di mercato {label}: quota del primo operatore {items[0]['share']}% lontana dalla curata "
                                          f"{cur['items'][0]['share']}%, tenute le curate")
                    continue
            self.shares[cid] = {"basis": "linee mobili (dati mensili CNMC)", "period": label, "source_id": rows[0]["source_id"],
                                "source_url": rows[0]["source_url"], "retrieved": rows[0]["retrieved"], "confidence": "ufficiale",
                                "items": items, "covered": round(total, 1)}

    # ---------- KPI derivati ----------
    def fallback_context(self):
        """Se l'API non ha ancora fornito PIL pro capite o densità, li ricava da PIL totale e superficie."""
        for c in self.countries:
            cid = c["id"]
            vals = self.values[cid]
            pop_pts = dict(self.wb.get("SP.POP.TOTL", {}).get(cid, []))
            if "density" not in vals and pop_pts:
                y = max(pop_pts)
                vals["density"] = self.derived(pop_pts[y] / c["land_km2"], y, "Popolazione / superficie (fallback finché World Bank non fornisce EN.POP.DNST)", ["population"], cid)
            gdp_pts = dict(self.wb.get("NY.GDP.MKTP.CD", {}).get(cid, []))
            common_years = sorted(set(gdp_pts) & set(pop_pts))
            if "gdp_pc" not in vals and common_years:
                y = common_years[-1]
                vals["gdp_pc"] = self.derived(gdp_pts[y] / pop_pts[y], y, "PIL / popolazione (fallback finché World Bank non fornisce NY.GDP.PCAP.CD)", ["population"], cid)
                self.series[cid]["gdp_pc"] = [[yy, round(gdp_pts[yy] / pop_pts[yy], 1)] for yy in common_years]

    def derived(self, value, year, note, inputs, cid):
        recs = [self.values[cid].get(i) for i in inputs]
        retrieved = max((r["retrieved"] for r in recs if r), default=datetime.date.today().isoformat())
        weakest = "calcolato"
        if any(r and r["confidence"] in ("stima", "secondaria") for r in recs):
            weakest = "calcolato su stime"
        return {"value": value, "period": str(year), "year": year, "source_id": "derived", "source_url": "",
                "retrieved": retrieved, "confidence": weakest, "note": note}

    def compute_derived(self):
        for c in self.countries:
            cid = c["id"]
            v = self.values[cid]
            g = lambda k: v[k]["value"] if k in v else None  # noqa: E731
            yr = lambda *ks: max(v[k]["year"] for k in ks)  # noqa: E731

            v["mnos"] = {"value": len(c["mnos"]), "period": "attuale", "year": datetime.date.today().year,
                         "source_id": "config", "source_url": "", "retrieved": datetime.date.today().isoformat(),
                         "confidence": "ufficiale", "note": ", ".join(c["mnos"])}

            sh = self.shares.get(cid)
            if sh:
                hhi = sum(i["share"] ** 2 for i in sh["items"])
                note = f"Base: {sh['basis']}, {sh['period']}; quote coperte {sh['covered']}%"
                rec = {"value": hhi, "period": sh["period"], "year": int(re.search(r"\d{4}", sh["period"]).group()), "source_id": sh["source_id"],
                       "source_url": sh["source_url"], "retrieved": sh["retrieved"],
                       "confidence": "calcolato" if sh["confidence"] == "ufficiale" else "calcolato su stime", "note": note}
                v["hhi_mobile"] = rec
                v["leader_share"] = dict(rec, value=sh["items"][0]["share"], note=f"{sh['items'][0]['operator']}. {note}")

            if g("mobile_rev") and g("mobile_subs"):
                v["arpu_mobile"] = self.derived(g("mobile_rev") * 1e9 / (g("mobile_subs") * 1e6) / 12, yr("mobile_rev", "mobile_subs"),
                                                self.definitions["arpu_mobile"], ["mobile_rev", "mobile_subs"], cid)
            if g("telecom_rev") and g("population"):
                v["rev_per_capita"] = self.derived(g("telecom_rev") * 1e9 / (g("population") * 1e6), yr("telecom_rev"),
                                                   self.definitions["rev_per_capita"], ["telecom_rev", "population"], cid)
            gdp_pts = dict(self.wb.get("NY.GDP.MKTP.CD", {}).get(cid, []))
            if g("telecom_rev") and gdp_pts:
                gy = max(gdp_pts)
                rate, _ = self.eur_rate("USD", gy)
                v["rev_gdp"] = self.derived(g("telecom_rev") * 1e9 / (gdp_pts[gy] * rate) * 100, yr("telecom_rev"),
                                            f"{self.definitions['rev_gdp']} PIL {gy}.", ["telecom_rev"], cid)
            if g("capex") and g("telecom_rev"):
                v["capex_intensity"] = self.derived(g("capex") / g("telecom_rev") * 100, yr("capex", "telecom_rev"),
                                                    self.definitions["capex_intensity"], ["capex", "telecom_rev"], cid)
            # composizione dei ricavi: il retail è la somma consumer + business quando entrambi sono pubblicati
            if g("rev_b2c") and g("rev_b2b"):
                v["rev_retail"] = self.derived(g("rev_b2c") + g("rev_b2b"), yr("rev_b2c", "rev_b2b"), "Consumer + business", ["rev_b2c", "rev_b2b"], cid)
                v["b2b_share"] = self.derived(g("rev_b2b") / (g("rev_b2c") + g("rev_b2b")) * 100, yr("rev_b2c", "rev_b2b"),
                                              self.definitions["b2b_share"], ["rev_b2c", "rev_b2b"], cid)
            if g("rev_wholesale") and g("rev_retail"):
                v["wholesale_share"] = self.derived(g("rev_wholesale") / (g("rev_wholesale") + g("rev_retail")) * 100, yr("rev_wholesale", "rev_retail"),
                                                    self.definitions["wholesale_share"], ["rev_wholesale", "rev_retail"], cid)
            if g("ftth_subs") and g("fbb_subs"):
                v["ftth_share"] = self.derived(g("ftth_subs") / g("fbb_subs") * 100, yr("ftth_subs", "fbb_subs"),
                                               self.definitions["ftth_share"], ["ftth_subs", "fbb_subs"], cid)
            if g("capex") and g("population"):
                v["capex_per_capita"] = self.derived(g("capex") * 1e9 / (g("population") * 1e6), yr("capex"),
                                                     self.definitions["capex_per_capita"], ["capex", "population"], cid)

    # ---------- freschezza ----------
    def freshness(self):
        today = datetime.date.today()
        last = defaultdict(str)
        for cid, kv in self.values.items():
            for rec in kv.values():
                sid = rec["source_id"]
                if sid in self.sources and rec["retrieved"] > last[sid]:
                    last[sid] = rec["retrieved"]
        for r in getattr(self, "reg_rows", []):
            if r["retrieved"] > last[r["source_id"]]:
                last[r["source_id"]] = r["retrieved"]
        for table in self.fx.values():
            for _, row in table.values():
                if row["retrieved"] > last["ecb_fx"]:
                    last["ecb_fx"] = row["retrieved"]
        out, stale = [], []
        for sid, s in self.sources.items():
            lr = last.get(sid)
            age = (today - datetime.date.fromisoformat(lr)).days if lr else None
            status = "assente" if lr is None else ("da aggiornare" if age > s["stale_after_days"] else "aggiornata")
            row = dict(s, last_retrieved=lr, age_days=age, status=status)
            out.append(row)
            if status == "da aggiornare":
                stale.append(row)
        return out, stale

    # ---------- output ----------
    def payload(self, sources):
        clean = lambda rec: dict(rec, value=round(rec["value"], 4))  # noqa: E731
        return {
            "built_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
            "countries": self.countries, "groups": self.groups, "kpis": self.kpis, "definitions": self.definitions,
            "values": {cid: {k: clean(r) for k, r in kv.items()} for cid, kv in self.values.items()},
            "series": self.series, "shares": self.shares, "sources": sources,
        }

    def long_csv(self):
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["country", "kpi", "label", "unit", "value", "period", "comparability", "confidence", "source_id", "source_url", "retrieved", "note"])
        for c in self.countries:
            for k in self.kpis:
                rec = self.values[c["id"]].get(k["id"])
                if rec:
                    w.writerow([c["id"], k["id"], k["label"], k["unit"], round(rec["value"], 4), rec["period"], k["comparability"],
                                rec["confidence"], rec["source_id"], rec["source_url"], rec["retrieved"], rec["note"]])
        return buf.getvalue()

    def run(self):
        self.load_fx()
        self.load_worldbank()
        self.load_curated()
        self.load_regulators()
        self.load_shares()
        self.apply_auto_shares()
        self.fallback_context()
        self.compute_derived()
        sources, stale = self.freshness()
        data = self.payload(sources)
        DIST.mkdir(exist_ok=True)
        blob = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        (DIST / "data.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        (DIST / "data.csv").write_text(self.long_csv(), encoding="utf-8")
        log_path = DATA / "auto" / "regulators_log.txt"
        reg_log = [l for l in log_path.read_text(encoding="utf-8").splitlines() if l.strip()] if log_path.exists() else []
        report = {"stale": stale, "anomalies": self.anomalies, "log": reg_log}
        (DIST / "stale.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        html = TEMPLATE.read_text(encoding="utf-8")
        if PLACEHOLDER not in html:
            sys.exit("Segnaposto dati non trovato nel template")
        safe = blob.replace("</", "<\\/")
        (DIST / "index.html").write_text(html.replace(PLACEHOLDER, safe), encoding="utf-8")
        filled = sum(len(kv) for kv in self.values.values())
        total = len(self.kpis) * len(self.countries)
        print(f"Build ok: {filled}/{total} valori, {len(stale)} fonti da aggiornare, {len(self.anomalies)} valori automatici scartati")
        for a in self.anomalies:
            print("SCARTATO:", a)
        for w in self.warnings:
            print("ATTENZIONE:", w)
        missing = [f"{c['id']}:{k['id']}" for c in self.countries for k in self.kpis if k["id"] not in self.values[c["id"]]]
        if missing:
            print("Valori mancanti:", ", ".join(missing))


if __name__ == "__main__":
    Build().run()
