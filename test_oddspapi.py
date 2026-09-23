"""
Diagnosi OddsPapi 2 — la scarsa copertura è solo di Sisal o di tutti
i bookmaker italiani? Consumo: 3 richieste. NON tocca data.db né il workflow.
"""

import os
import time

import requests

BASE_URL = "https://api.oddspapi.io/v4"
API_KEY = os.environ.get("ODDSPAPI_KEY")
NAMES = {23: "Serie A", 17: "Premier League", 8: "LaLiga",
         35: "Bundesliga", 34: "Ligue 1"}
BOOKS = ["goldbet.it", "eurobet.it", "bet365.it"]

for book in BOOKS:
    r = requests.get(f"{BASE_URL}/odds-by-tournaments", timeout=60, params={
        "apiKey": API_KEY, "tournamentIds": ",".join(map(str, NAMES)),
        "bookmaker": book, "oddsFormat": "decimal", "language": "en"})
    time.sleep(1.1)
    print(f"\n########## {book} ##########")
    if r.status_code != 200:
        print(f"  ERRORE {r.status_code}: {r.text[:300]}")
        continue
    data = r.json()
    fixtures = data if isinstance(data, list) else (data.get("data") or [data])
    print(f"  Partite totali: {len(fixtures)}")
    for tid, name in NAMES.items():
        fxs = [f for f in fixtures if f.get("tournamentId") == tid]
        con_1x2 = [f for f in fxs
                   if ((f.get("bookmakerOdds") or {}).get(book) or {})
                   .get("markets", {}).get("101")]
        date = sorted((f.get("startTime") or "")[:10] for f in con_1x2)
        agg = sorted(f.get("updatedAt") or "" for f in fxs)
        mercati = [len(((f.get("bookmakerOdds") or {}).get(book) or {})
                       .get("markets") or {}) for f in con_1x2]
        print(f"  {name:15s} partite={len(fxs):3d}  con 1X2={len(con_1x2):3d}"
              + (f"  date {date[0]} -> {date[-1]}" if date else "")
              + (f"  mercati medi={sum(mercati)//len(mercati)}" if mercati else "")
              + (f"  ultimo aggiornamento={agg[-1][:16]}" if agg else ""))

print("\nFatto. Richieste usate: 3.")
