"""Migraties: oude databases bijwerken naar het huidige schema. Elke stap kijkt zelf of hij nog
nodig is, en doet niets op een database die al bij is (of die net uit schema.sql is gemaakt).
Niets hieruit wordt door de live app gebruikt; alleen db.init roept migreer() aan."""

import re
import sqlite3
from pathlib import Path

import db

MIGRATIE_JAAR = 2026  # alle data van vóór de jaren hoort bij dit jaar
PERSONEN = ["dd", "j", "h", "m"]  # hm is de gezamenlijke bestelling van h en m, geen persoon


def migreer(conn):
    """Alle stappen, in deze volgorde (oudste eerst); de hernoeming van een tabel gaat voor alles."""
    _hernoem_deelnemertabel(conn)
    _migreer_naar_regels(conn)
    _voeg_versie_toe(conn)
    _voeg_betaler_toe(conn)
    _voeg_klant_betaald_toe(conn)
    _voeg_via_verrekend_toe(conn)
    _voeg_kostenverdeling_toe(conn)
    _voeg_jaren_toe(conn)
    _hernoem_kostenverdeling(conn)
    _vul_kostendeelnemers(conn)
    _verwijder_wijnprijs(conn)
    with db.tx(conn):
        # categorie 'klein' (halve flessen) bestaat niet meer: valt onder basis
        conn.execute("UPDATE wine SET categorie = 'basis' WHERE categorie = 'klein'")


def _hernoem_deelnemertabel(conn):
    """kosten_deelnemer heet nu eigen_kosten_deelnemer. schema.sql heeft de nieuwe al leeg aangemaakt;
    die maakt plaats voor de oude, zodat de latere stappen (ook die naar jaren) met de data verdergaan."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'kosten_deelnemer'").fetchone():
        return
    with db.tx(conn):
        if conn.execute("SELECT 1 FROM eigen_kosten_deelnemer").fetchone():
            raise RuntimeError("Zowel kosten_deelnemer als eigen_kosten_deelnemer bevat data.")
        conn.execute("DROP TABLE eigen_kosten_deelnemer")
        conn.execute("ALTER TABLE kosten_deelnemer RENAME TO eigen_kosten_deelnemer")


def _kolommen(conn, tabel):
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({tabel})")}


def _voeg_versie_toe(conn):
    """Versiekolom op aankoop en klant (optimistische vergrendeling bij bewerken)."""
    for tabel in ("aankoop", "klant"):
        if "versie" not in _kolommen(conn, tabel):
            conn.execute(f"ALTER TABLE {tabel} ADD COLUMN versie INTEGER NOT NULL DEFAULT 0")


def _voeg_betaler_toe(conn):
    """Wie een aankoop betaald heeft (leeg: nog onbekend)."""
    if "betaald_door" not in _kolommen(conn, "aankoop"):
        conn.execute("ALTER TABLE aankoop ADD COLUMN betaald_door TEXT REFERENCES via(code)")


def _voeg_klant_betaald_toe(conn):
    """Of een besteller zijn rekening aan de via betaald heeft."""
    if "betaald" not in _kolommen(conn, "klant"):
        conn.execute("ALTER TABLE klant ADD COLUMN betaald INTEGER NOT NULL DEFAULT 0")


def _voeg_via_verrekend_toe(conn):
    """Of een via zijn saldo met de anderen verrekend heeft."""
    if "verrekend" not in _kolommen(conn, "via"):
        conn.execute("ALTER TABLE via ADD COLUMN verrekend INTEGER NOT NULL DEFAULT 0")


def _voeg_kostenverdeling_toe(conn):
    """Over wie overige aankopen verdeeld worden; bestaande posten blijven bij de bestellers."""
    if "verdeling" not in _kolommen(conn, "kosten"):
        conn.execute("""ALTER TABLE kosten ADD COLUMN verdeling TEXT NOT NULL DEFAULT 'bestellers'
                        CHECK (verdeling IN ('bestellers', 'personen'))""")


def _vul_kostendeelnemers(conn):
    """Eigen kosten van vóór de deelnemers: daar deed iedereen aan mee."""
    posten = conn.execute(
        """SELECT id, jaar FROM kosten WHERE verdeling = 'eigen'
           AND id NOT IN (SELECT kosten_id FROM eigen_kosten_deelnemer)"""
    ).fetchall()
    conn.executemany("INSERT INTO eigen_kosten_deelnemer (kosten_id, jaar, via) VALUES (?, ?, ?)",
                     [(r["id"], r["jaar"], p) for r in posten for p in PERSONEN])


def _verwijder_wijnprijs(conn):
    """De standaardprijs per wijn werd niet gebruikt; de prijs staat per aankoopregel."""
    if "prijs" in _kolommen(conn, "wine"):
        conn.execute("ALTER TABLE wine DROP COLUMN prijs")


# oude codes voor de verdeling van overige aankopen → de huidige
_KOSTENVERDELING = "CASE verdeling WHEN 'personen' THEN 'eigen' WHEN 'bestellers' THEN 'reiskosten' ELSE verdeling END"


def _hernoem_kostenverdeling(conn):
    """Verdeling 'bestellers'/'personen' heet nu 'reiskosten'/'eigen'. De CHECK zit in de tabel,
    dus die wordt opnieuw opgebouwd."""
    sql = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'kosten'").fetchone()["sql"]
    if "'personen'" not in sql:
        return
    kolommen = "id, jaar, omschrijving, bedrag, betaald_door, aangemaakt_op, versie"
    conn.execute("PRAGMA foreign_keys = OFF")  # nodig om de tabel om te bouwen
    try:
        with db.tx(conn):
            conn.execute(_tabel_sql("kosten", "kosten_nieuw"))
            conn.execute(f"INSERT INTO kosten_nieuw ({kolommen}, verdeling) "
                         f"SELECT {kolommen}, {_KOSTENVERDELING} FROM kosten")
            conn.execute("DROP TABLE kosten")
            conn.execute("ALTER TABLE kosten_nieuw RENAME TO kosten")
            if conn.execute("PRAGMA foreign_key_check").fetchone():
                raise RuntimeError("Hernoemen van de kostenverdeling gaf ongeldige verwijzingen.")
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


def _migreer_naar_regels(conn):
    """Oude opzet (aankoop = één wijn + aankoop_verdeling) omzetten naar bon + regels."""
    if "wine_id" not in _kolommen(conn, "aankoop"):
        return
    conn.execute("PRAGMA foreign_keys = OFF")  # nodig om de aankoop-tabel om te bouwen
    try:
        with db.tx(conn):
            conn.execute(
                """INSERT INTO aankoop_regel (id, aankoop_id, wine_id, aantal, prijs)
                   SELECT id, id, wine_id, aantal, prijs FROM aankoop"""
            )
            # regel_verdeling is dan al nieuw aangemaakt, met een jaar
            conn.execute(
                f"""INSERT INTO regel_verdeling (regel_id, jaar, via, aantal)
                    SELECT aankoop_id, {MIGRATIE_JAAR}, via, aantal FROM aankoop_verdeling"""
            )
            conn.execute("DROP TABLE aankoop_verdeling")
            conn.execute(
                """CREATE TABLE aankoop_nieuw (
                       id INTEGER PRIMARY KEY,
                       omschrijving TEXT NOT NULL DEFAULT '',
                       aangemaakt_op TEXT NOT NULL DEFAULT (datetime('now', 'localtime')))"""
            )
            conn.execute(
                "INSERT INTO aankoop_nieuw (id, aangemaakt_op) SELECT id, aangemaakt_op FROM aankoop"
            )
            conn.execute("DROP TABLE aankoop")
            conn.execute("ALTER TABLE aankoop_nieuw RENAME TO aankoop")
            # alleen de regels: tabellen die al naar via(jaar, code) verwijzen, controleert _voeg_jaren_toe
            if conn.execute("PRAGMA foreign_key_check(aankoop_regel)").fetchone():
                raise RuntimeError("Migratie naar aankoopregels gaf ongeldige verwijzingen.")
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


# kolommen van vóór de jaren; die gaan mee naar de nieuwe tabellen, met jaar = MIGRATIE_JAAR
_JAREN_KOLOMMEN = {
    "via": "code, naam, volgorde, verrekend",
    "wine": "id, naam, categorie, is_vast",
    "klant": "id, via, naam, aantekening, iets_luxer_n, luxe_n, versie, betaald",
    "aankoop": "id, omschrijving, aangemaakt_op, versie, betaald_door",
    "regel_verdeling": "regel_id, via, aantal",
    "kosten": "id, omschrijving, bedrag, betaald_door, verdeling, aangemaakt_op, versie",
    "eigen_kosten_deelnemer": "kosten_id, via",
}


def _tabel_sql(tabel, nieuwe_naam):
    """De CREATE TABLE uit schema.sql, onder een andere naam."""
    m = re.search(rf"CREATE TABLE IF NOT EXISTS {tabel} \(.*?\n\);", db.SCHEMA.read_text(), re.S)
    return m.group(0).replace(f"IF NOT EXISTS {tabel} (", f"{nieuwe_naam} (", 1)


def _voeg_jaren_toe(conn):
    """Alles van vóór de jaren hoort bij MIGRATIE_JAAR. De tabellen met een via of een unieke naam
    krijgen een jaar in hun sleutel, dus die worden opnieuw opgebouwd. Vooraf een kopie van de database in backups/."""
    if "jaar" in _kolommen(conn, "via"):
        return
    pad = conn.execute("PRAGMA database_list").fetchone()["file"]
    if pad:
        map_ = Path(pad).parent / "backups"
        map_.mkdir(exist_ok=True)
        kopie = sqlite3.connect(map_ / (Path(pad).name + ".voor-jaren"))
        try:
            conn.backup(kopie)
        finally:
            kopie.close()
    conn.execute("PRAGMA foreign_keys = OFF")  # nodig om tabellen om te bouwen
    try:
        with db.tx(conn):
            conn.execute("INSERT OR IGNORE INTO jaar (jaar) VALUES (?)", (MIGRATIE_JAAR,))
            for tabel, kolommen in _JAREN_KOLOMMEN.items():
                conn.execute(_tabel_sql(tabel, f"{tabel}_nieuw"))
                bron = kolommen.replace("verdeling", _KOSTENVERDELING) if tabel == "kosten" else kolommen
                conn.execute(f"INSERT INTO {tabel}_nieuw (jaar, {kolommen}) "
                             f"SELECT {MIGRATIE_JAAR}, {bron} FROM {tabel}")
                conn.execute(f"DROP TABLE {tabel}")
                conn.execute(f"ALTER TABLE {tabel}_nieuw RENAME TO {tabel}")
            if conn.execute("PRAGMA foreign_key_check").fetchone():
                raise RuntimeError("Migratie naar jaren gaf ongeldige verwijzingen.")
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
