"""
Scarica le quote attuali dei bookmaker ITALIANI (licenza ADM) per le partite
in arrivo dei 5 campionati principali — 1X2, doppia chance, Goal/No Goal e
Under/Over 1.5/2.5/3.5 (arrivano tutti nella stessa richiesta, costo zero), così il modello le confronta con le
proprie probabilità e trova il valore su quote che puoi davvero giocare.

Fonte: OddsPapi (piano gratuito: 250 richieste al mese, senza carta).
Verificata a mano: le quote coincidono con quelle esposte sul sito Eurobet.

COSTO: ogni bookmaker = 1 richiesta (copre tutti e 5 i campionati insieme).
  5 bookmaker (4 italiani + Pinnacle) = 5 richieste per esecuzione,
  circa 150 al mese con un aggiornamento al giorno. In più, raramente,
  1 richiesta per aggiornare l'anagrafica delle squadre.

BOOKMAKER "CLONI": alcuni marchi usano le stesse identiche quote di un altro
(stessa società/piattaforma). Interroghiamo solo l'originale:
  - Goldbet  = anche Lottomatica, Planetwin365, BetFlag
  - Sisal    = anche Snai, PokerStars (copertura ancora scarsa su OddsPapi)

PINNACLE non ha licenza ADM, quindi NON finisce tra le quote giocabili
(odds_snapshots): va nella tabella separata reference_odds, come prezzo
di riferimento "sharp" per il futuro backtest del modello.
"""

import difflib
import json
import os
import re
import unicodedata
import sqlite3
import time
from datetime import date, datetime, timedelta, timezone

import requests

from db_utils import init_db
from markets import market_of, fair_probabilities

DB_PATH = "data.db"
API_KEY = os.environ.get("ODDSPAPI_KEY", "")
BASE_URL = "https://api.oddspapi.io/v4"

# ID dei tornei su OddsPapi -> nome del campionato nel nostro database
TOURNAMENTS = {
    23: "Serie A",
    17: "Premier League",
    8: "La Liga",
    35: "Bundesliga",
    34: "Ligue 1",
}

# slug OddsPapi -> nome mostrato nell'app
ITALIAN_BOOKMAKERS = {
    "goldbet.it": "Goldbet",
    "eurobet.it": "Eurobet",
    "bet365.it": "bet365",
    "sisal.it": "Sisal",
}
REFERENCE_BOOKMAKER = ("pinnacle", "Pinnacle")

# Mercati che salviamo, con il nome usato nell'anagrafica di OddsPapi. Gli
# ID numerici dei mercati (es. 101 = 1X2, 104 = Goal/No Goal) e le linee
# degli Under/Over li leggiamo dall'anagrafica /markets, salvata nel
# database la prima volta (1 sola richiesta, poi mai più).
WANTED_MARKETS = {"Full Time Result", "Double Chance Full Time",
                  "Both Teams To Score", "Over Under Full Time"}
SCORER_MARKET = "Anytime Goal Scorer"   # marcatore in qualsiasi momento
# I bookmaker aprono i marcatori solo 1-3 giorni prima della partita:
# prima di allora questo mercato semplicemente non c'è (è normale).
OVER_UNDER_LINES = {1.5, 2.5, 3.5}
OUTCOME_TO_KEY = {
    "Full Time Result": {"1": "Home", "X": "Draw", "2": "Away"},
    "Double Chance Full Time": {"1X": "1X", "X2": "X2", "2X": "X2", "12": "12"},
    "Both Teams To Score": {"Yes": "Goal", "No": "NoGoal"},
}

# Parole "di contorno" nei nomi delle squadre, ignorate nel confronto
# (es. "Parma Calcio" -> "parma", "AC Monza" -> "monza")
NOISE = {"fc", "ac", "as", "ss", "ssc", "cf", "calcio", "afc", "sc", "sv",
         "vfl", "vfb", "tsg", "fsv", "rc", "ogc", "losc", "cd", "ud", "rcd",
         "club", "de", "1909", "1907", "1913", "1927", "04", "05", "1846"}


def api_get(path, **params):
    if not API_KEY:
        raise RuntimeError("Manca la chiave API (ODDSPAPI_KEY) nei secrets.")
    params["apiKey"] = API_KEY
    r = requests.get(f"{BASE_URL}{path}", params=params, timeout=60)
    time.sleep(1.1)  # l'API chiede almeno 1 secondo tra una chiamata e l'altra
    r.raise_for_status()
    return r.json()


# ---------------------------------------------------------------------------
# Anagrafica squadre (ID OddsPapi -> nome), salvata nel database così non la
# riscarichiamo ogni giorno: si aggiorna solo se compare un ID sconosciuto.
# ---------------------------------------------------------------------------
def load_team_names(conn, needed_ids):
    conn.execute("""CREATE TABLE IF NOT EXISTS oddspapi_participants (
                        id INTEGER PRIMARY KEY, name TEXT NOT NULL)""")
    known = dict(conn.execute("SELECT id, name FROM oddspapi_participants"))
    if needed_ids - known.keys():
        print("  Aggiorno l'anagrafica squadre (1 richiesta)...")
        data = api_get("/participants", sportId=10, language="en")
        conn.executemany(
            "INSERT OR REPLACE INTO oddspapi_participants (id, name) VALUES (?, ?)",
            [(int(k), v) for k, v in data.items() if v])
        conn.commit()
        known = dict(conn.execute("SELECT id, name FROM oddspapi_participants"))
    return known


# ---------------------------------------------------------------------------
# Collegamento partita OddsPapi -> partita nel nostro database.
# I nomi delle squadre differiscono tra le fonti, quindi invece di una lunga
# tabella di corrispondenze confrontiamo i nomi "a somiglianza", ma SOLO tra
# le poche partite dello stesso campionato in quei giorni: così il rischio
# di collegare la partita sbagliata è praticamente nullo.
# ---------------------------------------------------------------------------
# Abbreviazioni usate nello storico (football-data.co.uk) troppo diverse dal
# nome completo per il confronto a somiglianza: le espandiamo prima.
ABBREVIATIONS = {
    "Man City": "Manchester City", "Man United": "Manchester United",
    "Wolves": "Wolverhampton Wanderers", "Nott'm Forest": "Nottingham Forest",
    "Ath Madrid": "Atletico Madrid", "Ath Bilbao": "Athletic Bilbao",
    "Paris SG": "Paris Saint Germain", "M'gladbach": "Borussia Monchengladbach",
    "Ein Frankfurt": "Eintracht Frankfurt", "Rennes": "Stade Rennais Rennes",
    "Sp Gijon": "Sporting Gijon", "Sociedad": "Real Sociedad",
}


def simplify(name):
    name = ABBREVIATIONS.get(name, name)
    words = re.sub(r"[^a-z0-9 ]", " ", (name or "").lower()).split()
    kept = [w for w in words if w not in NOISE]
    return " ".join(kept or words)


def similarity(a, b):
    a, b = simplify(a), simplify(b)
    if a == b or (a and b and (a in b or b in a)):
        return 1.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def find_match_id(cur, league, start_time, home, away):
    d = date.fromisoformat(start_time[:10])
    candidates = cur.execute("""
        SELECT m.id, h.name, a.name FROM matches m
        JOIN teams h ON m.home_team_id = h.id
        JOIN teams a ON m.away_team_id = a.id
        WHERE m.league = ? AND m.date BETWEEN ? AND ?
    """, (league, (d - timedelta(days=1)).isoformat(),
          (d + timedelta(days=1)).isoformat())).fetchall()
    best, best_score = None, 0
    for match_id, h, a in candidates:
        sh, sa = similarity(home, h), similarity(away, a)
        # entrambe le squadre devono somigliare, e la coppia nel complesso
        # parecchio (1.4 su 2): tollera piccole differenze di nome, ma non
        # collega una partita a quella sbagliata
        if min(sh, sa) >= 0.5 and sh + sa >= 1.4 and sh + sa > best_score:
            best, best_score = match_id, sh + sa
    return best


def load_market_meta(conn):
    """Anagrafica dei mercati che ci interessano: id -> (nome, linea, esiti)."""
    conn.execute("""CREATE TABLE IF NOT EXISTS oddspapi_markets (
                        market_id INTEGER PRIMARY KEY, name TEXT, handicap REAL,
                        outcomes TEXT)""")
    has_scorer = conn.execute("SELECT 1 FROM oddspapi_markets WHERE name = ?",
                              (SCORER_MARKET,)).fetchone()
    if not has_scorer:  # prima volta, o anagrafica salvata prima dei marcatori
        print("  Scarico l'anagrafica dei mercati (1 richiesta, solo la prima volta)...")
        data = api_get("/markets", sportId=10)
        rows = []
        for m in data if isinstance(data, list) else []:
            # il filtro sportId dell'API non filtra davvero: lo rifacciamo noi
            if m.get("sportId") != 10:
                continue
            name = m.get("marketName")
            if (name in WANTED_MARKETS and not m.get("playerProp")) or name == SCORER_MARKET:
                outcomes = {str(o["outcomeId"]): o.get("outcomeName")
                            for o in m.get("outcomes") or []}
                rows.append((m["marketId"], name, m.get("handicap"), json.dumps(outcomes)))
        conn.execute("DELETE FROM oddspapi_markets")
        conn.executemany("INSERT OR REPLACE INTO oddspapi_markets VALUES (?, ?, ?, ?)", rows)
        conn.commit()
        print(f"    {len(rows)} mercati utili salvati")
    return {str(mid): (name, handicap, json.loads(outs))
            for mid, name, handicap, outs in conn.execute("SELECT * FROM oddspapi_markets")}


def parse_markets(book_data, meta):
    """Tutte le quote utili di un bookmaker per una partita.
    Ritorna ({selezione: quota}, {nome giocatore: quota marcatore})."""
    prices, scorers = {}, {}
    for mid, market in ((book_data or {}).get("markets") or {}).items():
        if mid not in meta or market.get("marketActive") is False:
            continue
        name, handicap, outcome_names = meta[mid]
        if name == SCORER_MARKET:
            # qui le quote sono per giocatore: la chiave è l'id del giocatore
            for oid, outcome in (market.get("outcomes") or {}).items():
                if outcome_names.get(oid) == "No":
                    continue
                for pid, p in (outcome.get("players") or {}).items():
                    if pid != "0" and p.get("playerName") and p.get("price") \
                            and p.get("active") is not False:
                        scorers[p["playerName"]] = float(p["price"])
            continue
        if name == "Over Under Full Time" and handicap not in OVER_UNDER_LINES:
            continue
        for oid, outcome in (market.get("outcomes") or {}).items():
            p = (outcome.get("players") or {}).get("0") or {}
            if not p.get("price") or p.get("active") is False:
                continue
            label = outcome_names.get(oid)
            if name == "Over Under Full Time":
                key = f"{label}{handicap}" if label in ("Over", "Under") else None
            else:
                key = OUTCOME_TO_KEY[name].get(label)
            if key:
                prices[key] = float(p["price"])
    return prices, scorers


# ---------------------------------------------------------------------------
# Nomi dei marcatori: il bookmaker scrive "Cognome, Nome" (es. "Vlahovic,
# Dusan"), BSD scrive "Nome Cognome" (es. "Dušan Vlahović"). Li colleghiamo
# cercando SOLO tra i giocatori delle due squadre di quella partita.
# ---------------------------------------------------------------------------
def _person_tokens(name):
    if "," in name:
        last, first = name.split(",", 1)
        name = f"{first} {last}"
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return re.findall(r"[a-z]+", name)


def _person_score(a, b):
    ta, tb = _person_tokens(a), _person_tokens(b)
    if not ta or not tb:
        return 0
    if set(ta) == set(tb):
        return 1.0
    short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    if set(short) <= set(long_):
        return 0.95        # es. "Vitinha" / "Vitor Machado Ferreira Vitinha"
    if ta[-1] == tb[-1] and ta[0][0] == tb[0][0]:
        return 0.9         # stesso cognome e stessa iniziale del nome
    return difflib.SequenceMatcher(None, " ".join(ta), " ".join(tb)).ratio()


def find_player_id(cur, match_id, name):
    roster = cur.execute("""
        SELECT pl.id, pl.name FROM players pl JOIN matches m
          ON pl.team_id IN (m.home_team_id, m.away_team_id)
        WHERE m.id = ? AND pl.name != ''
    """, (match_id,)).fetchall()
    scored = sorted(((_person_score(name, n), pid) for pid, n in roster), reverse=True)
    if scored and scored[0][0] >= 0.85 and (len(scored) == 1 or scored[1][0] < scored[0][0]):
        return scored[0][1]
    return None


# ---------------------------------------------------------------------------
# SCOMMESSE VIRTUALI: la prova dal vivo, senza rischiare soldi.
# Ogni volta che una quota italiana supera il prezzo giusto di Pinnacle
# (di almeno EDGE_MIN) (con quota non oltre MAX_ODDS, dove il backtest ha dato
# risultati sensati), la registriamo come se l'avessimo giocata. Nelle
# esecuzioni successive, fino al giorno della partita, aggiorniamo il prezzo
# giusto di Pinnacle: l'ultimo valore fa da "chiusura" per misurare il CLV.
# ---------------------------------------------------------------------------
EDGE_MIN = 0.0    # registriamo anche i vantaggi piccoli: più dati per misurare
                  # il CLV, e nell'analisi si possono sempre dividere per soglia
MAX_ODDS = 5.0


def record_virtual_bets(cur, run_prices, snapshot_time):
    new = 0
    for match_id, by_book in run_prices.items():
        pin = by_book.get(REFERENCE_BOOKMAKER[1])
        fair = fair_probabilities(pin) if pin else {}
        if not fair:
            continue
        # aggiorna la "chiusura" delle scommesse già registrate (partita non iniziata)
        for selection, p in fair.items():
            cur.execute("""UPDATE virtual_bets SET close_fair_prob = ?, close_updated_at = ?
                           WHERE match_id = ? AND selection = ? AND match_id IN (
                               SELECT id FROM matches WHERE date >= date('now')
                               AND home_goals IS NULL)""",
                        (p, snapshot_time, match_id, selection))
        for book, prices in by_book.items():
            if book == REFERENCE_BOOKMAKER[1]:
                continue
            for selection, odds in prices.items():
                p = fair.get(selection)
                if p is None or odds > MAX_ODDS or odds * p - 1 < EDGE_MIN:
                    continue
                cur.execute("""INSERT OR IGNORE INTO virtual_bets (match_id, selection,
                                   bookmaker, odds, fair_prob, edge, found_at,
                                   close_fair_prob, close_updated_at)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (match_id, selection, book, odds, p, odds * p - 1,
                             snapshot_time, p, snapshot_time))
                new += cur.rowcount
    print(f"  Scommesse virtuali nuove (quota italiana sopra Pinnacle di almeno "
          f"{EDGE_MIN:.0%}): {new}")


def main():
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)
    cur = conn.cursor()
    snapshot_time = datetime.now(timezone.utc).isoformat()
    ids = ",".join(str(t) for t in TOURNAMENTS)

    books = list(ITALIAN_BOOKMAKERS.items()) + [REFERENCE_BOOKMAKER]
    responses = {}
    for slug, label in books:
        print(f"Scarico quote {label}...")
        try:
            data = api_get("/odds-by-tournaments", tournamentIds=ids,
                           bookmaker=slug, oddsFormat="decimal")
            responses[slug] = data if isinstance(data, list) else []
        except requests.RequestException as e:
            print(f"  ERRORE su {label}: {e} — salto questo bookmaker")

    needed = {int(fx[k]) for fxs in responses.values() for fx in fxs
              for k in ("participant1Id", "participant2Id") if fx.get(k)}
    names = load_team_names(conn, needed)
    meta = load_market_meta(conn)

    unmatched = set()
    run_prices = {}   # match_id -> {bookmaker: {selezione: quota}} di questa esecuzione
    for slug, label in books:
        saved, scorer_rows, scorer_matches = 0, 0, 0
        for fx in responses.get(slug, []):
            league = TOURNAMENTS.get(fx.get("tournamentId"))
            prices, scorers = parse_markets((fx.get("bookmakerOdds") or {}).get(slug), meta)
            if not league or not prices or not fx.get("startTime"):
                continue
            home = names.get(int(fx["participant1Id"]), "?")
            away = names.get(int(fx["participant2Id"]), "?")
            match_id = find_match_id(cur, league, fx["startTime"], home, away)
            if match_id is None:
                unmatched.add(f"{league}: {home} - {away} ({fx['startTime'][:10]})")
                continue
            table = "reference_odds" if slug == REFERENCE_BOOKMAKER[0] else "odds_snapshots"
            run_prices.setdefault(match_id, {})[label] = prices
            for selection, odds in prices.items():
                # Salviamo una nuova "fotografia" solo se la quota è cambiata
                # dall'ultima volta: stesso storico dei movimenti, ma senza
                # riempire il database di righe identiche a ogni esecuzione.
                last = cur.execute(f"""SELECT odds FROM {table}
                                       WHERE match_id = ? AND bookmaker = ? AND selection = ?
                                       ORDER BY snapshot_time DESC LIMIT 1""",
                                   (match_id, label, selection)).fetchone()
                if last and abs(last[0] - odds) < 1e-9:
                    cur.execute(f"""UPDATE {table} SET snapshot_time = ?
                                    WHERE id = (SELECT id FROM {table}
                                                WHERE match_id = ? AND bookmaker = ? AND selection = ?
                                                ORDER BY snapshot_time DESC LIMIT 1)""",
                                (snapshot_time, match_id, label, selection))
                    continue
                cur.execute(f"""
                    INSERT INTO {table} (match_id, bookmaker, market,
                                         selection, odds, snapshot_time)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (match_id, label, market_of(selection), selection, odds, snapshot_time))
            saved += 1
            if table == "odds_snapshots":   # marcatori: solo bookmaker giocabili
                for player_name, odds in scorers.items():
                    cur.execute("""INSERT INTO scorer_odds (match_id, bookmaker, player_name,
                                       player_id, odds, snapshot_time) VALUES (?, ?, ?, ?, ?, ?)""",
                                (match_id, label, player_name,
                                 find_player_id(cur, match_id, player_name), odds, snapshot_time))
                scorer_rows += len(scorers)
                scorer_matches += bool(scorers)
        print(f"  -> {label}: quote salvate per {saved} partite"
              + (f", marcatori per {scorer_matches} partite ({scorer_rows} giocatori)"
                 if scorer_matches else ""))
    conn.commit()
    record_virtual_bets(cur, run_prices, snapshot_time)
    conn.commit()
    conn.close()

    if unmatched:
        # Normale per le partite oltre la finestra di fetch_fixtures.py (21
        # giorni). Se invece compare una partita vicina, è un nome squadra
        # troppo diverso: va aggiunto in team_utils.py.
        print(f"\nPartite con quote ma non trovate nel database ({len(unmatched)}):")
        for u in sorted(unmatched):
            print(f"  {u}")


if __name__ == "__main__":
    main()
