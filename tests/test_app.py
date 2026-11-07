import io
from datetime import date
import os
import re
import tempfile

# vóór het importeren van app: die opent (en migreert) de database bij het laden
os.environ["CHAMPAGNE_DB"] = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ["CHAMPAGNE_ENV_FILE"] = os.devnull  # een lokale .env mag de tests niet beïnvloeden
os.environ["SECRET_KEY"] = "test"
os.environ["CHAMPAGNE_WACHTWOORD"] = "geheim"

import openpyxl
import pytest
from werkzeug.datastructures import MultiDict

import app as app_module
import db
import logic

HTMX = {"HX-Request": "true"}

J = date.today().year  # het eerste jaar van een nieuwe database


@pytest.fixture
def anoniem(tmp_path, monkeypatch):
    """Client zonder inlog."""
    pad = str(tmp_path / "app.db")
    monkeypatch.setattr(app_module, "DB_PATH", pad)
    c = db.connect(pad)
    db.init(c)
    c.close()
    app_module.app.config["TESTING"] = True
    return app_module.app.test_client()


@pytest.fixture
def client(anoniem):
    """Ingelogde client."""
    with anoniem.session_transaction() as s:
        s["auth"] = app_module.vingerafdruk()
    return anoniem


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
    bon = logic.aankopen(conn, J)[0]
    r = client.post(f"/aankoop/{bon['a']['id']}", headers=HTMX,
                    data={"versie": bon["a"]["versie"] + 1, "regel-0-naam": "Juliette",
                          "regel-0-categorie": "luxe", "regel-0-via-dd": "3"})
    assert r.headers["HX-Retarget"] == "#melding" and "intussen gewijzigd" in r.text
    assert logic.aankopen(conn, J)[0]["totaal"] == 2


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
    assert logic.klanten(conn, J, "m")[0]["k"]["naam"] == "Joris"


def test_klant_bewerken_stuurt_versie_mee(client, conn):
    client.post("/klant", data={"via": "dd", "naam": "Joris", "iets_luxer_n": "2"})
    k = logic.klanten(conn, J)[0]
    assert f'name="versie" value="{k["k"]["versie"]}"' in client.get("/via/dd").text
    r = client.post(f"/klant/{k['k']['id']}", headers=HTMX, follow_redirects=True,
                    data={"via": "dd", "naam": "Joris", "iets_luxer_n": "3", "versie": k["k"]["versie"],
                          "terug": "/via/dd"})
    assert r.status_code == 200 and "0/3" in r.text


def test_klant_verwijderen_op_via_pagina(client, conn):
    klant_id = logic.klant_opslaan(conn, J, None, "dd", "Piet", "", 1, 0, {})
    r = client.post(f"/klant/{klant_id}/verwijder", headers=HTMX, follow_redirects=True,
                    data={"terug": "/via/dd"})
    assert r.request.path == "/via/dd" and "Nog geen bestellingen" in r.text


def test_overzicht_heeft_secties_in_volgorde_van_de_fases(client):
    assert re.findall(r'data-sectie="([^"]+)"', client.get("/").text) == ["besteld", "nog-te-kopen", "verrekening"]


def test_overzicht_telt_nog_te_kopen_en_te_veel(client, conn):
    assert "nog te kopen</span>" not in client.get("/").text
    logic.klant_opslaan(conn, J, None, "dd", "Piet", "", 3, 0, {})
    logic.aankoop_opslaan(conn, J, None, "", [{"naam": "Krug", "categorie": "luxe", "verdeling": {"dd": 2}}])
    tekst = client.get("/").text
    assert "3 nog te kopen</span>" in tekst and "2 teveel gekocht</span>" in tekst


def test_overzicht_toont_bestelde_totalen(client, conn):
    logic.klant_opslaan(conn, J, None, "dd", "Piet", "", 7, 0, {})
    assert '<td class="nog">7</td>' in client.get("/").text


def test_backup_bevat_alle_data(client, conn):
    klant_id = logic.klant_opslaan(conn, J, None, "dd", "=1+1", "wens\x0b", 0, 1, {})
    logic.aankoop_opslaan(conn, J, None, "Tissier", [{"naam": "Krug", "categorie": "luxe", "prijs": "30",
                                                   "verdeling": {"dd": 2}}])
    wine_id = conn.execute("SELECT id FROM wine WHERE naam = 'Krug'").fetchone()["id"]
    logic.toekenning_wijzig(conn, J, klant_id, wine_id, 1)

    r = client.get("/backup")
    assert "attachment" in r.headers["Content-Disposition"]
    assert re.search(rf"champagne-{J}-\d{{4}}-\d{{2}}-\d{{2}}-\d{{6}}\.xlsx", r.headers["Content-Disposition"])
    wb = openpyxl.load_workbook(io.BytesIO(r.data))
    assert wb.sheetnames == ["Bestellingen", "Toekenningen", "Aankopen", "Kosten", "Verrekening", "Snapshot"]
    klant = wb["Bestellingen"]["B2"]
    assert klant.value == "=1+1" and klant.data_type == "s"  # geen formule
    assert [c.value for c in wb["Bestellingen"][2]][-2:] == ["wens", "nee"]
    assert [c.value for c in wb["Toekenningen"][2]] == ["dd", "=1+1", "Krug", "Luxe", 1]
    kop = [c.value for c in wb["Aankopen"][1]]
    rij = dict(zip(kop, [c.value for c in wb["Aankopen"][2]]))
    assert (rij["Omschrijving"], rij["Wijn"], rij["Aantal"], rij["Prijs"], rij["dd"], rij["j"]) == ("Tissier", "Krug", 2, 30, 2, 0)


def test_backup_van_lege_database(client):
    assert client.get("/backup").status_code == 200


def test_paginas_renderen(client):
    for url in ("/", "/aankopen", "/aankoop/nieuw", "/via/dd"):
        assert client.get(url).status_code == 200, url


def test_klant_compleet_na_laatste_fles(client, conn):
    klant_id = logic.klant_opslaan(conn, J, None, "dd", "Piet", "", 0, 1, {})
    logic.aankoop_opslaan(conn, J, None, "", [{"naam": "Krug", "categorie": "luxe", "verdeling": {"dd": 1}}])
    wine_id = conn.execute("SELECT id FROM wine WHERE naam = 'Krug'").fetchone()["id"]
    kaart = f'id="klant-{klant_id}" data-compleet='

    assert kaart + '"0"' in client.get("/via/dd").text
    r = client.post("/toekenning", headers=HTMX, follow_redirects=True,
                    data={"klant_id": klant_id, "wine_id": wine_id, "delta": "1", "terug": "/via/dd"})
    assert kaart + '"1"' in r.text


def test_klant_zonder_bestelling_is_niet_compleet(client, conn):
    klant_id = logic.klant_opslaan(conn, J, None, "dd", "Piet", "", 0, 0, {})
    assert not logic.klant(conn, J, klant_id)["compleet"]


def test_aankoop_met_betaler_en_waarschuwingen(client, conn):
    client.post("/aankoop", data={"omschrijving": "Tissier", "regel-0-naam": "Juliette", "regel-0-categorie": "luxe",
                                  "regel-0-via-dd": "2", "betaald_door": ""})
    bon = logic.aankopen(conn, J)[0]
    assert "betaler ontbreekt" in client.get("/aankopen").text
    tekst = client.get("/").text
    assert "1 open punt" in tekst and f"/aankoop/{bon['a']['id']}/bewerk" in tekst

    client.post(f"/aankoop/{bon['a']['id']}", data={
        "omschrijving": "Tissier", "versie": bon["a"]["versie"], "betaald_door": "j", "regel-0-naam": "Juliette",
        "regel-0-categorie": "luxe", "regel-0-prijs": "12,50", "regel-0-via-dd": "2"})
    assert logic.aankopen(conn, J)[0]["a"]["betaald_door"] == "j"
    assert 'value="j" selected' in client.get(f"/aankoop/{bon['a']['id']}/bewerk").text
    tekst = client.get("/").text
    assert "open punt" not in tekst and "€ -25,00" in tekst


def test_kosten_toevoegen_bewerken_verwijderen(client, conn):
    r = client.post("/kosten", data={"omschrijving": "Tank", "bedrag": "", "betaald_door": "j", "verdeling": "bestellers"})
    assert r.status_code == 303
    k = logic.kosten(conn, J)[0]["k"]
    assert "Tank: bedrag ontbreekt" in client.get("/").text
    assert f'action="/kosten/{k["id"]}"' in client.get("/aankopen").text

    r = client.post(f"/kosten/{k['id']}", headers=HTMX, data={"omschrijving": "Tank", "betaald_door": "j", "verdeling": "bestellers", "versie": k["versie"] + 1})
    assert r.headers["HX-Retarget"] == "#melding" and "intussen gewijzigd" in r.text

    client.post(f"/kosten/{k['id']}", data={"omschrijving": "Tank", "bedrag": "40,5", "betaald_door": "h",
                                            "verdeling": "personen", "deelnemers": ["dd", "m"], "versie": k["versie"]})
    k = logic.kosten(conn, J)[0]["k"]
    assert (k["bedrag"], k["betaald_door"], k["verdeling"]) == (40.5, "h", "personen")
    assert logic.kosten(conn, J)[0]["deelnemers"] == ["dd", "m"]
    tekst = client.get("/aankopen").text
    assert 'value="dd" checked' in tekst and 'value="j" >' in tekst and "alle selecteren" in tekst

    client.post(f"/kosten/{k['id']}/verwijder")
    assert not logic.kosten(conn, J)


def test_kosten_zonder_omschrijving_geeft_melding(client):
    r = client.post("/kosten", headers=HTMX, data={"omschrijving": " ", "bedrag": "1"})
    assert r.headers["HX-Retarget"] == "#melding"


def test_via_pagina_toont_bedrag_per_bestelling(client, conn):
    klant_id = logic.klant_opslaan(conn, J, None, "dd", "Piet", "", 0, 2, {})
    logic.aankoop_opslaan(conn, J, None, "", [{"naam": "Krug", "categorie": "luxe", "prijs": "30", "verdeling": {"dd": 2}}],
                          betaald_door="dd")
    wine_id = conn.execute("SELECT id FROM wine WHERE naam = 'Krug'").fetchone()["id"]
    logic.toekenning_wijzig(conn, J, klant_id, wine_id, 1)
    tekst = client.get("/via/dd").text
    assert "In rekening brengen" in tekst and "Nog niet toegekend" in tekst and tekst.count("€ 30,00") >= 2
    assert "nog 1 fles open" in tekst


def test_via_pagina_toont_geen_bedragen_bij_ontbrekende_prijs(client, conn):
    logic.klant_opslaan(conn, J, None, "dd", "Jan", "", 0, 2, {})
    aankoop = logic.aankoop_opslaan(conn, J, None, "Bon", [{"naam": "Krug", "categorie": "luxe", "verdeling": {"dd": 2}}])
    tekst = client.get("/via/dd").text
    assert "Bedragen volgen zodra alles is ingevuld" in tekst and f"/aankoop/{aankoop}/bewerk" in tekst
    assert "<th>Reiskosten</th>" not in tekst


def test_via_pagina_toont_nul_bij_klant_zonder_flessen(client, conn):
    logic.klant_opslaan(conn, J, None, "dd", "Jan", "", 0, 2, {})
    logic.aankoop_opslaan(conn, J, None, "", [{"naam": "Krug", "categorie": "luxe", "prijs": "30", "verdeling": {"dd": 2}}])
    tekst = client.get("/via/dd").text
    assert "<td>€ 0,00</td>" in tekst and "Bedragen volgen" not in tekst


def test_verrekening_is_voorlopig_bij_ontbrekende_prijs(client, conn):
    logic.aankoop_opslaan(conn, J, None, "", [{"naam": "Krug", "categorie": "luxe", "prijs": "20", "verdeling": {"m": 1}},
                                           {"naam": "Ruinart", "categorie": "luxe", "verdeling": {"dd": 1, "m": 1}}],
                          betaald_door="h")
    tekst = client.get("/").text
    assert "de standen zijn voorlopig" in tekst and "1 zonder prijs" in tekst
    assert "<td>€ 20,00</td>" in tekst and " ?" not in tekst.split("Saldo positief")[0].split("</thead>")[-1]


def test_betaald_afvinken(client, conn):
    jan = logic.klant_opslaan(conn, J, None, "dd", "Jan", "", 0, 2, {})
    logic.aankoop_opslaan(conn, J, None, "", [{"naam": "Krug", "categorie": "luxe", "prijs": "30", "verdeling": {"dd": 2}}])
    assert "Jan heeft betaald" in client.get("/via/dd").text
    versie = logic.klant(conn, J, jan)["k"]["versie"]

    client.post(f"/klant/{jan}/betaald", data={"betaald": "1", "terug": "/via/dd"})
    k = logic.klant(conn, J, jan)["k"]
    assert k["betaald"] == 1 and k["versie"] == versie
    assert 'value="1" checked aria-label="Jan heeft betaald"' in client.get("/via/dd").text

    client.post(f"/klant/{jan}/betaald", data={"terug": "/via/dd"})
    assert logic.klant(conn, J, jan)["k"]["betaald"] == 0


def test_betaald_voor_verwijderde_klant_geeft_melding(client):
    r = client.post("/klant/999/betaald", headers=HTMX, data={"betaald": "1"})
    assert "bestaat niet" in r.text


def test_verrekend_afvinken(client, conn):
    assert 'value="1"  aria-label="dd is verrekend"' in client.get("/").text
    client.post("/via/dd/verrekend", data={"verrekend": "1"})
    assert 'value="1" checked aria-label="dd is verrekend"' in client.get("/").text
    assert 'value="1"  aria-label="j is verrekend"' in client.get("/").text
    client.post("/via/dd/verrekend", data={})
    assert 'value="1"  aria-label="dd is verrekend"' in client.get("/").text


def test_verrekend_voor_onbekende_via_geeft_melding(client):
    assert "Onbekende via" in client.post("/via/zz/verrekend", headers=HTMX, data={"verrekend": "1"}).text


# --- inloggen ----------------------------------------------------------------

def test_ingelogd_slaat_loginpagina_over(client):
    assert client.get("/").status_code == 200
    assert client.get("/login").headers["Location"] == "/"


def test_niet_ingelogd_naar_login(anoniem):
    r = anoniem.get("/via/m")
    assert r.status_code == 302 and r.headers["Location"] == "/login?next=/via/m"
    r = anoniem.post("/toekenning", headers=HTMX, data={})
    assert r.status_code == 204 and r.headers["HX-Redirect"] == "/login"
    assert anoniem.get("/static/style.css").status_code == 200
    assert 'type="password"' in anoniem.get("/login").text


def test_verkeerd_wachtwoord(anoniem, monkeypatch):
    monkeypatch.setattr(app_module.time, "sleep", lambda s: None)
    r = anoniem.post("/login", data={"wachtwoord": "fout"})
    assert "Verkeerd wachtwoord" in r.text
    assert anoniem.get("/").status_code == 302


def test_inloggen_en_next(anoniem):
    r = anoniem.post("/login", data={"wachtwoord": "geheim", "next": "/via/m"})
    assert r.status_code == 303 and r.headers["Location"] == "/via/m"
    assert anoniem.get("/").status_code == 200
    cookie = anoniem.get_cookie("session")
    assert cookie.expires is not None  # blijft bewaard na sluiten van de browser


def test_next_naar_andere_site_genegeerd(anoniem):
    for url in ("//evil.example", "https://evil.example", "/\\evil.example"):
        r = anoniem.post("/login", data={"wachtwoord": "geheim", "next": url})
        assert r.headers["Location"] == "/"
        anoniem.delete_cookie("session")


def test_ander_wachtwoord_logt_uit(anoniem, monkeypatch):
    anoniem.post("/login", data={"wachtwoord": "geheim"})
    monkeypatch.setattr(app_module, "WACHTWOORD", "nieuw")
    assert anoniem.get("/").status_code == 302


def test_loginpagina_heeft_oogje(anoniem):
    assert "data-toon-wachtwoord" in anoniem.get("/login").text


def test_inloggen_onder_prefix(anoniem):
    # achter nginx op /plop: ProxyFix leest X-Forwarded-Prefix
    prefix = {"X-Forwarded-Prefix": "/plop"}
    r = anoniem.get("/via/m", headers=prefix)
    assert r.headers["Location"] == "/plop/login?next=/via/m"
    r = anoniem.post("/login", headers=prefix, data={"wachtwoord": "geheim", "next": "/via/m"})
    assert r.headers["Location"] == "/plop/via/m"
    assert anoniem.get("/login", headers=prefix).headers["Location"] == "/plop/"


# --- jaren -------------------------------------------------------------------

def test_jaar_aanmaken_en_wisselen(client, conn):
    logic.klant_opslaan(conn, J, None, "dd", "Piet", "", 7, 0, {})
    tekst = client.get("/").text
    assert f'aanmaken">{J}</a>' in tekst and "Je bekijkt" not in tekst

    r = client.post("/jaren", data={"jaar": "2020"})
    assert r.status_code == 303
    tekst = client.get("/").text
    assert "Je bekijkt <strong>2020</strong>" in tekst and '<td class="nog">7</td>' not in tekst
    assert "2020 aangemaakt." in tekst
    assert "2020" in client.get("/jaren").text

    client.post("/jaar", data={"jaar": str(J)})
    tekst = client.get("/").text
    assert "Je bekijkt" not in tekst and '<td class="nog">7</td>' in tekst


def test_jaar_in_sessie_bepaalt_wat_je_bewerkt(client, conn):
    logic.jaar_aanmaken(conn, 2020)
    client.post("/jaar", data={"jaar": "2020"})
    client.post("/klant", data={"via": "dd", "naam": "Oud", "iets_luxer_n": "1"})
    assert [k["k"]["naam"] for k in logic.klanten(conn, 2020)] == ["Oud"]
    assert logic.klanten(conn, J) == []


def test_onbekend_jaar(client):
    with client.session_transaction() as s:
        s["jaar"] = 1999  # bijvoorbeeld verwijderd: dan het nieuwste
    assert "Je bekijkt" not in client.get("/").text
    r = client.post("/jaar", data={"jaar": "1999"}, headers=HTMX)
    assert "Onbekend jaar" in r.text
    r = client.post("/jaren", data={"jaar": str(J)}, headers=HTMX)
    assert "bestaat al" in r.text
