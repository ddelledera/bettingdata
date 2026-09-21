"""
Funzioni condivise per uniformare i nomi delle squadre tra le diverse fonti
dati (storico campionati, partite in arrivo, quote, coppe europee), così una
stessa squadra non finisce salvata due volte con nomi leggermente diversi.

Se in futuro una squadra risulta "non riconosciuta" nei log degli script
(succede soprattutto con le coppe europee, che coinvolgono squadre di tanti
paesi diversi), è qui che va aggiunta una nuova riga di correzione.
"""

# Ogni squadra può comparire con nomi diversi a seconda della fonte. Qui li
# uniformiamo tutti al nome "corto" usato nello storico dei campionati (la
# nostra fonte principale), così tutto si collega correttamente.
NORMALIZE_MAP = {
    # Serie A
    "FC Internazionale Milano": "Inter", "Inter Milan": "Inter",
    "AC Milan": "Milan",
    "Juventus FC": "Juventus",
    "SSC Napoli": "Napoli",
    "Atalanta BC": "Atalanta",
    "AS Roma": "Roma", "AS Roma 1927": "Roma",
    "SS Lazio": "Lazio",
    "Bologna FC 1909": "Bologna",
    "ACF Fiorentina": "Fiorentina",
    "Hellas Verona FC": "Hellas Verona", "Verona": "Hellas Verona",
    # Premier League
    "Manchester City FC": "Man City", "Manchester City": "Man City",
    "Manchester United FC": "Man United", "Manchester United": "Man United",
    "Liverpool FC": "Liverpool",
    "Arsenal FC": "Arsenal",
    "Chelsea FC": "Chelsea",
    "Tottenham Hotspur FC": "Tottenham", "Tottenham Hotspur": "Tottenham",
    "Newcastle United FC": "Newcastle", "Newcastle United": "Newcastle",
    "Aston Villa FC": "Aston Villa",
    "Brighton & Hove Albion FC": "Brighton", "Brighton and Hove Albion": "Brighton",
    # La Liga
    "Real Madrid CF": "Real Madrid",
    "FC Barcelona": "Barcelona",
    "Club Atlético de Madrid": "Ath Madrid", "Atletico Madrid": "Ath Madrid",
    "Real Sociedad de Fútbol": "Real Sociedad",
    "Villarreal CF": "Villarreal",
    "Real Betis Balompié": "Betis", "Real Betis": "Betis",
    "Sevilla FC": "Sevilla",
    # Bundesliga
    "FC Bayern München": "Bayern Munich", "Bayern Munich": "Bayern Munich",
    "Borussia Dortmund": "Dortmund",
    "Bayer 04 Leverkusen": "Leverkusen", "Bayer Leverkusen": "Leverkusen",
    "RB Leipzig": "RB Leipzig",
    "Eintracht Frankfurt": "Ein Frankfurt",
    "SC Freiburg": "Freiburg",
    "VfL Wolfsburg": "Wolfsburg",
    # Ligue 1
    "Paris Saint-Germain FC": "Paris SG", "Paris Saint Germain": "Paris SG",
    "Olympique de Marseille": "Marseille", "Olympique Marseille": "Marseille",
    "AS Monaco FC": "Monaco",
    "Olympique Lyonnais": "Lyon",
    "LOSC Lille": "Lille",
    "OGC Nice": "Nice",
    "RC Lens": "Lens",
}


def normalize_team_name(name):
    name = name.strip()
    return NORMALIZE_MAP.get(name, name)


def get_or_create_team(cur, name):
    name = normalize_team_name(name)
    cur.execute("SELECT id FROM teams WHERE name = ?", (name,))
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute("INSERT INTO teams (name) VALUES (?)", (name,))
    return cur.lastrowid
