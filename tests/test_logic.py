import sqlite3
from datetime import date
from pathlib import Path

import pytest

import db
import logic
import migraties
from logic import ValidatieFout

J = date.today().year  # het eerste jaar van een nieuwe database


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    db.init(c)
    return c


def nog(conn, via, cat):
    for r in logic.overzicht(conn, J)["per_via"]:
        if r["via"]["code"] == via:
            return r["cats"][cat]["nog"]


def vrij(conn, via, wine_id):
    for p in logic.pool(conn, J, via):
        if p["wijn"]["id"] == wine_id:
            return p["vrij"]
    return 0


def wijn_id(conn, naam):
    return conn.execute("SELECT id FROM wine WHERE naam = ?", (naam,)).fetchone()["id"]


def regel(naam, verdeling, categorie="iets_luxer", prijs=None):
    return {"naam": naam, "categorie": categorie, "prijs": prijs, "verdeling": verdeling}


def verdelingen(conn, aankoop_id=None):
    """[(wijnnaam, {via: n}), ...] van één bon (of de nieuwste)."""
    bon = logic.aankopen(conn, J, aankoop_id)[0]
    return [(x["r"]["naam"], x["verdeling"]) for x in bon["regels"]]


@pytest.fixture
def scenario(conn):
    pertois = logic.wijnen(conn, J, vast=True)[0]["id"]
    joris = logic.klant_opslaan(conn, J, None, "dd", "Joris", "", 8, 0, {pertois: 6})
    ddzelf = logic.klant_opslaan(conn, J, None, "dd", "Dd zelf", "", 20, 1, {})
    elodie = logic.klant_opslaan(conn, J, None, "j", "Elodie", "2 rosé", 4, 0, {})
    hema = logic.klant_opslaan(conn, J, None, "hm", "HEMA zelf", "", 8, 3, {})
    bon = logic.aankoop_opslaan(conn, J, None, "Tissier", [
        regel("Tissier Juliette", {"dd": 8, "j": 4, "hm": 4}, prijs="23,5"),
    ])
    return dict(joris=joris, ddzelf=ddzelf, elodie=elodie, hema=hema, bon=bon,
                juliette=wijn_id(conn, "Tissier Juliette"))


def test_seed(conn):
    assert [v["code"] for v in logic.vias(conn, J)] == ["dd", "j", "h", "m", "hm"]
    assert len(logic.wijnen(conn, J, vast=True)) == 4


def test_nog_kopen_na_gedeelde_aankoop(conn, scenario):
    assert nog(conn, "dd", "iets_luxer") == 28 - 8
    assert nog(conn, "j", "iets_luxer") == 0
    assert nog(conn, "hm", "iets_luxer") == 4
    assert nog(conn, "hm", "luxe") == 3
    tot = logic.overzicht(conn, J)["totaal"]["iets_luxer"]
    assert (tot["nodig"], tot["gekocht"], tot["nog"]) == (40, 16, 24)


def test_vaste_flessen_tellen_niet_mee_in_nog_kopen(conn, scenario):
    k = [k for k in logic.klanten(conn, J, "dd") if k["k"]["naam"] == "Joris"][0]
    assert k["totaal"] == 14
    assert k["open"] == {"iets_luxer": 8, "luxe": 0}


def test_bon_met_meerdere_wijnen(conn, scenario):
    bon = logic.aankoop_opslaan(conn, J, None, "Dagonet", [
        regel("Dagonet Emprise", {"dd": 3, "j": 4, "m": 3}),
        regel("tissier juliette", {"hm": 2}),  # bestaande wijn, andere hoofdletters
        regel("Dagonet BdB Millésime", {"dd": 1}, categorie="luxe", prijs="42,50"),
    ])
    b = logic.aankopen(conn, J, bon)[0]
    assert b["a"]["omschrijving"] == "Dagonet" and b["totaal"] == 13
    assert b["regels"][2]["r"]["prijs"] == 42.5
    assert vrij(conn, "hm", scenario["juliette"]) == 6  # telt op bij dezelfde wijn
    assert nog(conn, "dd", "luxe") == 0


def test_regel_zonder_verdeling(conn, scenario):
    with pytest.raises(ValidatieFout, match="X: verdeel minimaal één fles"):
        logic.aankoop_opslaan(conn, J, None, "", [regel("X", {"dd": 0})])
    assert len(logic.aankopen(conn, J)) == 1
    assert conn.execute("SELECT 1 FROM wine WHERE naam='X'").fetchone() is None  # rollback


def test_lege_bon(conn):
    with pytest.raises(ValidatieFout):
        logic.aankoop_opslaan(conn, J, None, "", [])


def test_aantal_is_som_van_verdeling(conn):
    bon = logic.aankoop_opslaan(conn, J, None, "", [regel("Dagonet ½", {"m": 30, "hm": 12}, None)])
    assert logic.aankopen(conn, J, bon)[0]["regels"][0]["r"]["aantal"] == 42


def test_basisflessen_lopen_via_aankoop_en_pool(conn, scenario):
    pertois = wijn_id(conn, "Pertois tradition")
    dd = next(r for r in logic.overzicht(conn, J)["per_via"] if r["via"]["code"] == "dd")
    assert next(x for x in dd["vast"] if x["wijn"]["id"] == pertois)["nog"] == 6  # Joris
    # categorie niet nodig voor een vaste fles; naam hoofdletterongevoelig
    logic.aankoop_opslaan(conn, J, None, "Pertois", [
        regel("pertois Tradition", {"dd": 4, "j": 6}, categorie=None),
    ])
    dd = next(r for r in logic.overzicht(conn, J)["per_via"] if r["via"]["code"] == "dd")
    assert next(x for x in dd["vast"] if x["wijn"]["id"] == pertois)["nog"] == 2
    totaal = next(x for x in logic.overzicht(conn, J)["totaal_vast"] if x["wijn"]["id"] == pertois)
    assert (totaal["nog"], totaal["overschot"]) == (2, 6)  # j heeft er 6 over
    assert vrij(conn, "dd", pertois) == 4
    assert nog(conn, "dd", "iets_luxer") == 20  # telt niet mee bij de luxe categorieën


def test_aanvullen_vult_tot_bestelling_of_pool(conn, scenario):
    pertois = wijn_id(conn, "Pertois tradition")
    logic.aankoop_opslaan(conn, J, None, "", [regel("Pertois tradition", {"dd": 4}, None)])
    logic.toekenning_aanvullen(conn, J, scenario["joris"], pertois)  # wil 6, pool heeft 4
    joris = next(k for k in logic.klanten(conn, J, "dd") if k["k"]["id"] == scenario["joris"])
    assert [(x["heeft"], x["open"]) for x in joris["vast_status"]] == [(4, 2)]
    with pytest.raises(ValidatieFout):  # pool leeg
        logic.toekenning_aanvullen(conn, J, scenario["joris"], pertois)
    logic.aankoop_opslaan(conn, J, None, "", [regel("Pertois tradition", {"dd": 5}, None)])
    logic.toekenning_aanvullen(conn, J, scenario["joris"], pertois)
    joris = next(k for k in logic.klanten(conn, J, "dd") if k["k"]["id"] == scenario["joris"])
    assert joris["vast_status"][0]["open"] == 0 and vrij(conn, "dd", pertois) == 3


def test_nieuwe_wijn_zonder_categorie(conn):
    with pytest.raises(ValidatieFout, match="kies een categorie"):
        logic.aankoop_opslaan(conn, J, None, "", [regel("Onbekend", {"dd": 1}, categorie=None)])


def test_categorie_conflict_met_andere_bon(conn, scenario):
    with pytest.raises(ValidatieFout, match="staat al als Iets luxer"):
        logic.aankoop_opslaan(conn, J, None, "", [regel("Tissier Juliette", {"dd": 1}, "luxe")])
    # op de eigen (enige) bon mag de categorie wel worden omgezet
    logic.aankoop_opslaan(conn, J, scenario["bon"], "Tissier", [
        regel("Tissier Juliette", {"dd": 8, "j": 4, "hm": 4}, "luxe"),
    ])
    assert logic.wijn(conn, J, scenario["juliette"])["categorie"] == "luxe"


def test_wijn_verdwijnt_als_hij_op_geen_bon_meer_staat(conn, scenario):
    logic.aankoop_opslaan(conn, J, scenario["bon"], "Tissier", [regel("Tissier Rosé", {"dd": 16})])
    assert [w["naam"] for w in logic.wijnen(conn, J, vast=False)] == ["Tissier Rosé"]
    logic.aankoop_verwijderen(conn, J, scenario["bon"])
    assert logic.wijnen(conn, J, vast=False) == []


def test_overdracht(conn, scenario):
    logic.overdragen(conn, J, scenario["juliette"], "dd", "j", 2)
    assert vrij(conn, "dd", scenario["juliette"]) == 6
    assert vrij(conn, "j", scenario["juliette"]) == 6
    assert nog(conn, "j", "iets_luxer") == -2  # overschot
    tot = logic.overzicht(conn, J)["totaal"]["iets_luxer"]
    assert (tot["nog"], tot["overschot"]) == (26, 2)  # dd-tekort niet weggestreept
    # overdragen past de verdeling van de regel aan; aantal blijft gelijk
    assert verdelingen(conn) == [("Tissier Juliette", {"dd": 6, "j": 6, "hm": 4})]
    logic.overdragen(conn, J, scenario["juliette"], "hm", "j", 4)  # hm valt weg uit verdeling
    assert verdelingen(conn) == [("Tissier Juliette", {"dd": 6, "j": 10})]
    with pytest.raises(ValidatieFout):
        logic.overdragen(conn, J, scenario["juliette"], "dd", "j", 7)
    with pytest.raises(ValidatieFout):
        logic.overdragen(conn, J, scenario["juliette"], "h", "j", 1)


def test_overdragen_over_meerdere_bonnen(conn, scenario):
    nieuw = logic.aankoop_opslaan(conn, J, None, "", [regel("Tissier Juliette", {"dd": 2})])
    logic.overdragen(conn, J, scenario["juliette"], "dd", "m", 5)  # nieuwste regel eerst
    assert verdelingen(conn, nieuw) == [("Tissier Juliette", {"m": 2})]
    assert verdelingen(conn, scenario["bon"]) == [
        ("Tissier Juliette", {"dd": 5, "j": 4, "m": 3, "hm": 4})
    ]


def test_toekennen_binnen_pool(conn, scenario):
    logic.toekenning_wijzig(conn, J, scenario["elodie"], scenario["juliette"], 3)
    assert vrij(conn, "j", scenario["juliette"]) == 1
    k = logic.klanten(conn, J, "j")[0]
    assert k["open"]["iets_luxer"] == 1
    with pytest.raises(ValidatieFout):
        logic.toekenning_wijzig(conn, J, scenario["elodie"], scenario["juliette"], 2)
    logic.toekenning_wijzig(conn, J, scenario["elodie"], scenario["juliette"], -3)
    assert logic.klanten(conn, J, "j")[0]["toekenningen"] == []


def test_verwijderen_geblokkeerd_na_toekenning(conn, scenario):
    logic.toekenning_wijzig(conn, J, scenario["elodie"], scenario["juliette"], 1)
    with pytest.raises(ValidatieFout):
        logic.aankoop_verwijderen(conn, J, scenario["bon"])
    assert len(logic.aankopen(conn, J)) == 1  # rollback
    with pytest.raises(ValidatieFout):
        logic.overdragen(conn, J, scenario["juliette"], "j", "dd", 4)


def test_aankoop_wijzigen_mag_niet_onder_toekenning(conn, scenario):
    logic.toekenning_wijzig(conn, J, scenario["hema"], scenario["juliette"], 4)
    with pytest.raises(ValidatieFout):
        logic.aankoop_opslaan(conn, J, scenario["bon"], "Tissier", [
            regel("Tissier Juliette", {"dd": 10, "j": 4, "hm": 2}),
        ])
    logic.aankoop_opslaan(conn, J, scenario["bon"], "Tissier", [
        regel("Tissier Juliette", {"dd": 10, "j": 4, "hm": 4}),
    ])
    assert vrij(conn, "dd", scenario["juliette"]) == 10


def test_klant_naar_andere_via_neemt_toekenning_mee(conn, scenario):
    logic.toekenning_wijzig(conn, J, scenario["elodie"], scenario["juliette"], 2)
    with pytest.raises(ValidatieFout):  # h heeft geen Juliette
        logic.klant_opslaan(conn, J, scenario["elodie"], "h", "Elodie", "", 4, 0, {})
    logic.klant_opslaan(conn, J, scenario["elodie"], "dd", "Elodie", "", 4, 0, {})
    assert vrij(conn, "dd", scenario["juliette"]) == 6


def test_migratie_naar_bonnen_met_regels():
    c = db.connect(":memory:")
    c.executescript("""
        CREATE TABLE via (code TEXT PRIMARY KEY, naam TEXT NOT NULL, volgorde INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE wine (id INTEGER PRIMARY KEY, naam TEXT NOT NULL UNIQUE, categorie TEXT NOT NULL,
                           prijs REAL, is_vast INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE aankoop (id INTEGER PRIMARY KEY, wine_id INTEGER NOT NULL REFERENCES wine(id),
                              aantal INTEGER NOT NULL, prijs REAL, aangemaakt_op TEXT NOT NULL DEFAULT '');
        CREATE TABLE aankoop_verdeling (aankoop_id INTEGER NOT NULL REFERENCES aankoop(id) ON DELETE CASCADE,
                                        via TEXT NOT NULL, aantal INTEGER NOT NULL, PRIMARY KEY (aankoop_id, via));
        INSERT INTO via VALUES ('dd','dd',0),('j','j',1),('h','h',2),('m','m',3),('hm','hm',4);
        INSERT INTO wine VALUES (9, 'Tissier Juliette', 'iets_luxer', NULL, 0);
        INSERT INTO aankoop VALUES (1, 9, 16, 23.5, '2026-10-27 17:26:46');
        INSERT INTO aankoop_verdeling VALUES (1,'dd',8),(1,'j',5),(1,'hm',3);
    """)
    db.init(c)
    m = migraties.MIGRATIE_JAAR
    assert logic.jaren(c) == [m]
    bon = logic.aankopen(c, m)[0]
    assert [(x["r"]["naam"], x["verdeling"]) for x in bon["regels"]] == [("Tissier Juliette", {"dd": 8, "j": 5, "hm": 3})]
    assert bon["a"]["aangemaakt_op"] == "2026-10-27 17:26:46" and bon["regels"][0]["r"]["prijs"] == 23.5
    kolommen = {r["name"] for r in c.execute("PRAGMA table_info(aankoop)")}
    assert "wine_id" not in kolommen and "versie" in kolommen
    assert c.execute("PRAGMA foreign_key_check").fetchall() == []
    logic.aankoop_verwijderen(c, m, 1)  # cascade naar regels werkt na het ombouwen
    assert c.execute("SELECT COUNT(*) FROM aankoop_regel").fetchone()[0] == 0


def test_overdragen_nieuwste_bon_eerst_ook_na_bewerken(conn, scenario):
    nieuw = logic.aankoop_opslaan(conn, J, None, "", [regel("Tissier Juliette", {"dd": 2})])
    logic.aankoop_opslaan(conn, J, scenario["bon"], "Tissier", [  # oude bon opnieuw opgeslagen
        regel("Tissier Juliette", {"dd": 8, "j": 4, "hm": 4}),
    ])
    logic.overdragen(conn, J, scenario["juliette"], "dd", "m", 1)
    assert verdelingen(conn, nieuw) == [("Tissier Juliette", {"dd": 1, "m": 1})]


def test_bon_bewerken_na_overdracht_overschrijft_niet(conn, scenario):
    versie = logic.aankopen(conn, J, scenario["bon"])[0]["a"]["versie"]
    logic.overdragen(conn, J, scenario["juliette"], "dd", "j", 2)  # iemand anders, intussen
    with pytest.raises(ValidatieFout, match="intussen gewijzigd"):
        logic.aankoop_opslaan(conn, J, scenario["bon"], "Tissier", [
            regel("Tissier Juliette", {"dd": 8, "j": 4, "hm": 4}),
        ], versie=versie)
    assert verdelingen(conn) == [("Tissier Juliette", {"dd": 6, "j": 6, "hm": 4})]
    nieuwe_versie = logic.aankopen(conn, J, scenario["bon"])[0]["a"]["versie"]
    logic.aankoop_opslaan(conn, J, scenario["bon"], "Tissier", [
        regel("Tissier Juliette", {"dd": 6, "j": 6, "hm": 4}),
    ], versie=nieuwe_versie)


def test_klant_bewerken_met_oude_versie(conn, scenario):
    versie = logic.klant(conn, J, scenario["elodie"])["k"]["versie"]
    logic.klant_opslaan(conn, J, scenario["elodie"], "j", "Elodie", "", 5, 0, {}, versie)
    with pytest.raises(ValidatieFout, match="intussen gewijzigd"):
        logic.klant_opslaan(conn, J, scenario["elodie"], "j", "Elodie", "", 6, 0, {}, versie)
    assert logic.klant(conn, J, scenario["elodie"])["nodig"]["iets_luxer"] == 5
    logic.klant_verwijderen(conn, J, scenario["elodie"])
    with pytest.raises(ValidatieFout, match="intussen gewijzigd"):
        logic.klant_opslaan(conn, J, scenario["elodie"], "j", "Elodie", "", 6, 0, {})


@pytest.mark.parametrize("actie", [
    lambda c, s: logic.overdragen(c, J, s["juliette"], "dd", "zz", 1),
    lambda c, s: logic.overdragen(c, J, 999, "dd", "j", 1),
    lambda c, s: logic.overdragen(c, J, "x", "dd", "j", 1),
    lambda c, s: logic.klant_opslaan(c, J, None, "zz", "A", "", 0, 0, {}),
    lambda c, s: logic.klant_opslaan(c, J, None, "dd", "A", "", 0, 0, {s["juliette"]: 1}),
    lambda c, s: logic.toekenning_aanvullen(c, J, 999, 1),
    lambda c, s: logic.toekenning_wijzig(c, J, 999, s["juliette"], 1),
    lambda c, s: logic.toekenning_wijzig(c, J, s["elodie"], 999, 1),
    lambda c, s: logic.toekenning_wijzig(c, J, s["elodie"], s["juliette"], "veel"),
    lambda c, s: logic.aankoop_opslaan(c, J, 999, "", [regel("X", {"dd": 1})]),
    lambda c, s: logic.aankoop_opslaan(c, J, None, "", [regel("X", {"zz": 1})]),
])
def test_ongeldige_invoer_geeft_validatiefout(conn, scenario, actie):
    with pytest.raises(ValidatieFout):
        actie(conn, scenario)


def test_tegenstrijdige_categorie_op_een_bon(conn):
    with pytest.raises(ValidatieFout, match="twee keer op de bon"):
        logic.aankoop_opslaan(conn, J, None, "", [regel("Y", {"dd": 1}), regel("y", {"dd": 1}, "luxe")])
    logic.aankoop_opslaan(conn, J, None, "", [regel("Y", {"dd": 1}), regel("y", {"j": 1})])
    assert [x["r"]["aantal"] for x in logic.aankopen(conn, J)[0]["regels"]] == [1, 1]


def test_melding_bij_te_veel_toekennen(conn, scenario):
    with pytest.raises(ValidatieFout, match="Niet genoeg Tissier Juliette vrij in de pool van j"):
        logic.toekenning_wijzig(conn, J, scenario["elodie"], scenario["juliette"], 5)


def test_init_weigert_basis_die_geen_vaste_fles_is(conn):
    conn.execute("INSERT INTO wine (jaar, naam, categorie) VALUES (?, 'Oud klein', 'basis')", (J,))
    with pytest.raises(RuntimeError, match="Oud klein"):
        db.init(conn)


def test_bestel_totalen_en_nog_per_via(conn, scenario):
    pertois = wijn_id(conn, "Pertois tradition")
    t = logic.bestel_totalen(conn, J)
    assert t["dd"] == {"vast": {pertois: 6, **{w: 0 for w in t["dd"]["vast"] if w != pertois}},
                       "iets_luxer": 28, "luxe": 1, "totaal": 35}
    assert (t["_"]["iets_luxer"], t["_"]["luxe"], t["_"]["totaal"]) == (40, 4, 50)  # incl. 6 Pertois
    nog_ = logic.nog_per_via(conn, J)
    assert nog_["iets_luxer"] == {"dd": 20, "j": 0, "h": 0, "m": 0, "hm": 4}
    assert nog_["basis"]["Pertois tradition"]["dd"] == 6


def test_gelijktijdige_schrijvers_wachten_op_elkaar(tmp_path):
    import threading
    pad = tmp_path / "c.db"
    a = db.connect(pad)
    db.init(a)
    k = logic.klant_opslaan(a, J, None, "dd", "A", "", 0, 0, {})
    logic.aankoop_opslaan(a, J, None, "", [regel("Z", {"dd": 2})])
    z = wijn_id(a, "Z")
    fouten = []

    def tik():
        c = db.connect(pad)
        try:
            logic.toekenning_wijzig(c, J, k, z, 1)
        except Exception as e:  # zonder BEGIN IMMEDIATE: 'database is locked'
            fouten.append(e)
        finally:
            c.close()

    with db.tx(a):
        logic.toekenning_wijzig(a, J, k, z, 1)
        t = threading.Thread(target=tik)
        t.start()
        t.join(0.3)  # de ander wacht op ons schrijflock
    t.join()
    assert fouten == []
    assert logic.klant(a, J, k)["toekenningen"][0]["aantal"] == 2


# --- verrekening -------------------------------------------------------------

def stand(vr, via):
    return next(r for r in vr["per_via"] if r["via"]["code"] == via)


def sluit(vr):
    return sum(r["saldo"] for r in vr["per_via"]) + vr["onbekende_betaler"] - vr["onverdeeld"]


def test_verdeel_telt_exact_op():
    assert logic._verdeel(100, {"a": 1, "b": 1, "c": 1}) == {"a": 34, "b": 33, "c": 33}
    assert logic._verdeel(100, {"a": 0, "b": 0}) == {"a": 0, "b": 0}
    assert sum(logic._verdeel(9999, {"a": 0.1, "b": 0.2, "c": 0.7}).values()) == 9999


def test_regelbedrag_is_prijs_per_fles_verdeeld_over_vias(conn):
    logic.aankoop_opslaan(conn, J, None, "T", [regel("Krug", {"dd": 3, "j": 1}, "luxe", "10,50")], betaald_door="dd")
    vr = logic.verrekening(conn, J)
    assert (stand(vr, "dd")["wijn"], stand(vr, "j")["wijn"]) == (3150, 1050)
    assert stand(vr, "dd")["saldo"] == 1050 and stand(vr, "j")["saldo"] == -1050
    assert not vr["waarschuwingen"] and sluit(vr) == 0


def test_vaste_kosten_naar_rato_van_de_wijn(conn):
    logic.aankoop_opslaan(conn, J, None, "T", [regel("Krug", {"dd": 3, "j": 1}, "luxe", "10")], betaald_door="j")
    logic.kosten_opslaan(conn, J, None, "Tank", "100", "h")
    vr = logic.verrekening(conn, J)
    assert (stand(vr, "dd")["kosten"], stand(vr, "j")["kosten"], stand(vr, "h")["kosten"]) == (7500, 2500, 0)
    assert stand(vr, "h")["saldo"] == 10000
    assert stand(vr, "j")["saldo"] == 4000 - 1000 - 2500
    assert sluit(vr) == 0


def test_ontbrekende_gegevens_geven_waarschuwingen(conn):
    bon = logic.aankoop_opslaan(conn, J, None, "T", [regel("Krug", {"dd": 2}, "luxe", "10"), regel("Ruinart", {"dd": 1}, "luxe")])
    tank = logic.kosten_opslaan(conn, J, None, "Tank", "", "j")
    hotel = conn.execute("INSERT INTO kosten (jaar, omschrijving, bedrag) VALUES (?, 'Hotel', 50)", (J,)).lastrowid  # oude post zonder betaler
    vr = logic.verrekening(conn, J)
    assert [w["tekst"] for w in vr["waarschuwingen"]] == [
        "T: betaler ontbreekt, 1 zonder prijs", "Tank: bedrag ontbreekt", "Hotel: betaler ontbreekt"]
    assert vr["waarschuwingen"][0]["aankoop_id"] == bon and vr["waarschuwingen"][1]["kosten_id"] == tank
    assert vr["onbekende_betaler"] == 2000 + 5000 and sluit(vr) == 0

    logic.aankoop_opslaan(conn, J, bon, "T", [regel("Krug", {"dd": 2}, "luxe", "10"), regel("Ruinart", {"dd": 1}, "luxe", "20")],
                          betaald_door="dd")
    logic.kosten_opslaan(conn, J, tank, "Tank", "30", "j")
    logic.kosten_opslaan(conn, J, hotel, "Hotel", "50", "j")
    vr = logic.verrekening(conn, J)
    assert not vr["waarschuwingen"] and vr["onbekende_betaler"] == 0 and sluit(vr) == 0


def test_vaste_kosten_zonder_wijn_met_prijs_blijven_onverdeeld(conn):
    logic.aankoop_opslaan(conn, J, None, "T", [regel("Krug", {"dd": 2}, "luxe")], betaald_door="dd")
    logic.kosten_opslaan(conn, J, None, "Tank", "40", "j")
    vr = logic.verrekening(conn, J)
    assert vr["onverdeeld"] == 4000 and stand(vr, "j")["saldo"] == 4000 and sluit(vr) == 0


def test_overdracht_verschuift_de_kosten(conn):
    logic.aankoop_opslaan(conn, J, None, "T", [regel("Krug", {"dd": 4}, "luxe", "10")], betaald_door="dd")
    logic.overdragen(conn, J, wijn_id(conn, "Krug"), "dd", "j", 1)
    vr = logic.verrekening(conn, J)
    assert (stand(vr, "dd")["wijn"], stand(vr, "j")["wijn"]) == (3000, 1000)


def test_betaler_blijft_na_overdracht_en_moet_bestaan(conn):
    bon = logic.aankoop_opslaan(conn, J, None, "T", [regel("Krug", {"dd": 4}, "luxe", "10")], betaald_door="j")
    logic.overdragen(conn, J, wijn_id(conn, "Krug"), "dd", "h", 1)
    assert logic.aankopen(conn, J, bon)[0]["a"]["betaald_door"] == "j"
    with pytest.raises(ValidatieFout):
        logic.aankoop_opslaan(conn, J, None, "T", [regel("Krug", {"dd": 1}, "luxe")], betaald_door="zz")
    with pytest.raises(ValidatieFout):
        logic.kosten_opslaan(conn, J, None, "Tank", "1", "zz")
    with pytest.raises(ValidatieFout):
        logic.kosten_opslaan(conn, J, None, "Tank", "1", "")


def test_klantbedragen_tellen_op_tot_via_totaal(conn):
    piet = logic.klant_opslaan(conn, J, None, "dd", "Piet", "", 0, 1, {})
    jan = logic.klant_opslaan(conn, J, None, "dd", "Jan", "", 0, 3, {})
    logic.aankoop_opslaan(conn, J, None, "A", [regel("Krug", {"dd": 2, "j": 1}, "luxe", "10")], betaald_door="dd")
    logic.aankoop_opslaan(conn, J, None, "B", [regel("Krug", {"dd": 2}, "luxe", "20,01")], betaald_door="dd")
    logic.kosten_opslaan(conn, J, None, "Tank", "33,33", "j")
    krug = wijn_id(conn, "Krug")
    logic.toekenning_wijzig(conn, J, piet, krug, 1)
    logic.toekenning_wijzig(conn, J, jan, krug, 2)

    b = logic.klant_bedragen(conn, J, "dd")
    rij = {r["k"]["naam"]: r for r in b["rijen"]}
    assert abs(rij["Jan"]["wijn"] - 2 * rij["Piet"]["wijn"]) <= 2  # gemiddelde prijs, op afronding na
    assert (rij["Piet"]["open"], rij["Jan"]["open"]) == (0, 1)
    assert b["vrij"]["flessen"] == 1
    for veld, totaal in (("wijn", "wijn"), ("kosten", "kosten"), ("totaal", "doorberekend")):
        assert sum(r[veld] for r in b["rijen"]) + b["vrij"][veld] == b["stand"][totaal]


def test_ontbrekende_prijs_of_bedrag_houdt_klantbedragen_tegen(conn):
    logic.aankoop_opslaan(conn, J, None, "A", [regel("Krug", {"j": 1}, "luxe")], betaald_door="j")
    assert [w["tekst"] for w in logic.klant_bedragen(conn, J, "dd")["ontbreekt"]] == ["A: 1 zonder prijs"]  # ook bij andere via
    conn.execute("UPDATE aankoop_regel SET prijs = 20")
    assert logic.klant_bedragen(conn, J, "dd")["ontbreekt"] == []
    logic.kosten_opslaan(conn, J, None, "Tank", "", "j")
    assert [w["tekst"] for w in logic.klant_bedragen(conn, J, "dd")["ontbreekt"]] == ["Tank: bedrag ontbreekt"]


def test_ontbrekende_betaler_houdt_klantbedragen_niet_tegen(conn):
    logic.aankoop_opslaan(conn, J, None, "A", [regel("Krug", {"dd": 1}, "luxe", "20")])
    assert logic.klant_bedragen(conn, J, "dd")["ontbreekt"] == []
    assert logic.verrekening(conn, J)["waarschuwingen"]


@pytest.mark.parametrize("waarde", ["nan", "inf", "-1", "abc"])
def test_ongeldige_bedragen_geweigerd(conn, waarde):
    with pytest.raises(ValidatieFout):
        logic.kosten_opslaan(conn, J, None, "Tank", waarde, "j")
    with pytest.raises(ValidatieFout):
        logic.aankoop_opslaan(conn, J, None, "T", [regel("Krug", {"dd": 1}, "luxe", waarde)])


def test_kosten_alleen_over_de_vias_gelijk_en_niet_doorberekend(conn):
    logic.klant_opslaan(conn, J, None, "hm", "Piet", "", 0, 1, {})
    logic.aankoop_opslaan(conn, J, None, "T", [regel("Krug", {"hm": 1}, "luxe", "10")], betaald_door="hm")
    logic.kosten_opslaan(conn, J, None, "Hotel", "100,01", "dd", verdeling="eigen", deelnemers=migraties.PERSONEN)
    vr = logic.verrekening(conn, J)
    assert [stand(vr, v)["eigen"] for v in ("dd", "j", "h", "m", "hm")] == [2501, 2500, 2500, 2500, 0]
    assert stand(vr, "dd")["saldo"] == 10001 - 2501 and stand(vr, "j")["saldo"] == -2500
    assert stand(vr, "hm")["saldo"] == 0 and sluit(vr) == 0
    assert logic.klant_bedragen(conn, J, "hm")["vrij"]["totaal"] == 1000

    logic.kosten_opslaan(conn, J, None, "Tank", "50", "j", verdeling="eigen", deelnemers=migraties.PERSONEN)
    logic.kosten_opslaan(conn, J, None, "Diesel", "20", "j")
    b = logic.klant_bedragen(conn, J, "hm")
    assert b["vrij"]["kosten"] == 2000  # alleen Diesel; hm heeft alle wijn


def test_onbekende_kostenverdeling_geweigerd(conn):
    with pytest.raises(ValidatieFout):
        logic.kosten_opslaan(conn, J, None, "Hotel", "1", "dd", verdeling="iedereen")


def test_eigen_kosten_alleen_over_de_deelnemers(conn):
    hotel = logic.kosten_opslaan(conn, J, None, "Hotel", "30", "dd", verdeling="eigen", deelnemers=["j", "dd"])
    logic.kosten_opslaan(conn, J, None, "Diner", "5", "dd", verdeling="eigen", deelnemers=["dd", "j", "h", "m", "hm"])
    vr = logic.verrekening(conn, J)
    assert [stand(vr, v)["eigen"] for v in ("dd", "j", "h", "m", "hm")] == [1600, 1600, 100, 100, 100]
    assert sluit(vr) == 0
    assert logic.kosten(conn, J, hotel)[0]["deelnemers"] == ["dd", "j"]

    logic.kosten_opslaan(conn, J, hotel, "Hotel", "30", "dd", verdeling="reiskosten", deelnemers=["j"])
    assert logic.kosten(conn, J, hotel)[0]["deelnemers"] == []


@pytest.mark.parametrize("deelnemers", [[], ["zz"]])
def test_eigen_kosten_vragen_geldige_deelnemers(conn, deelnemers):
    with pytest.raises(ValidatieFout):
        logic.kosten_opslaan(conn, J, None, "Hotel", "1", "dd", verdeling="eigen", deelnemers=deelnemers)


def test_bestaande_eigen_kosten_krijgen_iedereen_als_deelnemer(conn):
    conn.execute("INSERT INTO kosten (jaar, omschrijving, bedrag, betaald_door, verdeling) VALUES (?, 'Hotel', 8, 'dd', 'eigen')", (J,))
    db.init(conn)
    db.init(conn)
    assert logic.kosten(conn, J)[0]["deelnemers"] == migraties.PERSONEN


def test_namen_sorteren_hoofdletterongevoelig(conn):
    for naam in ("Sandra", "lotje", "Ravi"):
        logic.klant_opslaan(conn, J, None, "dd", naam, "", 0, 1, {})
    logic.aankoop_opslaan(conn, J, None, "A", [regel("Zoemer", {"dd": 1}), regel("appel", {"dd": 1})])
    assert [k["k"]["naam"] for k in logic.klanten(conn, J, "dd")] == ["lotje", "Ravi", "Sandra"]
    assert [w["naam"] for w in logic.wijnen(conn, J, vast=False)] == ["appel", "Zoemer"]


# --- jaren -------------------------------------------------------------------

def test_migratie_naar_jaren(tmp_path):
    """Een database van vlak voor de jaren: alles komt in MIGRATIE_JAAR, met een kopie vooraf."""
    pad = str(tmp_path / "oud.db")
    c = db.connect(pad)
    c.executescript((Path(__file__).parent / "schema_voor_jaren.sql").read_text() + """
        INSERT INTO via (code, naam, volgorde, verrekend) VALUES ('dd','dd',0,1),('j','j',1,0),('h','h',2,0),('m','m',3,0),('hm','hm',4,0);
        INSERT INTO wine VALUES (1, 'Pertois tradition', 'basis', 19.3, 1), (2, 'Dagonet tradition', 'basis', 19.5, 1),
                                (3, 'Pertois ½', 'basis', 10.9, 1), (4, 'Dagonet ½', 'basis', 10.5, 1),
                                (9, 'Krug', 'luxe', NULL, 0);
        INSERT INTO klant (id, via, naam, luxe_n, betaald) VALUES (5, 'dd', 'Piet', 1, 1);
        INSERT INTO klant_vast VALUES (5, 1, 6);
        INSERT INTO aankoop (id, omschrijving, betaald_door) VALUES (7, 'Bon', 'j');
        INSERT INTO aankoop_regel VALUES (8, 7, 9, 3, 30), (10, 7, 1, 6, NULL);
        INSERT INTO regel_verdeling VALUES (8, 'dd', 2), (8, 'j', 1), (10, 'dd', 6);
        INSERT INTO toekenning VALUES (5, 9, 1);
        INSERT INTO kosten (id, omschrijving, bedrag, betaald_door, verdeling) VALUES (11, 'Hotel', 40, 'h', 'personen');
        INSERT INTO kosten_deelnemer VALUES (11, 'dd'), (11, 'j');
    """)
    db.init(c)
    m = migraties.MIGRATIE_JAAR
    assert logic.jaren(c) == [m]
    assert (tmp_path / "backups" / "oud.db.voor-jaren").exists()
    assert c.execute("PRAGMA foreign_key_check").fetchall() == []
    assert [v["code"] for v in logic.vias(c, m)] == ["dd", "j", "h", "m", "hm"] and logic.vias(c, m)[0]["verrekend"]
    piet = logic.klant(c, m, 5)
    assert piet["k"]["betaald"] and piet["vast"] == {1: 6} and piet["toekenningen"][0]["naam"] == "Krug"
    assert [(x["r"]["naam"], x["verdeling"]) for x in logic.aankopen(c, m)[0]["regels"]] == [
        ("Krug", {"dd": 2, "j": 1}), ("Pertois tradition", {"dd": 6})]
    assert logic.kosten(c, m)[0]["deelnemers"] == ["dd", "j"] and logic.kosten(c, m)[0]["k"]["verdeling"] == "eigen"
    assert {w["id"] for w in logic.wijnen(c, m)} == {1, 2, 3, 4, 9}
    assert "prijs" not in migraties._kolommen(c, "wine")
    db.init(c)  # nog een keer: verandert niets
    assert logic.jaren(c) == [m] and len(logic.wijnen(c, m)) == 5


def test_migratie_hernoemt_kostenverdeling(conn):
    """Een database die al jaren heeft, maar nog de verdeling 'bestellers'/'personen'."""
    conn.executescript(f"""
        PRAGMA foreign_keys = OFF;
        DROP TABLE kosten;
        {migraties._tabel_sql("kosten", "kosten").replace("'reiskosten'", "'bestellers'").replace("'eigen'", "'personen'")};
        INSERT INTO kosten (id, jaar, omschrijving, bedrag, betaald_door, verdeling)
            VALUES (1, {J}, 'Tank', 50, 'j', 'bestellers'), (2, {J}, 'Hotel', 30, 'dd', 'personen');
        INSERT INTO eigen_kosten_deelnemer VALUES (2, {J}, 'dd'), (2, {J}, 'j');
        PRAGMA foreign_keys = ON;
    """)
    db.init(conn)
    assert [(k["k"]["omschrijving"], k["k"]["verdeling"], k["deelnemers"]) for k in logic.kosten(conn, J)] == [
        ("Tank", "reiskosten", []), ("Hotel", "eigen", ["dd", "j"])]
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE kosten SET verdeling = 'personen' WHERE id = 1")


def test_migratie_hernoemt_deelnemertabel(conn):
    """Een database met jaren, maar nog met de tabel kosten_deelnemer."""
    hotel = logic.kosten_opslaan(conn, J, None, "Hotel", "30", "dd", verdeling="eigen", deelnemers=["dd", "j"])
    conn.execute("ALTER TABLE eigen_kosten_deelnemer RENAME TO kosten_deelnemer")
    db.init(conn)  # maakt eerst een lege eigen_kosten_deelnemer, de migratie zet de oude ervoor in de plaats
    assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'kosten_deelnemer'").fetchone()
    assert logic.kosten(conn, J, hotel)[0]["deelnemers"] == ["dd", "j"]


def test_migratie_verwijdert_wijnprijs(conn):
    """Een database die al jaren heeft, maar nog de ongebruikte standaardprijs per wijn."""
    conn.execute("ALTER TABLE wine ADD COLUMN prijs REAL")
    conn.execute("UPDATE wine SET prijs = 19.3 WHERE is_vast = 1")
    aantal = len(logic.wijnen(conn, J))
    db.init(conn)
    assert "prijs" not in migraties._kolommen(conn, "wine")
    assert len(logic.wijnen(conn, J)) == aantal


def test_nieuw_jaar_neemt_vias_en_basisflessen_over(conn):
    assert logic.jaar_aanmaken(conn, "2025") == 2025
    assert logic.jaren(conn) == sorted({J, 2025}, reverse=True)
    assert [v["code"] for v in logic.vias(conn, 2025)] == [v["code"] for v in logic.vias(conn, J)]
    oud = [w["naam"] for w in logic.wijnen(conn, J, vast=True)]
    nieuw = logic.wijnen(conn, 2025, vast=True)
    assert [w["naam"] for w in nieuw] == oud
    assert not {w["id"] for w in nieuw} & {w["id"] for w in logic.wijnen(conn, J)}


@pytest.mark.parametrize("jaar", ["", "abc", "1999", "2101", str(J)])
def test_ongeldig_of_bestaand_jaar(conn, jaar):
    with pytest.raises(ValidatieFout):
        logic.jaar_aanmaken(conn, jaar)


def test_jaren_staan_los(conn, scenario):
    logic.jaar_aanmaken(conn, 2025)
    piet = logic.klant_opslaan(conn, 2025, None, "dd", "Piet", "", 2, 0, {})
    logic.aankoop_opslaan(conn, 2025, None, "Oud", [regel("Tissier Juliette", {"dd": 1}, prijs="10")])
    assert [k["k"]["naam"] for k in logic.klanten(conn, 2025)] == ["Piet"]
    assert "Piet" not in [k["k"]["naam"] for k in logic.klanten(conn, J)]
    assert nog(conn, "dd", "iets_luxer") == 20  # 2026 ongewijzigd
    assert logic.overzicht(conn, 2025)["totaal"]["iets_luxer"]["nog"] == 1
    assert len(logic.aankopen(conn, 2025)) == 1 and logic.verrekening(conn, 2025)["totaal"]["wijn"] == 1000
    juliette_2025 = conn.execute("SELECT id FROM wine WHERE jaar = 2025 AND naam = 'Tissier Juliette'").fetchone()["id"]
    assert juliette_2025 != scenario["juliette"]
    logic.toekenning_wijzig(conn, 2025, piet, juliette_2025, 1)
    assert vrij(conn, "dd", scenario["juliette"]) == 8  # pool van 2026 ongemoeid


def test_ids_van_een_ander_jaar_zijn_onbekend(conn, scenario):
    logic.jaar_aanmaken(conn, 2025)
    tank = logic.kosten_opslaan(conn, J, None, "Tank", "10", "dd")
    with pytest.raises(ValidatieFout):
        logic.klant_opslaan(conn, 2025, scenario["joris"], "dd", "Joris", "", 1, 0, {})
    with pytest.raises(ValidatieFout):
        logic.klant_betaald(conn, 2025, scenario["joris"], True)
    with pytest.raises(ValidatieFout):
        logic.toekenning_wijzig(conn, 2025, scenario["joris"], scenario["juliette"], 1)
    with pytest.raises(ValidatieFout):
        logic.aankoop_opslaan(conn, 2025, scenario["bon"], "X", [regel("Krug", {"dd": 1})])
    with pytest.raises(ValidatieFout):
        logic.kosten_opslaan(conn, 2025, tank, "Tank", "20", "dd")
    with pytest.raises(ValidatieFout):
        logic.overdragen(conn, 2025, scenario["juliette"], "dd", "j", 1)
    vast_2026 = logic.wijnen(conn, J, vast=True)[0]["id"]
    with pytest.raises(ValidatieFout):
        logic.klant_opslaan(conn, 2025, None, "dd", "Nieuw", "", 0, 0, {vast_2026: 1})
    logic.klant_verwijderen(conn, 2025, scenario["joris"])
    logic.aankoop_verwijderen(conn, 2025, scenario["bon"])
    logic.kosten_verwijderen(conn, 2025, tank)
    assert logic.klant(conn, J, scenario["joris"]) and logic.aankopen(conn, J, scenario["bon"])
    assert logic.kosten(conn, J, tank)


def test_jaar_verwijderen(conn, scenario):
    logic.jaar_aanmaken(conn, 2025)
    tank = logic.kosten_opslaan(conn, J, None, "Tank", "10", "dd")
    inhoud = {j["jaar"]: j for j in logic.jaren_inhoud(conn)}
    assert inhoud[2025] == {"jaar": 2025, "bestellingen": 0, "bonnen": 0, "kosten": 0}
    assert inhoud[J]["bestellingen"] == 4 and inhoud[J]["bonnen"] == 1 and inhoud[J]["kosten"] == 1
    logic.jaar_verwijderen(conn, J)  # niet leeg: alles gaat mee
    assert logic.jaren(conn) == [2025]
    for t in ("klant", "klant_vast", "toekenning", "aankoop", "aankoop_regel", "regel_verdeling",
              "kosten", "eigen_kosten_deelnemer"):
        assert conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] == 0, t
    assert not logic.kosten(conn, J, tank)
    assert [w["jaar"] for w in conn.execute("SELECT jaar FROM wine")] == [2025] * 4
    with pytest.raises(ValidatieFout, match="laatste jaar"):
        logic.jaar_verwijderen(conn, 2025)
    with pytest.raises(ValidatieFout):
        logic.jaar_verwijderen(conn, 1999)


def test_jaren_statistiek(conn, scenario):
    logic.jaar_aanmaken(conn, 2025)
    logic.klant_opslaan(conn, 2025, None, "dd", "Piet", "", 0, 0, {})  # bestelt niets: telt niet
    logic.aankoop_opslaan(conn, 2025, None, "B", [regel("Pertois ½", {"dd": 6}, None, "10"),
                                                  regel("Krug", {"j": 2}, "luxe", "50")])
    oud, nieuw = logic.jaren_statistiek(conn)
    assert oud["jaar"] == 2025 and nieuw["jaar"] == J
    assert oud["flessen"] == {"basis": 0, "klein": 6, "iets_luxer": 0, "luxe": 2}
    assert (oud["totaal"], oud["euro"], oud["bestellingen"], oud["per_bestelling"]) == (8, 160, 0, 0)
    assert nieuw["bestellingen"] == 4 and nieuw["totaal"] == sum(nieuw["flessen"].values())


def test_bestaand_jaar(conn):
    assert logic.bestaand_jaar(conn, str(J)) == J
    for fout in (str(J + 1), "abc", None):
        with pytest.raises(ValidatieFout, match="Onbekend jaar"):
            logic.bestaand_jaar(conn, fout)


def test_via(conn):
    assert logic.via(conn, J, "dd")["code"] == "dd"
    assert logic.via(conn, J, "xx") is None
    assert logic.via(conn, J + 1, "dd") is None
