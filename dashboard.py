"""
La pagina web dell'app. Mostra le partite in arrivo, confronta le nostre
probabilità con le migliori quote disponibili tra i bookmaker, e ordina
tutto per convenienza (valore).

Per vederla in azione (una volta pubblicata): si apre da sola nel browser.
Non c'è nulla da capire o configurare qui dentro.
"""

import sqlite3
import pandas as pd
import streamlit as st
from value_calculator import remove_bookmaker_margin, find_value_bets

DB_PATH = "data.db"

st.set_page_config(page_title="Previsioni Calcio", page_icon="⚽", layout="wide")
st.title("⚽ Le mie previsioni calcio")
st.caption("Partite in arrivo ordinate per convenienza, con quota migliore e puntata consigliata.")


def get_connection():
    return sqlite3.connect(DB_PATH, check_same_thread=False)


def load_upcoming_with_predictions(conn):
    query = """
        SELECT m.id, m.date, m.league, h.name AS home, a.name AS away,
               p.prob_home, p.prob_draw, p.prob_away
        FROM matches m
        JOIN teams h ON m.home_team_id = h.id
        JOIN teams a ON m.away_team_id = a.id
        JOIN model_predictions p ON p.match_id = m.id
        WHERE m.date >= date('now')
        ORDER BY m.date
    """
    return pd.read_sql_query(query, conn)


def best_odds_for_match(conn, match_id):
    """Per ogni esito (1/X/2), trova la quota più alta tra tutti i bookmaker
    monitorati (line shopping automatico)."""
    query = """
        SELECT selection, MAX(odds) as best_odds, bookmaker
        FROM odds_snapshots
        WHERE match_id = ?
        GROUP BY selection
    """
    rows = conn.execute(query, (match_id,)).fetchall()
    if len(rows) < 3:
        return None
    return {r[0]: r[1] for r in rows}, {r[0]: r[2] for r in rows}


def risk_label(odds):
    """Etichetta semplice del livello di rischio in base alla quota."""
    if odds <= 1.8:
        return "🟢 Rischio basso"
    elif odds <= 3.0:
        return "🟡 Rischio medio"
    return "🔴 Rischio alto"


conn = get_connection()

try:
    matches_df = load_upcoming_with_predictions(conn)
except Exception:
    st.warning("Il database non è ancora pronto. Esegui prima gli script di "
               "aggiornamento dati (storico, partite in arrivo, quote, modello).")
    st.stop()

if matches_df.empty:
    st.info("Nessuna partita in arrivo con previsione calcolata al momento. "
             "Torna più tardi, oppure aggiorna i dati.")
    st.stop()

min_ev = st.slider("Mostra solo scommesse con valore atteso di almeno:",
                    min_value=0, max_value=20, value=3, format="%d%%") / 100

all_opportunities = []
for _, row in matches_df.iterrows():
    odds_info = best_odds_for_match(conn, row["id"])
    if odds_info is None:
        continue  # non abbiamo ancora quote per questa partita
    best_odds, best_bookmaker = odds_info

    model_probs = {"Home": row["prob_home"], "Draw": row["prob_draw"], "Away": row["prob_away"]}
    value_bets = find_value_bets(model_probs, best_odds, min_ev=min_ev)

    for vb in value_bets:
        all_opportunities.append({
            "Partita": f"{row['home']} vs {row['away']}",
            "Campionato": row["league"],
            "Data": row["date"],
            "Esito": {"Home": "1 (casa)", "Draw": "X (pareggio)", "Away": "2 (trasferta)"}[vb["selection"]],
            "Nostra probabilità": f"{vb['model_probability']:.0%}",
            "Quota migliore": vb["odds"],
            "Bookmaker": best_bookmaker[vb["selection"]],
            "Valore atteso (EV)": f"{vb['ev']:+.1%}",
            "Puntata consigliata": f"{vb['kelly_stake_pct']:.1f}% del capitale",
            "Rischio": risk_label(vb["odds"]),
            "_ev_sort": vb["ev"],
        })

if not all_opportunities:
    st.info("Nessuna scommessa di valore trovata al momento con la soglia scelta. "
             "Prova ad abbassare la soglia qui sopra, oppure torna più tardi.")
else:
    opp_df = pd.DataFrame(all_opportunities).sort_values("_ev_sort", ascending=False)
    opp_df = opp_df.drop(columns=["_ev_sort"])
    st.subheader(f"{len(opp_df)} opportunità trovate, ordinate per convenienza")
    st.dataframe(opp_df, width='stretch', hide_index=True)

    st.caption(
        "Il 'valore atteso' è quanto ti aspetti di guadagnare in media, su tante "
        "ripetizioni, puntando su questa scommessa — non è una garanzia sulla "
        "singola partita. La puntata consigliata è calcolata in modo prudente "
        "(Kelly frazionato): più alta è, più il modello è convinto del valore."
    )

st.divider()
with st.expander("Tutte le partite in arrivo (anche senza valore)"):
    st.dataframe(matches_df[["date", "league", "home", "away",
                              "prob_home", "prob_draw", "prob_away"]],
                 width='stretch', hide_index=True)
