"""
CONTROLLO DEI GIORNI DI PARTITA (da avviare a mano il 9-10 ottobre, e
quando serve): in un colpo solo verifica le tre cose nuove che si possono
controllare solo quando si gioca.
  1. Chiusure Pinnacle: per le partite con scommesse virtuali iniziate
     nelle ultime 48 ore, quanto prima del calcio d'inizio è stata presa
     l'ultima quota Pinnacle, e quante richieste "chiusura" sono state fatte.
  2. Marcatori: quante quote marcatore sono arrivate, quante sono state
     abbinate a un nostro giocatore (e i nomi NON abbinati), quante
     scommesse virtuali sui marcatori sono state registrate.
  3. Formazioni BSD: per le partite di oggi e domani, se BSD pubblica
     formazioni probabili (e indisponibili) prima del calcio d'inizio.
Non modifica niente: stampa solo un riassunto nel log.
"""

import json
import os
import sqlite3
from datetime import date, datetime, timedelta, timezone

import requests

DB_PATH = "data.db"
BSD = "https://sports.bzzoiro.com/api/v2"
BSD_LEAGUES = {4: "Serie A", 1: "Premier League", 3: "La Liga", 5: "Bundesliga", 6: "Ligue 1"}


def closings(conn):
    print("=== 1. CHIUSURE PINNACLE (partite iniziate nelle ultime 48 ore) ===")
    rows = conn.execute("""
        SELECT m.id, h.name, a.name, m.kickoff_utc,
               (SELECT MAX(r.snapshot_time) FROM reference_odds r
                 WHERE r.match_id = m.id AND r.bookmaker = 'Pinnacle'
                   AND datetime(r.snapshot_time) < m.kickoff_utc),
               (SELECT COUNT(*) FROM virtual_bets v WHERE v.match_id = m.id)
        FROM matches m JOIN teams h ON h.id = m.home_team_id JOIN teams a ON a.id = m.away_team_id
        WHERE m.kickoff_utc <= datetime('now') AND m.kickoff_utc >= datetime('now', '-48 hours')
          AND EXISTS (SELECT 1 FROM virtual_bets v WHERE v.match_id = m.id)
        ORDER BY m.kickoff_utc""").fetchall()
    if not rows:
        print("  Nessuna partita con scommesse virtuali iniziata nelle ultime 48 ore.")
    minuti = []
    for mid, h, a, ko, last, n in rows:
        if last:
            m = (datetime.fromisoformat(ko).replace(tzinfo=timezone.utc)
                 - datetime.fromisoformat(last).astimezone(timezone.utc)).total_seconds() / 60
            minuti.append(m)
            print(f"  {h} - {a} ({ko} UTC, {n} scommesse): ultima Pinnacle {m:.0f} min prima")
        else:
            print(f"  {h} - {a} ({ko} UTC, {n} scommesse): nessuna quota Pinnacle prima dell'inizio!")
    if minuti:
        vicine = sum(x <= 60 for x in minuti)
        print(f"  Chiusura entro un'ora dall'inizio: {vicine} partite su {len(minuti)} "
              f"(mediana {sorted(minuti)[len(minuti) // 2]:.0f} min)")
    calls = conn.execute("""SELECT COUNT(*) FROM api_calls WHERE purpose = 'chiusura'
                            AND called_at >= ?""",
                         ((datetime.now(timezone.utc) - timedelta(hours=48)).isoformat(),)).fetchone()[0]
    month = conn.execute("""SELECT COUNT(*), SUM(purpose = 'chiusura') FROM api_calls
                            WHERE substr(called_at, 1, 7) = ?""",
                         (datetime.now(timezone.utc).strftime("%Y-%m"),)).fetchone()
    print(f"  Richieste 'chiusura' nelle ultime 48 ore: {calls}. Questo mese: {month[0]} richieste "
          f"OddsPapi in tutto, di cui {month[1] or 0} per le chiusure (limite 250).")


def scorers(conn):
    print("\n=== 2. MARCATORI ===")
    since = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
    tot, ok, partite = conn.execute("""SELECT COUNT(*), SUM(player_id IS NOT NULL), COUNT(DISTINCT match_id)
                                       FROM scorer_odds WHERE snapshot_time >= ?""", (since,)).fetchone()
    if not tot:
        print("  Nessuna quota marcatore nelle ultime 48 ore (i bookmaker le aprono 1-3 giorni prima).")
    else:
        print(f"  Quote marcatore delle ultime 48 ore: {tot} su {partite} partite; abbinate a un nostro "
              f"giocatore: {ok} ({ok / tot:.0%}).")
        missing = conn.execute("""
            SELECT s.player_name, h.name || ' - ' || a.name, COUNT(*)
            FROM scorer_odds s JOIN matches m ON m.id = s.match_id
            JOIN teams h ON h.id = m.home_team_id JOIN teams a ON a.id = m.away_team_id
            WHERE s.snapshot_time >= ? AND s.player_id IS NULL
            GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 25""", (since,)).fetchall()
        if missing:
            print("  Nomi NON abbinati (fino a 25):")
            for name, match, n in missing:
                print(f"    {name}  ({match})")
    try:
        vs = conn.execute("""SELECT COUNT(*), SUM(result IS NULL), SUM(result = 'segna'),
                                    SUM(result = 'non segna'), SUM(result = 'rimborsata')
                             FROM virtual_scorer_bets""").fetchone()
        print(f"  Scommesse virtuali marcatori: {vs[0]} in tutto; in attesa {vs[1] or 0}, "
              f"segna {vs[2] or 0}, non segna {vs[3] or 0}, rimborsate {vs[4] or 0}.")
    except sqlite3.OperationalError:
        print("  Tabella delle scommesse virtuali marcatori non ancora creata.")


def lineups():
    print("\n=== 3. FORMAZIONI BSD PRIMA DELLA PARTITA ===")
    key = os.environ.get("BSD_API_KEY", "")
    if not key:
        print("  Manca BSD_API_KEY: controllo saltato.")
        return
    head = {"Authorization": f"Token {key}"}
    today = date.today()
    found = 0
    for lid, league in BSD_LEAGUES.items():
        try:
            r = requests.get(f"{BSD}/events/", headers=head, timeout=30,
                             params={"league_id": lid, "date_from": today.isoformat(),
                                     "date_to": (today + timedelta(days=1)).isoformat(), "limit": 20})
            events = r.json().get("results", []) if r.status_code == 200 else []
        except (requests.RequestException, ValueError):
            events = []
        for e in events:
            if str(e.get("status", "")).lower() not in ("notstarted", "not_started", "scheduled", ""):
                continue
            try:
                d = requests.get(f"{BSD}/events/{e['id']}/lineups/", headers=head, timeout=30).json()
            except (requests.RequestException, ValueError):
                continue
            found += 1
            lu = d.get("lineups") or {}
            players = {side: len((lu.get(side) or {}).get("players") or []) for side in ("home", "away")}
            conf = {side: (lu.get(side) or {}).get("confidence") for side in ("home", "away")}
            unav = d.get("unavailable_players")
            print(f"  {league}: {e.get('home_team')} - {e.get('away_team')} ({e.get('event_date')}): "
                  f"stato {d.get('lineup_status')}, giocatori {players}, affidabilità {conf}, "
                  f"indisponibili {len(unav) if isinstance(unav, list) else unav}, "
                  f"aggiornato {d.get('updated_at')}")
            if found == 1 and lu:
                print(f"    esempio: {json.dumps(d, ensure_ascii=False)[:700]}")
    if not found:
        print("  Nessuna partita dei 5 campionati oggi o domani.")


def main():
    conn = sqlite3.connect(DB_PATH)
    print(f"Controllo del {datetime.now(timezone.utc):%d/%m/%Y %H:%M} UTC\n")
    closings(conn)
    scorers(conn)
    conn.close()
    lineups()
    print("\nFatto.")


if __name__ == "__main__":
    main()
