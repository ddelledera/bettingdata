-- Schema del database per l'app di previsioni calcio
-- Usa SQLite: è un singolo file (data.db), zero configurazione, zero server da gestire.

CREATE TABLE IF NOT EXISTS teams (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL
);

CREATE TABLE IF NOT EXISTS matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,              -- formato YYYY-MM-DD
    league TEXT NOT NULL,            -- es. "Serie A"
    season TEXT NOT NULL,            -- es. "2024-25"
    home_team_id INTEGER NOT NULL REFERENCES teams(id),
    away_team_id INTEGER NOT NULL REFERENCES teams(id),
    home_goals INTEGER,              -- NULL se la partita non è ancora giocata
    away_goals INTEGER,
    UNIQUE(date, home_team_id, away_team_id)
);

-- Storico delle quote nel tempo (mai sovrascrivere: ogni riga è una "fotografia"
-- della quota in un certo istante, così possiamo vedere come si muove la linea).
CREATE TABLE IF NOT EXISTS odds_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id INTEGER NOT NULL REFERENCES matches(id),
    bookmaker TEXT NOT NULL,
    market TEXT NOT NULL,            -- es. "1X2", "Over/Under 2.5"
    selection TEXT NOT NULL,         -- es. "Home", "Draw", "Away", "Over"
    odds REAL NOT NULL,
    snapshot_time TEXT NOT NULL      -- quando abbiamo registrato questa quota
);

-- Le nostre stime di probabilità calcolate dal modello, per ogni partita.
CREATE TABLE IF NOT EXISTS model_predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id INTEGER NOT NULL UNIQUE REFERENCES matches(id),
    prob_home REAL NOT NULL,
    prob_draw REAL NOT NULL,
    prob_away REAL NOT NULL,
    computed_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_matches_date ON matches(date);
CREATE INDEX IF NOT EXISTS idx_odds_match ON odds_snapshots(match_id);
