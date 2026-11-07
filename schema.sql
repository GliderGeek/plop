PRAGMA foreign_keys = ON;

-- Elk jaar (elke trip) staat los: via's, wijnen, bestellingen, bonnen en kosten horen bij één jaar.
-- Later komen hier de instellingen per jaar bij.
CREATE TABLE IF NOT EXISTS jaar (
    jaar INTEGER PRIMARY KEY CHECK (jaar BETWEEN 2000 AND 2100),
    aangemaakt_op TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS via (
    jaar INTEGER NOT NULL REFERENCES jaar(jaar),
    code TEXT NOT NULL,
    naam TEXT NOT NULL,
    volgorde INTEGER NOT NULL DEFAULT 0,
    verrekend INTEGER NOT NULL DEFAULT 0,  -- de via heeft zijn saldo verrekend
    PRIMARY KEY (jaar, code)
);

-- categorie: basis | iets_luxer | luxe
-- is_vast: de vaste basisflessen (startdata); die en alleen die hebben categorie 'basis'.
--          Ze worden per fles besteld en lopen net als de rest via bonnen.
-- prijs: standaardprijs uit de startdata (nu niet gebruikt; de prijs staat per aankoopregel)
CREATE TABLE IF NOT EXISTS wine (
    id INTEGER PRIMARY KEY,
    jaar INTEGER NOT NULL REFERENCES jaar(jaar),
    naam TEXT NOT NULL,
    categorie TEXT NOT NULL CHECK (categorie IN ('basis', 'iets_luxer', 'luxe')),
    prijs REAL,
    is_vast INTEGER NOT NULL DEFAULT 0,
    UNIQUE (jaar, naam)
);

-- 'voor wie'
CREATE TABLE IF NOT EXISTS klant (
    id INTEGER PRIMARY KEY,
    jaar INTEGER NOT NULL,
    via TEXT NOT NULL,
    naam TEXT NOT NULL,
    aantekening TEXT NOT NULL DEFAULT '',
    iets_luxer_n INTEGER NOT NULL DEFAULT 0 CHECK (iets_luxer_n >= 0),
    luxe_n INTEGER NOT NULL DEFAULT 0 CHECK (luxe_n >= 0),
    versie INTEGER NOT NULL DEFAULT 0,  -- tegen het overschrijven van andermans wijziging
    betaald INTEGER NOT NULL DEFAULT 0,  -- heeft de rekening aan de via betaald
    FOREIGN KEY (jaar, via) REFERENCES via(jaar, code)
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
    jaar INTEGER NOT NULL REFERENCES jaar(jaar),
    omschrijving TEXT NOT NULL DEFAULT '',
    aangemaakt_op TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    versie INTEGER NOT NULL DEFAULT 0,  -- tegen het overschrijven van andermans wijziging
    betaald_door TEXT,  -- NULL: nog onbekend
    FOREIGN KEY (jaar, betaald_door) REFERENCES via(jaar, code)
);

CREATE TABLE IF NOT EXISTS aankoop_regel (
    id INTEGER PRIMARY KEY,
    aankoop_id INTEGER NOT NULL REFERENCES aankoop(id) ON DELETE CASCADE,
    wine_id INTEGER NOT NULL REFERENCES wine(id),
    aantal INTEGER NOT NULL CHECK (aantal > 0),
    prijs REAL  -- per fles
);

-- hoeveel flessen van een regel bij welke via horen; overdragen past dit aan
-- jaar: dat van de bon, zodat alleen via's van hetzelfde jaar kunnen
CREATE TABLE IF NOT EXISTS regel_verdeling (
    regel_id INTEGER NOT NULL REFERENCES aankoop_regel(id) ON DELETE CASCADE,
    jaar INTEGER NOT NULL,
    via TEXT NOT NULL,
    aantal INTEGER NOT NULL CHECK (aantal > 0),
    PRIMARY KEY (regel_id, via),
    FOREIGN KEY (jaar, via) REFERENCES via(jaar, code)
);

-- fles uit de via-pool gereserveerd/toegekend aan een klant
CREATE TABLE IF NOT EXISTS toekenning (
    klant_id INTEGER NOT NULL REFERENCES klant(id) ON DELETE CASCADE,
    wine_id INTEGER NOT NULL REFERENCES wine(id),
    aantal INTEGER NOT NULL CHECK (aantal > 0),
    PRIMARY KEY (klant_id, wine_id)
);

-- overige aankopen (tank, overnachting); het bedrag mag later ingevuld worden
CREATE TABLE IF NOT EXISTS kosten (
    id INTEGER PRIMARY KEY,
    jaar INTEGER NOT NULL REFERENCES jaar(jaar),
    omschrijving TEXT NOT NULL,
    bedrag REAL,
    betaald_door TEXT,
    -- bestellers (reiskosten): naar rato van de wijn, doorberekend aan de bestellingen;
    -- personen (eigen kosten): gelijk over de deelnemers
    verdeling TEXT NOT NULL DEFAULT 'bestellers' CHECK (verdeling IN ('bestellers', 'personen')),
    aangemaakt_op TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    versie INTEGER NOT NULL DEFAULT 0,  -- tegen het overschrijven van andermans wijziging
    FOREIGN KEY (jaar, betaald_door) REFERENCES via(jaar, code)
);

-- wie meedoet aan eigen kosten; jaar: dat van de post
CREATE TABLE IF NOT EXISTS kosten_deelnemer (
    kosten_id INTEGER NOT NULL REFERENCES kosten(id) ON DELETE CASCADE,
    jaar INTEGER NOT NULL,
    via TEXT NOT NULL,
    PRIMARY KEY (kosten_id, via),
    FOREIGN KEY (jaar, via) REFERENCES via(jaar, code)
);
