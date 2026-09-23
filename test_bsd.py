"""
Esplorazione BSD, secondo giro. Dal primo abbiamo scoperto:
  - ID campionati: Serie A 4, Premier 1, La Liga 3, Bundesliga 5, Ligue 1 6
  - rosa: /players/?team_id=X ; statistiche: /players/{id}/stats/ (partita
    per partita, ma SENZA data e mescolando più stagioni)
Ora verifichiamo: squadre per campionato, partite giocate con data, e se
esistono statistiche giocatore PER PARTITA (molte meno richieste).
NON tocca data.db.
"""

import json
import os
from datetime import date, timedelta

import requests

BASE = "https://sports.bzzoiro.com/api/v2"
HEAD = {"Authorization": f"Token {os.environ.get('BSD_API_KEY', '')}"}


def get(path, **params):
    r = requests.get(f"{BASE}{path}", headers=HEAD, params=params, timeout=30)
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, r.text[:300]


def items(data):
    if isinstance(data, dict) and "results" in data:
        return data["results"]
    return data if isinstance(data, list) else []


def show(title, status, data, n=2, width=500):
    print(f"\n=== {title} -> {status}")
    lst = items(data)
    if isinstance(data, dict) and "count" in data:
        print(f"  totale: {data['count']}")
    if lst and isinstance(lst[0], dict):
        print(f"  campi: {sorted(lst[0].keys())}")
        for x in lst[:n]:
            print("  ", json.dumps(x, ensure_ascii=False)[:width])
    elif isinstance(data, dict):
        print(f"  campi: {sorted(data.keys())}")
        print("  ", json.dumps(data, ensure_ascii=False)[:width])
    else:
        print("  ", str(data)[:width])


def accepted(path):
    """Un parametro inventato fa rispondere all'API con quelli accettati."""
    s, d = get(path, parametro_inesistente=1)
    acc = d.get("accepted_parameters") if isinstance(d, dict) else None
    print(f"\n=== parametri accettati da {path}: {acc if acc else (s, str(d)[:200])}")


# 1. Squadre della Serie A
s, d = get("/teams/", league_id=4, limit=50)
print(f"\n=== /teams/?league_id=4 -> {s}, totale {d.get('count') if isinstance(d, dict) else '?'}")
teams = items(d)
print("  ", ", ".join(f"{t['name']} ({t['id']})" for t in teams))

# 2. Parametri accettati dagli endpoint che ci servono
accepted("/events/")
if teams:
    s, d = get("/players/", team_id=teams[0]["id"], limit=1)
    pl = next(iter(items(d)), None)
    if pl:
        accepted(f"/players/{pl['id']}/stats/")

# 3. Partite di Serie A già giocate nelle ultime 3 settimane
oggi = date.today()
s, d = get("/events/", league_id=4, date_from=(oggi - timedelta(days=21)).isoformat(),
           date_to=oggi.isoformat(), limit=50)
show("/events/?league_id=4 ultime 3 settimane", s, d, n=2)
finite = [e for e in items(d)
          if str(e.get("status", "")).lower() in ("finished", "ft", "ended", "completed")]
print(f"  di cui finite: {len(finite)}")

# 4. Sotto-risorse di una partita finita
ev = (finite or items(d) or [None])[0]
if ev:
    eid = ev["id"]
    for sub in ("", "player-stats/", "players/", "player_stats/", "stats/",
                "lineups/", "incidents/", "odds/"):
        s, d = get(f"/events/{eid}/{sub}")
        show(f"/events/{eid}/{sub}", s, d, n=2, width=700)

print("\nFatto.")
