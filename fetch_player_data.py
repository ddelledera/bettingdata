"""
Scarica da Bzzoiro Sports Data (BSD) le statistiche dei giocatori, PARTITA
PER PARTITA, per stimare chi ha più probabilità di segnare (vedi
scorer_model.py e run_player_model.py).

Endpoint verificati con una chiave vera (test_bsd.py):
  /events/?league_id=..&status=finished   partite giocate, con data
  /events/{id}/player-stats/              minuti, gol, xG, tiri di TUTTI i
                                          giocatori di quella partita
  /events/{id}/incidents/                 sostituzioni (chi è entrato dalla
                                          panchina -> non titolare) e gol, con
                                          goal_type "penalty" per i rigori
                                          segnati (quelli sbagliati non ci sono)
  /players/?team_id=..                    rosa attuale, con infortuni

COME FUNZIONA
1. Elenca le partite giocate dei 5 campionati (da inizio stagione scorsa) e
   le collega alle nostre partite (stesso confronto di nomi di fetch_odds.py).
2. Per ogni partita non ancora scaricata: statistiche + sostituzioni
   (2 richieste). La prima volta sono tante (circa 2 stagioni di partite):
   ne scarichiamo al massimo MAX_EVENTS_PER_RUN per esecuzione, il resto nei
   giorni successivi. Poi bastano le poche partite nuove di ogni settimana.
3. Dalle partite collegate ricava a quale nostra squadra corrisponde ogni
   squadra BSD.
4. Per le squadre con partite in arrivo scarica la rosa attuale: nomi,
   squadra di appartenenza (gestisce i trasferimenti) e infortuni.
"""

import os
import sqlite3
from datetime import date

import requests

from db_utils import init_db
from fetch_odds import find_match_id  # confronto nomi squadre, già collaudato

DB_PATH = "data.db"
API_KEY = os.environ.get("BSD_API_KEY", "")
BASE_URL = "https://sports.bzzoiro.com/api/v2"

# ID dei campionati su BSD -> nome nel nostro database
LEAGUES = {4: "Serie A", 1: "Premier League", 3: "La Liga",
           5: "Bundesliga", 6: "Ligue 1"}
# Coppe europee: gli ID li cerchiamo per nome nell'elenco /leagues di BSD.
# Servono soprattutto per i RISULTATI di Europa e Conference League, che la
# nostra fonte gratuita (football-data.org) non fornisce: senza, le schedine
# con quelle partite resterebbero "in attesa" per sempre.
CUPS = {"champions league": "Champions League", "europa league": "Europa League",
        "conference league": "Conference League"}
HISTORY_FROM = "2025-07-01"   # stagione scorsa + stagione in corso
MAX_EVENTS_PER_RUN = 700      # 2 richieste ciascuna: ~1.400 per esecuzione
# partite già scaricate a cui aggiungere rigori e squadra, per giro (il workflow
# "Recupera rigori BSD" lo alza per fare tutto in una volta)
MAX_BACKFILL_PER_RUN = int(os.environ.get("BSD_BACKFILL_MAX", "1300"))

session = requests.Session()
session.headers["Authorization"] = f"Token {API_KEY}"


def _get(path, **params):
    """GET con errori espliciti nel log invece di fallire in silenzio."""
    try:
        r = session.get(f"{BASE_URL}{path}", params=params, timeout=30)
    except requests.RequestException as e:
        print(f"  ATTENZIONE: {path} -> {e}")
        return None
    if r.status_code == 404:
        return {}  # BSD non ha questo dato: inutile riprovare ogni giorno
    if r.status_code != 200:
        print(f"  ATTENZIONE: {path} {params} -> {r.status_code}: {r.text[:200]}")
        return None  # errore temporaneo: si riprova alla prossima esecuzione
    return r.json()


def _get_all(path, **params):
    """Scorre tutte le pagine di un elenco paginato (count/next/results)."""
    out, offset = [], 0
    while True:
        data = _get(path, limit=200, offset=offset, **params)
        if not data:
            break
        page = data.get("results", [])
        out.extend(page)
        if not data.get("next") or not page:
            break
        offset += len(page)
    return out


# ---------------------------------------------------------------------------
# 1. Partite giocate
# ---------------------------------------------------------------------------
def find_cup_leagues():
    found = {}
    for lg in _get_all("/leagues/"):
        name = (lg.get("name") or "").lower()
        if lg.get("is_women"):
            continue
        for key, ours in CUPS.items():
            if key in name and ours not in found.values() and "qualif" not in name:
                found[lg["id"]] = ours
    print(f"  Coppe trovate su BSD: {found or 'nessuna'}")
    return found


def sync_events(cur, leagues):
    today = date.today().isoformat()
    for league_id, league in leagues.items():
        events = _get_all("/events/", league_id=league_id, status="finished",
                          date_from=HISTORY_FROM, date_to=today)
        for e in events:
            cur.execute("""
                INSERT INTO bsd_events (id, league, event_date, home_bsd_team_id,
                    away_bsd_team_id, home_team_name, away_team_name, home_score, away_score)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET home_score = excluded.home_score,
                                              away_score = excluded.away_score
            """, (e["id"], league, e["event_date"], e["home_team_id"],
                  e["away_team_id"], e.get("home_team"), e.get("away_team"),
                  e.get("home_score"), e.get("away_score")))
        print(f"  {league}: {len(events)} partite giocate su BSD")

    # collega alle nostre partite quelle non ancora collegate
    unlinked = cur.execute("""SELECT id, league, event_date, home_team_name, away_team_name
                              FROM bsd_events WHERE match_id IS NULL""").fetchall()
    linked = 0
    for eid, league, edate, home, away in unlinked:
        match_id = find_match_id(cur, league, edate, home or "", away or "")
        if match_id:
            cur.execute("UPDATE bsd_events SET match_id = ? WHERE id = ?", (match_id, eid))
            linked += 1
    print(f"  Collegate alle nostre partite: {linked} nuove "
          f"({len(unlinked) - linked} ancora da collegare)")


def sync_cup_results(cur):
    """Scrive il risultato finale delle partite di coppa collegate che nel
    nostro database non ce l'hanno ancora (Europa e Conference League)."""
    cur.execute("""
        UPDATE matches SET
            home_goals = (SELECT e.home_score FROM bsd_events e WHERE e.match_id = matches.id),
            away_goals = (SELECT e.away_score FROM bsd_events e WHERE e.match_id = matches.id)
        WHERE home_goals IS NULL
          AND league IN ('Champions League', 'Europa League', 'Conference League')
          AND id IN (SELECT match_id FROM bsd_events
                     WHERE match_id IS NOT NULL AND home_score IS NOT NULL)
    """)
    print(f"  Risultati di coppa aggiornati: {cur.rowcount}")


# ---------------------------------------------------------------------------
# 2. Statistiche giocatore per partita
# ---------------------------------------------------------------------------
def get_or_create_player(cur, bsd_id):
    row = cur.execute("SELECT id FROM players WHERE bsd_id = ?", (bsd_id,)).fetchone()
    if row:
        return row[0]
    # il nome arriva dopo, dalla rosa (le statistiche non lo contengono)
    cur.execute("INSERT INTO players (bsd_id, name) VALUES (?, '')", (bsd_id,))
    return cur.lastrowid


def penalty_goals(incidents):
    """Rigori segnati per giocatore (id BSD) dagli eventi della partita."""
    out = {}
    for inc in (incidents or {}).get("incidents", []) or []:
        if inc.get("type") == "goal" and inc.get("goal_type") == "penalty" and inc.get("player_id"):
            out[inc["player_id"]] = out.get(inc["player_id"], 0) + 1
    return out


def sync_player_stats(cur, conn):
    todo = cur.execute("""SELECT id, event_date FROM bsd_events WHERE stats_done = 0
                          ORDER BY event_date DESC LIMIT ?""",
                       (MAX_EVENTS_PER_RUN,)).fetchall()
    remaining = cur.execute("SELECT COUNT(*) FROM bsd_events WHERE stats_done = 0"
                            ).fetchone()[0] - len(todo)
    print(f"  Partite da scaricare in questa esecuzione: {len(todo)} "
          f"(ne resteranno {remaining} per i prossimi giorni)")

    saved = 0
    for i, (eid, edate) in enumerate(todo, 1):
        stats = _get(f"/events/{eid}/player-stats/")
        if stats is None:
            continue  # riproveremo alla prossima esecuzione
        incidents = _get(f"/events/{eid}/incidents/") or {}
        came_on = {inc.get("player_in_id") for inc in incidents.get("incidents", [])
                   if inc.get("type") == "substitution"}
        pens = penalty_goals(incidents)

        for p in stats.get("player_stats", []):
            minutes = p.get("minutes_played") or 0
            if minutes <= 0 or p.get("player_id") is None:
                continue  # in panchina senza entrare: niente da imparare
            player_id = get_or_create_player(cur, p["player_id"])
            started = 0 if p["player_id"] in came_on else 1
            cur.execute("""
                INSERT INTO player_match_stats
                    (player_id, match_bsd_id, match_date, started, minutes,
                     goals, shots, shots_on_target, xg, penalties_taken, penalties_scored,
                     bsd_team_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
                ON CONFLICT(player_id, match_bsd_id) DO UPDATE SET
                    started = excluded.started, minutes = excluded.minutes,
                    goals = excluded.goals, shots = excluded.shots,
                    shots_on_target = excluded.shots_on_target, xg = excluded.xg,
                    penalties_scored = excluded.penalties_scored,
                    bsd_team_id = excluded.bsd_team_id
            """, (player_id, eid, edate[:10], started, minutes, p.get("goals") or 0,
                  p.get("total_shots") or 0, p.get("shots_on_target") or 0,
                  p.get("expected_goals"),  # None se BSD non ha l'xG di quella partita
                  pens.get(p["player_id"], 0), p.get("team_id")))
            saved += 1
        cur.execute("UPDATE bsd_events SET stats_done = 1, pens_done = 1 WHERE id = ?", (eid,))
        if i % 50 == 0:
            conn.commit()
            print(f"    ...{i}/{len(todo)} partite")
    print(f"  Righe giocatore-partita salvate: {saved}")


def backfill_penalties(cur, conn):
    """Per le partite scaricate prima che salvassimo rigori e squadra: rilegge
    eventi e statistiche e completa penalties_scored e bsd_team_id. Qualche
    giro e poi non c'è più niente da fare."""
    todo = cur.execute("""SELECT id FROM bsd_events WHERE stats_done = 1 AND pens_done = 0
                          ORDER BY event_date DESC LIMIT ?""", (MAX_BACKFILL_PER_RUN,)).fetchall()
    remaining = cur.execute("SELECT COUNT(*) FROM bsd_events WHERE stats_done = 1 AND pens_done = 0"
                            ).fetchone()[0] - len(todo)
    if not todo:
        return
    print(f"  Partite a cui aggiungere rigori e squadra: {len(todo)} (ne resteranno {remaining})")
    done = 0
    for i, (eid,) in enumerate(todo, 1):
        incidents = _get(f"/events/{eid}/incidents/")
        stats = _get(f"/events/{eid}/player-stats/")
        if incidents is None or stats is None:
            continue  # errore temporaneo: riproveremo
        pens = penalty_goals(incidents)
        for p in stats.get("player_stats", []) or []:
            if not p.get("player_id"):
                continue
            cur.execute("""UPDATE player_match_stats SET penalties_scored = ?, bsd_team_id = ?
                           WHERE match_bsd_id = ? AND player_id =
                                 (SELECT id FROM players WHERE bsd_id = ?)""",
                        (pens.get(p["player_id"], 0), p.get("team_id"), eid, p["player_id"]))
        cur.execute("UPDATE bsd_events SET pens_done = 1 WHERE id = ?", (eid,))
        done += 1
        if i % 100 == 0:
            conn.commit()
            print(f"    ...{i}/{len(todo)} partite")
    print(f"  Completate: {done}")


# ---------------------------------------------------------------------------
# 3. Squadre BSD -> nostre squadre
# ---------------------------------------------------------------------------
def sync_team_map(cur):
    cur.execute("""
        INSERT OR REPLACE INTO bsd_teams (bsd_id, team_id)
        SELECT e.home_bsd_team_id, m.home_team_id FROM bsd_events e
        JOIN matches m ON m.id = e.match_id
        UNION
        SELECT e.away_bsd_team_id, m.away_team_id FROM bsd_events e
        JOIN matches m ON m.id = e.match_id
    """)
    print(f"  Squadre BSD collegate: {cur.execute('SELECT COUNT(*) FROM bsd_teams').fetchone()[0]}")


# ---------------------------------------------------------------------------
# 4. Rose attuali delle squadre con partite in arrivo
# ---------------------------------------------------------------------------
def sync_rosters(cur):
    teams = cur.execute("""
        SELECT DISTINCT t.id, t.name, b.bsd_id FROM teams t
        LEFT JOIN bsd_teams b ON b.team_id = t.id
        WHERE t.id IN (
            SELECT home_team_id FROM matches WHERE home_goals IS NULL AND date >= date('now')
            UNION
            SELECT away_team_id FROM matches WHERE home_goals IS NULL AND date >= date('now'))
    """).fetchall()
    done, skipped = 0, 0
    for team_id, team_name, bsd_team_id in teams:
        if bsd_team_id is None:
            skipped += 1  # squadra fuori dai 5 campionati (es. coppe europee)
            continue
        roster = _get_all("/players/", team_id=bsd_team_id)
        if not roster:
            continue
        roster_ids = []
        for p in roster:
            player_id = get_or_create_player(cur, p["id"])
            roster_ids.append(p["id"])
            cur.execute("""UPDATE players SET name = ?, team_id = ?, availability = ?,
                                  injury_type = ? WHERE id = ?""",
                        (p.get("name") or p.get("short_name") or "?", team_id,
                         p.get("availability"), p.get("injury_type") or None, player_id))
        # chi era in questa squadra ma non è più in rosa: trasferito
        marks = ",".join("?" * len(roster_ids))
        cur.execute(f"""UPDATE players SET team_id = NULL
                        WHERE team_id = ? AND bsd_id NOT IN ({marks})""",
                    (team_id, *roster_ids))
        done += 1
    print(f"  Rose aggiornate: {done} squadre "
          f"({skipped} senza corrispondenza BSD, normale per le squadre di coppa)")


def main():
    if not API_KEY:
        raise RuntimeError("Manca la chiave API (BSD_API_KEY).")
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)
    cur = conn.cursor()

    print("1. Partite giocate")
    sync_events(cur, {**LEAGUES, **find_cup_leagues()})
    sync_cup_results(cur)
    conn.commit()
    print("2. Statistiche giocatori")
    sync_player_stats(cur, conn)
    conn.commit()
    backfill_penalties(cur, conn)
    conn.commit()
    print("3. Corrispondenza squadre")
    sync_team_map(cur)
    conn.commit()
    print("4. Rose attuali")
    sync_rosters(cur)
    conn.commit()
    conn.close()
    print("Fatto.")


if __name__ == "__main__":
    main()
