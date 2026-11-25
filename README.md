# Skredvarsling veg – verktøy

Statisk nettside for stedsspesifikk skredvarsling (Statens vegvesen). GitHub Actions henter data og publiserer sida på GitHub Pages. Det trengs ingen server og ingen Claude-kvote.

## Oppbygning
| Mappe | Innhold |
|---|---|
| `site/` | Sida slik den publiseres: `index.html` (meny) og én HTML-fil per fane, kartfliser, bakgrunnsbilder, ikoner |
| `kilde/vaerpakke/` | `bygg.py` (henter data og lager `vaerpakke.html`), `mal.html`, `config.json` (strekninger, stasjoner, punkter) |
| `kilde/morgenbrief/` | `lag_brief.py` og `mal_brief.html` |
| `kilde/skredbaner/` | `hent_bilder.py` (hendelsesbilder fra PowerPoint) |
| `kilde/varslinger/` | `hent_varsler.py` (henter utsendte skredvarsel fra Gmail), `mal_varsler.html` + `bygg_side.py` (lager `site/varsler.html`), `lag_kartdata.py` (kartdata til fanen) |
| `kilde/engangs/` | `lag_kartbilder.py` (lager kartfliser og bakgrunnsbilder på nytt ved ny geometri) |
| `data/` | Excel- og PowerPoint-filer som brukes av skriptene |
| `.github/workflows/oppdater.yml` | Kjøreplan |

## Kjøreplan
- Værpakke: hver time kl. xx:23, 05:23–21:23 norsk vintertid (sommertid: én time senere).
- Morgenbrief: første vellykkede forsøk etter kl. 07:00 (forsøk hvert 20. min til ca. 08:00). Briefen viser tall og merknader, men ikke skrevet tekst.
- Utsendte skredvarsel: hentes fra Gmail ved hver kjøring, og i tillegg kl. xx:08, xx:38 og xx:53 i 10–13 UTC (11–14 vintertid). Sida publiseres bare når det har kommet nye varsel.
- GitHub kan forsinke eller hoppe over planlagte kjøringer; derfor kjøres de ofte.
- Manuell kjøring: **Actions → Oppdater data → Run workflow**. Den lager også en ny morgenbrief.

## Oppsett (én gang)
1. Lag et offentlig repo, for eksempel `skredvarsling-veg`, og last opp innholdet i denne mappa.
2. **Settings → Secrets and variables → Actions → New repository secret**. Legg inn tre hemmeligheter:
   - `FROST_CLIENT_ID`: Frost client-ID
   - `SKREDAS_TOKEN`: Skred AS-token
   - `WYSSEN_KEY`: Wyssen-nøkkel
   - `GMAIL_BRUKER`, `GMAIL_APP_PASSORD` (app-passord), `VARSEL_AVSENDER` (avsenderadressen til varslingsverktøyet): for fanen Varslinger. Uten disse hentes ingen varsel.
3. **Settings → Pages → Build and deployment → Source: GitHub Actions**.
4. **Actions**: slå på arbeidsflytene hvis GitHub spør om det. Kjør så **Oppdater data → Run workflow**.
5. Sida ligger på `https://<brukernavn>.github.io/<repo>/`.

## Endringer
- **Strekninger, stasjoner, kameraer og meteogrampunkter** endres i `kilde/vaerpakke/config.json`. Gjelder fra neste kjøring.
- **Hendelsesbilder**: last opp PowerPoint-fila «Dimensjonerende skredstørrelse_bilder …pptx» til `data/`. Bildene lages da ved hver kjøring.
- **Statiske faner** (Start, Prosjektinfo, SP-solver, Setningskatalog, Varselskriver, Prosedyre) er vanlige filer i `site/`. Erstatt fila for å oppdatere fanen.
- **Ny geometri** (løsneområder, veglinje): kjør `kilde/engangs/lag_kartbilder.py` og last opp `site/fliser`, `site/bakgrunn` og `site/detalj` på nytt.

## Fanen Varslinger (skredvarsel for entreprenør)
- Lenke per strekning: `varsler.html#e134`, `#rv13`, `#rv15`, `#tyin`, `#rv85`, `#beisfjord`, `#rv94`, `#e69`.
- Varslene lagres i `site/varsler/` (`varsler.json` er arkivet, `v/` er originalene). Et varsel fjernes ved å slette det fra `varsler.json` og `v/`, og så lage `varsler_data.js` på nytt (`python3 kilde/varslinger/hent_varsler.py --ut site/varsler --fil` uten filer).
- Eksempelet for rv. 15 (utkast 25.11.2026, merket «test») kan slettes når ekte varsel kommer.
- Ny geometri: `python3 kilde/varslinger/lag_kartdata.py <prosjektinfo data.json> kilde/vaerpakke/config.json site/varsler/kart_data.js`.

## Merknader
- Repoet er offentlig. Ikke legg inn nøkler, passord eller telefonnumre i filene. Nøkler hører bare hjemme i Secrets.
- Lagrede notater, innstillinger og lærte setninger ligger i nettleseren (localStorage) for hver adresse. Det som er lagret på claude.ai-versjonen, følger ikke med automatisk. Bruk eksport/import i Varselskriver.
- Bakgrunnskart: © Kartverket. MEPS: MET Norway (CC BY 4.0). Værsymboler: MET Norway (MIT-lisens).
