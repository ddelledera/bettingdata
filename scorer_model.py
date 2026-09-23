"""
Distribuisce i gol attesi di una squadra (che il nostro Dixon-Coles calcola
già, per la partita nel suo complesso) tra i giocatori di quella squadra,
per stimare la probabilità che ciascuno segni almeno un gol.

COME FUNZIONA: ad ogni giocatore diamo un "punteggio" che riflette quanto
tende a essere pericoloso in attacco (basato sui suoi xG e gol per 90 minuti
di gioco, quando gioca), corretto per quanti minuti ci si aspetta che giochi
in questa partita specifica. Poi normalizziamo questi punteggi in modo che
sommino a 1 (una "quota" della minaccia offensiva totale della squadra), e
usiamo quella quota per dividere i gol attesi della squadra tra i giocatori.

Infine, per ogni giocatore, la probabilità di segnare almeno una volta si
calcola con la formula di Poisson: 1 - e^(-lambda), dove lambda è il numero
di gol attesi per quel giocatore in questa partita.
"""

import numpy as np


def compute_scorer_rating(player_stats):
    """
    player_stats: dict con almeno 'xg_per_90', 'goals_per_90',
    'expected_minutes' (quanti minuti ci aspettiamo che giochi in QUESTA
    partita, non la sua media stagionale).

    Ritorna un punteggio (non ancora normalizzato) di quanto il giocatore
    "assorbe" la minaccia offensiva della squadra in questa partita.
    """
    xg90 = player_stats.get("xg_per_90") or 0
    goals90 = player_stats.get("goals_per_90") or 0
    expected_minutes = player_stats.get("expected_minutes") or 0

    # Peso maggiore agli xG (più stabili nel tempo) rispetto ai gol veri
    # (più rumorosi, dipendono anche dalla fortuna sotto porta)
    per_90_score = 0.65 * xg90 + 0.35 * goals90
    return max(per_90_score * (expected_minutes / 90), 0)


def distribute_team_goals(team_expected_goals, players_stats):
    """
    team_expected_goals: i gol attesi della squadra in questa partita
        (il lambda che il nostro Dixon-Coles calcola già).
    players_stats: lista di dict, uno per giocatore della squadra, con
        almeno 'player_id', 'name', 'xg_per_90', 'goals_per_90',
        'expected_minutes'.

    Ritorna la stessa lista di giocatori, arricchita con:
        'team_xg_share'     -> quota (0-1) della minaccia offensiva assorbita
        'expected_goals'    -> gol attesi per QUEL giocatore in questa partita
        'prob_score_anytime'-> probabilità di segnare almeno un gol
    Se nessun giocatore ha un punteggio (es. dati mancanti per tutti),
    ritorna una lista vuota — meglio non prevedere nulla che inventare numeri.
    """
    scores = [compute_scorer_rating(p) for p in players_stats]
    total = sum(scores)
    if total <= 0:
        return []

    results = []
    for player, score in zip(players_stats, scores):
        share = score / total
        lam = team_expected_goals * share
        prob_score = 1 - np.exp(-lam)
        results.append({
            **player,
            "team_xg_share": round(share, 4),
            "expected_goals": round(lam, 4),
            "prob_score_anytime": round(prob_score, 4),
        })
    return results
