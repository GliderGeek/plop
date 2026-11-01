# Champagnebestellingen

Een kleine webapp om de champagne te beheren die we tijdens onze trip kopen voor onszelf, vrienden en familie. De app vervangt de Excelsheet (`Champagne 2026.xlsx`).

## Doel

We bestellen met z'n vieren via vijf via-codes: **dd, j, h, m** en **hm** (de gezamenlijke bestelling van h en m). De app ondersteunt drie fases:

1. **Voorbereiding**: op de pagina van de via per klant ("voor wie") vastleggen wat die wil; het overzicht toont de totalen:
   - Exacte aantallen van de vier basisflessen: Pertois/Dagonet tradition, heel en half.
   - Een aantal flessen *Iets luxer*.
   - Een aantal flessen *Luxe*.
   - Wensen als vrije tekst.
2. **Tijdens de trip**: aankopen invoeren. Een aankoop is één bon met één of meer wijnen, elk met een categorie (*Iets luxer* of *Luxe*), optionele prijs en een verdeling over de via's (het aantal is de som daarvan). Elke via ziet hoeveel flessen hij nog nodig heeft. Flessen kun je toekennen aan een specifieke klant (voorlopig optioneel) of overdragen aan een andere via.
3. **Achteraf**: de kosten verrekenen. Dat valt voorlopig buiten de app.

Basisflessen lopen via hetzelfde patroon als de rest: ze worden gekocht op een bon, komen in de pool van een via en worden toegekend aan klanten. Het verschil is dat de vraag per specifieke fles is (bijv. 6 Pertois tradition) in plaats van per categorie; één tik vult aan wat een klant nog nodig heeft.

Er zijn geen rollen of logins: iedereen kan alles.

## TODO

- site op domeinnaam + https
- kosten verrekenen erin zetten

## Techniek

- Python + Flask + Jinja2-templates
- htmx voor gedeeltelijke updates, meegeleverd in `static/`; geen JS-framework
- SQLite via de standaard `sqlite3`-module, uitsluitend met geparametriseerde queries

| Bestand | Doel |
|---|---|
| `app.py` | Routes en htmx-partials |
| `logic.py` | Alle berekeningen (nog te kopen, pool, toekenningen), aankopen en validatie |
| `backup.py` | Snapshot van alle data als Excel-bestand |
| `db.py` | Verbinding, schema aanmaken, migraties, startdata (via's en basisflessen) |
| `schema.sql` | Databaseschema |
| `templates/`, `static/` | Pagina's, partials, CSS en een beetje JS voor het aankoopformulier en het feestje als een bestelling compleet is |
| `tests/` | Unit-tests voor `logic.py` en route-tests voor `app.py` |

Een aankoop (`aankoop`) heeft regels (`aankoop_regel`) met elk een verdeling over de via's (`regel_verdeling`). Wijnen worden niet apart beheerd: een regel verwijst naar een wijn op naam (hoofdletterongevoelig), die automatisch wordt aangemaakt en weer verdwijnt als hij op geen enkele bon meer staat. Overdragen tussen via's past de verdeling van de regels aan (nieuwste eerst) en wordt niet apart opgeslagen. Elke wijziging draait in een transactie (`BEGIN IMMEDIATE`, zodat gelijktijdige acties op elkaar wachten) met een consistentiecheck, die alles terugdraait als een via meer flessen toegekend zou hebben dan hij bezit. Bonnen en klanten hebben een `versie`: wie een bewerkformulier opslaat terwijl iemand anders die bon of klant intussen heeft gewijzigd (ook door een overdracht), krijgt een melding in plaats van andermans wijziging te overschrijven.

De knop *Backup* onderaan het overzicht downloadt een Excel-bestand met tijdstip in de naam, met tabbladen voor bestellingen, toekenningen en aankopen. Dat is een leesbare momentopname; de app kan het bestand niet terugzetten.

Schemawijzigingen voor bestaande databases staan als kleine migraties in `db.py` en draaien automatisch bij het starten.

## Ontwikkelen

Het project gebruikt [uv](https://docs.astral.sh/uv/). De Python-versie staat in `.python-version` (3.13) en `pyproject.toml` (`requires-python`). Dependencies staan in `pyproject.toml` en zijn vastgelegd in `uv.lock`. uv installeert zo nodig zelf de juiste Python-versie.

Installeren (maakt `.venv` aan en installeert alles, inclusief de dev-dependencies zoals pytest):

```sh
uv sync
```

De app starten:

```sh
uv run flask --app app run --debug
```

Open http://localhost:5000. Om de app op je telefoon te gebruiken via hetzelfde wifi-netwerk: voeg `--host 0.0.0.0` toe en open `http://<ip-van-je-computer>:5000`. Laat dan op een netwerk dat je niet vertrouwt `--debug` weg: die zet de Werkzeug-debugger aan.

De database `champagne.db` wordt bij de eerste start aangemaakt naast `app.py`. Met `CHAMPAGNE_DB=/pad/naar/bestand.db` gebruik je een ander bestand. Opnieuw beginnen: verwijder het bestand.

Tests draaien:

```sh
uv run pytest
```

Een dependency toevoegen: `uv add <pakket>`, of `uv add --dev <pakket>` voor alleen ontwikkeling.
