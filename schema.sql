PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS via (
    code TEXT PRIMARY KEY,
    naam TEXT NOT NULL,
    volgorde INTEGER NOT NULL DEFAULT 0
);

-- categorie: basis | iets_luxer | luxe
-- is_vast: de vaste basisflessen (startdata); die en alleen die hebben categorie 'basis'.
--          Ze worden per fles besteld en lopen net als de rest via bonnen.
-- prijs: standaardprijs uit de startdata (nu niet gebruikt; de prijs staat per aankoopregel)
CREATE TABLE IF NOT EXISTS wine (
    id INTEGER PRIMARY KEY,
    naam TEXT NOT NULL UNIQUE,
    categorie TEXT NOT NULL CHECK (categorie IN ('basis', 'iets_luxer', 'luxe')),
    prijs REAL,
    is_vast INTEGER NOT NULL DEFAULT 0
);

-- 'voor wie'
CREATE TABLE IF NOT EXISTS klant (
    id INTEGER PRIMARY KEY,
    via TEXT NOT NULL REFERENCES via(code),
    naam TEXT NOT NULL,
    aantekening TEXT NOT NULL DEFAULT '',
    iets_luxer_n INTEGER NOT NULL DEFAULT 0 CHECK (iets_luxer_n >= 0),
    luxe_n INTEGER NOT NULL DEFAULT 0 CHECK (luxe_n >= 0),
    versie INTEGER NOT NULL DEFAULT 0  -- tegen het overschrijven van andermans wijziging
);

-- bestelling van vaste basisflessen per klant
CREATE TABLE IF NOT EXISTS klant_vast (
    klant_id INTEGER NOT NULL REFERENCES klant(id) ON DELETE CASCADE,
    wine_id INTEGER NOT NULL REFERENCES wine(id),
    aantal INTEGER NOT NULL CHECK (aantal >= 0),
    PRIMARY KEY (klant_id, wine_id)
);

-- een bon: één of meer wijnregels
CREATE TABLE IF NOT EXISTS aankoop (
    id INTEGER PRIMARY KEY,
    omschrijving TEXT NOT NULL DEFAULT '',
    aangemaakt_op TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    versie INTEGER NOT NULL DEFAULT 0  -- tegen het overschrijven van andermans wijziging
);

CREATE TABLE IF NOT EXISTS aankoop_regel (
    id INTEGER PRIMARY KEY,
    aankoop_id INTEGER NOT NULL REFERENCES aankoop(id) ON DELETE CASCADE,
    wine_id INTEGER NOT NULL REFERENCES wine(id),
    aantal INTEGER NOT NULL CHECK (aantal > 0),
    prijs REAL
);

-- hoeveel flessen van een regel bij welke via horen; overdragen past dit aan
CREATE TABLE IF NOT EXISTS regel_verdeling (
    regel_id INTEGER NOT NULL REFERENCES aankoop_regel(id) ON DELETE CASCADE,
    via TEXT NOT NULL REFERENCES via(code),
    aantal INTEGER NOT NULL CHECK (aantal > 0),
    PRIMARY KEY (regel_id, via)
);

-- fles uit de via-pool gereserveerd/toegekend aan een klant
CREATE TABLE IF NOT EXISTS toekenning (
    klant_id INTEGER NOT NULL REFERENCES klant(id) ON DELETE CASCADE,
    wine_id INTEGER NOT NULL REFERENCES wine(id),
    aantal INTEGER NOT NULL CHECK (aantal > 0),
    PRIMARY KEY (klant_id, wine_id)
);
