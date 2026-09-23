"""
Verifica manuale OddsPapi — prossima giornata di Serie A con nomi squadre,
quote 1X2 di Pinnacle + Goldbet + Eurobet + bet365 IT e ora dell'ultimo cambio.
Da confrontare con Oddschecker / siti dei bookmaker NELLO STESSO MOMENTO.
Consumo: 5 richieste. NON tocca data.db né il workflow.
"""

import os
import time

import requests

BASE_URL = "https://api.oddspapi.io/v4"
API_KEY = os.environ.get("ODDSPAPI_KEY")
SERIE_A = 23
BOOKS = ["pinnacle", "goldbet.it", "eurobet.it", "bet365.it"]
SHORT = {"pinnacle": "PIN", "goldbet.it": "GOLD", "eurobet.it": "EURO",
         "bet365.it": "B365"}


def get(path, **params):
    params["apiKey"] = API_KEY
    r = requests.get(f"{BASE_URL}{path}", params=params, timeout=60)
    time.sleep(1.1)
    if r.status_code != 200:
        print(f"ERRORE {r.status_code} su {path} {params.get('bookmaker', '')}: "
              f"{r.text[:300]}")
        return None
    return r.json()


def price_1x2(book_data):
    m = ((book_data or {}).get("markets") or {}).get("101")
    if not m:
        return None
    res = []
    for oid in ("101", "102", "103"):
        p = m.get("outcomes", {}).get(oid, {}).get("players", {}).get("0", {})
        res.append((p.get("price"), p.get("changedAt") or ""))
    return res


names = get("/participants", sportId=10, language="it") or {}
print(f"Squadre nell'anagrafica: {len(names)}")

matches = {}  # fixtureId -> {info, quote per bookmaker}
for book in BOOKS:
    data = get("/odds-by-tournaments", tournamentIds=SERIE_A, bookmaker=book,
               oddsFormat="decimal", language="it")
    for fx in (data if isinstance(data, list) else []):
        entry = matches.setdefault(fx["fixtureId"], {"fx": fx, "q": {}})
        q = price_1x2((fx.get("bookmakerOdds") or {}).get(book))
        if q:
            entry["q"][book] = q

print("\nQUOTE 1X2 — SERIE A (orari UTC)\n")
for e in sorted(matches.values(), key=lambda e: e["fx"].get("startTime") or ""):
    fx = e["fx"]
    home = names.get(str(fx.get("participant1Id")), fx.get("participant1Id"))
    away = names.get(str(fx.get("participant2Id")), fx.get("participant2Id"))
    print(f"{(fx.get('startTime') or '')[:16].replace('T', ' ')}  {home} - {away}")
    best = [0, 0, 0]
    for book in BOOKS:
        q = e["q"].get(book)
        if not q:
            print(f"    {SHORT[book]:5s}  (nessuna quota)")
            continue
        changed = max(c for _, c in q)[:16].replace("T", " ")
        print(f"    {SHORT[book]:5s} {q[0][0] or '-':>6} {q[1][0] or '-':>6} "
              f"{q[2][0] or '-':>6}   cambiata: {changed}")
        if book != "pinnacle":
            best = [max(b, x[0] or 0) for b, x in zip(best, q)]
    if any(best):
        print(f"    MIGLIORE IT {best[0]:>6} {best[1]:>6} {best[2]:>6}")
    print()

print("Fatto. Richieste usate: 5.")
