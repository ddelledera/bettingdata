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

VERSIONE 3 — i nostri dati aggiungono qualcosa al prezzo di Pinnacle?
(la versione 2 ha mostrato che regolare decadimento e ridge non serve)
  - MODELLO A CORREZIONE: si parte dalla probabilità di Pinnacle del mattino
    (margine tolto col metodo potenza) e una regressione logistica
    multinomiale, molto regolarizzata, impara solo una piccola correzione:
        log-odds finali = log-odds Pinnacle + correzione dai nostri segnali
    Se i segnali non contengono informazione, la correzione resta ~0 e la
    probabilità finale torna a essere quella di Pinnacle.
  - SEGNALI, provati separatamente e poi insieme (ablation): forza delle
    squadre da gol, da tiri totali, da tiri in porta (ciascuno: attacco e
    difesa stimati dal passato, come Dixon-Coles), più il campionato.
    Niente giorni di riposo: nei file mancano coppe nazionali ed europee, e
    il riposo calcolato solo sul campionato sarebbe sbagliato.
  - PERIODI: SVILUPPO (ago 2024 - set 2026) per scegliere segnali e
    regolarizzazione, con validazione "a scorrimento": ogni mese la
    correzione è allenata solo sui mesi precedenti. HOLDOUT (da ottobre
    2026): congelato, non si guarda finché la variante non è decisa; si
    apre una volta sola cambiando OPEN_HOLDOUT. Il vecchio "test" della
    versione 2 ormai è stato visto e fa parte dello sviluppo.
  - REGOLA DI ADOZIONE, decisa prima di vedere i risultati: la correzione
    entra nell'app solo se sull'holdout (1) il log loss migliora rispetto a
    Pinnacle con intervallo al 95% tutto sotto zero, (2) il Brier non
    peggiora, (3) la calibrazione non peggiora, (4) il miglioramento non
    viene da un solo campionato. CLV e ROI restano una conferma economica.
  - COPERTURA PINNACLE: per stagione, campionato, mese e tipo di partita,
    per capire se le partite senza Pinnacle sono diverse dalle altre. Tutti
    i confronti usano solo le partite che hanno Pinnacle.

Il risultato va in backtest_report.json, mostrato nella scheda "Performance
del modello" della dashboard. Non tocca data.db. Gira una volta a settimana
(workflow backtest.yml) e si può lanciare a mano.
"""

import json
import math
from datetime import date, datetime, timedelta, timezone
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

# --- Versione 3 --------------------------------------------------------------
SIGNALS_FROM = date(2023, 8, 1)    # da qui calcoliamo i segnali: la stagione
                                   # 2023-24 serve solo ad allenare la correzione
HOLDOUT_FROM = date(2026, 10, 1)   # holdout congelato: da qui in avanti
OPEN_HOLDOUT = False               # si mette True UNA volta, a variante decisa
MIN_RESIDUAL_TRAIN = 600           # partite (con Pinnacle) prima di allenare la correzione
BOOTSTRAP_REPS = 2000
SIGNAL_SETS = {                    # ablation: da dove viene l'eventuale informazione
    "gol": ["gol"],
    "tiri": ["tiri"],
    "tiri in porta": ["porta"],
    "gol + tiri + tiri in porta": ["gol", "tiri", "porta"],
    "tutti + campionato": ["gol", "tiri", "porta", "+camp"],
    "tutti + campionato × segnali": ["gol", "tiri", "porta", "+camp", "+campxseg"],
}
L2_GRID = [0.003, 0.03, 0.3]       # regolarizzazione: più alta = correzione più vicina a zero


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


def devig_power(values):
    """Come devig(), ma con il metodo "potenza": toglie il margine più dagli
    sfavoriti che dai favoriti, come fanno davvero i bookmaker. Il metodo
    proporzionale invece lascia agli sfavoriti una probabilità troppo alta,
    e così fa sembrare di valore scommesse che non lo sono (distorsione
    favorito-sfavorito)."""
    try:
        inv = [1 / float(v) for v in values]
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    if any(math.isnan(x) for x in inv):
        return None
    lo, hi = 1.0, 3.0            # cerchiamo k tale che la somma di inv^k sia 1
    for _ in range(60):
        k = (lo + hi) / 2
        if sum(x ** k for x in inv) > 1:
            lo = k
        else:
            hi = k
    return [x ** k for x in inv]


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


class PoissonStrength:
    """Forza in attacco e difesa di ogni squadra per una grandezza "a
    conteggio" (tiri, tiri in porta), come Dixon-Coles fa per i gol:
    log(attese casa) = attacco_casa - difesa_ospite + fattore campo.
    Con lo stesso decadimento e la stessa penalità ridge del modello gol."""

    def __init__(self, decay_rate=0.001, ridge=0.5):
        self.decay_rate, self.ridge = decay_rate, ridge

    def fit(self, rows):
        from scipy.optimize import minimize
        self.teams = sorted({r[0] for r in rows} | {r[1] for r in rows})
        idx = {t: i for i, t in enumerate(self.teams)}
        n = len(self.teams)
        hi = np.array([idx[r[0]] for r in rows]); ai = np.array([idx[r[1]] for r in rows])
        hk = np.array([r[2] for r in rows], float); ak = np.array([r[3] for r in rows], float)
        w = np.exp(-self.decay_rate * np.array([r[4] for r in rows], float))
        mean = max(float(np.average(np.concatenate([hk, ak]))), 0.1)

        def obj(x):
            att, de, ha, mu = x[:n], x[n:2 * n], x[2 * n], x[2 * n + 1]
            lh = np.exp(np.clip(mu + att[hi] - de[ai] + ha, -10, 6))
            la = np.exp(np.clip(mu + att[ai] - de[hi], -10, 6))
            nll = np.sum(w * (lh - hk * np.log(lh))) + np.sum(w * (la - ak * np.log(la)))
            rh, ra = w * (lh - hk), w * (la - ak)
            g = np.zeros_like(x)
            np.add.at(g, hi, rh); np.add.at(g, n + ai, -rh)
            np.add.at(g, ai, ra); np.add.at(g, n + hi, -ra)
            g[2 * n] = rh.sum(); g[2 * n + 1] = rh.sum() + ra.sum()
            reg = x[:2 * n]
            g[:2 * n] += 2 * self.ridge * reg
            return nll + self.ridge * np.sum(reg ** 2), g

        x0 = np.zeros(2 * n + 2); x0[-1] = math.log(mean)
        res = minimize(obj, x0, jac=True, method="L-BFGS-B", options={"maxiter": 3000})
        self.att = dict(zip(self.teams, res.x[:n])); self.de = dict(zip(self.teams, res.x[n:2 * n]))
        self.ha = res.x[2 * n]
        return self

    def diff(self, home, away):
        """log(attese casa) - log(attese ospite): chi "produce" di più."""
        return (self.att[home] - self.de[away] + self.ha) - (self.att[away] - self.de[home])


def walk_forward(df, league):
    records = []
    for m_start in month_starts(SIGNALS_FROM, date.today()):
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
        count_models = {}
        for name, (hc, ac) in {"tiri": ("HS", "AS"), "porta": ("HST", "AST")}.items():
            rows = [(r["HomeTeam"], r["AwayTeam"], float(r[hc]), float(r[ac]),
                     (m_start - r["Date"].date()).days)
                    for _, r in train.iterrows()
                    if hc in train and pd.notna(r.get(hc)) and pd.notna(r.get(ac))]
            if len(rows) >= 300:
                count_models[name] = PoissonStrength().fit(rows)
        for _, r in test.iterrows():
            h, a = r["HomeTeam"], r["AwayTeam"]
            if h not in model.teams or a not in model.teams:
                continue  # neopromossa senza storico nel campionato: l'app la salta
            ph, pd_, pa = model.predict_match(h, a)
            goals = model.market_probabilities(h, a)
            hg, ag = int(r["FTHG"]), int(r["FTAG"])
            lam_h, lam_a = model.expected_goals(h, a)
            d = r["Date"].date()
            sig = {"gol": math.log(lam_h) - math.log(lam_a)}
            for name, cm in count_models.items():
                if h in cm.att and a in cm.att:
                    sig[name] = cm.diff(h, a)
            records.append({
                "league": league, "date": d.isoformat(),
                "period": ("prima" if d < TEST_FROM else
                           "sviluppo" if d < HOLDOUT_FROM else "holdout"),
                "sig": sig,
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
                "pin_pre_pow": devig_power([r.get("PSH"), r.get("PSD"), r.get("PSA")]),
                "pin_close_pow": devig_power([r.get("PSCH"), r.get("PSCD"), r.get("PSCA")]),
                "pin_pre_ou_pow": devig_power([r.get("P>2.5"), r.get("P<2.5")]),
                "pin_close_ou_pow": devig_power([r.get("PC>2.5"), r.get("PC<2.5")]),
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
ODDS_BANDS = [(1.0, 2.5), (2.5, 5.0), (5.0, 1000.0)]


def sharp_vs_soft(records, odds_key, fair_key, close_key, outcome_fn, n_sel, by_odds=False):
    """Strategia SENZA modello: si gioca quando la quota di un bookmaker è più
    alta della quota "giusta" di Pinnacle (senza margine) nello stesso
    momento. È il metodo classico del value betting: il bookmaker più
    efficiente fa da stima della probabilità vera."""
    out = []
    bands = ODDS_BANDS if by_odds else [(1.0, 1000.0)]
    for t in SHARP_THRESHOLDS:
      for lo_o, hi_o in bands:
        n, profit, clv = 0, 0.0, []
        for r in records:
            odds, fair, close = r.get(odds_key), r.get(fair_key), r.get(close_key)
            if not odds or not fair:
                continue
            for k in range(n_sel):
                if not odds[k] or odds[k] * fair[k] - 1 < t or not lo_o <= odds[k] < hi_o:
                    continue
                n += 1
                won = outcome_fn(r) == k
                profit += (odds[k] - 1) if won else -1
                if close:
                    clv.append(odds[k] * close[k] - 1)
        if n:
            out.append({"soglia_vantaggio": f"{t:+.0%}"
                        + (f" · quote {lo_o:g}-{hi_o:g}" if by_odds and hi_o < 1000
                           else (f" · quote oltre {lo_o:g}" if by_odds else "")),
                        "scommesse": n,
                        "roi": round(profit / n, 3),
                        "clv_medio": round(float(np.mean(clv)), 3) if clv else None})
    return out


# ---------------------------------------------------------------------------
# Versione 3: modello a correzione su Pinnacle
# ---------------------------------------------------------------------------
LEAGUE_NAMES = list(LEAGUES.values())


def features(r, spec):
    """Vettore dei segnali di una partita per un insieme di segnali."""
    base = [s for s in spec if not s.startswith("+")]
    x = [r["sig"].get(s) for s in base]
    if any(v is None for v in x):
        return None
    if "+camp" in spec:
        dummies = [1.0 if r["league"] == l else 0.0 for l in LEAGUE_NAMES[1:]]
        x = x + dummies
        if "+campxseg" in spec:
            x = x + [d * v for d in dummies for v in x[:len(base)]]
    return x


def fit_offset_mnl(P0, X, y, l2):
    """Logistica multinomiale con Pinnacle come offset:
        logit_k = log P0_k + b_k + x·w_k   (pareggio = riferimento, b=w=0)
    Penalità l2 su tutti i coefficienti: senza informazione -> Pinnacle."""
    from scipy.optimize import minimize
    n, d = X.shape
    L0 = np.log(np.clip(P0, 1e-9, 1))
    Y = np.eye(3)[y]

    def obj(t):
        B = t.reshape(2, d + 1)                       # righe: casa, ospite
        z = L0.copy()
        z[:, 0] += B[0, 0] + X @ B[0, 1:]
        z[:, 2] += B[1, 0] + X @ B[1, 1:]
        z -= z.max(axis=1, keepdims=True)
        P = np.exp(z); P /= P.sum(axis=1, keepdims=True)
        nll = -np.sum(Y * np.log(np.clip(P, 1e-12, 1))) / n
        G = (P - Y) / n
        g = np.concatenate([[G[:, 0].sum()], X.T @ G[:, 0], [G[:, 2].sum()], X.T @ G[:, 2]])
        return nll + l2 * np.sum(t ** 2), g + 2 * l2 * t

    res = minimize(obj, np.zeros(2 * (d + 1)), jac=True, method="L-BFGS-B")
    return res.x.reshape(2, d + 1)


def apply_offset_mnl(B, P0, X):
    z = np.log(np.clip(P0, 1e-9, 1)).copy()
    z[:, 0] += B[0, 0] + X @ B[0, 1:]
    z[:, 2] += B[1, 0] + X @ B[1, 1:]
    z -= z.max(axis=1, keepdims=True)
    P = np.exp(z)
    return P / P.sum(axis=1, keepdims=True)


def residual_walk_forward(records):
    """Per ogni variante (segnali × regolarizzazione) e ogni mese: allena la
    correzione solo sulle partite dei mesi precedenti e la applica al mese.
    Scrive le probabilità in r["pr"][nome variante]."""
    usable = sorted([r for r in records if r.get("pin_pre_pow")], key=lambda r: r["date"])
    months = sorted({r["date"][:7] for r in usable})
    for set_name, spec in SIGNAL_SETS.items():
        for l2 in L2_GRID:
            key = f"{set_name} (reg. {l2:g})"
            for m in months:
                train = [r for r in usable if r["date"][:7] < m]
                test = [r for r in usable if r["date"][:7] == m]
                tr = [(r, features(r, spec)) for r in train]
                tr = [(r, x) for r, x in tr if x is not None]
                if len(tr) < MIN_RESIDUAL_TRAIN:
                    continue
                X = np.array([x for _, x in tr])
                mu, sd = X.mean(axis=0), X.std(axis=0)
                sd[sd == 0] = 1
                B = fit_offset_mnl(np.array([r["pin_pre_pow"] for r, _ in tr]), (X - mu) / sd,
                                   np.array([r["result"] for r, _ in tr]), l2)
                te = [(r, features(r, spec)) for r in test]
                te = [(r, x) for r, x in te if x is not None]
                if not te:
                    continue
                P = apply_offset_mnl(B, np.array([r["pin_pre_pow"] for r, _ in te]),
                                     (np.array([x for _, x in te]) - mu) / sd)
                for (r, _), p in zip(te, P):
                    r.setdefault("pr", {})[key] = [float(v) for v in p]
    return [f"{s} (reg. {l2:g})" for s in SIGNAL_SETS for l2 in L2_GRID]


def paired_comparison(records, prob_a, prob_b, metric="log_loss", reps=BOOTSTRAP_REPS, seed=0):
    """Differenza A - B partita per partita (negativa = A migliore), con
    intervallo al 95% ricampionando settimane intere (le partite della stessa
    giornata non sono indipendenti)."""
    diffs, weeks = [], []
    for r in records:
        pa, pb = prob_a(r), prob_b(r)
        if pa is None or pb is None:
            continue
        o = r["result"]
        if metric == "log_loss":
            diffs.append(-math.log(max(pa[o], 1e-12)) + math.log(max(pb[o], 1e-12)))
        else:
            diffs.append(sum((pa[k] - (k == o)) ** 2 - (pb[k] - (k == o)) ** 2 for k in range(3)))
        y, w, _ = date.fromisoformat(r["date"]).isocalendar()
        weeks.append((y, w))
    if len(diffs) < 50:
        return None
    diffs = np.array(diffs)
    ids = {w: i for i, w in enumerate(sorted(set(weeks)))}
    wk = np.array([ids[w] for w in weeks])
    sums, counts = np.bincount(wk, weights=diffs), np.bincount(wk)
    pick = np.random.default_rng(seed).integers(0, len(sums), size=(reps, len(sums)))
    boot = sums[pick].sum(axis=1) / counts[pick].sum(axis=1)
    lo, hi = np.percentile(boot, [2.5, 97.5])
    return {"partite": int(len(diffs)), "differenza": round(float(diffs.mean()), 5),
            "da": round(float(lo), 5), "a": round(float(hi), 5),
            "significativa": bool(hi < 0 or lo > 0)}


def calibration_error(recs, prob):
    """Errore medio di calibrazione (ECE) sui tre esiti, a fasce del 10%."""
    pairs = [(prob(r)[k], int(r["result"] == k)) for r in recs for k in range(3)]
    tot, err = len(pairs), 0.0
    for i in range(10):
        sel = [(p, y) for p, y in pairs if i / 10 <= p < (i + 1) / 10 or (i == 9 and p == 1)]
        if sel:
            err += len(sel) / tot * abs(np.mean([p for p, _ in sel]) - np.mean([y for _, y in sel]))
    return float(err)


def adoption_check(recs, cand, base):
    """La regola di adozione (decisa prima di vedere i risultati)."""
    ll = paired_comparison(recs, cand, base)
    br = paired_comparison(recs, cand, base, metric="brier")
    if not ll or not br:
        return None
    ece_c, ece_b = calibration_error(recs, cand), calibration_error(recs, base)
    per_lega = {}
    for l in LEAGUE_NAMES:
        c = paired_comparison([r for r in recs if r["league"] == l], cand, base, reps=200)
        if c:
            per_lega[l] = c["differenza"]
    migliori = sorted(per_lega, key=per_lega.get)
    senza_migliore = paired_comparison([r for r in recs if not migliori or r["league"] != migliori[0]],
                                       cand, base, reps=200)
    regole = {
        "log loss migliore, intervallo tutto sotto zero": ll["a"] < 0,
        "Brier non peggiora": br["differenza"] <= 0,
        "calibrazione non peggiora": ece_c <= ece_b + 0.005,
        "migliora in almeno 3 campionati su 5": sum(v < 0 for v in per_lega.values()) >= 3,
        "migliora anche senza il campionato migliore": bool(senza_migliore and senza_migliore["differenza"] < 0),
    }
    return {"log_loss": ll, "brier": br,
            "calibrazione": {"candidato": round(ece_c, 4), "pinnacle": round(ece_b, 4)},
            "per_campionato": {k: round(v, 5) for k, v in per_lega.items()},
            "regole": regole, "adottare": all(regole.values())}


def pinnacle_coverage(records):
    """Quante partite hanno le quote Pinnacle (mattino e chiusura), e se
    quelle senza sono diverse dalle altre (es. più squilibrate)."""
    def fav(r):
        p = devig(r["odds_avg"]) if r.get("odds_avg") and all(r["odds_avg"]) else None
        return max(p) if p else None
    has = lambda r: bool(r.get("pin_pre") and r.get("pin_close"))

    def table(keyf):
        groups = {}
        for r in records:
            groups.setdefault(keyf(r), []).append(r)
        return [{"gruppo": k, "partite": len(v), "con_pinnacle": sum(map(has, v)),
                 "copertura": round(sum(map(has, v)) / len(v), 3)} for k, v in sorted(groups.items())]

    def mean_fav(rs):
        v = [fav(r) for r in rs if fav(r) is not None]
        return round(float(np.mean(v)), 3) if v else None
    con = [r for r in records if has(r)]
    senza = [r for r in records if not has(r)]
    season = lambda r: (f"{int(r['date'][:4]) - (r['date'][5:7] < '07')}-"
                        f"{str(int(r['date'][:4]) - (r['date'][5:7] < '07') + 1)[-2:]}")
    return {"per_stagione": table(season), "per_campionato": table(lambda r: r["league"]),
            "per_mese": table(lambda r: r["date"][:7]),
            "favorita_media": {"con_pinnacle": mean_fav(con), "senza_pinnacle": mean_fav(senza)}}


def summarize_v3(records):
    keys = residual_walk_forward(records)
    pin = lambda r: r.get("pin_pre_pow")
    dev = [r for r in records if r["period"] == "sviluppo" and r.get("pin_pre_pow")]
    # stesse partite per tutte le varianti: quelle dove esistono tutte
    dev_all = [r for r in dev if all(k in r.get("pr", {}) for k in keys)]
    out = [r["result"] for r in dev_all]
    varianti = []
    for k in keys:
        c = paired_comparison(dev_all, lambda r, k=k: r["pr"][k], pin)
        varianti.append({"variante": k,
                         "log_loss": round(log_loss([r["pr"][k] for r in dev_all], out), 5) if dev_all else None,
                         "contro_pinnacle": c})
    scelta = min(varianti, key=lambda v: v["log_loss"])["variante"] if dev_all else None
    ref = {
        "partite": len(dev_all),
        "pinnacle_mattino": round(log_loss([r["pin_pre_pow"] for r in dev_all], out), 5) if dev_all else None,
        "pinnacle_chiusura": round(log_loss([r["pin_close_pow"] for r in dev_all
                                             if r.get("pin_close_pow")],
                                            [r["result"] for r in dev_all if r.get("pin_close_pow")]), 5)
                             if dev_all else None,
        "dixon_coles": round(log_loss([r["p"] for r in dev_all], out), 5) if dev_all else None,
    }
    anteprima = (adoption_check(dev_all, lambda r: r["pr"][scelta], pin) if scelta else None)

    hold = [r for r in records if r["period"] == "holdout" and r.get("pin_pre_pow")
            and scelta and scelta in r.get("pr", {})]
    holdout = {"aperto": OPEN_HOLDOUT, "da": HOLDOUT_FROM.isoformat(), "partite_con_pinnacle": len(hold)}
    if OPEN_HOLDOUT and hold:
        holdout["esito"] = adoption_check(hold, lambda r: r["pr"][scelta], pin)
    return {
        "periodo_sviluppo": {"da": TEST_FROM.isoformat(),
                             "a": (HOLDOUT_FROM - timedelta(days=1)).isoformat()},
        "riferimenti": ref, "varianti": varianti, "variante_scelta": scelta,
        "anteprima_regola_sviluppo": anteprima, "holdout": holdout,
        "copertura_pinnacle": pinnacle_coverage([r for r in records if r["period"] != "prima"]),
    }


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
        # stesse strategie, con il margine di Pinnacle tolto col metodo "potenza"
        # e divise per fascia di quota: se il vantaggio sparisce, era un
        # artefatto del calcolo e non valore vero
        "1x2_quota_massima_potenza": sharp_vs_soft(records, "odds_max", "pin_pre_pow",
                                                   "pin_close_pow", lambda r: r["result"], 3,
                                                   by_odds=True),
        "ou25_quota_massima_potenza": sharp_vs_soft(records, "odds_ou_max", "pin_pre_ou_pow",
                                                    "pin_close_ou_pow", lambda r: 1 - r["over25"], 2),
    }
    return report


def main():
    everything, per_league = [], {}
    for code, league in LEAGUES.items():
        print(f"{league}: scarico lo storico e simulo mese per mese...")
        df = load_league(code)
        if df.empty:
            continue
        recs = walk_forward(df, league)
        everything += recs
        legacy = [r for r in recs if r["period"] != "prima"]
        if legacy:
            per_league[league] = summarize(legacy)["1x2"]["log_loss"]
    # il report "classico" resta sullo stesso periodo di prima (da TEST_FROM)
    all_records = [r for r in everything if r["period"] != "prima"]
    if not all_records:
        print("Nessuna partita valutata: niente report.")
        return
    report = summarize(all_records)
    report["per_campionato_log_loss"] = per_league
    print("\nVersione 3: alleno il modello a correzione mese per mese...")
    report["v3"] = summarize_v3(everything)
    report["periodo"] = {"da": min(r["date"] for r in all_records),
                         "a": max(r["date"] for r in all_records)}
    report["generato"] = datetime.now(timezone.utc).isoformat()
    with open(REPORT_PATH, "w") as f:
        json.dump(report, f, indent=1, ensure_ascii=False)
    print(json.dumps({k: report[k] for k in ("partite_valutate", "periodo")}, ensure_ascii=False))
    print("log loss 1X2:", report["1x2"]["log_loss"])
    print("miglior peso del modello nella miscela:", report["1x2"]["peso_modello_migliore"])
    v3 = report["v3"]
    print(f"\nVERSIONE 3 — sviluppo {v3['periodo_sviluppo']['da']} / {v3['periodo_sviluppo']['a']}")
    print("riferimenti (stesse partite):", v3["riferimenti"])
    print("varianti del modello a correzione (differenza di log loss contro Pinnacle mattino):")
    for v in v3["varianti"]:
        c = v["contro_pinnacle"]
        print(f"  {v['variante']:<48} {v['log_loss']}  "
              + (f"{c['differenza']:+.5f} (95%: {c['da']:+.5f} / {c['a']:+.5f})"
                 + ("  SIGNIFICATIVA" if c["significativa"] else "") if c else "")
              + ("   <- scelta" if v["variante"] == v3["variante_scelta"] else ""))
    a = v3["anteprima_regola_sviluppo"]
    if a:
        print("regola di adozione sullo SVILUPPO (solo indicativa, non decide niente):")
        for k, ok in a["regole"].items():
            print(f"  {'OK ' if ok else 'NO '} {k}")
        print("  per campionato:", a["per_campionato"], " calibrazione:", a["calibrazione"])
    print("holdout:", v3["holdout"])
    cov = v3["copertura_pinnacle"]
    print("copertura Pinnacle per stagione:", [(g["gruppo"], g["copertura"]) for g in cov["per_stagione"]])
    print("copertura Pinnacle per campionato:", [(g["gruppo"], g["copertura"]) for g in cov["per_campionato"]])
    print("copertura Pinnacle per mese:", [(g["gruppo"], g["copertura"]) for g in cov["per_mese"]])
    print("probabilità media della favorita, con / senza Pinnacle:", cov["favorita_media"])
    print("scommesse 1X2 per fascia di EV:")
    for b in report["1x2"]["scommesse_modello"]:
        print("  ", b)
    print("strategia senza modello: bookmaker contro Pinnacle")
    for name, rows in report["contro_pinnacle"].items():
        for b in rows:
            print("  ", name, b)


if __name__ == "__main__":
    main()
