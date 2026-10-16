"""Test degli acquisti degli insider (SEC Form 4, dati ufficiali gratuiti).

Domanda: dopo che un amministratore o dirigente compra azioni della propria societa' sul mercato
(codice transazione 'P') e il modulo viene DEPOSITATO, il titolo batte il mercato nelle settimane
successive? La data nota e' quella di deposito (non quella dell'operazione); si entra alla chiusura
del primo giorno di borsa successivo.

Cautele incorporate:
- confronto con SPY e con IWM (small cap): gli insider comprano molte small cap, e una small cap
  media puo' battere SPY senza alcun segnale;
- gruppo di CONTROLLO (placebo): stessi titoli, date casuali. Conta la differenza rispetto al placebo;
- i titoli senza prezzi (ritirati dalla borsa) vengono contati: escluderli gonfia i risultati;
- nessun costo e' sottratto dai rendimenti: nel report si sottrae un costo di giro ipotizzato.
"""
import datetime
import math
import os
import random
import time
import zipfile

from . import settings as S
from . import signal_lab as L

UA = {"User-Agent": "UnibotResearch personal non-commercial research script"}
# i trimestri recenti stanno in una cartella diversa da quelli vecchi
BASES = ("https://www.sec.gov/files/datastandardsinnovation/data/insider-transactions-data-sets/",
         "https://www.sec.gov/files/structureddata/data/insider-transactions-data-sets/")
DEFAULT_QUARTERS = ["2025q1", "2025q2", "2025q3", "2025q4", "2026q1", "2026q2", "2026q3"]
HORIZONS = (5, 20, 60)
ROUND_TRIP_COST = 0.01     # IPOTESI: spread + slippage + commissioni per una small cap
MIN_VALUE, BIG_VALUE, CLUSTER_VALUE = 50_000, 250_000, 20_000
DEDUPE_DAYS = 30


def download_quarter(q, cache_dir, http=None, pause=2.0):
    path = os.path.join(cache_dir, "%s_form345.zip" % q)
    if os.path.exists(path) and os.path.getsize(path) > 100_000:
        return path
    if http is None:
        import requests as http
    os.makedirs(cache_dir, exist_ok=True)
    last = None
    for base in BASES:
        for attempt in range(4):
            try:
                r = http.get(base + "%s_form345.zip" % q, headers=UA, timeout=180)
            except Exception as e:  # noqa: BLE001
                last = e
                time.sleep(pause * (attempt + 1))
                continue
            if r.status_code == 404:
                break                       # prova l'altra cartella
            if r.status_code == 200 and len(r.content) > 100_000:
                with open(path, "wb") as f:
                    f.write(r.content)
                time.sleep(pause)           # cortesia verso il server SEC
                return path
            last = Exception("HTTP %s" % r.status_code)
            time.sleep(pause * (attempt + 1))
    raise RuntimeError("download %s fallito: %s" % (q, last))


def _clean_symbol(s):
    s = str(s or "").strip().upper().replace(".", "-")
    if not s or s in ("NONE", "N/A", "NA", "NAN") or " " in s or len(s) > 6:
        return None
    return s


def load_filings(zip_path, min_price=S.STOCK_MIN_PRICE):
    """Un record per modulo Form 4 con acquisti 'P' sul mercato: simbolo, data di deposito, valore."""
    import csv
    import pandas as pd

    def rd(z, name, cols):
        with z.open(name) as f:
            return pd.read_csv(f, sep="\t", dtype=str, usecols=cols, quoting=csv.QUOTE_NONE,
                               encoding_errors="replace", low_memory=False)

    with zipfile.ZipFile(zip_path) as z:
        sub = rd(z, "SUBMISSION.tsv", ["ACCESSION_NUMBER", "FILING_DATE", "DOCUMENT_TYPE",
                                       "ISSUERTRADINGSYMBOL", "AFF10B5ONE"])
        tr = rd(z, "NONDERIV_TRANS.tsv", ["ACCESSION_NUMBER", "TRANS_DATE", "TRANS_CODE", "TRANS_SHARES",
                                          "TRANS_PRICEPERSHARE", "TRANS_ACQUIRED_DISP_CD"])
        own = rd(z, "REPORTINGOWNER.tsv", ["ACCESSION_NUMBER", "RPTOWNERCIK", "RPTOWNER_RELATIONSHIP"])

    sub = sub[sub["DOCUMENT_TYPE"] == "4"].copy()
    tr = tr[(tr["TRANS_CODE"] == "P") & (tr["TRANS_ACQUIRED_DISP_CD"] == "A")].copy()
    tr["shares"] = pd.to_numeric(tr["TRANS_SHARES"], errors="coerce")
    tr["price"] = pd.to_numeric(tr["TRANS_PRICEPERSHARE"], errors="coerce")
    tr = tr[(tr["price"] >= min_price) & (tr["shares"] > 0)]
    tr["value"] = tr["shares"] * tr["price"]
    agg = tr.groupby("ACCESSION_NUMBER").agg(value=("value", "sum"), trans_date=("TRANS_DATE", "first")).reset_index()

    own["role_ok"] = own["RPTOWNER_RELATIONSHIP"].fillna("").str.contains("Officer|Director", case=False)
    own_agg = own.groupby("ACCESSION_NUMBER").agg(
        owners=("RPTOWNERCIK", lambda s: frozenset(s.dropna())), role_ok=("role_ok", "any")).reset_index()

    df = agg.merge(sub, on="ACCESSION_NUMBER", how="inner").merge(own_agg, on="ACCESSION_NUMBER", how="left")
    df["symbol"] = df["ISSUERTRADINGSYMBOL"].map(_clean_symbol)
    df = df.dropna(subset=["symbol"])
    fd = pd.to_datetime(df["FILING_DATE"].str.title(), format="%d-%b-%Y", errors="coerce")
    df = df[fd.notna()].copy()
    df["filing_date"] = fd[fd.notna()].dt.strftime("%Y-%m-%d")
    td = pd.to_datetime(df["trans_date"].str.title(), format="%d-%b-%Y", errors="coerce")
    df["trans_date"] = td.dt.strftime("%Y-%m-%d")
    df["role_ok"] = df["role_ok"].fillna(False).astype(bool)
    df["owners"] = df["owners"].map(lambda x: x if isinstance(x, frozenset) else frozenset())
    df["plan10b5"] = df["AFF10B5ONE"].fillna("0").isin(["1", "true", "True"])
    return df[["ACCESSION_NUMBER", "symbol", "filing_date", "trans_date", "value", "owners", "role_ok", "plan10b5"]]


def build_variants(filings):
    """Segnali per tre definizioni: acquisto >= 50k, acquisto >= 250k, cluster (>= 2 insider in 30 giorni)."""
    names = ("acquisto>=50k", "acquisto>=250k", "cluster>=2 insider")
    out = {n: [] for n in names}
    f = filings.sort_values(["symbol", "filing_date"])
    for sym, g in f.groupby("symbol", sort=False):
        last = {n: None for n in names}
        window = []                                  # (data, insider) degli acquisti recenti
        for row in g.itertuples():
            if not row.role_ok:
                continue
            d = datetime.date.fromisoformat(row.filing_date)

            def fire(name):
                if last[name] is None or (d - last[name]).days >= DEDUPE_DAYS:
                    last[name] = d
                    out[name].append({"symbol": sym, "known_date": row.filing_date, "event_date": row.trans_date,
                                      "side": "buy", "source": name, "assumed_lag": False, "value": row.value})

            if row.value >= MIN_VALUE:
                fire("acquisto>=50k")
            if row.value >= BIG_VALUE:
                fire("acquisto>=250k")
            if row.value >= CLUSTER_VALUE:
                window = [(wd, wo) for wd, wo in window if (d - wd).days <= DEDUPE_DAYS]
                window.append((d, row.owners))
                distinct = set().union(*[wo for _, wo in window])
                if len(distinct) >= 2:
                    fire("cluster>=2 insider")
    return out


def make_placebo(signals, prices, draws=5, seed=7, buffer_td=60):
    """Stessi titoli, date casuali dentro lo stesso periodo: indica cosa renderebbero quei titoli senza segnale."""
    rnd = random.Random(seed)
    if not signals:
        return []
    start = min(s["known_date"] for s in signals)
    out = []
    for s in signals:
        series = prices.get(s["symbol"], None)
        if not series:
            continue
        dates = [d for d, _ in series]
        lo = next((i for i, d in enumerate(dates) if d >= start), None)
        hi = len(dates) - 1 - buffer_td
        if lo is None or hi <= lo:
            continue
        for _ in range(draws):
            out.append({"symbol": s["symbol"], "known_date": dates[rnd.randint(lo, hi)], "event_date": None,
                        "side": "buy", "source": "placebo", "assumed_lag": False})
    return out


def welch(a, b):
    """(differenza delle medie, t di Welch) oppure None se i campioni sono troppo piccoli."""
    na, nb = len(a), len(b)
    if na < 2 or nb < 2:
        return None
    ma, mb = sum(a) / na, sum(b) / nb
    va = sum((x - ma) ** 2 for x in a) / (na - 1)
    vb = sum((x - mb) ** 2 for x in b) / (nb - 1)
    se = math.sqrt(va / na + vb / nb)
    return ma - mb, ((ma - mb) / se if se > 0 else 0.0)


def analyze(variants, prices, benchmarks=("SPY", "IWM"), horizons=HORIZONS):
    results = {}
    for name, sigs in variants.items():
        for bench in benchmarks:
            res = L.evaluate(sigs, prices, horizons=horizons, benchmark=bench)
            plc = L.evaluate(make_placebo(sigs, prices), prices, horizons=horizons, benchmark=bench)
            rows = {}
            for h in horizons:
                a = res.get("stats", {}).get(("buy", h), {}).get("alpha", {"n": 0})
                p = plc.get("stats", {}).get(("buy", h), {}).get("alpha", {"n": 0})
                cmp_ = welch(res.get("raw", {}).get(("buy", h), {}).get("alpha", []),
                             plc.get("raw", {}).get(("buy", h), {}).get("alpha", []))
                rows[h] = {"signal": a, "placebo": p, "diff": cmp_}
            results[(name, bench)] = {"rows": rows, "skipped": res.get("skipped", {}), "n": len(sigs),
                                      "error": res.get("error")}
    return results


def format_report(results, coverage):
    L_ = ["=== INSIDER LAB (SEC Form 4, acquisti sul mercato) ==="]
    L_.append("Titoli con prezzi: %d su %d richiesti (%.0f%%). I mancanti sono per lo piu' societa' ritirate: "
              "escluderle rende i risultati PIU' ROSEI della realta'." % (
                  coverage["with_prices"], coverage["requested"],
                  100.0 * coverage["with_prices"] / max(coverage["requested"], 1)))
    L_.append("Alpha = rendimento del titolo meno quello del benchmark. 'diff' = alpha dei segnali meno alpha del "
              "placebo (stessi titoli, date casuali). Costo di giro ipotizzato: %.1f%%." % (ROUND_TRIP_COST * 100))
    for (name, bench), r in results.items():
        L_.append("")
        L_.append("%s vs %s | eventi %d | scartati %s" % (
            name, bench, r["n"], ", ".join("%s %d" % kv for kv in r["skipped"].items() if kv[1])))
        if r["error"]:
            L_.append("  ERRORE: " + r["error"])
            continue
        L_.append("  %4s %5s %9s %9s %7s %9s %8s %7s %10s" % ("gg", "n", "alpha%", "mediana%", "hit%", "placebo%",
                                                           "diff%", "t_diff", "netto_costi%"))
        for h, row in r["rows"].items():
            a, p, d = row["signal"], row["placebo"], row["diff"]
            if not a.get("n"):
                L_.append("  %4d %5d  (nessun dato maturo)" % (h, 0))
                continue
            L_.append("  %4d %5d %+8.2f%% %+8.2f%% %6.1f%% %+8.2f%% %+7.2f%% %7s %+9.2f%%" % (
                h, a["n"], a["mean"] * 100, a["median"] * 100, a["hit"] * 100,
                (p.get("mean", 0.0) * 100) if p.get("n") else float("nan"),
                (d[0] * 100) if d else float("nan"), ("%.2f" % d[1]) if d else "n/d",
                (a["mean"] - ROUND_TRIP_COST) * 100))
    L_.append("")
    L_.append("Come leggerlo: un segnale utile ha 'diff' positivo con |t_diff| >= 2 e resta positivo DOPO i costi. "
              "Eventi sullo stesso titolo o nelle stesse settimane non sono indipendenti: il t e' ottimistico.")
    return "\n".join(L_)


def run(quarters=None, cache_dir="unibot_data", max_symbols=1500, log=print):
    quarters = quarters or DEFAULT_QUARTERS
    import pandas as pd
    frames = []
    for q in quarters:
        log("scarico/leggo %s..." % q)
        frames.append(load_filings(download_quarter(q, cache_dir)))
    filings = pd.concat(frames, ignore_index=True).drop_duplicates("ACCESSION_NUMBER")
    log("moduli con acquisti sul mercato: %d (%d titoli)" % (len(filings), filings["symbol"].nunique()))
    variants = build_variants(filings)
    for k, v in variants.items():
        log("  %-20s %5d eventi su %4d titoli" % (k, len(v), len({s["symbol"] for s in v})))

    symbols = sorted({s["symbol"] for v in variants.values() for s in v})
    if len(symbols) > max_symbols:
        rnd = random.Random(1)
        keep = set(rnd.sample(symbols, max_symbols))
        variants = {k: [s for s in v if s["symbol"] in keep] for k, v in variants.items()}
        symbols = sorted(keep)
        log("campione casuale di %d titoli (limite per non sovraccaricare la fonte prezzi)" % max_symbols)

    prices = L.YahooHistory(cache_dir=os.path.join(cache_dir, "prices"), start="2024-12-01")
    for b in ("SPY", "IWM"):
        prices.get(b)
    log("scarico i prezzi di %d titoli..." % len(symbols))
    t0 = time.time()
    for i, sym in enumerate(symbols, 1):
        prices.get(sym)
        if i % 100 == 0:
            log("  %d/%d (%.0fs) | senza dati %d | errori di rete %d" % (
                i, len(symbols), time.time() - t0, len(prices.failed), len(prices.network_failed)))
    # secondo giro solo per gli errori di rete (non per i titoli davvero senza dati)
    retry = sorted(prices.network_failed)
    if retry:
        log("ritento %d titoli falliti per rete..." % len(retry))
        prices.network_failed.clear()
        time.sleep(5)
        for sym in retry:
            prices.get(sym)
    coverage = {"requested": len(symbols), "with_prices": sum(1 for s in symbols if prices.get(s))}
    log("analisi...")
    return format_report(analyze(variants, prices), coverage), prices


def main_cli(quarters_arg=None):
    quarters = [q.strip() for q in quarters_arg.split(",")] if quarters_arg else None
    try:
        text, prices = run(quarters)
    except Exception as e:  # noqa: BLE001
        print("insider-lab non eseguito: %s" % e)
        return 2
    print(text)
    if prices.network_failed:
        print("Attenzione: %d titoli non scaricati per errori di rete: rilancia, la cache evita di ripartire da zero."
              % len(prices.network_failed))
    return 0
