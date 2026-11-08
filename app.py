import hashlib
import hmac
import os
import re
import time
import pathlib
from datetime import datetime, timedelta
from io import BytesIO

from dotenv import load_dotenv
from flask import (Flask, flash, g, make_response, redirect, render_template,
                   request, send_file, session, url_for)
from werkzeug.middleware.proxy_fix import ProxyFix

import backup as backup_module
import db
import logic
import presentatie
from logic import CATEGORIEEN, KOSTEN_VERDELINGEN, ZOEK_CATEGORIEEN, ValidatieFout

load_dotenv(pathlib.Path(__file__).parent / '.env')

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

# Eén gedeeld wachtwoord, altijd verplicht (ook lokaal), net als de sleutel voor de cookie.
SECRET_KEY = os.environ.get("SECRET_KEY")
WACHTWOORD = os.environ.get("CHAMPAGNE_WACHTWOORD")
if not SECRET_KEY or not WACHTWOORD:
    raise RuntimeError("Zet SECRET_KEY en CHAMPAGNE_WACHTWOORD in .env, zie .env.example")
app.secret_key = SECRET_KEY
app.config.update(
    PERMANENT_SESSION_LIFETIME=timedelta(days=365),
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("CHAMPAGNE_HTTPS") == "1",
)
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


def alle_jaren():
    if "jaren" not in g:
        g.jaren = logic.jaren(get_conn())
    return g.jaren


def huidig_jaar():
    """Het jaar dat deze browser bekijkt (in de sessie); anders het nieuwste."""
    gekozen = session.get("jaar")
    return gekozen if gekozen in alle_jaren() else alle_jaren()[0]


@app.context_processor
def globals_():
    jaar = huidig_jaar()
    return {
        "CATEGORIEEN": CATEGORIEEN,
        "KOSTEN_VERDELINGEN": KOSTEN_VERDELINGEN,
        "ZOEK_CATEGORIEEN": ZOEK_CATEGORIEEN,
        "SOORTEN": logic.SOORTEN,
        "alle_vias": logic.vias(get_conn(), jaar),
        "jaar": jaar,
        "alle_jaren": alle_jaren(),
        "nieuwste_jaar": alle_jaren()[0],
    }


app.jinja_env.filters.update(presentatie.FILTERS)


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


# --- inloggen ----------------------------------------------------------------

def vingerafdruk():
    """Staat in de sessie; een ander wachtwoord maakt alle bestaande sessies ongeldig."""
    return hmac.new(app.secret_key.encode(), WACHTWOORD.encode(), hashlib.sha256).hexdigest()[:16]


def ingelogd():
    return hmac.compare_digest(session.get("auth", ""), vingerafdruk())


@app.before_request
def vereis_inlog():
    if request.endpoint in ("static", "login") or ingelogd():
        return None
    if is_htmx():
        resp = make_response("", 204)
        resp.headers["HX-Redirect"] = url_for("login")
        return resp
    # next is relatief aan de app (zonder prefix als /plop); bij het terugsturen komt die ervoor
    return redirect(url_for("login", next=request.full_path.rstrip("?")))


def veilige_next(url):
    """Alleen paden binnen de site, geen '//andere-site'."""
    return url if url and url.startswith("/") and not url.startswith(("//", "/\\")) else None


@app.route("/login", methods=["GET", "POST"])
def login():
    volgende = veilige_next(request.values.get("next")) or "/"
    if ingelogd():
        return redirect(request.script_root + volgende)
    fout = None
    if request.method == "POST":
        if hmac.compare_digest(request.form.get("wachtwoord", "").encode(), WACHTWOORD.encode()):
            session.permanent = True
            session["auth"] = vingerafdruk()
            return redirect(request.script_root + volgende, code=303)
        time.sleep(1)  # remt raden af
        fout = "Verkeerd wachtwoord"
    return render_template("login.html", fout=fout, volgende=volgende)


# --- overzicht ---------------------------------------------------------------

@app.get("/")
def overzicht():
    conn, jaar = get_conn(), huidig_jaar()
    return render_template("overzicht.html", ov=logic.overzicht(conn, jaar),
                           totalen=logic.bestel_totalen(conn, jaar),
                           vr=logic.verrekening(conn, jaar))


@app.get("/backup")
def backup():
    nu, jaar = datetime.now(), huidig_jaar()
    return send_file(BytesIO(backup_module.werkboek(get_conn(), jaar, nu)), as_attachment=True,
                     download_name=f"champagne-{jaar}-{nu:%Y-%m-%d-%H%M%S}.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# --- klanten -----------------------------------------------------------------

def klant_form_opslaan(conn, klant_id):
    f = request.form
    jaar = huidig_jaar()
    vast = {w["id"]: f.get(f"vast_{w['id']}") for w in logic.wijnen(conn, jaar, vast=True)}
    return logic.klant_opslaan(conn, jaar, klant_id, f.get("via"), f.get("naam"), f.get("aantekening"),
                               f.get("iets_luxer_n"), f.get("luxe_n"), vast, f.get("versie"))


@app.post("/klant")
def klant_nieuw():
    klant_form_opslaan(get_conn(), None)
    return terug(url_for("overzicht"))


@app.post("/klant/<int:klant_id>")
def klant_opslaan(klant_id):
    klant_form_opslaan(get_conn(), klant_id)
    return terug(url_for("overzicht"))


@app.post("/klant/<int:klant_id>/verwijder")
def klant_verwijder(klant_id):
    logic.klant_verwijderen(get_conn(), huidig_jaar(), klant_id)
    return terug(url_for("overzicht"))


# --- aankopen ----------------------------------------------------------------

def aankoop_form(bon=None):
    conn, jaar = get_conn(), huidig_jaar()
    return render_template(
        "aankoop.html",
        bon=bon,
        wijnnamen=[w["naam"] for w in logic.wijnen(conn, jaar)],
        vaste_namen=[w["naam"] for w in logic.wijnen(conn, jaar, vast=True)],
        nog=logic.nog_per_via(conn, jaar),
    )


@app.get("/aankoop/nieuw")
def aankoop_nieuw():
    return aankoop_form()


@app.get("/aankoop/<int:aankoop_id>/bewerk")
def aankoop_bewerk(aankoop_id):
    bonnen = logic.aankopen(get_conn(), huidig_jaar(), aankoop_id)
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
    conn, jaar = get_conn(), huidig_jaar()
    regels = regels_uit_form(request.form, logic.vias(conn, jaar))
    logic.aankoop_opslaan(conn, jaar, aankoop_id, request.form.get("omschrijving"), regels,
                          request.form.get("versie"), request.form.get("betaald_door"))
    flash("Aankoop opgeslagen.", "ok")
    return redirect(url_for("aankopen"), code=303)


@app.post("/aankoop/<int:aankoop_id>/verwijder")
def aankoop_verwijder(aankoop_id):
    logic.aankoop_verwijderen(get_conn(), huidig_jaar(), aankoop_id)
    return redirect(url_for("aankopen"), code=303)


@app.get("/aankopen")
def aankopen():
    conn, jaar = get_conn(), huidig_jaar()
    return render_template("aankopen.html", aankopen=logic.aankopen(conn, jaar), kosten=logic.kosten(conn, jaar))


# --- via ---------------------------------------------------------------------

@app.get("/via/<code>")
def via(code):
    conn, jaar = get_conn(), huidig_jaar()
    v = logic.via(conn, jaar, code)
    if v is None:
        return redirect(url_for("overzicht"))
    ov = next(r for r in logic.overzicht(conn, jaar)["per_via"] if r["via"]["code"] == code)
    return render_template("via.html", v=v, ov=ov, pool=logic.pool(conn, jaar, code),
                           klanten=logic.klanten(conn, jaar, code), vaste=logic.wijnen(conn, jaar, vast=True),
                           bedragen=logic.klant_bedragen(conn, jaar, code))


@app.post("/klant/<int:klant_id>/betaald")
def klant_betaald(klant_id):
    logic.klant_betaald(get_conn(), huidig_jaar(), klant_id, request.form.get("betaald"))
    return terug(url_for("overzicht"))


@app.post("/via/<code>/verrekend")
def via_verrekend(code):
    logic.via_verrekend(get_conn(), huidig_jaar(), code, request.form.get("verrekend"))
    return terug(url_for("overzicht"))


@app.post("/toekenning")
def toekenning():
    f = request.form
    if f.get("aanvullen"):
        logic.toekenning_aanvullen(get_conn(), huidig_jaar(), f.get("klant_id"), f.get("wine_id"))
    else:
        logic.toekenning_wijzig(get_conn(), huidig_jaar(), f.get("klant_id"), f.get("wine_id"), f.get("delta"))
    return terug(url_for("overzicht"))


@app.post("/overdracht")
def overdracht():
    f = request.form
    logic.overdragen(get_conn(), huidig_jaar(), f.get("wine_id"), f.get("van_via"), f.get("naar_via"),
                     f.get("aantal"))
    return terug(url_for("overzicht"))


# --- overige aankopen -------------------------------------------------------------

@app.post("/kosten")
@app.post("/kosten/<int:kosten_id>")
def kosten_opslaan(kosten_id=None):
    f = request.form
    logic.kosten_opslaan(get_conn(), huidig_jaar(), kosten_id, f.get("omschrijving"), f.get("bedrag"),
                         f.get("betaald_door"), f.get("versie"), f.get("verdeling"), f.getlist("deelnemers"))
    return redirect(url_for("aankopen", _anchor="vaste-kosten"), code=303)


@app.post("/kosten/<int:kosten_id>/verwijder")
def kosten_verwijder(kosten_id):
    logic.kosten_verwijderen(get_conn(), huidig_jaar(), kosten_id)
    return redirect(url_for("aankopen", _anchor="vaste-kosten"), code=303)


# --- jaren -------------------------------------------------------------------

@app.get("/instellingen")
def instellingen():
    return render_template("instellingen.html", jaren_inhoud=logic.jaren_inhoud(get_conn()))


@app.post("/jaren")
def jaar_nieuw():
    session["jaar"] = logic.jaar_aanmaken(get_conn(), request.form.get("jaar"))
    flash(f"{session['jaar']} aangemaakt.", "ok")
    return redirect(url_for("overzicht"), code=303)


@app.post("/jaar")
def jaar_kiezen():
    session["jaar"] = logic.bestaand_jaar(get_conn(), request.form.get("jaar"))
    return redirect(url_for("overzicht"), code=303)


@app.post("/jaar/<int:jaar>/verwijder")
def jaar_verwijder(jaar):
    logic.jaar_verwijderen(get_conn(), jaar)
    flash(f"{jaar} verwijderd.", "ok")
    return redirect(url_for("instellingen"), code=303)


# --- over de jaren -----------------------------------------------------------

@app.get("/over-de-jaren")
def over_de_jaren():
    stat = logic.jaren_statistiek(get_conn())
    return render_template("over_de_jaren.html", stat=stat, grafiek=presentatie.kolommen_grafiek(stat))
