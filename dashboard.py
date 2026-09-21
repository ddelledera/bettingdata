"""
La pagina web dell'app. Mostra le partite in arrivo, confronta le nostre
probabilità con le migliori quote disponibili tra i bookmaker, e ordina
tutto per convenienza (valore). Organizzata in schede per restare leggibile.

Per vederla in azione (una volta pubblicata): si apre da sola nel browser.
Non c'è nulla da capire o configurare qui dentro.
"""

import sqlite3
import pandas as pd
import streamlit as st
from value_calculator import remove_bookmaker_margin, find_value_bets, combine_parlay

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


def list_bookmakers(conn):
    """Tutti i bookmaker di cui abbiamo quote per le partite in arrivo —
    servono per la schedina, che va giocata tutta presso lo stesso bookmaker."""
    query = """
        SELECT DISTINCT o.bookmaker
        FROM odds_snapshots o
        JOIN matches m ON o.match_id = m.id
        WHERE m.date >= date('now')
        ORDER BY o.bookmaker
    """
    return [r[0] for r in conn.execute(query).fetchall()]


def odds_by_bookmaker_for_match(conn, match_id, bookmaker):
    """Le quote di UN SOLO bookmaker per una partita (la più recente per
    ciascun esito, se ne abbiamo registrate più di una nel tempo)."""
    query = """
        SELECT selection, odds FROM odds_snapshots
        WHERE match_id = ? AND bookmaker = ?
        ORDER BY snapshot_time DESC
    """
    rows = conn.execute(query, (match_id, bookmaker)).fetchall()
    odds = {}
    for selection, o in rows:
        if selection not in odds:
            odds[selection] = o
    return odds if len(odds) >= 3 else None


def risk_label(odds):
    """Etichetta semplice del livello di rischio in base alla quota."""
    if odds <= 1.8:
        return "🟢 Rischio basso"
    elif odds <= 3.0:
        return "🟡 Rischio medio"
    return "🔴 Rischio alto"


def compute_opportunities(conn, matches_df, min_ev):
    """Calcola tutte le opportunità di valore (quota migliore tra tutti i
    bookmaker), usata dalla scheda principale."""
    all_opportunities = []
    for _, row in matches_df.iterrows():
        odds_info = best_odds_for_match(conn, row["id"])
        if odds_info is None:
            continue
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
    return all_opportunities


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

tab_opportunita, tab_schedina, tab_tutte = st.tabs(
    ["🎯 Opportunità di valore", "🎟️ Schedina", "📋 Tutte le partite"]
)

# ---------------------------------------------------------------------------
# SCHEDA 1: Opportunità di valore
# ---------------------------------------------------------------------------
with tab_opportunita:
    min_ev = st.slider("Mostra solo scommesse con valore atteso di almeno:",
                        min_value=0, max_value=20, value=3, format="%d%%") / 100

    all_opportunities = compute_opportunities(conn, matches_df, min_ev)

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

# ---------------------------------------------------------------------------
# SCHEDA 2: Schedina da un unico bookmaker
# ---------------------------------------------------------------------------
with tab_schedina:
    st.caption(
        "Una schedina reale va giocata tutta presso lo stesso bookmaker (non puoi "
        "combinare una quota di un sito con una di un altro). Qui sotto vedi solo "
        "le quote di UN bookmaker alla volta, così quello che costruisci è "
        "davvero giocabile."
    )

    bookmakers = list_bookmakers(conn)
    if not bookmakers:
        st.info("Non ci sono ancora quote salvate per costruire una schedina.")
    else:
        selected_bookmaker = st.selectbox("Scegli il bookmaker:", bookmakers)

        candidate_legs = {}  # etichetta leggibile -> dati della selezione
        for _, row in matches_df.iterrows():
            bm_odds = odds_by_bookmaker_for_match(conn, row["id"], selected_bookmaker)
            if bm_odds is None:
                continue  # questo bookmaker non copre questa partita
            model_probs = {"Home": row["prob_home"], "Draw": row["prob_draw"], "Away": row["prob_away"]}
            for vb in find_value_bets(model_probs, bm_odds, min_ev=0.0):
                esito_label = {"Home": "1 (casa)", "Draw": "X (pareggio)", "Away": "2 (trasferta)"}[vb["selection"]]
                label = (f"{row['home']} vs {row['away']} — {esito_label} @ {vb['odds']} "
                         f"(nostra prob. {vb['model_probability']:.0%}, EV {vb['ev']:+.1%})")
                candidate_legs[label] = vb

        if not candidate_legs:
            st.info(f"Nessuna selezione con valore trovata su {selected_bookmaker} al momento.")
        else:
            chosen = st.multiselect(
                "Seleziona le partite da mettere in schedina:",
                options=list(candidate_legs.keys()),
            )
            if chosen:
                legs = [candidate_legs[c] for c in chosen]
                combo = combine_parlay(legs)

                col1, col2, col3, col4 = st.columns(4)
                col1.metric("Quota combinata", f"{combo['combined_odds']}")
                col2.metric("Nostra probabilità combinata", f"{combo['combined_probability']:.1%}")
                col3.metric("Valore atteso (EV)", f"{combo['ev']:+.1%}")
                col4.metric("Puntata consigliata", f"{combo['kelly_stake_pct']:.1f}% del capitale")

                st.caption(
                    "La probabilità combinata assume che le partite scelte siano indipendenti "
                    "tra loro (il risultato dell'una non influenza l'altra) — vero nella "
                    "stragrande maggioranza dei casi, a meno di partite con conseguenze dirette "
                    "l'una sull'altra (es. stesso girone di Champions League, stessa giornata "
                    "decisiva). Più selezioni aggiungi, più il rischio complessivo sale, anche "
                    "se ognuna singolarmente ha valore."
                )
            else:
                st.caption("Seleziona una o più partite qui sopra per vedere la schedina combinata.")

# ---------------------------------------------------------------------------
# SCHEDA 3: Tutte le partite in arrivo
# ---------------------------------------------------------------------------
with tab_tutte:
    leagues_available = sorted(matches_df["league"].unique())
    selected_leagues = st.multiselect(
        "Filtra per campionato:", options=leagues_available, default=leagues_available
    )
    filtered_df = matches_df[matches_df["league"].isin(selected_leagues)]
    st.dataframe(
        filtered_df[["date", "league", "home", "away", "prob_home", "prob_draw", "prob_away"]]
        .rename(columns={
            "date": "Data", "league": "Campionato", "home": "Casa", "away": "Trasferta",
            "prob_home": "Prob. 1", "prob_draw": "Prob. X", "prob_away": "Prob. 2",
        }),
        width='stretch', hide_index=True,
    )
