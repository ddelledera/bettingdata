"""
Modello Dixon-Coles per stimare la probabilità di vittoria/pareggio/sconfitta
di una partita di calcio, a partire dai risultati storici.

COME FUNZIONA (in parole semplici):
Ogni squadra ha due "voti" che il modello impara dai dati:
  - forza in attacco (quanti gol tende a segnare)
  - forza in difesa (quanti gol tende a concedere)
Con questi voti, il modello calcola quanto è probabile ogni risultato
possibile (0-0, 1-0, 2-1, ecc.) e da lì ricava le probabilità di
vittoria casa / pareggio / vittoria trasferta.

Include anche una piccola correzione (la parte "Dixon-Coles" vera e propria)
che aggiusta le stime per i risultati bassi (0-0, 1-0, 0-1, 1-1), perché il
semplice modello di Poisson tende a sbagliarli leggermente.
"""

import numpy as np
from scipy.optimize import minimize
from scipy.stats import poisson


def _tau(home_goals, away_goals, lambda_home, lambda_away, rho):
    """Correzione Dixon-Coles per i risultati a basso punteggio."""
    if home_goals == 0 and away_goals == 0:
        return 1 - lambda_home * lambda_away * rho
    elif home_goals == 0 and away_goals == 1:
        return 1 + lambda_home * rho
    elif home_goals == 1 and away_goals == 0:
        return 1 + lambda_away * rho
    elif home_goals == 1 and away_goals == 1:
        return 1 - rho
    return 1.0


class DixonColesModel:
    def __init__(self, decay_rate=0.001):
        # decay_rate: quanto "dimenticare" le partite vecchie.
        # Un valore più alto dà più peso alle partite recenti.
        self.decay_rate = decay_rate
        self.teams = []
        self.params = None  # dict: team -> (attacco, difesa); più home_adv, rho

    def fit(self, matches):
        """
        matches: lista di dict con chiavi
            'home_team', 'away_team', 'home_goals', 'away_goals', 'days_ago'
        'days_ago' = quanti giorni fa è stata giocata (0 = oggi/più recente).
        """
        self.teams = sorted(set([m["home_team"] for m in matches] +
                                 [m["away_team"] for m in matches]))
        n = len(self.teams)
        team_idx = {t: i for i, t in enumerate(self.teams)}

        # Vettore parametri: [attacco_1..attacco_n, difesa_1..difesa_n, home_adv, rho]
        x0 = np.concatenate([np.zeros(n), np.zeros(n), [0.1, -0.05]])

        weights = np.array([np.exp(-self.decay_rate * m["days_ago"]) for m in matches])

        def neg_log_likelihood(x):
            attack = x[:n]
            defense = x[n:2 * n]
            home_adv = x[2 * n]
            rho = x[2 * n + 1]
            ll = 0.0
            for w, m in zip(weights, matches):
                hi, ai = team_idx[m["home_team"]], team_idx[m["away_team"]]
                lam_home = np.exp(attack[hi] - defense[ai] + home_adv)
                lam_away = np.exp(attack[ai] - defense[hi])
                lam_home = np.clip(lam_home, 1e-6, 15)
                lam_away = np.clip(lam_away, 1e-6, 15)
                tau = _tau(m["home_goals"], m["away_goals"], lam_home, lam_away, rho)
                tau = max(tau, 1e-10)
                p = (tau * poisson.pmf(m["home_goals"], lam_home) *
                     poisson.pmf(m["away_goals"], lam_away))
                p = max(p, 1e-10)
                ll += w * np.log(p)
            return -ll

        # Vincolo: la somma delle forze d'attacco è fissata a 0 (altrimenti il
        # modello ha infinite soluzioni equivalenti: serve un punto di riferimento).
        constraints = {"type": "eq", "fun": lambda x: np.sum(x[:n])}

        result = minimize(neg_log_likelihood, x0, constraints=constraints,
                           method="SLSQP", options={"maxiter": 200, "ftol": 1e-8})

        attack = result.x[:n]
        defense = result.x[n:2 * n]
        home_adv = result.x[2 * n]
        rho = result.x[2 * n + 1]

        self.params = {
            "attack": {t: attack[team_idx[t]] for t in self.teams},
            "defense": {t: defense[team_idx[t]] for t in self.teams},
            "home_adv": home_adv,
            "rho": rho,
        }
        return self

    def predict_match(self, home_team, away_team, max_goals=8):
        """Restituisce (prob_vittoria_casa, prob_pareggio, prob_vittoria_trasferta)."""
        a = self.params["attack"]
        d = self.params["defense"]
        home_adv = self.params["home_adv"]
        rho = self.params["rho"]

        lam_home = np.exp(a[home_team] - d[away_team] + home_adv)
        lam_away = np.exp(a[away_team] - d[home_team])

        prob_home, prob_draw, prob_away = 0.0, 0.0, 0.0
        for hg in range(max_goals + 1):
            for ag in range(max_goals + 1):
                tau = _tau(hg, ag, lam_home, lam_away, rho)
                p = tau * poisson.pmf(hg, lam_home) * poisson.pmf(ag, lam_away)
                if hg > ag:
                    prob_home += p
                elif hg == ag:
                    prob_draw += p
                else:
                    prob_away += p

        # Normalizza (la somma potrebbe non essere esattamente 1 per via del
        # taglio a max_goals e della correzione rho)
        total = prob_home + prob_draw + prob_away
        return prob_home / total, prob_draw / total, prob_away / total
