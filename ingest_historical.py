"""
Scarica lo storico reale delle partite (risultati + quote storiche dei
bookmaker) da una fonte gratuita che non richiede alcun account, e lo
carica nel nostro database.

Fonte: football-data.co.uk — pubblica da oltre 30 anni file CSV gratuiti
con risultati e quote per i principali campionati europei. Nessuna
registrazione richiesta.

Questo script è pensato per girare automaticamente (lo collegheremo a un
programmatore gratuito nella fase successiva, così scarica i dati nuovi
da solo, senza che tu debba fare nulla).
"""

import sqlite3
from db_utils import init_db
import requests
import pandas as pd
from io import StringIO
from datetime import datetime, date
from team_utils import get_or_create_team

DB_PATH = "data.db"

# Codice-lega usato dalla fonte dati, per ciascun campionato che seguiamo.
LEAGUE_CODES = {
    "I1": "Serie A",
    "E0": "Premier League",
    "SP1": "La Liga",
    "D1": "Bundesliga",
    "F1": "Ligue 1",
    "N1": "Eredivisie",
    "B1": "Belgian Pro League",
    "P1": "Primeira Liga",
    "T1": "Super Lig",
    "G1": "Super League Greece",
    "SC0": "Scottish Premiership",
    # Seconde divisioni degli stessi paesi: spesso le squadre di Conference
    # League vengono proprio da qui (es. Sunderland gioca in Championship).
    "E1": "Championship",
    "I2": "Serie B",
    "SP2": "Segunda Division",
    "D2": "2. Bundesliga",
    "F2": "Ligue 2",
    "SC1": "Scottish Championship",
}


def get_current_season_codes(num_seasons=4):
    """
    Genera i codici stagione (formato 'YYZZ', es. '2526' per la stagione
    2025-26) delle ultime 'num_seasons' stagioni, INCLUSA quella in corso.

    Così non serve più aggiornare la lista a mano ogni anno quando inizia
    una nuova stagione — prima era un elenco fisso che si "dimenticava"
    automaticamente della stagione più recente, uno dei problemi trovati
    nella revisione del codice.

    Una stagione di calcio europea va grosso modo da luglio a giugno
    dell'anno dopo: se siamo tra luglio e dicembre, la stagione in corso
    è "iniziata quest'anno"; se siamo tra gennaio e giugno, è iniziata
    l'anno scorso.
    """
    today = date.today()
    current_start_year = today.year if today.month >= 7 else today.year - 1
    codes = []
    for i in range(num_seasons):
        start_year = current_start_year - i
        end_year = start_year + 1
        codes.append(f"{str(start_year)[-2:]}{str(end_year)[-2:]}")
    return list(reversed(codes))  # dal più vecchio al più recente


SEASONS = get_current_season_codes(4)

BASE_URL = "https://www.football-data.co.uk/mmz4281/{season}/{league}.csv"

# Colonne con le quote 1X2 medie di mercato (presenti nella maggior parte
# dei file storici). Le usiamo per popolare anche lo storico delle quote.
ODDS_COLUMNS = {"Home": "AvgH", "Draw": "AvgD", "Away": "AvgA"}


def download_season(league_code, season):
    """Scarica un file CSV di una stagione/campionato. Ritorna un DataFrame o None."""
    url = BASE_URL.format(season=season, league=league_code)
    try:
        response = requests.get(url, timeout=20)
        response.raise_for_status()
        # encoding='latin-1' perché il sito usa questa codifica per i caratteri accentati
        df = pd.read_csv(StringIO(response.content.decode("latin-1")))
        return df
    except Exception as e:
        print(f"  Avviso: impossibile scaricare {league_code} {season}: {e}")
        return None


def load_into_db(df, league_name, season, conn):
    cur = conn.cursor()
    inserted, updated_odds = 0, 0

    for _, row in df.iterrows():
        # Alcune righe a fine file possono essere vuote
        if pd.isna(row.get("HomeTeam")) or pd.isna(row.get("AwayTeam")):
            continue

        match_date = pd.to_datetime(row["Date"], dayfirst=True, errors="coerce")
        if pd.isna(match_date):
            continue

        home_id = get_or_create_team(cur, row["HomeTeam"])
        away_id = get_or_create_team(cur, row["AwayTeam"])

        home_goals = row.get("FTHG")
        away_goals = row.get("FTAG")
        home_goals = int(home_goals) if pd.notna(home_goals) else None
        away_goals = int(away_goals) if pd.notna(away_goals) else None

        cur.execute("""
            INSERT INTO matches (date, league, season, home_team_id, away_team_id,
                                  home_goals, away_goals)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(date, home_team_id, away_team_id) DO UPDATE SET
                home_goals = excluded.home_goals,
                away_goals = excluded.away_goals
        """, (match_date.strftime("%Y-%m-%d"), league_name, season,
              home_id, away_id, home_goals, away_goals))

        match_id = cur.execute(
            "SELECT id FROM matches WHERE date=? AND home_team_id=? AND away_team_id=?",
            (match_date.strftime("%Y-%m-%d"), home_id, away_id)
        ).fetchone()[0]
        inserted += 1

        # Se il file ha le quote medie storiche, le salviamo come "fotografia" di chiusura
        if all(col in row and pd.notna(row[col]) for col in ODDS_COLUMNS.values()):
            for selection, col in ODDS_COLUMNS.items():
                cur.execute("""
                    INSERT INTO odds_snapshots (match_id, bookmaker, market, selection,
                                                 odds, snapshot_time)
                    VALUES (?, 'market_average', '1X2', ?, ?, ?)
                """, (match_id, selection, float(row[col]),
                      match_date.strftime("%Y-%m-%d")))
            updated_odds += 1

    conn.commit()
    return inserted, updated_odds


def main():
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)

    for league_code, league_name in LEAGUE_CODES.items():
        for season in SEASONS:
            print(f"Scarico {league_name} stagione {season}...")
            df = download_season(league_code, season)
            if df is None or df.empty:
                continue
            n_matches, n_odds = load_into_db(df, league_name, season, conn)
            print(f"  -> {n_matches} partite caricate, {n_odds} con quote storiche")

    conn.close()
    print("\nFatto. Dati salvati in data.db")


if __name__ == "__main__":
    main()
