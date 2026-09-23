"""
Controlla le schedine confermate (salvate in tracked_slips.json): per
quelle le cui partite sono ormai TUTTE terminate, calcola il risultato
reale — vinta o persa — e lo salva insieme al dettaglio di ogni partita.

Va eseguito dopo run_model.py (serve che i risultati delle partite siano
già aggiornati nel database).
"""

import json
import sqlite3
from datetime import datetime, timezone

from markets import is_winner, long_label

DB_PATH = "data.db"
SLIPS_PATH = "tracked_slips.json"


def load_slips():
    try:
        with open(SLIPS_PATH) as f:
            return json.load(f)
    except FileNotFoundError:
        return []


def save_slips(slips):
    with open(SLIPS_PATH, "w") as f:
        json.dump(slips, f, indent=2, ensure_ascii=False)


def get_match_result(conn, match_id):
    """Ritorna (gol casa, gol trasferta) se la partita è finita, altrimenti None."""
    row = conn.execute(
        "SELECT home_goals, away_goals FROM matches WHERE id = ?", (match_id,)
    ).fetchone()
    if row is None or row[0] is None:
        return None
    return row


def main():
    slips = load_slips()
    if not slips:
        print("Nessuna schedina salvata da controllare.")
        save_slips(slips)  # crea comunque il file (vuoto) se non esiste ancora
        return

    conn = sqlite3.connect(DB_PATH)
    updated = 0
    still_pending = 0

    for slip in slips:
        if slip.get("status") != "pending":
            continue

        results = []
        all_played = True
        for leg in slip["legs"]:
            score = get_match_result(conn, leg["match_id"])
            if score is None:
                all_played = False
                break
            hg, ag = score
            results.append({
                "match_label": leg["match_label"],
                "selection": long_label(leg["selection"]),
                "actual_result": f"{hg}-{ag}",
                # vale per tutti i mercati: 1X2, doppia chance, Goal/No Goal, Under/Over
                "won": is_winner(leg["selection"], hg, ag),
            })

        if not all_played:
            still_pending += 1
            continue

        all_won = all(r["won"] for r in results)
        combined_odds = slip["combined_odds"]
        stake = slip["stake"]
        payout = stake * combined_odds if all_won else 0.0
        profit = payout - stake

        slip["status"] = "won" if all_won else "lost"
        slip["settled_at"] = datetime.now(timezone.utc).isoformat()
        slip["result_summary"] = {
            "legs": results,
            "payout": round(payout, 2),
            "profit": round(profit, 2),
        }
        updated += 1
        print(f"Schedina {slip['id'][:8]}: {'VINTA' if all_won else 'PERSA'} "
              f"(puntata {stake}, profitto {profit:+.2f})")

    conn.close()
    save_slips(slips)
    print(f"\nSchedine concluse questa volta: {updated}. Ancora in attesa: {still_pending}.")


if __name__ == "__main__":
    main()
