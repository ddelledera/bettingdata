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
from db_utils import init_db
import requests
from datetime import date, timedelta
from team_utils import get_or_create_team
from team_matching import resolve_team, repair_team_names, EUROPEAN
import run_stats

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

DAYS_AHEAD = 21  # quante partite future guardare (abbastanza da superare le
                  # soste per le nazionali, che durano fino a 2 settimane)


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
    unresolved = set()
    for m in matches:
        home_name = m["homeTeam"]["name"]
        away_name = m["awayTeam"]["name"]
        match_date = m["utcDate"][:10]  # es. "2026-09-27"

        home_id = resolve_team(cur, home_name, league_name)
        away_id = resolve_team(cur, away_name, league_name)
        for name, tid in ((home_name, home_id), (away_name, away_id)):
            if tid is None:
                unresolved.add(name)
        home_id = home_id or get_or_create_team(cur, home_name)
        away_id = away_id or get_or_create_team(cur, away_name)

        # orario di inizio in UTC (es. "2026-09-27T13:00:00Z" -> "2026-09-27 13:00:00"):
        # serve a non usare quote prese a partita iniziata e a misurare la
        # vera quota di chiusura. Aggiornato a ogni giro: gli orari cambiano.
        cur.execute("""
            INSERT INTO matches (date, league, season, home_team_id, away_team_id,
                                  home_goals, away_goals, kickoff_utc)
            VALUES (?, ?, ?, ?, ?, NULL, NULL, datetime(?))
            ON CONFLICT(date, home_team_id, away_team_id) DO UPDATE SET
                kickoff_utc = COALESCE(excluded.kickoff_utc, kickoff_utc)
        """, (match_date, league_name, "current", home_id, away_id, m.get("utcDate")))
        inserted += 1
    conn.commit()
    if unresolved:
        # il modello non potrà prevedere queste partite: nome da aggiungere
        # in team_utils.py (NORMALIZE_MAP)
        print(f"  ATTENZIONE, squadre non riconosciute: {sorted(unresolved)}")
    return inserted, sorted(unresolved)


def main():
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)
    # ripara anche le coppe: squadre salvate con il nome di football-data.org
    # o di The Odds API invece di quello dello storico
    repair_team_names(conn, list(COMPETITION_CODES.values()) + list(EUROPEAN))

    per_league, unresolved = {}, {}
    for code, league_name in COMPETITION_CODES.items():
        print(f"Controllo partite in arrivo: {league_name}...")
        matches = fetch_upcoming(code)
        n, missing = load_into_db(matches, league_name, conn)
        per_league[league_name] = n
        if missing:
            unresolved[league_name] = missing
        print(f"  -> {n} partite in programma trovate")
    run_stats.record("partite", {"per_campionato": per_league, "non_riconosciute": unresolved})

    conn.close()


if __name__ == "__main__":
    main()
