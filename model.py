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

NOTA TECNICA SULLA VELOCITÀ: l'allenamento funziona calcolando ripetutamente
"quanto siamo lontani dal risultato giusto" e aggiustando i voti delle
squadre di conseguenza. Con centinaia di squadre (17 campionati insieme, nel
modello europeo), calcolare la direzione giusta "per tentativi" — cambiando
un voto alla volta e vedendo cosa succede — diventa lentissimo: servirebbe
un tentativo per ciascuna delle centinaia di squadre, ad ogni passo.
Per questo qui la direzione giusta è calcolata con una formula esatta
(il "gradiente analitico"), invece che per tentativi — stesso risultato,
molto più veloce, indipendentemente da quante squadre ci sono.
"""

import numpy as np
from scipy.optimize import minimize
from scipy.special import gammaln


def _poisson_logpmf(k, lam):
    """Log-probabilità di Poisson, calcolata per tutte le partite insieme."""
    lam = np.clip(lam, 1e-6, None)
    return k * np.log(lam) - lam - gammaln(k + 1)


def _tau_correction(home_goals, away_goals, lam_home, lam_away, rho):
    """Correzione Dixon-Coles per i risultati a basso punteggio, per tutte
    le partite insieme."""
    tau = np.ones_like(lam_home)
    m00 = (home_goals == 0) & (away_goals == 0)
    m01 = (home_goals == 0) & (away_goals == 1)
    m10 = (home_goals == 1) & (away_goals == 0)
    m11 = (home_goals == 1) & (away_goals == 1)
    tau[m00] = 1 - lam_home[m00] * lam_away[m00] * rho
    tau[m01] = 1 + lam_home[m01] * rho
    tau[m10] = 1 + lam_away[m10] * rho
    tau[m11] = 1 - rho
    return np.clip(tau, 1e-10, None)


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

        home_i = np.array([team_idx[m["home_team"]] for m in matches])
        away_i = np.array([team_idx[m["away_team"]] for m in matches])
        home_g = np.array([m["home_goals"] for m in matches], dtype=float)
        away_g = np.array([m["away_goals"] for m in matches], dtype=float)
        weights = np.array([np.exp(-self.decay_rate * m["days_ago"]) for m in matches])

        x0 = np.concatenate([np.zeros(n), np.zeros(n), [0.1, -0.05]])

        def neg_log_likelihood_and_grad(x):
            attack = x[:n]
            defense = x[n:2 * n]
            home_adv = x[2 * n]
            rho = x[2 * n + 1]

            log_lam_home = np.clip(attack[home_i] - defense[away_i] + home_adv, -20, 3)
            log_lam_away = np.clip(attack[away_i] - defense[home_i], -20, 3)
            lam_home = np.exp(log_lam_home)
            lam_away = np.exp(log_lam_away)

            tau = _tau_correction(home_g, away_g, lam_home, lam_away, rho)
            ll = weights * (np.log(tau) +
                             _poisson_logpmf(home_g, lam_home) +
                             _poisson_logpmf(away_g, lam_away))
            nll = -np.sum(ll)

            # Derivate di log(tau) rispetto a lam_home, lam_away, rho — solo
            # per le partite a basso punteggio dove la correzione si applica
            # davvero (per tutte le altre è 0, come tau stesso vale 1).
            dlogtau_dlamh = np.zeros_like(lam_home)
            dlogtau_dlama = np.zeros_like(lam_away)
            dlogtau_drho = np.zeros_like(lam_home)

            m00 = (home_g == 0) & (away_g == 0)
            m01 = (home_g == 0) & (away_g == 1)
            m10 = (home_g == 1) & (away_g == 0)
            m11 = (home_g == 1) & (away_g == 1)

            dlogtau_dlamh[m00] = -lam_away[m00] * rho / tau[m00]
            dlogtau_dlama[m00] = -lam_home[m00] * rho / tau[m00]
            dlogtau_drho[m00] = -lam_home[m00] * lam_away[m00] / tau[m00]

            dlogtau_dlamh[m01] = rho / tau[m01]
            dlogtau_drho[m01] = lam_home[m01] / tau[m01]

            dlogtau_dlama[m10] = rho / tau[m10]
            dlogtau_drho[m10] = lam_away[m10] / tau[m10]

            dlogtau_drho[m11] = -1.0 / tau[m11]

            # Derivata della log-verosimiglianza rispetto ai "log-gol attesi"
            # di ciascuna partita (somma del contributo standard di Poisson
            # più il contributo della correzione Dixon-Coles).
            dll_dxh = weights * ((home_g - lam_home) + lam_home * dlogtau_dlamh)
            dll_dxa = weights * ((away_g - lam_away) + lam_away * dlogtau_dlama)
            dll_drho = np.sum(weights * dlogtau_drho)

            grad = np.zeros_like(x)
            np.add.at(grad, home_i, dll_dxh)            # attacco della squadra di casa
            np.add.at(grad, n + away_i, -dll_dxh)        # difesa della squadra ospite
            grad[2 * n] += np.sum(dll_dxh)               # vantaggio del fattore campo

            np.add.at(grad, away_i, dll_dxa)             # attacco della squadra ospite
            np.add.at(grad, n + home_i, -dll_dxa)        # difesa della squadra di casa

            grad[2 * n + 1] = dll_drho                   # correzione Dixon-Coles

            return nll, -grad  # neghiamo: stiamo minimizzando, non massimizzando

        # Vincolo: la somma delle forze d'attacco è fissata a 0 (altrimenti il
        # modello ha infinite soluzioni equivalenti: serve un punto di riferimento).
        constraint_jac = np.concatenate([np.ones(n), np.zeros(n), [0, 0]])
        constraints = {
            "type": "eq",
            "fun": lambda x: np.sum(x[:n]),
            "jac": lambda x: constraint_jac,
        }

        result = minimize(neg_log_likelihood_and_grad, x0, jac=True,
                           constraints=constraints, method="SLSQP",
                           options={"maxiter": 200, "ftol": 1e-8})

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

        goals = np.arange(max_goals + 1)
        hg_grid, ag_grid = np.meshgrid(goals, goals, indexing="ij")
        hg_flat = hg_grid.ravel().astype(float)
        ag_flat = ag_grid.ravel().astype(float)

        lam_h_arr = np.full_like(hg_flat, lam_home)
        lam_a_arr = np.full_like(ag_flat, lam_away)
        tau = _tau_correction(hg_flat, ag_flat, lam_h_arr, lam_a_arr, rho)
        probs = tau * np.exp(_poisson_logpmf(hg_flat, lam_h_arr) +
                              _poisson_logpmf(ag_flat, lam_a_arr))

        prob_home = probs[hg_flat > ag_flat].sum()
        prob_draw = probs[hg_flat == ag_flat].sum()
        prob_away = probs[hg_flat < ag_flat].sum()

        total = prob_home + prob_draw + prob_away
        return prob_home / total, prob_draw / total, prob_away / total
