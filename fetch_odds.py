"""
Scarica le quote attuali dei bookmaker per le partite in arrivo, così il
modello può confrontarle con le proprie probabilità e trovare il valore.

Fonte: The Odds API (livello gratuito: 500 "crediti" al mese, senza carta
di credito). Anche qui serve una chiave API gratuita — stesso discorso
dello script precedente, te la spiego tra poco con calma.

NOTA SUI CREDITI: ogni chiamata a questo script per un campionato costa 1
credito (interroghiamo solo il mercato 1X2, una sola area geografica).
Con 5 campionati seguiti, girare questo script una volta al giorno costa
5 crediti/giorno, cioè circa 150 al mese: ben sotto il limite gratuito di
500. Meglio non farlo girare più spesso del necessario.
"""

import os
import sqlite3
import requests
from datetime import datetime, timezone
from team_utils import normalize_team_name

DB_PATH = "data.db"
API_KEY = os.environ.get("ODDS_API_KEY", "")
BASE_URL = "https://api.the-odds-api.com/v4/sports/{sport_key}/odds"

# Chiave-campionato usata da The Odds API, per ciascun campionato seguito
SPORT_KEYS = {
    "soccer_italy_serie_a": "Serie A",
    "soccer_epl": "Premier League",
    "soccer_spain_la_liga": "La Liga",
    "soccer_germany_bundesliga": "Bundesliga",
    "soccer_france_ligue_one": "Ligue 1",
}


def find_match_id(cur, home_name, away_name, match_date):
    home_name = normalize_team_name(home_name)
    away_name = normalize_team_name(away_name)
    row = cur.execute("""
        SELECT m.id FROM matches m
        JOIN teams h ON m.home_team_id = h.id
        JOIN teams a ON m.away_team_id = a.id
        WHERE h.name = ? AND a.name = ? AND m.date = ?
    """, (home_name, away_name, match_date)).fetchone()
    return row[0] if row else None


def fetch_odds(sport_key):
    if not API_KEY:
        raise RuntimeError(
            "Manca la chiave API (ODDS_API_KEY). "
            "Va impostata prima di eseguire questo script."
        )
    url = BASE_URL.format(sport_key=sport_key)
    response = requests.get(url, params={
        "apiKey": API_KEY,
        "regions": "eu",
        "markets": "h2h",
        "oddsFormat": "decimal",
    }, timeout=20)
    response.raise_for_status()
    return response.json()


def load_into_db(events, conn):
    cur = conn.cursor()
    saved = 0
    snapshot_time = datetime.now(timezone.utc).isoformat()

    for event in events:
        match_date = event["commence_time"][:10]
        match_id = find_match_id(cur, event["home_team"], event["away_team"], match_date)
        if match_id is None:
            continue  # partita non presente nel nostro database (nomi diversi o non tracciata)

        for bookmaker in event.get("bookmakers", []):
            for market in bookmaker.get("markets", []):
                if market["key"] != "h2h":
                    continue
                for outcome in market["outcomes"]:
                    if outcome["name"] == event["home_team"]:
                        selection = "Home"
                    elif outcome["name"] == event["away_team"]:
                        selection = "Away"
                    else:
                        selection = "Draw"
                    cur.execute("""
                        INSERT INTO odds_snapshots (match_id, bookmaker, market,
                                                     selection, odds, snapshot_time)
                        VALUES (?, ?, '1X2', ?, ?, ?)
                    """, (match_id, bookmaker["title"], selection,
                          outcome["price"], snapshot_time))
                    saved += 1
    conn.commit()
    return saved


def main():
    conn = sqlite3.connect(DB_PATH)
    with open("schema.sql") as f:
        conn.executescript(f.read())

    for sport_key, league_name in SPORT_KEYS.items():
        print(f"Scarico quote live: {league_name}...")
        events = fetch_odds(sport_key)
        n = load_into_db(events, conn)
        print(f"  -> {n} quote salvate")

    conn.close()


if __name__ == "__main__":
    main()
