"""
BACKTEST DEI MARCATORI — M0 / M1 / M2
(il modello marcatori è l'unico posto dove Pinnacle non ci dà già il prezzo
giusto: qui un modello migliore può fare davvero la differenza)

Per ogni partita giocata dei 5 campionati, e per ogni giocatore sceso in
campo, stima la probabilità che segni almeno un gol usando SOLO le partite
precedenti, e la confronta con quello che è successo. Si valutano solo i
giocatori scesi in campo perché la scommessa "marcatore" viene rimborsata
se il giocatore non gioca.

Varianti (stessi gol attesi della squadra, stessi minuti attesi per tutte):
  M0  come l'app oggi: 65% xG/90 + 35% gol/90 (rigori inclusi in entrambi)
  M1  npxG/90 (xG senza rigori): i rigori della squadra finiscono ripartiti
      come il resto, in proporzione agli npxG
  M2  npxG/90 + COMPONENTE RIGORI separata:
        lambda = lambda su azione + rigori attesi della squadra
                 × probabilità di essere il rigorista × presenza in campo
                 × probabilità di trasformazione
      con la probabilità di rigorista stimata dai rigori segnati (BSD non
      registra quelli sbagliati) e "ristretta" verso la media: 1 su 1 non
      diventa 100% rigorista.
  M2 con gol "veri" mescolati agli npxG: 90/10 e 75/25 (gol senza rigori).

Periodi: SVILUPPO fino al 31/07/2026 per scegliere; HOLDOUT da agosto 2026,
chiuso finché OPEN_HOLDOUT resta False.
Metriche: log loss e Brier (più bassi = meglio), calibrazione per fasce.
Usa solo data.db (niente richieste), scrive scorer_backtest_report.json.
"""

import json
import math
import sqlite3
from collections import defaultdict, deque
from datetime import date, datetime, timedelta, timezone

import numpy as np

from model import DixonColesModel

DB_PATH = "data.db"
REPORT_PATH = "scorer_backtest_report.json"
LEAGUES = ["Serie A", "Premier League", "La Liga", "Bundesliga", "Ligue 1"]
HOLDOUT_FROM = "2026-08-01"
OPEN_HOLDOUT = False
STATS_WINDOW_DAYS = 365
RECENT_TEAM_MATCHES = 5
PRIOR_MINUTES = 450          # come l'app
PRIOR_PER_90 = 0.10
PEN_XG = 0.79                # xG di un rigore
PEN_CONVERSION = 0.78        # rigori trasformati (media dei grandi campionati)
TAKER_PRIOR = 1.0            # "rigori fittizi" distribuiti in proporzione agli npxG
MIN_HISTORY_DAYS = 60        # prima di prevedere serve un po' di storia
BANDS = [0, 0.1, 0.2, 0.3, 0.4, 0.5, 1.01]
BOOTSTRAP_REPS = 2000

VARIANTS = {
    "M0 attuale (xG 65% + gol 35%)": {"kind": "m0"},
    "M1 npxG": {"kind": "np", "w_goals": 0.0, "pens": False},
    "M2 npxG + rigori": {"kind": "np", "w_goals": 0.0, "pens": True},
    "M2 npxG 90% + gol 10% + rigori": {"kind": "np", "w_goals": 0.10, "pens": True},
    "M2 npxG 75% + gol 25% + rigori": {"kind": "np", "w_goals": 0.25, "pens": True},
}
BASE = "M0 attuale (xG 65% + gol 35%)"


def load(conn):
    events = conn.execute(f"""
        SELECT e.id, e.league, substr(e.event_date, 1, 10), e.home_bsd_team_id, e.away_bsd_team_id,
               e.match_id, m.home_team_id, m.away_team_id
        FROM bsd_events e JOIN matches m ON m.id = e.match_id
        WHERE e.stats_done = 1 AND e.pens_done = 1 AND e.home_score IS NOT NULL
          AND e.league IN ({','.join('?' * len(LEAGUES))})
        ORDER BY e.event_date, e.id""", LEAGUES).fetchall()
    rows = conn.execute("""
        SELECT match_bsd_id, player_id, bsd_team_id, minutes, goals, COALESCE(xg, 0),
               COALESCE(penalties_scored, 0), xg IS NULL AND shots > 0
        FROM player_match_stats WHERE bsd_team_id IS NOT NULL""").fetchall()
    by_event = defaultdict(list)
    for r in rows:
        by_event[r[0]].append(r[1:])
    return events, by_event


def team_goal_expectations(conn, events):
    """Gol attesi di ogni squadra per ogni partita, dal Dixon-Coles dell'app
    allenato mese per mese solo sulle partite precedenti (come il backtest 1X2)."""
    out = {}
    months = sorted({e[2][:7] for e in events})
    for league in LEAGUES:
        hist = conn.execute("""SELECT date, home_team_id, away_team_id, home_goals, away_goals
                               FROM matches WHERE league = ? AND home_goals IS NOT NULL""",
                            (league,)).fetchall()
        for m in months:
            start = date.fromisoformat(m + "-01")
            train = [h for h in hist if h[0] < start.isoformat()
                     and h[0] >= (start - timedelta(days=3 * 365)).isoformat()]
            todo = [e for e in events if e[1] == league and e[2][:7] == m]
            if not todo or len(train) < 300:
                continue
            model = DixonColesModel().fit([
                {"home_team": h[1], "away_team": h[2], "home_goals": h[3], "away_goals": h[4],
                 "days_ago": (start - date.fromisoformat(h[0][:10])).days} for h in train])
            for e in todo:
                if e[6] in model.teams and e[7] in model.teams:
                    out[e[0]] = model.expected_goals(e[6], e[7])
    return out


def predict_all(conn):
    events, by_event = load(conn)
    lam_team = team_goal_expectations(conn, events)
    history = defaultdict(deque)          # giocatore -> (data, min, gol, xg, rigori, xg_mancante)
    team_events = defaultdict(list)       # squadra BSD -> [(data, id evento)]
    league_pen = defaultdict(lambda: [0, 0])   # campionato -> [rigori segnati, partite-squadra]
    preds = []
    first_date = events[0][2] if events else None

    for eid, league, d, home_b, away_b, match_id, h_id, a_id in events:
        rows = by_event.get(eid, [])
        ok = (eid in lam_team and rows and first_date
              and (date.fromisoformat(d) - date.fromisoformat(first_date)).days >= MIN_HISTORY_DAYS)
        if ok:
            lp = league_pen[league]
            # rigori (tentati) attesi per squadra e partita, dalle partite precedenti
            pen_attempts = (lp[0] / PEN_CONVERSION + 2) / (lp[1] + 20)   # prior leggero
            for side, team_b, lam in ((0, home_b, lam_team[eid][0]), (1, away_b, lam_team[eid][1])):
                recent = [x for x in team_events[team_b] if x[0] < d][-RECENT_TEAM_MATCHES:]
                if len(recent) < RECENT_TEAM_MATCHES:
                    continue
                recent_ids = {x[1] for x in recent}
                pool = {}
                cutoff = (date.fromisoformat(d) - timedelta(days=STATS_WINDOW_DAYS)).isoformat()
                # giocatori con minuti nelle ultime partite della squadra
                recent_minutes = defaultdict(int)
                for rid in recent_ids:
                    for pid, tb, mins, *_ in by_event.get(rid, []):
                        if tb == team_b:
                            recent_minutes[pid] += mins
                for pid, rm in recent_minutes.items():
                    em = min(rm / RECENT_TEAM_MATCHES, 90)
                    h = [x for x in history[pid] if x[0] >= cutoff]
                    mins = sum(x[1] for x in h)
                    if em <= 0 or not mins:
                        continue
                    mins_xg = sum(x[1] for x in h if not x[5])
                    xg = sum(x[3] for x in h if not x[5])
                    goals = sum(x[2] for x in h)
                    pens = sum(x[4] for x in h)
                    pens_xgok = sum(x[4] for x in h if not x[5])
                    k = PRIOR_MINUTES
                    shrink = lambda num, den: (num + PRIOR_PER_90 * k / 90) / (den + k) * 90
                    pool[pid] = {
                        "em": em,
                        "xg90": shrink(xg, mins_xg), "g90": shrink(goals, mins),
                        "npxg90": shrink(max(xg - PEN_XG * pens_xgok, 0), mins_xg),
                        "npg90": shrink(goals - pens, mins),
                        "pens": pens,
                    }
                if not pool:
                    continue
                played = {r[0]: r for r in rows if r[1] == team_b and r[2] > 0}
                probs = {}
                for name, v in VARIANTS.items():
                    if v["kind"] == "m0":
                        sc = {p: (0.65 * s["xg90"] + 0.35 * s["g90"]) * s["em"] / 90 for p, s in pool.items()}
                        tot = sum(sc.values())
                        lam_i = {p: lam * x / tot for p, x in sc.items()} if tot else {}
                    else:
                        w = v["w_goals"]
                        sc = {p: ((1 - w) * s["npxg90"] + w * s["npg90"]) * s["em"] / 90
                              for p, s in pool.items()}
                        tot = sum(sc.values())
                        if not tot:
                            continue
                        if not v["pens"]:
                            lam_i = {p: lam * x / tot for p, x in sc.items()}
                        else:
                            lam_pen = min(pen_attempts * PEN_CONVERSION, lam * 0.5)
                            lam_np = max(lam - lam_pen, 0.05)
                            share = {p: x / tot for p, x in sc.items()}
                            # probabilità di rigorista × presenza in campo, normalizzata
                            tk = {p: (s["pens"] + TAKER_PRIOR * share[p]) * min(s["em"] / 90, 1)
                                  for p, s in pool.items()}
                            tt = sum(tk.values())
                            lam_i = {p: lam_np * share[p] + lam_pen * tk[p] / tt for p in pool}
                    probs[name] = lam_i
                for pid, r in played.items():
                    if pid not in pool or any(pid not in probs[n] for n in probs):
                        continue
                    preds.append({
                        "event": eid, "date": d, "league": league, "player": pid,
                        "scored": int(r[3] > 0), "pens": r[5],
                        "p": {n: 1 - math.exp(-probs[n][pid]) for n in probs},
                    })
        # aggiorno la storia DOPO aver previsto la partita
        for pid, tb, mins, goals, xg, pens, xg_missing in rows:
            if mins > 0:
                history[pid].append((d, mins, goals, xg, pens, bool(xg_missing)))
        for tb in (home_b, away_b):
            team_events[tb].append((d, eid))
            league_pen[league][1] += 1
        league_pen[league][0] += sum(r[5] for r in rows)
    return preds


def bin_ll(p, y):
    p = min(max(p, 1e-9), 1 - 1e-9)
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


def paired(preds, a, b, metric):
    f = bin_ll if metric == "log_loss" else (lambda p, y: (p - y) ** 2)
    diffs = np.array([f(x["p"][a], x["scored"]) - f(x["p"][b], x["scored"]) for x in preds])
    weeks = [date.fromisoformat(x["date"]).isocalendar()[:2] for x in preds]
    ids = {w: i for i, w in enumerate(sorted(set(weeks)))}
    wk = np.array([ids[w] for w in weeks])
    sums, counts = np.bincount(wk, weights=diffs), np.bincount(wk)
    pick = np.random.default_rng(0).integers(0, len(sums), size=(BOOTSTRAP_REPS, len(sums)))
    boot = sums[pick].sum(axis=1) / counts[pick].sum(axis=1)
    lo, hi = np.percentile(boot, [2.5, 97.5])
    return {"differenza": round(float(diffs.mean()), 5), "da": round(float(lo), 5),
            "a": round(float(hi), 5), "significativa": bool(hi < 0 or lo > 0)}


def calibration(preds, name):
    out = []
    for lo, hi in zip(BANDS[:-1], BANDS[1:]):
        sel = [x for x in preds if lo <= x["p"][name] < hi]
        if len(sel) >= 30:
            out.append({"fascia": f"{lo:.0%}-{min(hi, 1):.0%}" if hi < 1 else f"oltre {lo:.0%}",
                        "n": len(sel),
                        "prevista": round(float(np.mean([x["p"][name] for x in sel])), 3),
                        "reale": round(float(np.mean([x["scored"] for x in sel])), 3)})
    return out


def summarize(preds):
    rep = {}
    for period, sel in (("sviluppo", [x for x in preds if x["date"] < HOLDOUT_FROM]),
                        ("holdout", [x for x in preds if x["date"] >= HOLDOUT_FROM])):
        if period == "holdout" and not OPEN_HOLDOUT:
            rep["holdout"] = {"aperto": False, "da": HOLDOUT_FROM, "previsioni": len(sel)}
            continue
        if not sel:
            continue
        names = [n for n in VARIANTS if all(n in x["p"] for x in sel)]
        var = []
        for n in names:
            var.append({
                "variante": n,
                "log_loss": round(float(np.mean([bin_ll(x["p"][n], x["scored"]) for x in sel])), 5),
                "brier": round(float(np.mean([(x["p"][n] - x["scored"]) ** 2 for x in sel])), 5),
                "prevista_media": round(float(np.mean([x["p"][n] for x in sel])), 4),
                "contro_M0_log_loss": None if n == BASE else paired(sel, n, BASE, "log_loss"),
                "contro_M0_brier": None if n == BASE else paired(sel, n, BASE, "brier"),
                "calibrazione": calibration(sel, n),
            })
        rep[period] = {"previsioni": len(sel), "partite": len({x["event"] for x in sel}),
                       "frequenza_reale": round(float(np.mean([x["scored"] for x in sel])), 4),
                       "da": min(x["date"] for x in sel), "a": max(x["date"] for x in sel),
                       "varianti": var,
                       "scelta": min(var, key=lambda v: v["log_loss"])["variante"]}
    rep["generato"] = datetime.now(timezone.utc).isoformat()
    return rep


def main():
    conn = sqlite3.connect(DB_PATH)
    pronte = conn.execute("SELECT COUNT(*) FROM bsd_events WHERE stats_done = 1 AND pens_done = 1").fetchone()[0]
    mancanti = conn.execute("SELECT COUNT(*) FROM bsd_events WHERE stats_done = 1 AND pens_done = 0").fetchone()[0]
    print(f"Partite con rigori e squadra: {pronte} (ancora da completare: {mancanti})")
    preds = predict_all(conn)
    rep = summarize(preds)
    rep["partite_da_completare"] = mancanti
    with open(REPORT_PATH, "w") as f:
        json.dump(rep, f, indent=1, ensure_ascii=False)
    dev = rep.get("sviluppo")
    if not dev:
        print("Nessuna previsione nel periodo di sviluppo.")
        return
    print(f"SVILUPPO {dev['da']} / {dev['a']}: {dev['previsioni']} previsioni su {dev['partite']} partite, "
          f"hanno segnato il {dev['frequenza_reale']:.1%}")
    for v in dev["varianti"]:
        c, b = v["contro_M0_log_loss"], v["contro_M0_brier"]
        print(f"  {v['variante']:<36} log loss {v['log_loss']}  Brier {v['brier']}  "
              f"prevista media {v['prevista_media']:.1%}"
              + (f"\n      contro M0: log loss {c['differenza']:+.5f} (95%: {c['da']:+.5f} / {c['a']:+.5f})"
                 f"{' SIGNIFICATIVA' if c['significativa'] else ''}, Brier {b['differenza']:+.5f}"
                 f"{' SIGNIFICATIVA' if b['significativa'] else ''}" if c else ""))
        print("      calibrazione: " + ", ".join(f"{k['fascia']} {k['prevista']:.0%}->{k['reale']:.0%} (n={k['n']})"
                                             for k in v["calibrazione"]))
    print("scelta sullo sviluppo:", dev["scelta"])
    print("holdout:", rep.get("holdout"))


if __name__ == "__main__":
    main()
