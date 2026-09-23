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
    expected_goals_home REAL,
    expected_goals_away REAL,
    prob_btts REAL,                  -- segnano entrambe (Goal)
    prob_over15 REAL,
    prob_over25 REAL,
    prob_over35 REAL,
    computed_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_matches_date ON matches(date);
CREATE INDEX IF NOT EXISTS idx_odds_match ON odds_snapshots(match_id);

-- I giocatori: 'bsd_id' è l'identificativo che usa la fonte dati (Bzzoiro
-- Sports Data), serve per non duplicare lo stesso giocatore ad ogni scarico.
CREATE TABLE IF NOT EXISTS players (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bsd_id INTEGER UNIQUE,
    name TEXT NOT NULL,
    team_id INTEGER REFERENCES teams(id),   -- squadra ATTUALE (dalla rosa BSD)
    availability TEXT,                      -- es. "available", "injured"
    injury_type TEXT
);

-- Le statistiche di un giocatore in UNA partita già giocata — la "materia
-- prima" da cui calcoliamo le sue medie (xG/90, gol/90, minuti tipici).
CREATE TABLE IF NOT EXISTS player_match_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id INTEGER NOT NULL REFERENCES players(id),
    match_bsd_id INTEGER NOT NULL,  -- id della partita nella fonte BSD
    match_date TEXT NOT NULL,
    started INTEGER,           -- 1 se titolare, 0 se subentrato
    minutes INTEGER,
    goals INTEGER,
    shots INTEGER,
    shots_on_target INTEGER,
    xg REAL,
    penalties_taken INTEGER,
    penalties_scored INTEGER,
    UNIQUE(player_id, match_bsd_id)
);

-- Le nostre previsioni sui marcatori per le partite in arrivo.
CREATE TABLE IF NOT EXISTS player_predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id INTEGER NOT NULL REFERENCES players(id),
    match_id INTEGER NOT NULL REFERENCES matches(id),
    expected_minutes REAL,
    starting_probability REAL,
    team_xg_share REAL,
    expected_goals REAL,
    prob_score_anytime REAL,
    computed_at TEXT NOT NULL,
    UNIQUE(player_id, match_id)
);

CREATE INDEX IF NOT EXISTS idx_player_stats_player ON player_match_stats(player_id);
CREATE INDEX IF NOT EXISTS idx_player_predictions_match ON player_predictions(match_id);

-- Quote "di riferimento" di bookmaker NON giocabili dall'Italia (oggi solo
-- Pinnacle, il bookmaker più efficiente del mercato). Tenute separate da
-- odds_snapshots perché l'app non deve mai proporle come quote da giocare:
-- servono per il backtest (confronto con la quota di chiusura).
CREATE TABLE IF NOT EXISTS reference_odds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id INTEGER NOT NULL REFERENCES matches(id),
    bookmaker TEXT NOT NULL,
    market TEXT NOT NULL,
    selection TEXT NOT NULL,
    odds REAL NOT NULL,
    snapshot_time TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reference_odds_match ON reference_odds(match_id);

-- Partite già giocate secondo BSD, collegate (quando possibile) alla nostra
-- tabella matches. stats_done = 1 quando abbiamo già scaricato le
-- statistiche giocatore di quella partita (così non le riscarichiamo).
CREATE TABLE IF NOT EXISTS bsd_events (
    id INTEGER PRIMARY KEY,               -- id della partita su BSD
    league TEXT NOT NULL,
    event_date TEXT NOT NULL,
    home_bsd_team_id INTEGER NOT NULL,
    away_bsd_team_id INTEGER NOT NULL,
    home_team_name TEXT,
    away_team_name TEXT,
    match_id INTEGER REFERENCES matches(id),
    stats_done INTEGER NOT NULL DEFAULT 0,
    home_score INTEGER,
    away_score INTEGER
);
CREATE INDEX IF NOT EXISTS idx_bsd_events_date ON bsd_events(event_date);

-- Corrispondenza squadra BSD -> nostra squadra (ricavata dalle partite
-- collegate: se la partita BSD e' la nostra, le due squadre coincidono).
CREATE TABLE IF NOT EXISTS bsd_teams (
    bsd_id INTEGER PRIMARY KEY,
    team_id INTEGER NOT NULL REFERENCES teams(id)
);

-- Quote "marcatore in qualsiasi momento" dei bookmaker italiani. I bookmaker
-- scrivono il nome del giocatore a modo loro: player_id è il collegamento al
-- nostro giocatore (NULL se il nome non è stato riconosciuto con sicurezza).
CREATE TABLE IF NOT EXISTS scorer_odds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id INTEGER NOT NULL REFERENCES matches(id),
    bookmaker TEXT NOT NULL,
    player_name TEXT NOT NULL,
    player_id INTEGER REFERENCES players(id),
    odds REAL NOT NULL,
    snapshot_time TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_scorer_odds_match ON scorer_odds(match_id);
