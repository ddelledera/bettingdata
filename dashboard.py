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
from value_calculator import remove_bookmaker_margin, find_value_bets, combine_parlay, find_best_combination
import math

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
    """Per ogni esito (1/X/2), trova la quota più alta tra i bookmaker,
    usando SOLO l'ultima quota registrata per ciascun bookmaker (non il
    massimo tra tutti gli snapshot storici mai salvati, che potrebbe non
    essere più la quota realmente disponibile oggi)."""
    query = """
        WITH ultima_quota AS (
            SELECT bookmaker, selection, odds,
                   ROW_NUMBER() OVER (
                       PARTITION BY bookmaker, selection
                       ORDER BY snapshot_time DESC
                   ) AS rn
            FROM odds_snapshots
            WHERE match_id = ?
        )
        SELECT selection, MAX(odds) as best_odds, bookmaker
        FROM ultima_quota
        WHERE rn = 1
        GROUP BY selection
    """
    rows = conn.execute(query, (match_id,)).fetchall()
    if len(rows) < 3:
        return None
    return {r[0]: r[1] for r in rows}, {r[0]: r[2] for r in rows}


def list_bookmakers(conn):
    """Tutti i bookmaker di cui abbiamo quote per le partite in arrivo,
    con quelli italiani (ADM) messi per primi — servono per la schedina,
    che va giocata tutta presso lo stesso bookmaker."""
    query = """
        SELECT DISTINCT o.bookmaker
        FROM odds_snapshots o
        JOIN matches m ON o.match_id = m.id
        WHERE m.date >= date('now')
        ORDER BY o.bookmaker
    """
    all_bookmakers = [r[0] for r in conn.execute(query).fetchall()]
    italiani = sorted(b for b in all_bookmakers if b in BOOKMAKER_ITALIA)
    altri = sorted(b for b in all_bookmakers if b not in BOOKMAKER_ITALIA)
    return italiani + altri


def bookmaker_display_label(bookmaker):
    """Etichetta mostrata nei menu: segnala con una bandierina i bookmaker
    che sappiamo avere licenza ADM (autorizzati in Italia). Gli altri sono
    bookmaker reali ma non necessariamente utilizzabili legalmente
    dall'Italia — mostrati comunque, per confronto e per non bloccare le
    simulazioni quando i pochi bookmaker italiani coperti dalla nostra
    fonte non hanno quote per una partita."""
    if bookmaker in BOOKMAKER_ITALIA:
        return f"🇮🇹 {bookmaker} (ADM)"
    return bookmaker


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


# Bookmaker con licenza ADM (autorizzati in Italia) TRA QUELLI CHE LA NOSTRA
# FONTE DELLE QUOTE COPRE DAVVERO. Ho controllato l'elenco ufficiale e
# completo di The Odds API (tutte le regioni: EU, UK, Francia, Svezia,
# Finlandia): la maggior parte dei bookmaker italiani noti (Eurobet, Snai,
# Sisal, Lottomatica, Goldbet, Betflag, Planetwin365, AdmiralBet, ecc.)
# NON sono coperti da questa fonte gratuita, quindi non possiamo mostrarli
# anche se esistono davvero. Gli unici due esplicitamente etichettati come
# entità italiane sono questi:
BOOKMAKER_ITALIA = {"Unibet", "Codere"}
# Nota: "Unibet" potrebbe in teoria riferirsi anche a Unibet Francia/Olanda/
# Svezia (la fonte non distingue sempre il paese nel nome mostrato) — quindi
# anche questo non è garantito al 100% essere l'entità italiana.


def competition_filter(matches_df, key):
    """Mostra un selettore di competizioni (una, più di una, o tutte) e
    ritorna solo le partite di quelle scelte. Usata in ogni scheda."""
    available = sorted(matches_df["league"].unique())
    selected = st.multiselect("Competizione:", options=available, default=available, key=key)
    if not selected:
        return matches_df.iloc[0:0]
    return matches_df[matches_df["league"].isin(selected)]


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

tab_opportunita, tab_schedina, tab_simulazione, tab_tutte = st.tabs(
    ["🎯 Opportunità di valore", "🎟️ Schedina", "🎲 Simulazione", "📋 Tutte le partite"]
)

# ---------------------------------------------------------------------------
# SCHEDA 1: Opportunità di valore
# ---------------------------------------------------------------------------
with tab_opportunita:
    filtered_opp = competition_filter(matches_df, key="comp_opportunita")

    min_ev = st.slider("Mostra solo scommesse con valore atteso di almeno:",
                        min_value=0, max_value=20, value=3, format="%d%%") / 100

    all_opportunities = compute_opportunities(conn, filtered_opp, min_ev)

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
        filtered_sched = competition_filter(matches_df, key="comp_schedina")
        selected_bookmaker = st.selectbox("Scegli il bookmaker:", bookmakers,
                                           format_func=bookmaker_display_label)

        candidate_legs = {}  # etichetta leggibile -> dati della selezione
        for _, row in filtered_sched.iterrows():
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
# SCHEDA 3: Simulazione (trova la combinazione migliore data una puntata,
# un ritorno desiderato, un bookmaker e un numero di partite)
# ---------------------------------------------------------------------------
with tab_simulazione:
    st.caption(
        "Dimmi quanto vuoi puntare, il ritorno che cerchi, su quale bookmaker "
        "e su quante partite: trovo la combinazione più sicura che raggiunge "
        "l'obiettivo (o quella più vicina possibile, se l'obiettivo è troppo alto)."
    )

    bookmakers_sim = list_bookmakers(conn)
    if not bookmakers_sim:
        st.info("Non ci sono ancora quote salvate per fare una simulazione.")
    else:
        filtered_sim = competition_filter(matches_df, key="comp_simulazione")

        col_a, col_b = st.columns(2)
        with col_a:
            stake = st.number_input("Puntata (€):", min_value=1.0, value=10.0, step=1.0)
            sim_bookmaker = st.selectbox("Bookmaker:", bookmakers_sim, key="sim_bookmaker",
                                          format_func=bookmaker_display_label)
        with col_b:
            target_roi_pct = st.slider("Ritorno desiderato:", min_value=20, max_value=500,
                                        value=100, step=10, format="+%d%%")
            num_matches = st.slider("Numero di partite:", min_value=1, max_value=5, value=3)

        max_risk = st.select_slider(
            "Rischio massimo per singola selezione:",
            options=["🟢 Solo basso", "🟡 Basso o medio", "🔴 Qualsiasi"],
            value="🔴 Qualsiasi",
        )
        risk_order = {"🟢 Rischio basso": 0, "🟡 Rischio medio": 1, "🔴 Rischio alto": 2}
        max_risk_level = {"🟢 Solo basso": 0, "🟡 Basso o medio": 1, "🔴 Qualsiasi": 2}[max_risk]
        st.caption(
            "Limitare al rischio basso riduce le partite disponibili tra cui scegliere: "
            "con poche selezioni 'sicure', potrebbe non essere possibile raggiungere il "
            "ritorno desiderato — in quel caso te lo segnalo."
        )

        if st.button("🔍 Trova la combinazione migliore"):
            # per ciascuna partita disponibile su questo bookmaker, teniamo
            # TUTTE le selezioni che rispettano il rischio scelto (non solo
            # la migliore per EV): così l'algoritmo può scegliere, partita
            # per partita, quella che serve davvero per la combinazione
            # complessiva più solida — il rischio pesa sulla scelta finale,
            # non solo su cosa scartare a monte.
            legs_by_match = {}
            for _, row in filtered_sim.iterrows():
                bm_odds = odds_by_bookmaker_for_match(conn, row["id"], sim_bookmaker)
                if bm_odds is None:
                    continue
                model_probs = {"Home": row["prob_home"], "Draw": row["prob_draw"], "Away": row["prob_away"]}
                candidates = find_value_bets(model_probs, bm_odds, min_ev=-1)  # anche EV negativo: decide l'algoritmo
                candidates = [c for c in candidates
                              if risk_order[risk_label(c["odds"])] <= max_risk_level]
                if candidates:
                    for c in candidates:
                        c["match_label"] = f"{row['home']} vs {row['away']}"
                        c["match_date"] = row["date"]
                    legs_by_match[row["id"]] = candidates

            result = find_best_combination(legs_by_match, num_matches, target_roi_pct / 100)

            if result is None:
                st.warning(f"Non ci sono abbastanza partite disponibili su {sim_bookmaker} "
                           f"(con il rischio scelto) per formare una combinazione di "
                           f"{num_matches} partite. Prova ad allargare il rischio massimo, "
                           f"o riduci il numero di partite.")
            else:
                combo, hit_target = result
                combined_odds = math.prod(leg["odds"] for leg in combo)
                combined_prob = math.prod(leg["model_probability"] for leg in combo)
                projected_return = stake * combined_odds
                projected_profit = projected_return - stake

                if not hit_target:
                    st.info(f"Non ho trovato una combinazione da {num_matches} partite su "
                            f"{sim_bookmaker} (con il rischio scelto) che raggiunga "
                            f"+{target_roi_pct}% — questa è quella con il ritorno più alto "
                            f"possibile disponibile ora.")
                else:
                    st.success("Trovata una combinazione che raggiunge l'obiettivo:")

                st.subheader("Combinazione proposta")
                for leg in combo:
                    esito_label = {"Home": "1 (casa)", "Draw": "X (pareggio)",
                                    "Away": "2 (trasferta)"}[leg["selection"]]
                    st.write(f"- **{leg['match_label']}** ({leg['match_date']}) — "
                             f"{esito_label} @ {leg['odds']} "
                             f"(nostra prob. {leg['model_probability']:.0%}) "
                             f"— {risk_label(leg['odds'])}")

                c1, c2, c3 = st.columns(3)
                c1.metric("Quota combinata", f"{combined_odds:.2f}")
                c2.metric("Ritorno stimato", f"€{projected_return:.2f}")
                c3.metric("Guadagno stimato", f"€{projected_profit:.2f}")

                st.caption(
                    f"Probabilità combinata secondo il nostro modello: {combined_prob:.1%}. "
                    "Come per la schedina, questo numero assume partite indipendenti tra loro."
                )
                st.info(
                    "La conferma e il salvataggio automatico di questa schedina (con resoconto "
                    "a fine partite) arrivano nel prossimo aggiornamento — per ora questa scheda "
                    "ti aiuta a decidere, il salvataggio lo fai ancora tu a mano se vuoi tenerne traccia."
                )

# ---------------------------------------------------------------------------
# SCHEDA 4: Tutte le partite in arrivo
# ---------------------------------------------------------------------------
with tab_tutte:
    filtered_df = competition_filter(matches_df, key="comp_tutte")
    st.dataframe(
        filtered_df[["date", "league", "home", "away", "prob_home", "prob_draw", "prob_away"]]
        .rename(columns={
            "date": "Data", "league": "Campionato", "home": "Casa", "away": "Trasferta",
            "prob_home": "Prob. 1", "prob_draw": "Prob. X", "prob_away": "Prob. 2",
        }),
        width='stretch', hide_index=True,
    )
