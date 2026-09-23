"""
Scarica partite in arrivo E quote live per Champions League, Europa League
e Conference League, in un solo passaggio.

A differenza di fetch_fixtures.py + fetch_odds.py (due passaggi separati),
qui basta uno: The Odds API restituisce già insieme, per queste coppe, sia
l'elenco delle partite sia le quote dei bookmaker.
"""

import os
import sqlite3
from db_utils import init_db
import requests
from datetime import datetime, timezone
from team_utils import get_or_create_team

DB_PATH = "data.db"
API_KEY = os.environ.get("ODDS_API_KEY", "")
BASE_URL = "https://api.the-odds-api.com/v4/sports/{sport_key}/odds"

SPORT_KEYS = {
    "soccer_uefa_champs_league": "Champions League",
    "soccer_uefa_europa_league": "Europa League",
    "soccer_uefa_europa_conference_league": "Conference League",
}


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


def load_into_db(events, league_name, conn):
    cur = conn.cursor()
    saved_matches, saved_odds = 0, 0
    snapshot_time = datetime.now(timezone.utc).isoformat()

    for event in events:
        match_date = event["commence_time"][:10]
        home_id = get_or_create_team(cur, event["home_team"])
        away_id = get_or_create_team(cur, event["away_team"])

        cur.execute("""
            INSERT INTO matches (date, league, season, home_team_id, away_team_id,
                                  home_goals, away_goals)
            VALUES (?, ?, 'current', ?, ?, NULL, NULL)
            ON CONFLICT(date, home_team_id, away_team_id) DO NOTHING
        """, (match_date, league_name, home_id, away_id))
        match_id = cur.execute(
            "SELECT id FROM matches WHERE date=? AND home_team_id=? AND away_team_id=?",
            (match_date, home_id, away_id)
        ).fetchone()[0]
        saved_matches += 1

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
                    saved_odds += 1

    conn.commit()
    return saved_matches, saved_odds


def main():
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)

    for sport_key, league_name in SPORT_KEYS.items():
        print(f"Scarico partite e quote: {league_name}...")
        events = fetch_odds(sport_key)
        n_m, n_o = load_into_db(events, league_name, conn)
        print(f"  -> {n_m} partite, {n_o} quote")

    conn.close()


if __name__ == "__main__":
    main()
