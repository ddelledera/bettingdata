"""
Scarica da Bzzoiro Sports Data (BSD) le statistiche dei giocatori delle
squadre che hanno partite in arrivo, per poter poi stimare chi ha più
probabilità di segnare (vedi scorer_model.py).

STATO: prima versione. Gli endpoint esatti sono basati sulla documentazione
pubblica (sports.bzzoiro.com/docs/) — la prima esecuzione vera dirà se le
assunzioni fatte qui sono corrette. Per questo lo script stampa sempre
il dettaglio di eventuali errori, invece di fallire in silenzio: se qualcosa
non torna, il log dice esattamente cosa correggere.
"""

import os
import sqlite3
import requests
from datetime import datetime, timezone
from team_utils import normalize_team_name

DB_PATH = "data.db"
API_KEY = os.environ.get("BSD_API_KEY", "")
BASE_URL = "https://sports.bzzoiro.com/api/v2"


def _headers():
    return {"Authorization": f"Token {API_KEY}"}


def _get(path, params=None):
    """GET con gestione errori esplicita: se qualcosa va storto, stampa
    il dettaglio invece di fallire in silenzio."""
    url = f"{BASE_URL}{path}"
    response = requests.get(url, headers=_headers(), params=params or {}, timeout=20)
    if response.status_code != 200:
        print(f"  ATTENZIONE: {url} -> {response.status_code}")
        print(f"    Risposta: {response.text[:300]}")
        return None
    return response.json()


def find_bsd_team_id(team_name, league_hint=None):
    """Cerca una squadra su BSD per nome, ritorna il suo id BSD o None."""
    data = _get("/teams/", params={"search": team_name})
    if not data or not data.get("results"):
        return None
    # Prendiamo il primo risultato: di solito la ricerca per nome è già
    # abbastanza precisa. Se in pratica non lo fosse, lo scopriremo dai
    # log (nomi di squadra sbagliati nei dati salvati) e affineremo qui.
    return data["results"][0]["id"]


def get_team_players(bsd_team_id):
    """Lista dei giocatori di una squadra, con le loro statistiche stagionali."""
    data = _get(f"/teams/{bsd_team_id}/players/")
    if not data:
        return []
    return data.get("results", data if isinstance(data, list) else [])


def get_player_season_stats(bsd_player_id):
    """Statistiche stagionali aggregate di un giocatore (gol, xG, minuti)."""
    return _get(f"/players/{bsd_player_id}/stats/")


def get_or_create_player(cur, bsd_id, name, team_id):
    cur.execute("SELECT id FROM players WHERE bsd_id = ?", (bsd_id,))
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute(
        "INSERT INTO players (bsd_id, name, team_id) VALUES (?, ?, ?)",
        (bsd_id, name, team_id),
    )
    return cur.lastrowid


def get_team_id_by_name(cur, name):
    name = normalize_team_name(name)
    row = cur.execute("SELECT id FROM teams WHERE name = ?", (name,)).fetchone()
    return row[0] if row else None


def main():
    if not API_KEY:
        raise RuntimeError(
            "Manca la chiave API (BSD_API_KEY). "
            "Va impostata prima di eseguire questo script."
        )

    conn = sqlite3.connect(DB_PATH)
    with open("schema.sql") as f:
        conn.executescript(f.read())
    cur = conn.cursor()

    # Squadre con almeno una partita in arrivo, nei campionati che seguiamo
    teams_with_upcoming = cur.execute("""
        SELECT DISTINCT t.id, t.name FROM teams t
        WHERE t.id IN (
            SELECT home_team_id FROM matches WHERE home_goals IS NULL AND date >= date('now')
            UNION
            SELECT away_team_id FROM matches WHERE home_goals IS NULL AND date >= date('now')
        )
    """).fetchall()

    print(f"Squadre con partite in arrivo da aggiornare: {len(teams_with_upcoming)}")

    players_updated, teams_not_found = 0, []

    for team_id, team_name in teams_with_upcoming:
        print(f"\n{team_name}...")
        bsd_team_id = find_bsd_team_id(team_name)
        if bsd_team_id is None:
            print(f"  Squadra non trovata su BSD, salto.")
            teams_not_found.append(team_name)
            continue

        players = get_team_players(bsd_team_id)
        print(f"  {len(players)} giocatori nella rosa")

        for p in players:
            bsd_player_id = p.get("id")
            player_name = p.get("name") or p.get("full_name") or "?"
            if bsd_player_id is None:
                continue

            player_id = get_or_create_player(cur, bsd_player_id, player_name, team_id)

            stats = get_player_season_stats(bsd_player_id)
            if not stats:
                continue

            # Salviamo le statistiche stagionali come una singola "riga
            # riassuntiva" (match_bsd_id negativo, per non confonderla con
            # una vera partita) — comodo finché non serve lo storico
            # partita-per-partita, che aggiungeremo se questa prima
            # versione si dimostra affidabile.
            minutes = stats.get("minutes_played") or stats.get("minutes") or 0
            goals = stats.get("goals") or 0
            xg = stats.get("expected_goals") or stats.get("xg") or 0
            shots = stats.get("shots") or 0
            shots_on_target = stats.get("shots_on_target") or 0
            penalties_taken = stats.get("penalties_taken") or 0
            penalties_scored = stats.get("penalties_scored") or 0

            cur.execute("""
                INSERT INTO player_match_stats
                    (player_id, match_bsd_id, match_date, started, minutes,
                     goals, shots, shots_on_target, xg, penalties_taken, penalties_scored)
                VALUES (?, -1, date('now'), NULL, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(player_id, match_bsd_id) DO UPDATE SET
                    match_date = excluded.match_date,
                    minutes = excluded.minutes,
                    goals = excluded.goals,
                    shots = excluded.shots,
                    shots_on_target = excluded.shots_on_target,
                    xg = excluded.xg,
                    penalties_taken = excluded.penalties_taken,
                    penalties_scored = excluded.penalties_scored
            """, (player_id, minutes, goals, shots, shots_on_target, xg,
                  penalties_taken, penalties_scored))
            players_updated += 1

    conn.commit()
    conn.close()

    print(f"\nFatto. Statistiche aggiornate per {players_updated} giocatori.")
    if teams_not_found:
        print(f"Squadre non trovate su BSD ({len(teams_not_found)}): {teams_not_found}")


if __name__ == "__main__":
    main()
