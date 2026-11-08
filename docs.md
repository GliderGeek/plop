# Repo opzet

| Bestand | Doel |
|---|---|
| `app.py` | Routes en htmx-partials |
| `logic.py` | Alle berekeningen (nog te kopen, pool, toekenningen), aankopen en validatie |
| `presentatie.py` | Template-filters (bedragen, getallen, namen) en de geometrie van de grafiek *Over de jaren* |
| `backup.py` | Snapshot van alle data als Excel-bestand |
| `db.py` | Verbinding, transacties, schema aanmaken, startdata (via's en basisflessen) voor een lege database |
| `migraties.py` | Oude databases bijwerken naar het huidige schema; niet gebruikt door de live app |
| `schema.sql` | Databaseschema |
| `templates/`, `static/` | Pagina's, partials, CSS en een beetje JS voor het aankoopformulier en het feestje als een bestelling compleet is |
| `tests/` | Unit-tests voor `logic.py` en route-tests voor `app.py` |

Alles hoort bij een jaar (tabel `jaar`). `via`, `wine`, `klant`, `aankoop` en `kosten` hebben een kolom `jaar`; een via is uniek per jaar (`(jaar, code)`) en een wijnnaam ook. `regel_verdeling` en `eigen_kosten_deelnemer` hebben het jaar van hun bon of post, zodat de foreign key naar `via(jaar, code)` alleen via's van hetzelfde jaar toelaat. Elke functie in `logic.py` krijgt het jaar mee en filtert erop, ook bij opzoeken op id: een id uit een ander jaar bestaat voor die functie niet. Data van vóór de jaren is bij de migratie in 2026 gezet (met een kopie `backups/champagne.db.voor-jaren`).

Een aankoop (`aankoop`) heeft regels (`aankoop_regel`) met elk een verdeling over de via's (`regel_verdeling`). Wijnen worden niet apart beheerd: een regel verwijst naar een wijn op naam (hoofdletterongevoelig), die automatisch wordt aangemaakt en weer verdwijnt als hij op geen enkele bon meer staat. Overdragen tussen via's past de verdeling van de regels aan (nieuwste eerst) en wordt niet apart opgeslagen. Elke wijziging draait in een transactie (`BEGIN IMMEDIATE`, zodat gelijktijdige acties op elkaar wachten) met een consistentiecheck, die alles terugdraait als een via meer flessen toegekend zou hebben dan hij bezit. Bonnen en klanten hebben een `versie`: wie een bewerkformulier opslaat terwijl iemand anders die bon of klant intussen heeft gewijzigd (ook door een overdracht), krijgt een melding in plaats van andermans wijziging te overschrijven.

De verrekening wordt niet opgeslagen maar steeds berekend, in hele centen, uit `aankoop.betaald_door`, de aankoopregels en de tabel `kosten`; een overdracht verschuift de kosten dus mee. De bedragen per bestelling rekenen met de gemiddelde prijs van een wijn binnen de via, omdat toekenningen niet aan een bon hangen. Flessen die nog niet zijn toegekend, staan op een eigen rij, zodat de rijen altijd optellen tot het totaal van de via.

De knop *Backup* onderaan het overzicht downloadt een Excel-bestand met tijdstip in de naam, met tabbladen voor bestellingen, toekenningen, aankopen, kosten en verrekening. Dat is een leesbare momentopname; de app kan het bestand niet terugzetten.

Schemawijzigingen voor bestaande databases staan als kleine migraties in `migraties.py` en draaien automatisch bij het starten.

Het project gebruikt [uv](https://docs.astral.sh/uv/). De Python-versie staat in `.python-version` (3.13) en `pyproject.toml` (`requires-python`). Dependencies staan in `pyproject.toml` en zijn vastgelegd in `uv.lock`. uv installeert zo nodig zelf de juiste Python-versie.