"""
Test esplorativo di OddsPapi (NON tocca data.db né il workflow).

Obiettivo:
  1. trovare gli ID dei 5 campionati principali
  2. scaricare le quote 1X2 con UNA chiamata a /odds-by-tournaments
  3. vedere quali bookmaker italiani e se Pinnacle sono davvero presenti
  4. stampare le quote di una partita da confrontare a mano con Oddschecker

Consumo previsto: 2 richieste (su 250/mese).

Uso:
  export ODDSPAPI_KEY=la_tua_chiave      (Windows: set ODDSPAPI_KEY=...)
  python test_oddspapi.py

Salva anche le risposte grezze in oddspapi_tournaments.json e
oddspapi_odds.json: se qualcosa non torna, mandamele e aggiusto il parsing.
"""

import json
import os
import sys
import time

import requests

BASE_URL = "https://api.oddspapi.io/v4"
API_KEY = os.environ.get("ODDSPAPI_KEY")

# (nome campionato, paese) — il paese serve a non confondere la Serie A
# italiana con quella brasiliana, ecc.
TARGET_LEAGUES = [
    ("serie a", "italy"),
    ("premier league", "england"),
    ("laliga", "spain"),
    ("bundesliga", "germany"),
    ("ligue 1", "france"),
]

# Parole chiave per riconoscere gli slug dei bookmaker che ci interessano.
# Non conosciamo ancora gli slug esatti: li cerchiamo per sottostringa.
ITALIAN_KEYWORDS = [
    "snai", "sisal", "eurobet", "lottomatica", "goldbet", "planetwin",
    "betflag", "bet365", "better", "eplay", "quigioco", "netbet",
    "betsson", "888", "pokerstars", "admiral", "leovegas", "bwin",
]
SHARP_KEYWORDS = ["pinnacle", "betfair"]

MARKET_1X2 = "101"
OUTCOME_LABELS = {"101": "1", "102": "X", "103": "2"}


def get(path, **params):
    params["apiKey"] = API_KEY
    r = requests.get(f"{BASE_URL}{path}", params=params, timeout=60)
    if r.status_code != 200:
        print(f"ERRORE {r.status_code} su {path}: {r.text[:500]}")
        sys.exit(1)
    time.sleep(1.1)  # cooldown dichiarato dall'API: 1000 ms
    return r.json()


def norm(s):
    return (s or "").lower().replace(" ", "").replace("-", "")


def find_tournaments(tournaments):
    """Cerca gli ID dei 5 campionati. Stampa i candidati per controllo."""
    if tournaments:
        print("Campi disponibili in un torneo:", sorted(tournaments[0].keys()))
    found = {}
    for league, country in TARGET_LEAGUES:
        candidates = []
        for t in tournaments:
            name = norm(t.get("tournamentName"))
            # il campo del paese può chiamarsi in modi diversi: li proviamo tutti
            where = norm(" ".join(str(t.get(k, "")) for k in
                                  ("categoryName", "countryName", "category",
                                   "country", "categorySlug")))
            if norm(league) == name or norm(league) in name:
                candidates.append((country in where, t))
        candidates.sort(key=lambda c: not c[0])  # prima quelli col paese giusto
        print(f"\n{league.title()} ({country}) — candidati:")
        for ok, t in candidates[:5]:
            print(f"  {'✔' if ok else ' '} id={t.get('tournamentId')}  "
                  f"{t.get('tournamentName')}  | "
                  f"{t.get('categoryName') or t.get('countryName') or '?'}  | "
                  f"partite in arrivo: {t.get('upcomingFixtures', '?')}")
        if candidates and candidates[0][0]:
            found[league] = candidates[0][1]["tournamentId"]
    return found


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


def main():
    if not API_KEY:
        print("Manca la variabile d'ambiente ODDSPAPI_KEY.")
        sys.exit(1)

    # --- Richiesta 1: tornei di calcio ---
    tournaments = get("/tournaments", sportId=10)
    with open("oddspapi_tournaments.json", "w", encoding="utf-8") as f:
        json.dump(tournaments, f, ensure_ascii=False, indent=1)
    ids = find_tournaments(tournaments)
    if not ids:
        print("\nNessun campionato riconosciuto: mandami oddspapi_tournaments.json")
        sys.exit(1)
    print(f"\nUso questi ID: {ids}")

    # --- Richiesta 2: quote di tutti e 5 i campionati in una volta ---
    # Nessun filtro bookmaker: vogliamo vedere TUTTI gli slug disponibili.
    fixtures = get("/odds-by-tournaments",
                   tournamentIds=",".join(str(i) for i in ids.values()),
                   oddsFormat="decimal", language="en")
    with open("oddspapi_odds.json", "w", encoding="utf-8") as f:
        json.dump(fixtures, f, ensure_ascii=False, indent=1)

    if isinstance(fixtures, dict):  # a volte le API avvolgono la lista
        fixtures = fixtures.get("data") or fixtures.get("fixtures") or [fixtures]
    print(f"\nPartite restituite: {len(fixtures)}")

    # --- Quali bookmaker compaiono, e in quante partite con 1X2 ---
    coverage = {}
    for fx in fixtures:
        for slug, data in (fx.get("bookmakerOdds") or {}).items():
            if price_1x2(data):
                coverage[slug] = coverage.get(slug, 0) + 1
    print(f"Bookmaker distinti con 1X2: {len(coverage)}")

    def show(title, keywords):
        print(f"\n{title}")
        hits = {s: n for s, n in coverage.items()
                if any(k in s.lower() for k in keywords)}
        if not hits:
            print("  (nessuno trovato)")
        for s, n in sorted(hits.items(), key=lambda x: -x[1]):
            print(f"  {s:30s} {n}/{len(fixtures)} partite")
        return hits

    it_books = show("Bookmaker italiani (o varianti) trovati:", ITALIAN_KEYWORDS)
    sharp_books = show("Riferimenti sharp trovati:", SHARP_KEYWORDS)
    it_suffix = [s for s in coverage if s.endswith(".it") or s.endswith("-it")
                 or s.endswith("it") and "." in s]
    if it_suffix:
        print("\nSlug con suffisso italiano:", ", ".join(sorted(it_suffix)))

    # --- Una partita di Serie A da confrontare a mano ---
    serie_a = ids.get("serie a")
    sample = next((fx for fx in fixtures
                   if fx.get("tournamentId") == serie_a
                   and any(s in (fx.get("bookmakerOdds") or {}) for s in it_books)),
                  None) or (fixtures[0] if fixtures else None)
    if sample:
        home = sample.get("participant1Name", sample.get("participant1Id"))
        away = sample.get("participant2Name", sample.get("participant2Id"))
        print(f"\n=== Da verificare su Oddschecker / siti bookmaker ===")
        print(f"{home} - {away}   inizio: {sample.get('startTime')}")
        print(f"{'bookmaker':30s} {'1':>6s} {'X':>6s} {'2':>6s}   ultimo cambio")
        for slug in sorted(set(it_books) | set(sharp_books)):
            data = (sample.get("bookmakerOdds") or {}).get(slug)
            p = price_1x2(data) if data else None
            if p:
                changed = max((c for _, c in p.values() if c), default="?")
                print(f"{slug:30s} {p['1'][0] or '-':>6} {p['X'][0] or '-':>6} "
                      f"{p['2'][0] or '-':>6}   {changed}")

    print("\nFatto. Richieste usate: 2.")


if __name__ == "__main__":
    main()
