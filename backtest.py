"""
BACKTEST DEL MODELLO — quanto sono affidabili davvero le nostre probabilità?

Metodo "walk-forward", cioè senza barare: per ogni mese dal 2024-25 a oggi,
il modello viene allenato SOLO sulle partite giocate prima di quel mese e
poi prevede le partite del mese, esattamente come fa l'app ogni giorno.
Le previsioni vengono poi confrontate con:
  - i risultati veri (calibrazione: quando diciamo 60%, succede il 60%?);
  - le quote di mercato dello stesso file (football-data.co.uk):
      media dei bookmaker qualche giorno prima (Avg*)  -> le quote "giocabili"
      Pinnacle alla chiusura (PSC*)                    -> il prezzo più efficiente

Domande a cui risponde:
  1. Il modello prevede meglio o peggio del mercato? (log loss, Brier)
  2. È calibrato? (tabella per fasce di probabilità)
  3. Scommettendo quando il modello vede valore, si sarebbe guadagnato? (ROI)
     E per fascia di valore atteso: gli EV altissimi sono veri o errori?
  4. Le quote prese battono la chiusura di Pinnacle? (CLV: il test più
     affidabile di un modello di value betting, meno influenzato dalla fortuna)
  5. Conviene "avvicinare" le nostre probabilità a quelle del mercato?
     (miscela modello/mercato: quale peso dà le previsioni migliori)

Il risultato va in backtest_report.json, mostrato nella scheda "Performance
del modello" della dashboard. Non tocca data.db. Gira una volta a settimana
(workflow backtest.yml) e si può lanciare a mano.
"""

import json
import math
from datetime import date, datetime, timezone
from io import StringIO

import numpy as np
import pandas as pd
import requests

from model import DixonColesModel

LEAGUES = {"I1": "Serie A", "E0": "Premier League", "SP1": "La Liga",
           "D1": "Bundesliga", "F1": "Ligue 1"}
TEST_FROM = date(2024, 8, 1)       # prima partita valutata
TRAIN_SEASONS_BEFORE = 3           # stagioni di storia prima del periodo di test
URL = "https://www.football-data.co.uk/mmz4281/{season}/{league}.csv"
EV_BANDS = [(0.0, 0.05), (0.05, 0.10), (0.10, 0.25), (0.25, 10.0)]
BLEND_WEIGHTS = [0.0, 0.2, 0.4, 0.5, 0.6, 0.8, 1.0]   # peso del modello
REPORT_PATH = "backtest_report.json"


# ---------------------------------------------------------------------------
# Dati
# ---------------------------------------------------------------------------
def season_codes():
    first = TEST_FROM.year - TRAIN_SEASONS_BEFORE
    today = date.today()
    last = today.year if today.month >= 7 else today.year - 1
    return [f"{str(y)[-2:]}{str(y + 1)[-2:]}" for y in range(first, last + 1)]


def load_league(code):
    frames = []
    for season in season_codes():
        try:
            r = requests.get(URL.format(season=season, league=code), timeout=30)
            r.raise_for_status()
            frames.append(pd.read_csv(StringIO(r.content.decode("latin-1"))))
        except Exception as e:
            print(f"  Avviso: {code} {season} non disponibile ({e})")
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    df = df.dropna(subset=["HomeTeam", "AwayTeam", "FTHG", "FTAG"])
    df["Date"] = pd.to_datetime(df["Date"], dayfirst=True, errors="coerce")
    return df.dropna(subset=["Date"]).sort_values("Date").reset_index(drop=True)


def devig(values):
    """Quote -> probabilità senza margine (None se manca qualcosa)."""
    try:
        inv = [1 / float(v) for v in values]
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    if any(math.isnan(x) for x in inv):
        return None
    s = sum(inv)
    return [x / s for x in inv]


def col(row, name):
    v = row.get(name)
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) or v <= 1 else v


# ---------------------------------------------------------------------------
# Walk-forward
# ---------------------------------------------------------------------------
def month_starts(start, end):
    d = date(start.year, start.month, 1)
    while d <= end:
        yield d
        d = date(d.year + (d.month == 12), d.month % 12 + 1, 1)


def walk_forward(df, league):
    records = []
    for m_start in month_starts(TEST_FROM, date.today()):
        m_end = date(m_start.year + (m_start.month == 12), m_start.month % 12 + 1, 1)
        train = df[df["Date"].dt.date < m_start]
        test = df[(df["Date"].dt.date >= m_start) & (df["Date"].dt.date < m_end)]
        if test.empty or len(train) < 300:
            continue
        model = DixonColesModel().fit([
            {"home_team": r.HomeTeam, "away_team": r.AwayTeam,
             "home_goals": int(r.FTHG), "away_goals": int(r.FTAG),
             "days_ago": (m_start - r.Date.date()).days}
            for r in train.itertuples()])
        for _, r in test.iterrows():
            h, a = r["HomeTeam"], r["AwayTeam"]
            if h not in model.teams or a not in model.teams:
                continue  # neopromossa senza storico nel campionato: l'app la salta
            ph, pd_, pa = model.predict_match(h, a)
            goals = model.market_probabilities(h, a)
            hg, ag = int(r["FTHG"]), int(r["FTAG"])
            records.append({
                "league": league, "date": r["Date"].date().isoformat(),
                "p": [ph, pd_, pa], "p_over25": goals["prob_over25"], "p_btts": goals["prob_btts"],
                "result": 0 if hg > ag else (1 if hg == ag else 2),
                "over25": int(hg + ag >= 3), "btts": int(hg > 0 and ag > 0),
                "odds_avg": [col(r, "AvgH"), col(r, "AvgD"), col(r, "AvgA")],
                "pin_pre": devig([r.get("PSH"), r.get("PSD"), r.get("PSA")]),
                "pin_close": devig([r.get("PSCH"), r.get("PSCD"), r.get("PSCA")]),
                "odds_ou": [col(r, "Avg>2.5"), col(r, "Avg<2.5")],
                "pin_close_ou": devig([r.get("PC>2.5"), r.get("PC<2.5")]),
                # per la strategia "bookmaker contro Pinnacle" (senza modello)
                "odds_b365": [col(r, "B365H"), col(r, "B365D"), col(r, "B365A")],
                "odds_max": [col(r, "MaxH"), col(r, "MaxD"), col(r, "MaxA")],
                "pin_pre_ou": devig([r.get("P>2.5"), r.get("P<2.5")]),
                "odds_ou_b365": [col(r, "B365>2.5"), col(r, "B365<2.5")],
                "odds_ou_max": [col(r, "Max>2.5"), col(r, "Max<2.5")],
            })
        print(f"  {league} {m_start:%Y-%m}: {len(test)} partite")
    return records


# ---------------------------------------------------------------------------
# Metriche
# ---------------------------------------------------------------------------
def log_loss(probs, outcomes):
    return float(np.mean([-math.log(max(p[o], 1e-12)) for p, o in zip(probs, outcomes)]))


def brier(probs, outcomes):
    return float(np.mean([sum((p[k] - (k == o)) ** 2 for k in range(len(p)))
                          for p, o in zip(probs, outcomes)]))


def calibration(pairs, bins=10):
    """pairs: (probabilità prevista, 0/1 successo) -> tabella per fasce."""
    table = []
    for i in range(bins):
        lo, hi = i / bins, (i + 1) / bins
        sel = [(p, y) for p, y in pairs if lo <= p < hi or (i == bins - 1 and p == 1)]
        if len(sel) >= 20:
            table.append({"fascia": f"{lo:.0%}-{hi:.0%}", "n": len(sel),
                          "prevista": round(float(np.mean([p for p, _ in sel])), 3),
                          "reale": round(float(np.mean([y for _, y in sel])), 3)})
    return table


def betting(records, prob_fn, odds_fn, outcome_fn, n_sel):
    """Simula: 1 unità su ogni esito con EV >= 0 alle quote medie di mercato.
    Ritorna risultati per fascia di EV, compreso il CLV medio."""
    bands = {b: {"n": 0, "won": 0, "profit": 0.0, "clv": []} for b in EV_BANDS}
    for r in records:
        probs, odds = prob_fn(r), odds_fn(r)
        if probs is None or odds is None:
            continue
        for k in range(n_sel):
            if not odds[k]:
                continue
            ev = probs[k] * odds[k] - 1
            band = next((b for b in EV_BANDS if b[0] <= ev < b[1]), None)
            if band is None:
                continue
            won = outcome_fn(r) == k
            bands[band]["n"] += 1
            bands[band]["won"] += won
            bands[band]["profit"] += (odds[k] - 1) if won else -1
            close = r.get("pin_close") if n_sel == 3 else r.get("pin_close_ou")
            if close:
                bands[band]["clv"].append(odds[k] * close[k] - 1)
    out = []
    for (lo, hi), b in bands.items():
        if b["n"] == 0:
            continue
        out.append({"fascia_ev": f"{lo:+.0%} / {hi:+.0%}" if hi < 10 else f"oltre {lo:+.0%}",
                    "scommesse": b["n"], "vinte": round(b["won"] / b["n"], 3),
                    "roi": round(b["profit"] / b["n"], 3),
                    "clv_medio": round(float(np.mean(b["clv"])), 3) if b["clv"] else None})
    total_n = sum(b["n"] for b in bands.values())
    total = {"scommesse": total_n,
             "roi": round(sum(b["profit"] for b in bands.values()) / total_n, 3) if total_n else None}
    return out, total


SHARP_THRESHOLDS = [0.0, 0.02, 0.05]


def sharp_vs_soft(records, odds_key, fair_key, close_key, outcome_fn, n_sel):
    """Strategia SENZA modello: si gioca quando la quota di un bookmaker è più
    alta della quota "giusta" di Pinnacle (senza margine) nello stesso
    momento. È il metodo classico del value betting: il bookmaker più
    efficiente fa da stima della probabilità vera."""
    out = []
    for t in SHARP_THRESHOLDS:
        n, profit, clv = 0, 0.0, []
        for r in records:
            odds, fair, close = r.get(odds_key), r.get(fair_key), r.get(close_key)
            if not odds or not fair:
                continue
            for k in range(n_sel):
                if not odds[k] or odds[k] * fair[k] - 1 < t:
                    continue
                n += 1
                won = outcome_fn(r) == k
                profit += (odds[k] - 1) if won else -1
                if close:
                    clv.append(odds[k] * close[k] - 1)
        if n:
            out.append({"soglia_vantaggio": f"{t:+.0%}", "scommesse": n,
                        "roi": round(profit / n, 3),
                        "clv_medio": round(float(np.mean(clv)), 3) if clv else None})
    return out


def summarize(records):
    report = {}
    r1 = [r for r in records if r["pin_close"]]
    outcomes = [r["result"] for r in r1]
    model_p = [r["p"] for r in r1]
    report["partite_valutate"] = len(records)
    report["1x2"] = {
        "log_loss": {"modello": round(log_loss(model_p, outcomes), 4),
                     "mercato_media": round(log_loss([devig(r["odds_avg"]) or r["pin_close"]
                                                      for r in r1], outcomes), 4),
                     "pinnacle_chiusura": round(log_loss([r["pin_close"] for r in r1], outcomes), 4)},
        "brier": {"modello": round(brier(model_p, outcomes), 4),
                  "pinnacle_chiusura": round(brier([r["pin_close"] for r in r1], outcomes), 4)},
        "calibrazione": calibration([(r["p"][k], int(r["result"] == k))
                                     for r in records for k in range(3)]),
    }
    # Miscela modello/mercato (mercato = Pinnacle qualche giorno prima, cioè
    # un prezzo che al momento della scommessa si conosce già)
    rb = [r for r in r1 if r["pin_pre"]]
    blends = []
    for w in BLEND_WEIGHTS:
        probs = [[w * p + (1 - w) * q for p, q in zip(r["p"], r["pin_pre"])] for r in rb]
        blends.append({"peso_modello": w,
                       "log_loss": round(log_loss(probs, [r["result"] for r in rb]), 4)})
    best_w = min(blends, key=lambda b: b["log_loss"])["peso_modello"] if blends else None
    report["1x2"]["miscela"] = blends
    report["1x2"]["peso_modello_migliore"] = best_w

    report["1x2"]["scommesse_modello"], report["1x2"]["totale_modello"] = betting(
        records, lambda r: r["p"], lambda r: r["odds_avg"], lambda r: r["result"], 3)
    if best_w is not None:
        report["1x2"]["scommesse_miscela"], report["1x2"]["totale_miscela"] = betting(
            [r for r in records if r["pin_pre"]],
            lambda r: [best_w * p + (1 - best_w) * q for p, q in zip(r["p"], r["pin_pre"])],
            lambda r: r["odds_avg"], lambda r: r["result"], 3)

    ro = [r for r in records if r["pin_close_ou"]]
    report["over_under_2_5"] = {
        "log_loss": {"modello": round(log_loss([[r["p_over25"], 1 - r["p_over25"]] for r in ro],
                                               [1 - r["over25"] for r in ro]), 4),
                     "pinnacle_chiusura": round(log_loss([r["pin_close_ou"] for r in ro],
                                                         [1 - r["over25"] for r in ro]), 4)}
        if ro else None,
        "calibrazione": calibration([(r["p_over25"], r["over25"]) for r in records]),
    }
    report["over_under_2_5"]["scommesse_modello"], report["over_under_2_5"]["totale_modello"] = betting(
        records, lambda r: [r["p_over25"], 1 - r["p_over25"]], lambda r: r["odds_ou"],
        lambda r: 1 - r["over25"], 2)
    report["goal_no_goal"] = {"calibrazione": calibration([(r["p_btts"], r["btts"]) for r in records])}
    report["contro_pinnacle"] = {
        "1x2_bet365": sharp_vs_soft(records, "odds_b365", "pin_pre", "pin_close",
                                    lambda r: r["result"], 3),
        "1x2_quota_massima": sharp_vs_soft(records, "odds_max", "pin_pre", "pin_close",
                                           lambda r: r["result"], 3),
        "ou25_bet365": sharp_vs_soft(records, "odds_ou_b365", "pin_pre_ou", "pin_close_ou",
                                     lambda r: 1 - r["over25"], 2),
        "ou25_quota_massima": sharp_vs_soft(records, "odds_ou_max", "pin_pre_ou", "pin_close_ou",
                                            lambda r: 1 - r["over25"], 2),
    }
    return report


def main():
    all_records, per_league = [], {}
    for code, league in LEAGUES.items():
        print(f"{league}: scarico lo storico e simulo mese per mese...")
        df = load_league(code)
        if df.empty:
            continue
        recs = walk_forward(df, league)
        all_records += recs
        if recs:
            per_league[league] = summarize(recs)["1x2"]["log_loss"]
    if not all_records:
        print("Nessuna partita valutata: niente report.")
        return
    report = summarize(all_records)
    report["per_campionato_log_loss"] = per_league
    report["periodo"] = {"da": min(r["date"] for r in all_records),
                         "a": max(r["date"] for r in all_records)}
    report["generato"] = datetime.now(timezone.utc).isoformat()
    with open(REPORT_PATH, "w") as f:
        json.dump(report, f, indent=1, ensure_ascii=False)
    print(json.dumps({k: report[k] for k in ("partite_valutate", "periodo")}, ensure_ascii=False))
    print("log loss 1X2:", report["1x2"]["log_loss"])
    print("miglior peso del modello nella miscela:", report["1x2"]["peso_modello_migliore"])
    print("scommesse 1X2 per fascia di EV:")
    for b in report["1x2"]["scommesse_modello"]:
        print("  ", b)
    print("strategia senza modello: bookmaker contro Pinnacle")
    for name, rows in report["contro_pinnacle"].items():
        for b in rows:
            print("  ", name, b)


if __name__ == "__main__":
    main()
