"""
Quali mercati offrono davvero i bookmaker italiani su OddsPapi?
Cerchiamo: doppia chance (1X, X2, 12), Goal/No Goal, Under/Over e MARCATORI.
Consumo: 4 richieste. NON tocca data.db.
"""

import json
import os
import time
from collections import defaultdict

import requests

BASE = "https://api.oddspapi.io/v4"
KEY = os.environ.get("ODDSPAPI_KEY")
SERIE_A = 23
BOOKS = ["eurobet.it", "goldbet.it", "bet365.it"]


def get(path, **params):
    params["apiKey"] = KEY
    r = requests.get(f"{BASE}{path}", params=params, timeout=60)
    time.sleep(1.1)
    if r.status_code != 200:
        print(f"ERRORE {r.status_code} su {path}: {r.text[:300]}")
        return None
    return r.json()


# 1. Anagrafica mercati del calcio: id -> nome
markets = get("/markets", sportId=10) or []
if isinstance(markets, dict):
    markets = markets.get("data") or list(markets.values())
print(f"Mercati nell'anagrafica: {len(markets)}")
if markets:
    print("Esempio grezzo:", json.dumps(markets[0], ensure_ascii=False)[:600])
names, outcome_names = {}, {}
for m in markets:
    mid = str(m.get("marketId") or m.get("id"))
    names[mid] = m.get("marketName") or m.get("name") or "?"
    for o in m.get("outcomes", []) or []:
        oid = str(o.get("outcomeId") or o.get("id"))
        outcome_names[oid] = o.get("outcomeName") or o.get("name") or oid

# 2. Mercati presenti per ciascun bookmaker (prima partita di Serie A con quote)
for book in BOOKS:
    data = get("/odds-by-tournaments", tournamentIds=SERIE_A, bookmaker=book,
               oddsFormat="decimal")
    fixtures = data if isinstance(data, list) else []
    fx = next((f for f in fixtures if (f.get("bookmakerOdds") or {}).get(book)), None)
    print(f"\n################ {book} — {len(fixtures)} partite ################")
    if not fx:
        print("  nessuna quota")
        continue
    mk = fx["bookmakerOdds"][book].get("markets") or {}
    print(f"  Partita {fx.get('fixtureId')}: {len(mk)} mercati\n")
    # raggruppa per nome di mercato, per non stampare 95 righe uguali
    groups = defaultdict(list)
    for mid, mdata in mk.items():
        outs = mdata.get("outcomes") or {}
        players = {pid for o in outs.values() for pid in (o.get("players") or {})}
        sample = []
        for oid, o in list(outs.items())[:3]:
            for pid, p in list((o.get("players") or {}).items())[:1]:
                sample.append(f"{outcome_names.get(oid, p.get('bookmakerOutcomeId', oid))}"
                              f"={p.get('price')}"
                              + (f" [{p.get('playerName')}]" if p.get("playerName") else ""))
        groups[names.get(mid, "?")].append(
            f"id {mid}: {len(outs)} esiti, giocatori {len(players - {'0'})}  " + ", ".join(sample))
    for name in sorted(groups):
        rows = groups[name]
        print(f"  {name}  ({len(rows)} varianti)")
        for r in rows[:2]:
            print(f"      {r}")

print("\nFatto. Richieste usate: 4.")
