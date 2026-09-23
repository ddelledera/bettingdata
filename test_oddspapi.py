"""
Perché non vediamo i marcatori? Ipotesi principale: i bookmaker italiani
aprono il mercato marcatori solo pochi giorni prima della partita (quando
si conoscono le formazioni probabili), mentre il nostro test guardava la
Serie A del 10-12 ottobre, a più di due settimane di distanza.

Verifica: stesse domande, ma su partite dei PROSSIMI GIORNI (Nations League
durante la sosta, e il campionato brasiliano), dove i marcatori dovrebbero
già essere aperti. Consumo: circa 6 richieste. NON tocca data.db.
"""

import os
import time
from collections import Counter

import requests

BASE = "https://api.oddspapi.io/v4"
KEY = os.environ.get("ODDSPAPI_KEY")
BOOKS = ["eurobet.it", "bet365.it", "goldbet.it"]
CERCA = ["nations league", "brasileiro serie a"]


def get(path, **params):
    params["apiKey"] = KEY
    r = requests.get(f"{BASE}{path}", params=params, timeout=90)
    time.sleep(1.1)
    if r.status_code != 200:
        print(f"ERRORE {r.status_code} su {path}: {r.text[:300]}")
        return None
    return r.json()


# 1. Anagrafica: quali mercati sono "per giocatore"
markets = get("/markets", sportId=10) or []
prop_names = {str(m["marketId"]): m["marketName"] for m in markets if m.get("playerProp")}
tipi = Counter(prop_names.values())
print(f"Mercati per giocatore nell'anagrafica calcio: {len(prop_names)} "
      f"(tipi: {', '.join(n for n, _ in tipi.most_common(8))})")

# 2. Tornei con partite nei prossimi giorni
tournaments = get("/tournaments", sportId=10) or []
scelti = [t for t in tournaments
          if any(c in (t.get("tournamentName") or "").lower() for c in CERCA)
          and (t.get("upcomingFixtures") or 0) > 0]
scelti.sort(key=lambda t: -(t.get("upcomingFixtures") or 0))
scelti = scelti[:2]
for t in scelti:
    print(f"Torneo: {t['tournamentName']} ({t.get('categoryName')}) id={t['tournamentId']}, "
          f"partite imminenti: {t.get('upcomingFixtures')}")
if not scelti:
    print("Nessun torneo imminente trovato tra", CERCA)

# 3. Per ogni bookmaker: ci sono mercati per giocatore?
ids = ",".join(str(t["tournamentId"]) for t in scelti)
for book in BOOKS if ids else []:
    data = get("/odds-by-tournaments", tournamentIds=ids, bookmaker=book, oddsFormat="decimal")
    fixtures = data if isinstance(data, list) else []
    print(f"\n######## {book}: {len(fixtures)} partite ########")
    for fx in sorted(fixtures, key=lambda f: f.get("startTime") or "")[:6]:
        mk = ((fx.get("bookmakerOdds") or {}).get(book) or {}).get("markets") or {}
        props = {mid: m for mid, m in mk.items()
                 if mid in prop_names or any(
                     pid != "0" for o in (m.get("outcomes") or {}).values()
                     for pid in (o.get("players") or {}))}
        esempio = ""
        for mid, m in list(props.items())[:1]:
            for o in (m.get("outcomes") or {}).values():
                for pid, p in list((o.get("players") or {}).items())[:3]:
                    esempio += f" {p.get('playerName')}={p.get('price')};"
                break
        print(f"  {(fx.get('startTime') or '')[:16]}  mercati {len(mk):3d}  di cui per giocatore "
              f"{len(props):3d}  {', '.join(sorted({prop_names.get(x, x) for x in props}))[:90]}"
              + (f"\n      es.:{esempio}" if esempio else ""))

print("\nFatto.")
