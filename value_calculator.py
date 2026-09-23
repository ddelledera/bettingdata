"""
Funzioni per confrontare le nostre probabilità stimate con le quote dei
bookmaker, e capire quali scommesse hanno "valore" (value) e quanto
punterci sopra in modo prudente.
"""


def remove_bookmaker_margin(odds_dict):
    """
    Le quote di un bookmaker includono sempre un margine (il loro guadagno
    garantito). Questa funzione lo rimuove, per ottenere le probabilità
    "vere" implicite nel mercato.

    odds_dict: es. {"Home": 2.10, "Draw": 3.40, "Away": 3.60}
    Ritorna: probabilità de-vigged che sommano esattamente a 1.0
    """
    implied = {k: 1 / v for k, v in odds_dict.items()}
    total = sum(implied.values())  # sarà > 1.0, quello è il margine
    return {k: v / total for k, v in implied.items()}


def expected_value(model_probability, odds):
    """
    EV = quanto ti aspetti di guadagnare (in media, su tante ripetizioni)
    puntando 1 unità su questa scommessa.
    EV positivo = scommessa di valore secondo il tuo modello.
    """
    return (model_probability * odds) - 1


def kelly_fraction(model_probability, odds, fraction=0.25):
    """
    Quanto del tuo capitale (bankroll) puntare, secondo il criterio di Kelly.
    'fraction=0.25' significa Kelly frazionato a 1/4: molto più prudente del
    Kelly "pieno", che in pratica è troppo aggressivo e rischioso da usare.

    Ritorna una percentuale del bankroll (es. 0.02 = punta il 2% del capitale).
    Se il risultato è negativo o zero, significa: non c'è valore, non puntare.
    """
    b = odds - 1  # guadagno netto per unità puntata
    q = 1 - model_probability
    full_kelly = (model_probability * b - q) / b
    stake = max(full_kelly, 0) * fraction
    return stake


def find_value_bets(model_probabilities, odds_dict, min_ev=0.02):
    """
    Confronta le probabilità del modello con le quote e ritorna solo le
    scommesse con EV sopra la soglia minima (default 2%), ordinate dalla
    più conveniente.

    model_probabilities: es. {"Home": 0.52, "Draw": 0.25, "Away": 0.23}
    odds_dict: es. {"Home": 2.10, "Draw": 3.40, "Away": 3.60}
    """
    results = []
    for selection, prob in model_probabilities.items():
        odds = odds_dict.get(selection)
        if not odds or not prob:
            continue  # quota non disponibile per questo esito/mercato
        ev = expected_value(prob, odds)
        if ev >= min_ev:
            results.append({
                "selection": selection,
                "model_probability": round(prob, 3),
                "odds": odds,
                "ev": round(ev, 3),
                "kelly_stake_pct": round(kelly_fraction(prob, odds) * 100, 2),
            })
    results.sort(key=lambda r: r["ev"], reverse=True)
    return results


def combine_parlay(legs):
    """
    Combina più selezioni indipendenti (partite diverse) in un'unica
    schedina. Le partite diverse si considerano indipendenti tra loro
    (il risultato dell'una non influenza l'altra), quindi le probabilità
    si moltiplicano e le quote pure — è così che funziona davvero una
    schedina dal vivo.

    legs: lista di dict, ciascuno con almeno "model_probability" e "odds"
    Ritorna: probabilità combinata, quota combinata, EV, puntata Kelly.
    """
    if not legs:
        return {"combined_probability": 0, "combined_odds": 0, "ev": 0, "kelly_stake_pct": 0}

    combined_prob = 1.0
    combined_odds = 1.0
    for leg in legs:
        combined_prob *= leg["model_probability"]
        combined_odds *= leg["odds"]

    ev = expected_value(combined_prob, combined_odds)
    stake = kelly_fraction(combined_prob, combined_odds) * 100

    return {
        "combined_probability": round(combined_prob, 4),
        "combined_odds": round(combined_odds, 2),
        "ev": round(ev, 3),
        "kelly_stake_pct": round(stake, 2),
    }


def find_best_combination(legs_by_match, num_matches, target_roi):
    """
    Trova la combinazione di 'num_matches' partite che raggiunge un
    ritorno atteso almeno pari a 'target_roi' (es. 1.0 = vuoi raddoppiare
    i soldi puntati) con la probabilità combinata più alta, cioè la più
    "sicura" tra quelle che comunque raggiungono il ritorno voluto.

    Per ogni partita si sceglie AL MASSIMO UNA selezione, tra tutti i
    mercati disponibili (1X2, doppia chance, Goal/No Goal, Under/Over):
    due esiti della stessa partita (es. "1" e "Over 2.5") sono legati tra
    loro, e moltiplicarne le probabilità come se fossero indipendenti
    darebbe una probabilità combinata falsa.

    COME: con molti mercati per partita provare tutte le combinazioni è
    impossibile (miliardi di casi). Usiamo la "programmazione dinamica":
    si scorrono le partite una alla volta ricordando, per ogni numero di
    selezioni già scelte e per ogni livello di quota raggiunto, la
    combinazione più probabile finora. Risultato identico (a meno di
    arrotondamenti di un decimillesimo sulla quota), in una frazione di secondo.

    legs_by_match: dict {match_id: [leg, leg, ...]}, ciascun leg con almeno
        "odds" e "model_probability".
    Ritorna: (combinazione scelta, ha_raggiunto_il_target) oppure None se
    non ci sono abbastanza partite.
    """
    import math
    import numpy as np

    matches = [(mid, [l for l in legs if l["odds"] > 1 and l["model_probability"] > 0])
               for mid, legs in legs_by_match.items()]
    matches = [(mid, legs) for mid, legs in matches if legs]
    if len(matches) < num_matches or num_matches < 1:
        return None

    step = 0.0001                                  # precisione sul log della quota
    # ogni quota viene arrotondata al passo più vicino (errore massimo mezzo
    # passo per selezione): il tetto è quindi abbassato di k passi, e la
    # combinazione trovata viene poi ricontrollata con le quote esatte.
    top = max(1, math.ceil(math.log(1 + target_roi) / step) - num_matches)
    k = num_matches
    dp = np.full((k + 1, top + 1), -np.inf)
    dp[0, 0] = 0.0
    history = []                                   # per ricostruire la scelta
    buckets = np.arange(top + 1)

    for _, legs in matches:
        new = dp.copy()
        chosen_leg = np.full(dp.shape, -1)
        prev_bucket = np.tile(buckets, (k + 1, 1))
        for li, leg in enumerate(legs):
            lp = math.log(leg["model_probability"])
            s = round(math.log(leg["odds"]) / step)
            dest = np.minimum(buckets + s, top)
            for j in range(1, k + 1):
                cand = dp[j - 1] + lp
                # partite che non arrivano al tetto: spostamento semplice
                free = dest < top
                better = free & (cand > new[j, dest])
                new[j, dest[better]] = cand[better]
                chosen_leg[j, dest[better]] = li
                prev_bucket[j, dest[better]] = buckets[better]
                # tutte quelle che superano il tetto finiscono nello stesso punto
                over = np.where(~free)[0]
                if len(over):
                    b = over[np.argmax(cand[over])]
                    if cand[b] > new[j, top]:
                        new[j, top] = cand[b]
                        chosen_leg[j, top] = li
                        prev_bucket[j, top] = b
        history.append((chosen_leg, prev_bucket))
        dp = new

    if np.isfinite(dp[k, top]):
        combo, j, b = [], k, top
        for (mid, legs), (chosen_leg, prev_bucket) in zip(reversed(matches), reversed(history)):
            li = chosen_leg[j, b]
            if li >= 0:
                combo.append(legs[li])
                j, b = j - 1, prev_bucket[j, b]
        combo = tuple(reversed(combo))
        reached = math.prod(l["odds"] for l in combo) >= (1 + target_roi) - 1e-9
        return combo, reached

    # Obiettivo irraggiungibile: la combinazione con la quota più alta
    # possibile (una selezione per partita, le partite con la quota massima).
    best_per_match = sorted((max(legs, key=lambda l: l["odds"]) for _, legs in matches),
                            key=lambda l: l["odds"], reverse=True)
    return tuple(best_per_match[:num_matches]), False
