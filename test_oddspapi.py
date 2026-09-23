"""
Diagnosi OddsPapi — perché Sisal ha quote solo per 10 partite di Premier?
Consumo: 1 richiesta. NON tocca data.db né il workflow.
"""

import os
import sys
from collections import Counter

import requests

BASE_URL = "https://api.oddspapi.io/v4"
API_KEY = os.environ.get("ODDSPAPI_KEY")
NAMES = {23: "Serie A", 17: "Premier League", 8: "LaLiga",
         35: "Bundesliga", 34: "Ligue 1"}
BOOK = "sisal.it"

r = requests.get(f"{BASE_URL}/odds-by-tournaments", timeout=60, params={
    "apiKey": API_KEY, "tournamentIds": ",".join(map(str, NAMES)),
    "bookmaker": BOOK, "oddsFormat": "decimal", "language": "en"})
if r.status_code != 200:
    print(f"ERRORE {r.status_code}: {r.text[:400]}")
    sys.exit(1)
data = r.json()
fixtures = data if isinstance(data, list) else (data.get("data") or [data])

print(f"Partite totali nella risposta: {len(fixtures)}")
if fixtures:
    print("Campi di una partita:", sorted(fixtures[0].keys()))

for tid, name in NAMES.items():
    fxs = sorted((f for f in fixtures if f.get("tournamentId") == tid),
                 key=lambda f: f.get("startTime") or "")
    print(f"\n===== {name}: {len(fxs)} partite =====")
    stato = Counter()
    for f in fxs:
        b = (f.get("bookmakerOdds") or {}).get(BOOK)
        if not b:
            riga = "sisal ASSENTE"
        else:
            m = b.get("markets") or {}
            m101 = m.get("101")
            riga = (f"attivo={b.get('bookmakerIsActive')} "
                    f"sospeso={b.get('suspended')} mercati={len(m)} "
                    f"1X2={'si' if m101 else 'no'}"
                    + (f" (attivo={m101.get('marketActive')})" if m101 else ""))
        stato[riga] += 1
        print(f"  {(f.get('startTime') or '?')[:16]}  "
              f"{f.get('participant1Id')}-{f.get('participant2Id')}  {riga}")
    print("  Riepilogo:", dict(stato))

print("\nFatto. Richieste usate: 1.")
