"""
Scarica i risultati delle partite di Champions League già giocate, provando
le ultime 3 stagioni (fonte: football-data.org).

A COSA SERVE: il nostro modello valuta la forza di ogni squadra confrontando
i suoi risultati con quelli delle altre squadre del SUO campionato. Ma
Inter e Bayern Monaco non giocano mai in campionato — l'unico posto dove
si confrontano davvero è la Champions League. Usando questi risultati come
"ponte", il modello impara anche come si confrontano squadre di campionati
diversi, cosa che serve per prevedere le partite di Champions/Europa/Conference.

LIMITE ONESTO: il livello gratuito di football-data.org è documentato come
limitato alla sola stagione in corso. Questo script prova comunque a
chiedere anche le 2 stagioni precedenti: se il servizio le rifiuta, lo
script lo segnala chiaramente nei log e prosegue lo stesso con quello che
riesce a ottenere (mai un errore bloccante).
"""

import os
import sqlite3
import requests
from datetime import date
from team_utils import get_or_create_team

DB_PATH = "data.db"
API_KEY = os.environ.get("FOOTBALL_DATA_API_KEY", "")
URL = "https://api.football-data.org/v4/competitions/CL/matches"

# football-data.org identifica una stagione con l'anno in cui INIZIA
# (es. la stagione 2025-26 è "season=2025"). Proviamo quella corrente e le
# 2 precedenti, come chiesto: 3 anni di storico.
_today = date.today()
CURRENT_SEASON_START_YEAR = _today.year if _today.month >= 7 else _today.year - 1
SEASONS_TO_TRY = [CURRENT_SEASON_START_YEAR - i for i in range(3)]


def fetch_finished(season):
    if not API_KEY:
        raise RuntimeError(
            "Manca la chiave API (FOOTBALL_DATA_API_KEY). "
            "Va impostata prima di eseguire questo script."
        )
    response = requests.get(
        URL, headers={"X-Auth-Token": API_KEY},
        params={"status": "FINISHED", "season": season}, timeout=20,
    )
    if response.status_code == 403:
        return None  # stagione non inclusa nel livello gratuito
    response.raise_for_status()
    return response.json().get("matches", [])


def load_into_db(matches, conn):
    cur = conn.cursor()
    n = 0
    for m in matches:
        score = m.get("score", {}).get("fullTime", {})
        hg, ag = score.get("home"), score.get("away")
        if hg is None or ag is None:
            continue

        home_id = get_or_create_team(cur, m["homeTeam"]["name"])
        away_id = get_or_create_team(cur, m["awayTeam"]["name"])
        match_date = m["utcDate"][:10]
        season_label = m.get("season", {}).get("startDate", "")[:4] or "sconosciuta"

        cur.execute("""
            INSERT INTO matches (date, league, season, home_team_id, away_team_id,
                                  home_goals, away_goals)
            VALUES (?, 'Champions League', ?, ?, ?, ?, ?)
            ON CONFLICT(date, home_team_id, away_team_id) DO UPDATE SET
                home_goals = excluded.home_goals,
                away_goals = excluded.away_goals
        """, (match_date, season_label, home_id, away_id, hg, ag))
        n += 1
    conn.commit()
    return n


def main():
    conn = sqlite3.connect(DB_PATH)
    with open("schema.sql") as f:
        conn.executescript(f.read())

    total = 0
    for season in SEASONS_TO_TRY:
        print(f"Provo a scaricare Champions League, stagione {season}-{season+1}...")
        matches = fetch_finished(season)
        if matches is None:
            print(f"  Non disponibile nel livello gratuito (stagione troppo vecchia per questo piano).")
            continue
        n = load_into_db(matches, conn)
        print(f"  -> {n} partite salvate")
        total += n

    print(f"\nTotale partite Champions League raccolte come collegamento tra campionati: {total}")
    conn.close()


if __name__ == "__main__":
    main()
