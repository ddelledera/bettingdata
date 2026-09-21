"""
Scarica le partite in programma nei prossimi giorni (non ancora giocate)
per i campionati che seguiamo, e le inserisce nel database.

Fonte: football-data.org (livello gratuito). Serve una chiave API gratuita,
te lo spiego tra un attimo: è un passaggio manuale, ma richiede letteralmente
un click e un copia-incolla, niente di tecnico.

La chiave va inserita come "variabile d'ambiente" chiamata
FOOTBALL_DATA_API_KEY. Quando arriveremo a pubblicare l'app, ti mostrerò
esattamente dove incollarla (una casella di testo, non codice).
"""

import os
import sqlite3
import requests
from datetime import date, timedelta

DB_PATH = "data.db"
API_KEY = os.environ.get("FOOTBALL_DATA_API_KEY", "")
BASE_URL = "https://api.football-data.org/v4/competitions/{code}/matches"

# Codice-competizione usato da football-data.org per ciascun campionato
COMPETITION_CODES = {
    "SA": "Serie A",
    "PL": "Premier League",
    "PD": "La Liga",
    "BL1": "Bundesliga",
    "FL1": "Ligue 1",
}

DAYS_AHEAD = 10  # quante partite future guardare


def normalize_team_name(name):
    """Stessa logica dello script dei dati storici, per far combaciare i nomi."""
    fixes = {
        "FC Internazionale Milano": "Inter",
        "AC Milan": "Milan",
        "Hellas Verona FC": "Hellas Verona",
        "Juventus FC": "Juventus",
    }
    return fixes.get(name.strip(), name.strip())


def get_or_create_team(cur, name):
    name = normalize_team_name(name)
    cur.execute("SELECT id FROM teams WHERE name = ?", (name,))
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute("INSERT INTO teams (name) VALUES (?)", (name,))
    return cur.lastrowid


def fetch_upcoming(competition_code):
    if not API_KEY:
        raise RuntimeError(
            "Manca la chiave API (FOOTBALL_DATA_API_KEY). "
            "Va impostata prima di eseguire questo script."
        )
    date_from = date.today().isoformat()
    date_to = (date.today() + timedelta(days=DAYS_AHEAD)).isoformat()
    url = BASE_URL.format(code=competition_code)
    # NOTA: non filtriamo per "status" lato server. football-data.org usa
    # SCHEDULED per le partite senza orario confermato e TIMED per quelle
    # con orario già fissato (la maggior parte, nel breve periodo) — filtrare
    # solo su SCHEDULED escluderebbe quasi tutte le partite reali. Filtriamo
    # invece noi stessi, escludendo solo quelle già concluse o annullate.
    response = requests.get(
        url,
        headers={"X-Auth-Token": API_KEY},
        params={"dateFrom": date_from, "dateTo": date_to},
        timeout=20,
    )
    response.raise_for_status()
    all_matches = response.json().get("matches", [])
    escluse = {"FINISHED", "POSTPONED", "CANCELLED", "SUSPENDED", "AWARDED"}
    return [m for m in all_matches if m.get("status") not in escluse]


def load_into_db(matches, league_name, conn):
    cur = conn.cursor()
    inserted = 0
    for m in matches:
        home_name = m["homeTeam"]["name"]
        away_name = m["awayTeam"]["name"]
        match_date = m["utcDate"][:10]  # es. "2026-09-27"

        home_id = get_or_create_team(cur, home_name)
        away_id = get_or_create_team(cur, away_name)

        cur.execute("""
            INSERT INTO matches (date, league, season, home_team_id, away_team_id,
                                  home_goals, away_goals)
            VALUES (?, ?, ?, ?, ?, NULL, NULL)
            ON CONFLICT(date, home_team_id, away_team_id) DO NOTHING
        """, (match_date, league_name, "current", home_id, away_id))
        inserted += 1
    conn.commit()
    return inserted


def main():
    conn = sqlite3.connect(DB_PATH)
    with open("schema.sql") as f:
        conn.executescript(f.read())

    for code, league_name in COMPETITION_CODES.items():
        print(f"Controllo partite in arrivo: {league_name}...")
        matches = fetch_upcoming(code)
        n = load_into_db(matches, league_name, conn)
        print(f"  -> {n} partite in programma trovate")

    conn.close()


if __name__ == "__main__":
    main()
