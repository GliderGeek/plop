import os
import re

from flask import (Flask, abort, flash, g, make_response, redirect, render_template,
                   request, url_for)

import db
import logic
from logic import CATEGORIEEN, ZOEK_CATEGORIEEN, ValidatieFout

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "champagne-dev")
DB_PATH = os.environ.get("CHAMPAGNE_DB", os.path.join(app.root_path, "champagne.db"))


def get_conn():
    if "conn" not in g:
        g.conn = db.connect(DB_PATH)
    return g.conn


@app.teardown_appcontext
def sluit(_exc):
    conn = g.pop("conn", None)
    if conn is not None:
        conn.close()


with app.app_context():
    db.init(get_conn())


def is_htmx():
    return request.headers.get("HX-Request") == "true"


@app.context_processor
def globals_():
    return {
        "CATEGORIEEN": CATEGORIEEN,
        "ZOEK_CATEGORIEEN": ZOEK_CATEGORIEEN,
        "alle_vias": logic.vias(get_conn()),
    }


@app.template_filter("euro")
def euro(v):
    return "" if v is None else ("€ %.2f" % v).replace(".", ",")


@app.template_filter("getal")
def getal(v):
    """26.0 → '26', 23.5 → '23,5' (voor invoervelden)."""
    return "" if v is None else ("%g" % v).replace(".", ",")


@app.template_filter("kortnaam")
def kortnaam(naam):
    """'Pertois tradition' → 'Pertois'; 'Dagonet ½' blijft."""
    return naam.replace(" tradition", "")


@app.template_filter("kort")
def kort(naam):
    """'Pertois tradition' → 'P', 'Dagonet ½' → 'D½'."""
    return naam[:1] + ("½" if "½" in naam else "")


@app.errorhandler(ValidatieFout)
def validatiefout(e):
    if is_htmx():
        resp = make_response(render_template("partials/melding.html", fout=str(e)))
        resp.headers["HX-Retarget"] = "#melding"
        resp.headers["HX-Reswap"] = "innerHTML"
        return resp
    flash(str(e))
    return redirect(request.referrer or url_for("overzicht"))


def terug(default):
    return redirect(request.form.get("terug") or default, code=303)


# --- overzicht ---------------------------------------------------------------

@app.get("/")
def overzicht():
    return render_template("overzicht.html", ov=logic.overzicht(get_conn()))


# --- bestellingen ------------------------------------------------------------

def bestellingen_context(conn):
    ks = logic.klanten(conn)
    return {"vaste": logic.wijnen(conn, vast=True), "klanten": ks,
            "totalen": logic.bestel_totalen(conn, ks)}


def klant_of_404(conn, klant_id):
    k = logic.klant(conn, klant_id)
    if k is None:
        abort(404)
    return k


@app.get("/bestellingen")
def bestellingen():
    return render_template("bestellingen.html", **bestellingen_context(get_conn()))


def klant_form_opslaan(conn, klant_id):
    f = request.form
    vast = {w["id"]: f.get(f"vast_{w['id']}") for w in logic.wijnen(conn, vast=True)}
    return logic.klant_opslaan(conn, klant_id, f.get("via"), f.get("naam"), f.get("aantekening"),
                               f.get("iets_luxer_n"), f.get("luxe_n"), vast, f.get("versie"))


def klant_kaart(conn, klant_id, nieuw=False):
    k = klant_of_404(conn, klant_id)
    return render_template("partials/klant_kaart_oob.html", k=k, nieuw=nieuw,
                           **bestellingen_context(conn))


@app.post("/klant")
def klant_nieuw():
    conn = get_conn()
    klant_id = klant_form_opslaan(conn, None)
    if is_htmx():
        resp = make_response(klant_kaart(conn, klant_id, nieuw=True))
        resp.headers["HX-Retarget"] = "#lijst-" + request.form["via"]  # via kan zijn gewijzigd
        return resp
    return redirect(url_for("bestellingen"), code=303)


@app.get("/klant/<int:klant_id>")
def klant_toon(klant_id):
    return klant_kaart(get_conn(), klant_id)


@app.get("/klant/<int:klant_id>/bewerk")
def klant_bewerk(klant_id):
    conn = get_conn()
    return render_template("partials/klant_form.html", k=klant_of_404(conn, klant_id),
                           vaste=logic.wijnen(conn, vast=True))


@app.post("/klant/<int:klant_id>")
def klant_opslaan(klant_id):
    conn = get_conn()
    klant_form_opslaan(conn, klant_id)
    if is_htmx():
        return klant_kaart(conn, klant_id)
    return redirect(url_for("bestellingen"), code=303)


@app.post("/klant/<int:klant_id>/verwijder")
def klant_verwijder(klant_id):
    conn = get_conn()
    logic.klant_verwijderen(conn, klant_id)
    if is_htmx():
        return render_template("partials/totalen.html", oob=True, **bestellingen_context(conn))
    return redirect(url_for("bestellingen"), code=303)


# --- aankopen ----------------------------------------------------------------

def aankoop_form(bon=None):
    conn = get_conn()
    return render_template(
        "aankoop.html",
        bon=bon,
        wijnnamen=[w["naam"] for w in logic.wijnen(conn)],
        vaste_namen=[w["naam"] for w in logic.wijnen(conn, vast=True)],
        nog=logic.nog_per_via(conn),
    )


@app.get("/aankoop/nieuw")
def aankoop_nieuw():
    return aankoop_form()


@app.get("/aankoop/<int:aankoop_id>/bewerk")
def aankoop_bewerk(aankoop_id):
    bonnen = logic.aankopen(get_conn(), aankoop_id)
    if not bonnen:
        return redirect(url_for("aankopen"))
    return aankoop_form(bonnen[0])


def regels_uit_form(f, vias):
    """Velden regel-<i>-naam/vast/categorie/prijs/via-<code> → lijst regels.
    Bij categorie 'basis' komt de naam uit de dropdown met basisflessen.
    Helemaal lege regels (geen naam of verdeling) worden overgeslagen."""
    indices = sorted({int(m.group(1)) for k in f if (m := re.fullmatch(r"regel-(\d+)-naam", k))})
    regels = []
    for i in indices:
        r = {veld: f.get(f"regel-{i}-{veld}") for veld in ("naam", "categorie", "prijs")}
        r["verdeling"] = {v["code"]: f.get(f"regel-{i}-via-{v['code']}") for v in vias}
        leeg = all((n or "").strip() in ("", "0") for n in r["verdeling"].values())
        if not (r["naam"] or "").strip() and leeg:
            continue
        if r["categorie"] == "basis":
            r["naam"], r["categorie"] = f.get(f"regel-{i}-vast"), None
        regels.append(r)
    return regels


@app.post("/aankoop")
@app.post("/aankoop/<int:aankoop_id>")
def aankoop_opslaan(aankoop_id=None):
    conn = get_conn()
    regels = regels_uit_form(request.form, logic.vias(conn))
    logic.aankoop_opslaan(conn, aankoop_id, request.form.get("omschrijving"), regels,
                          request.form.get("versie"))
    flash("Aankoop opgeslagen.", "ok")
    return redirect(url_for("aankopen"), code=303)


@app.post("/aankoop/<int:aankoop_id>/verwijder")
def aankoop_verwijder(aankoop_id):
    logic.aankoop_verwijderen(get_conn(), aankoop_id)
    return redirect(url_for("aankopen"), code=303)


@app.get("/aankopen")
def aankopen():
    conn = get_conn()
    return render_template("aankopen.html", aankopen=logic.aankopen(conn))


# --- via ---------------------------------------------------------------------

@app.get("/via/<code>")
def via(code):
    conn = get_conn()
    v = next((v for v in logic.vias(conn) if v["code"] == code), None)
    if v is None:
        return redirect(url_for("overzicht"))
    ov = next(r for r in logic.overzicht(conn)["per_via"] if r["via"]["code"] == code)
    return render_template("via.html", v=v, ov=ov, pool=logic.pool(conn, code),
                           klanten=logic.klanten(conn, code))


@app.post("/toekenning")
def toekenning():
    f = request.form
    if f.get("aanvullen"):
        logic.toekenning_aanvullen(get_conn(), f.get("klant_id"), f.get("wine_id"))
    else:
        logic.toekenning_wijzig(get_conn(), f.get("klant_id"), f.get("wine_id"), f.get("delta"))
    return terug(url_for("overzicht"))


@app.post("/overdracht")
def overdracht():
    f = request.form
    logic.overdragen(get_conn(), f.get("wine_id"), f.get("van_via"), f.get("naar_via"),
                     f.get("aantal"))
    return terug(url_for("overzicht"))
