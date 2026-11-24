# Skredvarsling veg – verktøy

Statisk nettside for stedsspesifikk skredvarsling (Statens vegvesen). GitHub Actions henter data og publiserer sida på GitHub Pages. Det trengs ingen server og ingen Claude-kvote.

## Oppbygning
| Mappe | Innhold |
|---|---|
| `site/` | Sida slik den publiseres: `index.html` (meny) og én HTML-fil per fane, kartfliser, bakgrunnsbilder, ikoner |
| `kilde/vaerpakke/` | `bygg.py` (henter data og lager `vaerpakke.html`), `mal.html`, `config.json` (strekninger, stasjoner, punkter) |
| `kilde/morgenbrief/` | `lag_brief.py` og `mal_brief.html` |
| `kilde/skredbaner/` | `hent_bilder.py` (hendelsesbilder fra PowerPoint) |
| `kilde/engangs/` | `lag_kartbilder.py` (lager kartfliser og bakgrunnsbilder på nytt ved ny geometri) |
| `data/` | Excel- og PowerPoint-filer som brukes av skriptene |
| `.github/workflows/oppdater.yml` | Kjøreplan |

## Kjøreplan
- Værpakke: 05:37, 08:37, 11:37, 14:37, 17:37 og 20:37 norsk vintertid. Om sommeren kommer kjøringene én time senere.
- Morgenbrief: ca. 07:15. Briefen viser tall og merknader, men ikke skrevet tekst.
- GitHub kan forsinke kjøringene 5–30 min.
- Manuell kjøring: **Actions → Oppdater data → Run workflow**. Den lager også en ny morgenbrief.

## Oppsett (én gang)
1. Lag et offentlig repo, for eksempel `skredvarsling-veg`, og last opp innholdet i denne mappa.
2. **Settings → Secrets and variables → Actions → New repository secret**. Legg inn tre hemmeligheter:
   - `FROST_CLIENT_ID`: Frost client-ID
   - `SKREDAS_TOKEN`: Skred AS-token
   - `WYSSEN_KEY`: Wyssen-nøkkel
3. **Settings → Pages → Build and deployment → Source: GitHub Actions**.
4. **Actions**: slå på arbeidsflytene hvis GitHub spør om det. Kjør så **Oppdater data → Run workflow**.
5. Sida ligger på `https://<brukernavn>.github.io/<repo>/`.

## Endringer
- **Strekninger, stasjoner, kameraer og meteogrampunkter** endres i `kilde/vaerpakke/config.json`. Gjelder fra neste kjøring.
- **Hendelsesbilder**: last opp PowerPoint-fila «Dimensjonerende skredstørrelse_bilder …pptx» til `data/`. Bildene lages da ved hver kjøring.
- **Statiske faner** (Start, Prosjektinfo, SP-solver, Setningskatalog, Varselskriver, Prosedyre) er vanlige filer i `site/`. Erstatt fila for å oppdatere fanen.
- **Ny geometri** (løsneområder, veglinje): kjør `kilde/engangs/lag_kartbilder.py` og last opp `site/fliser`, `site/bakgrunn` og `site/detalj` på nytt.

## Merknader
- Repoet er offentlig. Ikke legg inn nøkler, passord eller telefonnumre i filene. Nøkler hører bare hjemme i Secrets.
- Lagrede notater, innstillinger og lærte setninger ligger i nettleseren (localStorage) for hver adresse. Det som er lagret på claude.ai-versjonen, følger ikke med automatisk. Bruk eksport/import i Varselskriver.
- Bakgrunnskart: © Kartverket. MEPS: MET Norway (CC BY 4.0). Værsymboler: MET Norway (MIT-lisens).
