"""
Esplorazione BSD (Bzzoiro Sports Data): invece di indovinare di nuovo i
nomi degli endpoint, chiediamo all'API cosa c'è davvero.
NON tocca data.db. BSD dichiara nessun limite di richieste.
"""

import json
import os

import requests

BASE = "https://sports.bzzoiro.com/api/v2"
HEAD = {"Authorization": f"Token {os.environ.get('BSD_API_KEY', '')}"}


def get(path, **params):
    r = requests.get(f"{BASE}{path}", headers=HEAD, params=params, timeout=30)
    try:
        data = r.json()
    except ValueError:
        data = r.text[:300]
    return r.status_code, data


def items(data):
    if isinstance(data, dict) and "results" in data:
        return data["results"]
    return data if isinstance(data, list) else []


def show(title, status, data, n=3):
    print(f"\n=== {title} -> {status}")
    lst = items(data)
    if isinstance(data, dict) and "count" in data:
        print(f"  totale: {data['count']}")
    if lst:
        print(f"  campi: {sorted(lst[0].keys()) if isinstance(lst[0], dict) else type(lst[0])}")
        for x in lst[:n]:
            print("  ", json.dumps(x, ensure_ascii=False)[:400])
    else:
        print("  ", json.dumps(data, ensure_ascii=False)[:600])


# 1. Campionati: cerchiamo gli ID dei 5 principali
s, d = get("/leagues/", limit=200)
show("/leagues/", s, d, n=0)
for lg in items(d):
    txt = json.dumps(lg, ensure_ascii=False).lower()
    if any(k in txt for k in ("serie a", "premier league", "laliga", "la liga",
                              "bundesliga", "ligue 1")):
        print("   ", json.dumps(lg, ensure_ascii=False)[:250])

# 2. Squadre per nome (il parametro giusto è 'name')
for nome in ("Inter", "Manchester City", "Man City"):
    s, d = get("/teams/", name=nome, limit=5)
    show(f"/teams/?name={nome}", s, d)

# 3. Rosa e statistiche di una squadra: proviamo le varianti possibili
s, d = get("/teams/", name="Inter", limit=5)
team = next(iter(items(d)), None)
if team:
    tid = team["id"]
    player = None
    for path, params in ((f"/teams/{tid}/players/", {}),
                         ("/players/", {"team_id": tid}),
                         ("/players/", {"team": tid}),
                         (f"/teams/{tid}/squad/", {}),
                         (f"/teams/{tid}/", {})):
        s, d = get(path, **params)
        show(f"{path} {params}", s, d, n=2)
        if s == 200 and items(d) and player is None and "players" in path:
            player = items(d)[0]
    if player:
        pid = player.get("id") or player.get("player_id")
        for path in (f"/players/{pid}/", f"/players/{pid}/stats/",
                     f"/players/{pid}/season-stats/"):
            s, d = get(path)
            show(path, s, d, n=2)
        s, d = get("/player-stats/", player_id=pid, limit=3)
        show("/player-stats/?player_id=", s, d, n=2)

print("\nFatto.")
