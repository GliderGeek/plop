import os
import tempfile

# vóór het importeren van app: die opent (en migreert) de database bij het laden
os.environ["CHAMPAGNE_DB"] = os.path.join(tempfile.mkdtemp(), "test.db")

import pytest
from werkzeug.datastructures import MultiDict

import app as app_module
import db
import logic

HTMX = {"HX-Request": "true"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    pad = str(tmp_path / "app.db")
    monkeypatch.setattr(app_module, "DB_PATH", pad)
    c = db.connect(pad)
    db.init(c)
    c.close()
    app_module.app.config["TESTING"] = True
    return app_module.app.test_client()


@pytest.fixture
def conn():
    c = db.connect(app_module.DB_PATH)
    yield c
    c.close()


def vias():
    return [{"code": c} for c in ("dd", "j", "h", "m", "hm")]


def test_regels_uit_form():
    f = MultiDict({
        "regel-0-naam": "Tissier Juliette", "regel-0-categorie": "iets_luxer", "regel-0-via-dd": "2",
        "regel-1-naam": "", "regel-1-categorie": "basis", "regel-1-vast": "Pertois ½", "regel-1-via-j": "3",
        "regel-2-naam": " ", "regel-2-via-dd": "0", "regel-2-via-j": "",  # leeg, ook met getypte 0
    })
    regels = app_module.regels_uit_form(f, vias())
    assert [(r["naam"], r["categorie"]) for r in regels] == [("Tissier Juliette", "iets_luxer"), ("Pertois ½", None)]
    assert regels[1]["verdeling"]["j"] == "3"


def test_aankoop_opslaan_en_fout_via_htmx(client, conn):
    r = client.post("/aankoop", data={"omschrijving": "Tissier", "regel-0-naam": "Juliette",
                                      "regel-0-categorie": "luxe", "regel-0-via-dd": "2"})
    assert r.status_code == 303
    bon = logic.aankopen(conn)[0]
    r = client.post(f"/aankoop/{bon['a']['id']}", headers=HTMX,
                    data={"versie": bon["a"]["versie"] + 1, "regel-0-naam": "Juliette",
                          "regel-0-categorie": "luxe", "regel-0-via-dd": "3"})
    assert r.headers["HX-Retarget"] == "#melding" and "intussen gewijzigd" in r.text
    assert logic.aankopen(conn)[0]["totaal"] == 2


def test_ongeldige_post_geeft_melding_geen_500(client):
    r = client.post("/toekenning", headers=HTMX, data={"klant_id": "x", "wine_id": "1", "delta": "1"})
    assert r.status_code == 200 and r.headers["HX-Retarget"] == "#melding"
    r = client.post("/overdracht", headers=HTMX, data={"wine_id": "1", "van_via": "dd", "naar_via": "zz"})
    assert r.headers["HX-Retarget"] == "#melding"


def test_onbekende_klant_geeft_melding(client):
    r = client.post("/klant/999", headers=HTMX, data={"via": "dd", "naam": "Joris"})
    assert r.headers["HX-Retarget"] == "#melding"


def test_klant_toevoegen_op_via_pagina(client, conn):
    assert 'type="hidden" name="via" value="m"' in client.get("/via/m").text
    r = client.post("/klant", headers=HTMX, follow_redirects=True,
                    data={"via": "m", "naam": "Joris", "iets_luxer_n": "2", "terug": "/via/m"})
    assert r.request.path == "/via/m" and "Joris" in r.text
    assert logic.klanten(conn, "m")[0]["k"]["naam"] == "Joris"


def test_klant_bewerken_stuurt_versie_mee(client, conn):
    client.post("/klant", data={"via": "dd", "naam": "Joris", "iets_luxer_n": "2"})
    k = logic.klanten(conn)[0]
    assert f'name="versie" value="{k["k"]["versie"]}"' in client.get("/via/dd").text
    r = client.post(f"/klant/{k['k']['id']}", headers=HTMX, follow_redirects=True,
                    data={"via": "dd", "naam": "Joris", "iets_luxer_n": "3", "versie": k["k"]["versie"],
                          "terug": "/via/dd"})
    assert r.status_code == 200 and "0/3" in r.text


def test_klant_verwijderen_op_via_pagina(client, conn):
    klant_id = logic.klant_opslaan(conn, None, "dd", "Piet", "", 1, 0, {})
    r = client.post(f"/klant/{klant_id}/verwijder", headers=HTMX, follow_redirects=True,
                    data={"terug": "/via/dd"})
    assert r.request.path == "/via/dd" and "Nog geen bestellingen" in r.text


def test_overzicht_toont_bestelde_totalen(client, conn):
    logic.klant_opslaan(conn, None, "dd", "Piet", "", 7, 0, {})
    assert '<td class="nog">7</td>' in client.get("/").text


def test_paginas_renderen(client):
    for url in ("/", "/aankopen", "/aankoop/nieuw", "/via/dd"):
        assert client.get(url).status_code == 200, url


def test_klant_compleet_na_laatste_fles(client, conn):
    klant_id = logic.klant_opslaan(conn, None, "dd", "Piet", "", 0, 1, {})
    logic.aankoop_opslaan(conn, None, "", [{"naam": "Krug", "categorie": "luxe", "verdeling": {"dd": 1}}])
    wine_id = conn.execute("SELECT id FROM wine WHERE naam = 'Krug'").fetchone()["id"]
    kaart = f'id="klant-{klant_id}" data-compleet='

    assert kaart + '"0"' in client.get("/via/dd").text
    r = client.post("/toekenning", headers=HTMX, follow_redirects=True,
                    data={"klant_id": klant_id, "wine_id": wine_id, "delta": "1", "terug": "/via/dd"})
    assert kaart + '"1"' in r.text


def test_klant_zonder_bestelling_is_niet_compleet(client, conn):
    klant_id = logic.klant_opslaan(conn, None, "dd", "Piet", "", 0, 0, {})
    assert not logic.klant(conn, klant_id)["compleet"]
