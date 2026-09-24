"""
ESPLORAZIONE BSD: i RIGORI si possono ricostruire?
(prima di cambiare il modello marcatori verifichiamo i dati, come da piano)

Per ~30 partite recenti dei 5 campionati guarda:
  - gli "incidents" di ogni partita (oggi usiamo solo le sostituzioni):
    che tipi esistono, che campi hanno, e quali parlano di rigori
    (segnati, parati, sbagliati) e con quale giocatore;
  - le statistiche giocatore per partita: se c'è qualche campo sui rigori
    o un xG senza rigori (npxG);
  - altre sotto-risorse della partita (tiri, formazioni) e se le
    formazioni esistono anche PRIMA della partita (servirà per i minuti).
Stampa un riassunto nel log. NON tocca data.db e non salva niente.
Si avvia a mano dal workflow "Esplora dati BSD".
"""

import json
import os
import sqlite3
from collections import Counter, defaultdict

import requests

BASE = "https://sports.bzzoiro.com/api/v2"
HEAD = {"Authorization": f"Token {os.environ.get('BSD_API_KEY', '')}"}
N_EVENTS = 30


def get(path, **params):
    try:
        r = requests.get(f"{BASE}{path}", headers=HEAD, params=params, timeout=30)
    except requests.RequestException as e:
        return None, str(e)
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, r.text[:200]


def short(x, width=600):
    return json.dumps(x, ensure_ascii=False)[:width]


PEN_WORDS = ("penal", "pen_", "rigor", "11m")


def looks_penalty(obj):
    """Un incidente parla di rigore se un campo col nome "penalty..." è vero,
    o se un testo (tipo, descrizione) contiene la parola."""
    if not isinstance(obj, dict):
        return False
    for k, v in obj.items():
        if any(w in k.lower() for w in PEN_WORDS) and v not in (False, None, 0, "", "false", "False"):
            return True
        if isinstance(v, str) and any(w in v.lower() for w in PEN_WORDS):
            return True
        if isinstance(v, dict) and looks_penalty(v):
            return True
    return False


def list_of(data, *keys):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in keys:
            if isinstance(data.get(k), list):
                return data[k]
    return []


def main():
    if not HEAD["Authorization"].split()[-1]:
        raise SystemExit("Manca BSD_API_KEY.")
    conn = sqlite3.connect("data.db")
    # partite recenti dei 5 campionati, già scaricate (quindi con statistiche)
    events = conn.execute("""SELECT id, league, event_date, home_team_name, away_team_name,
                                    home_score, away_score
                             FROM bsd_events
                             WHERE league IN ('Serie A','Premier League','La Liga','Bundesliga','Ligue 1')
                               AND home_score IS NOT NULL
                             ORDER BY event_date DESC LIMIT ?""", (N_EVENTS,)).fetchall()
    conn.close()
    print(f"Partite esaminate: {len(events)}\n")

    inc_types = Counter()
    inc_fields = defaultdict(set)
    inc_examples = {}
    pen_incidents = []
    stat_fields = Counter()
    stat_pen_fields = set()
    stat_examples = []
    sub_status = defaultdict(Counter)
    goals_total, pen_goals = 0, 0

    for eid, league, edate, home, away, hs, as_ in events:
        s, inc = get(f"/events/{eid}/incidents/")
        for x in list_of(inc, "incidents", "results"):
            t = str(x.get("type"))
            inc_types[t] += 1
            inc_fields[t] |= set(x.keys())
            inc_examples.setdefault(t, x)
            if looks_penalty(x):
                pen_incidents.append((f"{league} {edate[:10]} {home}-{away} {hs}-{as_}", x))
            if "goal" in t.lower():
                goals_total += 1
                pen_goals += looks_penalty(x)
        s, st = get(f"/events/{eid}/player-stats/")
        for p in list_of(st, "player_stats", "results"):
            stat_fields.update(p.keys())
            for k, v in p.items():
                if any(w in k.lower() for w in ("pen", "npxg", "non_pen", "nonpen")):
                    stat_pen_fields.add(k)
            if len(stat_examples) < 1 and (p.get("goals") or 0) > 0:
                stat_examples.append(p)
        for sub in ("lineups/", "shotmap/", "shots/", "statistics/", "stats/"):
            s, d = get(f"/events/{eid}/{sub}")
            sub_status[sub][s] += 1
            if s == 200 and sub not in inc_examples:
                inc_examples["__" + sub] = d

    print("=== INCIDENTS: tipi trovati (quante volte) ===")
    for t, n in inc_types.most_common():
        print(f"  {t}: {n}   campi: {sorted(inc_fields[t])}")
    print("\n=== INCIDENTS: un esempio per tipo ===")
    for t, x in inc_examples.items():
        if not t.startswith("__"):
            print(f"  [{t}] {short(x)}")
    print(f"\n=== INCIDENTS che sembrano rigori: {len(pen_incidents)} "
          f"(gol totali {goals_total}, gol che sembrano su rigore {pen_goals}) ===")
    for where, x in pen_incidents[:25]:
        print(f"  {where}: {short(x, 400)}")

    print("\n=== STATISTICHE GIOCATORE: campi (in quante righe) ===")
    print("  " + ", ".join(f"{k} ({n})" for k, n in sorted(stat_fields.items())))
    print(f"  campi che parlano di rigori / npxG: {sorted(stat_pen_fields) or 'nessuno'}")
    if stat_examples:
        print(f"  esempio (giocatore che ha segnato): {short(stat_examples[0], 1200)}")

    print("\n=== ALTRE SOTTO-RISORSE DELLA PARTITA (codici di risposta) ===")
    for sub, c in sub_status.items():
        print(f"  /events/{{id}}/{sub}: {dict(c)}")
    for k, d in inc_examples.items():
        if k.startswith("__"):
            print(f"  esempio {k[2:]}: {short(d, 900)}")

    # formazioni PRIMA della partita: prova sulle prossime partite dei 5 campionati
    print("\n=== FORMAZIONI PRIMA DELLA PARTITA? ===")
    for league_id in (4, 1):
        s, d = get("/events/", league_id=league_id, status="notstarted", limit=3)
        evs = list_of(d, "results")
        if not evs:
            s, d = get("/events/", league_id=league_id, limit=3)
            evs = list_of(d, "results")
        for e in evs[:3]:
            s2, d2 = get(f"/events/{e['id']}/lineups/")
            print(f"  {e.get('home_team')} - {e.get('away_team')} ({e.get('event_date')}, "
                  f"stato {e.get('status')}): lineups -> {s2} {short(d2, 300)}")
    print("\nFatto.")


if __name__ == "__main__":
    main()
