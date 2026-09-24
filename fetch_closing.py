"""
Scarica le quote di Pinnacle poco prima dell'inizio delle partite: sono la
"chiusura" contro cui misuriamo il CLV delle scommesse virtuali.

Lo avvia il workflow closing.yml solo quando check_closing.py dice che
serve. COSTO: 1 richiesta OddsPapi (copre tutti e 5 i campionati).

Le quote finiscono in reference_odds come quelle del giro giornaliero, e la
chiusura delle scommesse virtuali si aggiorna solo per le partite non
ancora iniziate.
"""

import sqlite3
from datetime import datetime, timezone

import requests

from db_utils import init_db
from markets import fair_probabilities
from check_closing import closing_targets, budget_status
from fetch_odds import (DB_PATH, TOURNAMENTS, REFERENCE_BOOKMAKER, api_get, save_api_calls,
                        load_team_names, load_market_meta, parse_markets, fixture_started,
                        match_id_for_fixture, save_prices, update_virtual_closing)


def main():
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)
    cur = conn.cursor()
    now = datetime.now(timezone.utc)

    # ricontrollo: tra la verifica e ora qualcun altro potrebbe aver già scaricato
    targets = closing_targets(conn, now)
    ok, detail = budget_status(conn, now)
    if not targets or not ok:
        print(f"Niente da fare ora ({detail}).")
        return

    slug, label = REFERENCE_BOOKMAKER
    snapshot_time = now.isoformat()
    print(f"Scarico la chiusura Pinnacle per {len(targets)} partite in partenza...")
    try:
        data = api_get("/odds-by-tournaments", purpose="chiusura",
                       tournamentIds=",".join(str(t) for t in TOURNAMENTS),
                       bookmaker=slug, oddsFormat="decimal")
    except requests.RequestException as e:
        save_api_calls(conn)
        print(f"  ERRORE: {e}")
        return
    fixtures = data if isinstance(data, list) else []

    names = load_team_names(conn, {int(fx[k]) for fx in fixtures
                                   for k in ("participant1Id", "participant2Id") if fx.get(k)})
    meta = load_market_meta(conn)
    target_ids = {mid for mid, _ in targets}
    saved, closed = 0, 0
    for fx in fixtures:
        league = TOURNAMENTS.get(fx.get("tournamentId"))
        prices, _ = parse_markets((fx.get("bookmakerOdds") or {}).get(slug), meta)
        if not league or not prices or not fx.get("startTime") or fixture_started(fx, now):
            continue
        home = names.get(int(fx["participant1Id"]), "?")
        away = names.get(int(fx["participant2Id"]), "?")
        match_id = match_id_for_fixture(cur, fx, league, home, away)
        if match_id is None:
            continue
        # tutte le partite in arrivo: la richiesta costa uguale, e così lo
        # storico dei movimenti di Pinnacle è più ricco
        save_prices(cur, "reference_odds", match_id, label, prices, snapshot_time)
        fair = fair_probabilities(prices)
        if fair:
            update_virtual_closing(cur, match_id, fair, snapshot_time)
        saved += 1
        closed += match_id in target_ids
    conn.commit()
    save_api_calls(conn)
    conn.close()
    print(f"  Quote Pinnacle salvate per {saved} partite; chiusura presa per "
          f"{closed} delle {len(targets)} partite in partenza.")


if __name__ == "__main__":
    main()
