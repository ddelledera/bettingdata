"""
Test esplorativo di OddsPapi (NON tocca data.db né il workflow) — versione 2.

Cosa abbiamo imparato dal primo giro:
  - ID campionati: Serie A 23, Premier 17, LaLiga 8, Bundesliga 35, Ligue 1 34
  - /odds-by-tournaments accetta UN SOLO bookmaker per chiamata
    (parametro 'bookmaker', singolare)

Cosa fa ora:
  1. /bookmakers -> elenca gli slug esatti dei bookmaker italiani e sharp
  2. scarica le quote dei 5 campionati per Pinnacle + fino a 2 italiani
  3. stampa una partita di Serie A da confrontare con Oddschecker

Consumo: al massimo 4 richieste (su 250/mese).
"""

import json
import os
import sys
import time

import requests

BASE_URL = "https://api.oddspapi.io/v4"
API_KEY = os.environ.get("ODDSPAPI_KEY")

TOURNAMENT_IDS = {"Serie A": 23, "Premier League": 17, "LaLiga": 8,
                  "Bundesliga": 35, "Ligue 1": 34}

ITALIAN_KEYWORDS = [
    "snai", "sisal", "eurobet", "lottomatica", "goldbet", "planetwin",
    "betflag", "bet365", "better", "eplay", "quigioco", "netbet",
    "betsson", "888", "pokerstars", "admiral", "leovegas", "bwin", "starcasino",
]
SHARP_KEYWORDS = ["pinnacle", "betfair"]
# ordine di preferenza per i 2 italiani da provare nelle quote
PREFERRED_IT = ["snai", "sisal", "eurobet", "lottomatica"]

MARKET_1X2 = "101"
OUTCOME_LABELS = {"101": "1", "102": "X", "103": "2"}
requests_used = 0


def get(path, fatal=True, **params):
    global requests_used
    params["apiKey"] = API_KEY
    r = requests.get(f"{BASE_URL}{path}", params=params, timeout=60)
    requests_used += 1
    time.sleep(1.1)  # cooldown dichiarato: 1000 ms
    if r.status_code != 200:
        print(f"ERRORE {r.status_code} su {path} {params.get('bookmaker', '')}: "
              f"{r.text[:400]}")
        if fatal:
            sys.exit(1)
        return None
    return r.json()


def price_1x2(book_data):
    market = (book_data.get("markets") or {}).get(MARKET_1X2)
    if not market:
        return None
    out = {}
    for oid, label in OUTCOME_LABELS.items():
        p = (market.get("outcomes", {}).get(oid, {})
             .get("players", {}).get("0", {}))
        out[label] = (p.get("price"), p.get("changedAt"))
    return out


def as_list(data):
    if isinstance(data, dict):
        return data.get("data") or data.get("fixtures") or [data]
    return data or []


def main():
    if not API_KEY:
        print("Manca il secret ODDSPAPI_KEY.")
        sys.exit(1)

    # --- 1. Elenco bookmaker ---
    books = get("/bookmakers")
    with open("oddspapi_bookmakers.json", "w", encoding="utf-8") as f:
        json.dump(books, f, ensure_ascii=False, indent=1)
    print(f"Bookmaker totali nel sistema: {len(books)}")

    def matching(keywords):
        return [b for b in books
                if any(k in (b.get("slug", "") + b.get("bookmakerName", "")).lower()
                       for k in keywords)]

    it_books = matching(ITALIAN_KEYWORDS)
    sharp_books = matching(SHARP_KEYWORDS)
    for title, lst in (("\nBookmaker italiani (o varianti):", it_books),
                       ("\nRiferimenti sharp:", sharp_books)):
        print(title)
        for b in sorted(lst, key=lambda x: x.get("slug", "")):
            clone = f"  (clone di {b['cloneOf']})" if b.get("cloneOf") else ""
            print(f"  {b.get('slug', ''):30s} {b.get('bookmakerName', '')}{clone}")

    # --- 2. Scelta dei bookmaker da interrogare ---
    slugs = [b["slug"] for b in books]
    to_query = []
    if "pinnacle" in slugs:
        to_query.append("pinnacle")
    for key in PREFERRED_IT:
        cands = [b["slug"] for b in it_books if key in b["slug"].lower()]
        # preferisci la variante italiana se esiste (es. finisce con 'it')
        cands.sort(key=lambda s: not s.lower().rstrip(".").endswith("it"))
        if cands and len(to_query) < 3:
            to_query.append(cands[0])
    print(f"\nInterrogo le quote per: {to_query}")

    ids = ",".join(str(i) for i in TOURNAMENT_IDS.values())
    odds = {}  # slug -> lista partite
    for slug in to_query:
        data = get("/odds-by-tournaments", fatal=False, tournamentIds=ids,
                   bookmaker=slug, oddsFormat="decimal", language="en")
        if data is None:
            continue
        fixtures = as_list(data)
        odds[slug] = fixtures
        with open(f"oddspapi_odds_{slug.replace('.', '_')}.json", "w",
                  encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        per_league = {}
        for fx in fixtures:
            if (fx.get("bookmakerOdds") or {}).get(slug) and \
               price_1x2(fx["bookmakerOdds"][slug]):
                per_league[fx.get("tournamentId")] = \
                    per_league.get(fx.get("tournamentId"), 0) + 1
        detail = ", ".join(f"{n}: {per_league.get(i, 0)}"
                           for n, i in TOURNAMENT_IDS.items())
        print(f"  {slug}: {len(fixtures)} partite, con 1X2 -> {detail}")

    # --- 3. Una partita di Serie A da confrontare a mano ---
    all_fx = {}
    for slug, fixtures in odds.items():
        for fx in fixtures:
            all_fx.setdefault(fx["fixtureId"], fx)
            all_fx[fx["fixtureId"]].setdefault("_books", {})[slug] = \
                (fx.get("bookmakerOdds") or {}).get(slug)
    serie_a = [fx for fx in all_fx.values() if fx.get("tournamentId") == 23]
    serie_a.sort(key=lambda fx: (-len([v for v in fx["_books"].values() if v]),
                                 fx.get("startTime", "")))
    if serie_a:
        fx = serie_a[0]
        home = fx.get("participant1Name", fx.get("participant1Id"))
        away = fx.get("participant2Name", fx.get("participant2Id"))
        print("\n=== Da verificare su Oddschecker / siti bookmaker ===")
        print(f"{home} - {away}   inizio: {fx.get('startTime')}")
        print(f"{'bookmaker':30s} {'1':>6s} {'X':>6s} {'2':>6s}   ultimo cambio")
        for slug, data in fx["_books"].items():
            p = price_1x2(data) if data else None
            if p:
                changed = max((c for _, c in p.values() if c), default="?")
                print(f"{slug:30s} {p['1'][0] or '-':>6} {p['X'][0] or '-':>6} "
                      f"{p['2'][0] or '-':>6}   {changed}")
            else:
                print(f"{slug:30s}   (nessuna quota 1X2)")
    else:
        print("\nNessuna partita di Serie A nelle risposte.")

    print(f"\nFatto. Richieste usate: {requests_used}.")


if __name__ == "__main__":
    main()
