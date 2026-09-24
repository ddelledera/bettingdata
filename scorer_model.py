"""
Probabilità che ogni giocatore segni almeno un gol in una partita.

VERSIONE ADOTTATA IL 24/09/2026 (backtest_marcatori.py, holdout superato):
"M2 npxG + rigori | se gioca". Rispetto alla versione precedente (M0):
  1. Gol su azione: i gol attesi della squadra SENZA i rigori vengono divisi
     tra i giocatori in proporzione agli npxG/90 (xG senza rigori) per i
     minuti attesi.
  2. Rigori a parte: i rigori attesi della squadra vanno al probabile
     rigorista, stimato dai rigori segnati nell'ultimo anno ("ristretto"
     verso la media: 1 su 1 non fa di nessuno il rigorista sicuro), pesato
     per la presenza in campo.
  3. "Se gioca": la scommessa marcatore viene rimborsata se il giocatore non
     entra, quindi la probabilità giusta è P(segna | gioca). I gol attesi
     vengono divisi per la frequenza con cui il giocatore è sceso in campo
     nelle ultime partite della squadra (minimo 0,2).
Infine P(segna almeno un gol) = 1 - e^(-lambda).
I parametri sono quelli congelati nel protocollo del backtest: non cambiarli
senza rifare il backtest.
"""

import numpy as np

PEN_CONVERSION = 0.78      # rigori trasformati
TAKER_PRIOR = 1.0          # "rigori fittizi" distribuiti in proporzione agli npxG
CONDITIONAL_MIN_PLAY = 0.2


def distribute_team_goals(team_expected_goals, players_stats, pen_attempts_per_match):
    """
    team_expected_goals: gol attesi della squadra (Dixon-Coles).
    players_stats: lista di dict con 'npxg_per_90', 'expected_minutes',
        'penalties_scored' (ultimo anno), 'play_rate' (quota delle ultime
        partite della squadra in cui è sceso in campo), più i campi da
        conservare (player_id, name, ...).
    pen_attempts_per_match: rigori tentati attesi per squadra e partita nel
        campionato.
    Ritorna la lista arricchita con 'team_xg_share', 'expected_goals' (se
    gioca) e 'prob_score_anytime' (se gioca). Lista vuota se mancano i dati.
    """
    scores = [max(p.get("npxg_per_90") or 0, 0) * (p.get("expected_minutes") or 0) / 90
              for p in players_stats]
    total = sum(scores)
    if total <= 0:
        return []
    lam_pen = min(pen_attempts_per_match * PEN_CONVERSION, team_expected_goals * 0.5)
    lam_np = max(team_expected_goals - lam_pen, 0.05)
    shares = [s / total for s in scores]
    takers = [((p.get("penalties_scored") or 0) + TAKER_PRIOR * sh)
              * min((p.get("expected_minutes") or 0) / 90, 1)
              for p, sh in zip(players_stats, shares)]
    tt = sum(takers) or 1.0

    results = []
    for p, sh, tk in zip(players_stats, shares, takers):
        lam = lam_np * sh + lam_pen * tk / tt
        lam_if_plays = lam / max(p.get("play_rate") or 0, CONDITIONAL_MIN_PLAY)
        results.append({
            **p,
            "team_xg_share": round(sh, 4),
            "expected_goals": round(lam_if_plays, 4),
            "prob_score_anytime": round(float(1 - np.exp(-lam_if_plays)), 4),
        })
    return results
