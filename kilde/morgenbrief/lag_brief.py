#!/usr/bin/env python3
"""Morgenbrief for stedsspesifikk skredvarsling veg.
Leser data fra bygg.py (--json), henter Varsom-regionoversikt og MET-farevarsler, og lager morgenbrief.html.

Bruk:
  python3 lag_brief.py data.json mal_brief.html morgenbrief.html [--tekst tekst.json] [--kort brief_kort.md]
--kort skriver et kompakt tekstsammendrag.
--tekst legger inn skrevne oppsummeringer: {"landsdel": {"S": "...", "N": "..."}, "prosjekt": {"e134": "...", ...}}.
   (Brukes ikke i GitHub-versjonen; briefen viser da bare tall og merknader.)
Grensene i MERK er indikative oppmerksomhetsgrenser for briefen, ikke terskler for varsling."""
import json, sys, math, time, datetime as dt, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo

pos = [a for i, a in enumerate(sys.argv[1:], 1) if not a.startswith('--') and not sys.argv[i - 1].startswith('--')]
opt = lambda k: sys.argv[sys.argv.index(k) + 1] if k in sys.argv else None
DATA, MAL, UT = (pos + ['data.json', 'mal_brief.html', 'morgenbrief.html'][len(pos):])[:3]
TEKST, KORT = opt('--tekst'), opt('--kort')
UA = 'SVV-vaerpakke-veg/0.9 (stedsspesifikk skredvarsling, morgenbrief)'

# Indikative oppmerksomhetsgrenser (per delområde/døgn). Endres her.
MERK = {'rr': 20, 'nysno': 20, 'kast': 20, 'vind': 15, 'regn_pa_sno_rr': 5, 'dHS3': 20}
LANDSDEL = {'S': {'navn': 'Sør-Norge', 'prosj': ['e134', 'rv13', 'rv15', 'tyin'], 'reg': range(3022, 3038)},
            'N': {'navn': 'Nord-Norge', 'prosj': ['rv85', 'beisfjord', 'rv94', 'e69'], 'reg': range(3006, 3019)}}
DIR8 = ['N', 'NØ', 'Ø', 'SØ', 'S', 'SV', 'V', 'NV']

D = json.load(open(DATA, encoding='utf-8'))
CFG, B = D['config'], D['baked']
A = B.get('arkiv', {})
NOW = dt.datetime.now(dt.timezone.utc)
I_DAG = NOW.astimezone(ZoneInfo('Europe/Oslo')).date()                    # Varsom-dato (kalenderdøgn, norsk tid)
I_MORGEN = I_DAG + dt.timedelta(days=1)
D0 = B['d0']                                         # siste observerte døgn (slutter kl. 06 UTC denne datoen)
P1 = dt.datetime.combine(dt.date.fromisoformat(D0), dt.time(6), dt.timezone.utc)   # start prognosedøgn 1
feil = list(B.get('feil', []))

def get(url, forsok=3, timeout=60):
    for i in range(forsok):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': 'application/json'}), timeout=timeout) as r:
                return json.loads(r.read())
        except Exception:
            if i == forsok - 1: raise
            time.sleep(2 * (i + 1))

r1 = lambda v: None if v is None else round(v, 1)
mx = lambda xs: max([x for x in xs if x is not None], default=None)
mn = lambda xs: min([x for x in xs if x is not None], default=None)

def hovedretning(rows, fmin=5.5):
    """FF-vektet middelretning for timer med vind ≥ fmin (retning vinden kommer fra)."""
    v = [(x['DD'], x['FF']) for x in rows if x.get('DD') is not None and x.get('FF') is not None and x['FF'] >= fmin]
    if not v: return None
    sx = sum(f * math.sin(math.radians(d)) for d, f in v); cx = sum(f * math.cos(math.radians(d)) for d, f in v)
    return DIR8[round((math.degrees(math.atan2(sx, cx)) % 360) / 45) % 8]

def isoterm(Tt, Tb, ht, hb):
    if Tt is None: return None
    g = (Tb - Tt) / (ht - hb) if (hb is not None and Tb is not None and ht > hb) else 0.0065
    if not g > 0.002: g = 0.0065
    return max(0, round(ht + Tt / g))

# ---------------- eksterne kilder: Varsom-oversikt og MET-farevarsler ----------------
def varsom_oversikt():
    url = f'https://api01.nve.no/hydrology/forecast/avalanche/v6.3.0/api/RegionSummary/Simple/1/{I_DAG}/{I_MORGEN}'
    ut = {}
    for r in get(url):
        if r.get('TypeName') != 'A': continue
        ut[r['Id']] = {'navn': r['Name'], 'dager': {w['ValidFrom'][:10]: {'fg': w.get('DangerLevel'), 'tekst': w.get('MainText')} for w in r.get('AvalancheWarningList', [])}}
    return ut

def senter(geom):
    pts = []
    def walk(c):
        if isinstance(c[0], (int, float)): pts.append(c)
        else:
            for x in c: walk(x)
    walk(geom.get('coordinates') or [])
    return (sum(p[1] for p in pts) / len(pts), sum(p[0] for p in pts) / len(pts)) if pts else (None, None)

def farevarsel_kort(f):
    p = f['properties']; lvl = (p.get('awareness_level') or '').split(';')
    return {'tittel': (p.get('title') or '').strip().rstrip('.'), 'type': p.get('eventAwarenessName'), 'niva': lvl[1].strip() if len(lvl) > 1 else '',
            'omrade': p.get('area'), 'beskrivelse': p.get('description'), 'fra': (f.get('when') or {}).get('interval', [None])[0],
            'til': (f.get('when') or {}).get('interval', [None, None])[-1]}

def farevarsler_alle():
    ut = {'S': [], 'N': []}
    for f in get('https://api.met.no/weatherapi/metalerts/2.0/current.json').get('features', []):
        p = f['properties']
        if p.get('geographicDomain') != 'land': continue
        lat, _ = senter(f.get('geometry') or {})
        ut['N' if lat and lat >= 65 else 'S'].append(farevarsel_kort(f))
    return ut

def farevarsler_punkt(lat, lon):
    j = get(f'https://api.met.no/weatherapi/metalerts/2.0/current.json?lat={lat:.4f}&lon={lon:.4f}')
    return [farevarsel_kort(f) for f in j.get('features', []) if f['properties'].get('geographicDomain') == 'land']

try: VO = varsom_oversikt()
except Exception as e: VO = {}; feil.append(f'Varsom regionoversikt: {e}')
try: FV = farevarsler_alle()
except Exception as e: FV = {'S': [], 'N': []}; feil.append(f'MET farevarsler: {e}')
FVP = {}
with ThreadPoolExecutor(4) as ex:
    fut = {r['id']: ex.submit(farevarsler_punkt, r['delomr'][0]['lat'], r['delomr'][0]['lon']) for r in CFG['strekninger']}
    for k, f in fut.items():
        try: FVP[k] = f.result()
        except Exception as e: FVP[k] = []; feil.append(f'MET farevarsel {k}: {e}')

# ---------------- per prosjekt ----------------
def prog_periode(rows, a, b, d):
    xs = [x for x in rows if a < dt.datetime.fromisoformat(x['t'].replace('Z', '+00:00')) + dt.timedelta(hours=1) <= b]
    if not xs: return None
    rr = sum(x.get('RR') or 0 for x in xs); rrx = sum(x.get('RRmax') or x.get('RR') or 0 for x in xs)
    ns = sum((x.get('RR') or 0) for x in xs if x.get('T') is not None and x['T'] <= 0.5)
    iso = [isoterm(x.get('T'), x.get('Tb'), d['topp'], d['bunn']) for x in xs]
    regn_bunn = sum((x.get('RR') or 0) for x, z in zip(xs, iso) if z is not None and z > d['bunn'] + 150)
    return {'timer': len(xs), 'rr': r1(rr), 'rrx': r1(rrx), 'nysno': r1(ns), 'Tt0': r1(mn(x.get('T') for x in xs)), 'Tt1': r1(mx(x.get('T') for x in xs)),
            'Tb1': r1(mx(x.get('Tb') for x in xs)), 'ff': r1(mx(x.get('FF') for x in xs)), 'fg': r1(mx(x.get('FG') for x in xs)),
            'retn': hovedretning(xs), 'iso0': mn(iso), 'iso1': mx(iso), 'regn_bunn': r1(regn_bunn)}

def hoyde_txt(p):
    h1, h2, f = p.get('h1'), p.get('h2'), p.get('fill')
    if h1 is None: return ''
    return f'over {h1}' if f == 1 else f'under {h1}' if f == 2 else f'{min(h1, h2 or h1)}–{max(h1, h2 or h1)}'

def varsom_dag(w):
    if not w: return None
    return {'region': w.get('region'), 'fg': w.get('faregrad'), 'tekst': w.get('hovedtekst'), 'utstedt': w.get('utstedt'),
            'problemer': [{'type': p.get('type'), 'eksp': ', '.join(DIR8[i] for i, c in enumerate(str(p.get('eksp') or '')) if c == '1'),
                           'h': hoyde_txt(p), 'str': p.get('storrelse'), 'sens': p.get('sens'), 'utb': p.get('utbredelse')} for p in w.get('problemer', [])]}

def skygge_dag(rid, dato):
    x = (B.get('skygge') or {}).get(rid, {}).get(dato)
    if not x: return None
    E = (CFG.get('skygge') or {}).get('enheter', {}).get(rid, {})
    per = {}
    for en, v in x['enheter'].items():
        dl = (E.get(en) or {}).get('del') or ''
        per[dl] = max(per.get(dl, 0), v[0])
    hoy = [E.get(en, {}).get('navn', en) for en, v in x['enheter'].items() if v[0] > 0]
    return {'maks': max([v[0] for v in x['enheter'].values()], default=0), 'per_del': per, 'baner': hoy}

def frost_rader24(sid):
    f = B.get('frost', {}).get(sid)
    return f['rows'][-24:] if f else []

PROSJ = {}
for r in CFG['strekninger']:
    rid = r['id']; d0m3 = (dt.date.fromisoformat(D0) - dt.timedelta(days=3)).isoformat()
    deler = []
    for d in r['delomr']:
        k = rid + '|' + d['navn']; g = A.get('gts', {}).get(k, {}); x0 = g.get(D0) or {}; x3 = g.get(d0m3) or {}
        rows = (B.get('prog', {}).get(k) or {}).get('rows', [])
        obs = {'rr': x0.get('rr'), 'nysno': x0.get('sdfsw'), 'tm': x0.get('tm'), 'hs': x0.get('sd'),
               'dhs3': r1(x0['sd'] - x3['sd']) if x0.get('sd') is not None and x3.get('sd') is not None else None,
               'rr3': r1(sum(g.get((dt.date.fromisoformat(D0) - dt.timedelta(days=i)).isoformat(), {}).get('rr') or 0 for i in range(3))) if x0 else None}
        deler.append({'navn': d['navn'], 'topp': d['topp'], 'bunn': d['bunn'], 'obs': obs if x0 else None,
                      'p1': prog_periode(rows, P1, P1 + dt.timedelta(hours=24), d), 'p2': prog_periode(rows, P1 + dt.timedelta(hours=24), P1 + dt.timedelta(hours=48), d)})
    st = []
    for s in r['stasjoner']:
        sid = s.get('sn') or s.get('id'); dg = (A.get('frostdag', {}).get(sid) or {}).get(D0) or {}
        r24 = frost_rader24(sid); hs = [x.get('HS') for x in (B.get('frost', {}).get(sid) or {}).get('rows', []) if x.get('HS') is not None]
        har = any(x.get('T') is not None or x.get('FF') is not None for x in r24)
        st.append({'navn': s.get('navn') or s.get('n'), 'moh': s.get('moh') or (B.get('frostmeta', {}).get(s.get('sn')) or {}).get('moh'), 'data': har,
                   'n': dg.get('n'), 'Tmin': dg.get('Tmin'), 'Tmax': dg.get('Tmax'), 'RR': dg.get('RR'), 'FFmax': dg.get('FFmax'), 'FGmax': dg.get('FGmax'),
                   'retn': hovedretning(r24), 'dHS72': r1(hs[-1] - hs[0]) if len(hs) > 1 else None, 'HS': hs[-1] if hs else None})
    vs = A.get('varsom', {}).get(rid, {})
    P = {'id': rid, 'navn': r['navn'], 'deler': deler, 'stasjoner': st,
         'varsom': {'i_dag': varsom_dag(vs.get(I_DAG.isoformat())), 'i_morgen': varsom_dag(vs.get(I_MORGEN.isoformat()))},
         'skygge': {'i_dag': skygge_dag(rid, I_DAG.isoformat()), 'i_morgen': skygge_dag(rid, I_MORGEN.isoformat())},
         'farevarsler': FVP.get(rid, [])}
    # merknader (regelbaserte)
    m = []
    for dl in deler:
        o, p1, p2 = dl['obs'] or {}, dl['p1'] or {}, dl['p2'] or {}
        if (o.get('rr') or 0) >= MERK['rr']: m.append(f"{dl['navn']}: {o['rr']} mm nedbør siste døgn (seNorge)")
        if (o.get('nysno') or 0) >= MERK['nysno']: m.append(f"{dl['navn']}: {o['nysno']} cm nysnø siste døgn (seNorge)")
        if (o.get('dhs3') or 0) >= MERK['dHS3']: m.append(f"{dl['navn']}: snødybde +{o['dhs3']} cm siste 3 døgn (seNorge)")
        for lbl, p in (('i dag', p1), ('i morgen', p2)):
            if not p: continue
            if (p.get('rr') or 0) >= MERK['rr']: m.append(f"{dl['navn']}: {p['rr']} mm nedbør {lbl} (maks {p['rrx']} mm)")
            if (p.get('fg') or 0) >= MERK['kast'] or (p.get('ff') or 0) >= MERK['vind']:
                m.append(f"{dl['navn']}: vind {p['ff']} m/s, kast {p['fg']} m/s {lbl}" + (f" fra {p['retn']}" if p.get('retn') else ''))
            if (p.get('regn_bunn') or 0) >= MERK['regn_pa_sno_rr'] and ((o.get('hs') or 0) > 0):
                m.append(f"{dl['navn']}: ca. {p['regn_bunn']} mm som regn i løsneområdet {lbl} (0-isoterm opp til {p['iso1']} moh.)")
    rrsn = mx([(dl['obs'] or {}).get('rr') for dl in deler] + [(dl['p1'] or {}).get('rrx') for dl in deler]) or 0
    for s in st:
        if (s.get('RR') or 0) >= 30 and s['RR'] > 3 * max(rrsn, 5): m.append(f"{s['navn']}: {s['RR']} mm nedbør siste døgn – mye mer enn seNorge, kontroller (mulig målefeil)")
        if (s.get('FGmax') or 0) >= MERK['kast']: m.append(f"{s['navn']}: vindkast {s['FGmax']} m/s siste døgn" + (f" fra {s['retn']}" if s.get('retn') else ''))
    for lbl, sk in (('i dag', P['skygge']['i_dag']), ('i morgen', P['skygge']['i_morgen'])):
        if sk and sk['maks'] > 0: m.append(f"Skyggevarsel {lbl}: {['grønt', 'gult', 'oransje', 'rødt'][sk['maks']]} for {', '.join(sk['baner'][:4])}{' …' if len(sk['baner']) > 4 else ''}")
    s0, s1 = P['skygge']['i_dag'], P['skygge']['i_morgen']
    if s0 and s1 and s1['maks'] > s0['maks']: m.append('Skyggevarselet øker fra i dag til i morgen')
    for lbl, v in (('i dag', P['varsom']['i_dag']), ('i morgen', P['varsom']['i_morgen'])):
        if v and str(v.get('fg')) in ('3', '4', '5'): m.append(f"Faregrad {v['fg']} i {v['region']} {lbl}")
    for f in P['farevarsler']: m.append(f"MET farevarsel: {f['tittel']} ({f['niva']})")
    mangler = [s['navn'] for s in st if not s['data']]
    P['merknader'] = m; P['mangler'] = mangler
    PROSJ[rid] = P

# ---------------- landsdeler ----------------
LD = {}
for k, L in LANDSDEL.items():
    reg = [{'id': i, 'navn': VO[i]['navn'], 'i_dag': VO[i]['dager'].get(I_DAG.isoformat()), 'i_morgen': VO[i]['dager'].get(I_MORGEN.isoformat())} for i in L['reg'] if i in VO]
    LD[k] = {'navn': L['navn'], 'prosj': [p for p in L['prosj'] if p in PROSJ], 'regioner': reg, 'farevarsler': FV.get(k, [])}

S = B.get('status', {})
BRIEF = {'laget': NOW.strftime('%Y-%m-%dT%H:%M:%SZ'), 'data_hentet': B.get('created'), 'd0': D0, 'i_dag': I_DAG.isoformat(), 'i_morgen': I_MORGEN.isoformat(),
         'p1': P1.strftime('%Y-%m-%dT%H:%M:%SZ'), 'meps': (S.get('meps') or {}).get('kjoring'), 'prog_oppdatert': (S.get('prog') or {}).get('modell'),
         'obs_siste': (S.get('frost') or {}).get('siste'), 'merk': MERK, 'landsdeler': LD, 'prosjekt': PROSJ, 'feil': feil, 'tekst': None,
         'varsom_utstedt': any((v.get('i_dag') or {}).get('fg') not in (None, '0') for L in LD.values() for v in L['regioner'])}
if TEKST:
    try: BRIEF['tekst'] = json.load(open(TEKST, encoding='utf-8'))
    except Exception as e: feil.append(f'tekst.json: {e}')

# ---------------- kompakt sammendrag ----------------
def kort():
    f = lambda v, e='': '–' if v is None else f'{v}{e}'
    o = [f"# Grunnlag for morgenbrief {I_DAG} (laget {BRIEF['laget']}, MEPS {BRIEF['meps']}, siste obs {BRIEF['obs_siste']})",
         f"Obs-døgn = 06–06 UTC som slutter {D0}. Prognose 'i dag' = {P1:%d.%m} 06 UTC + 24 t, 'i morgen' = neste 24 t. Varsom-dato i dag = {I_DAG}."]
    for k, L in LD.items():
        o.append(f"\n## {L['navn']}")
        fg = [f"{r['navn']} {(r['i_dag'] or {}).get('fg', '–')}/{(r['i_morgen'] or {}).get('fg', '–')}" for r in L['regioner']]
        o.append('Varsom faregrad i dag/i morgen (0 = ikke vurdert): ' + ', '.join(fg))
        t = [(r['navn'], (r['i_dag'] or {}).get('tekst')) for r in L['regioner'] if (r['i_dag'] or {}).get('fg') not in (None, '0')]
        for n, tx in t: o.append(f'- Hovedbudskap {n}: {tx}')
        o.append('MET-farevarsler (land): ' + ('; '.join(f"{x['tittel']} [{x['niva']}]" for x in L['farevarsler']) or 'ingen'))
        for pid in L['prosj']:
            P = PROSJ[pid]; o.append(f"\n### {P['navn']} ({pid})")
            v0, v1 = P['varsom']['i_dag'] or {}, P['varsom']['i_morgen'] or {}
            o.append(f"Varsom {v0.get('region', '–')}: faregrad {v0.get('fg', '–')} i dag, {v1.get('fg', '–')} i morgen. Skredproblem i dag: " +
                     ('; '.join(f"{p['type']} ({p['h']} moh., {p['eksp']}, {p['str']}, {p['sens']})" for p in v0.get('problemer', [])) or 'ingen'))
            s0, s1 = P['skygge']['i_dag'], P['skygge']['i_morgen']
            o.append(f"Skyggevarsel maks (0 grønt–3 rødt): i dag {f(s0 and s0['maks'])}, i morgen {f(s1 and s1['maks'])}")
            for d in P['deler']:
                ob, p1, p2 = d['obs'] or {}, d['p1'] or {}, d['p2'] or {}
                o.append(f"- {d['navn']} ({d['bunn']}–{d['topp']} moh.): siste døgn RR {f(ob.get('rr'), ' mm')}, nysnø {f(ob.get('nysno'), ' cm')}, Tm {f(ob.get('tm'), ' °C')}, HS {f(ob.get('hs'), ' cm')} (Δ3d {f(ob.get('dhs3'))}). "
                         f"I dag: RR {f(p1.get('rr'))} (maks {f(p1.get('rrx'))}) mm, nysnø est. {f(p1.get('nysno'))} mm, T topp {f(p1.get('Tt0'))}/{f(p1.get('Tt1'))} °C, vind {f(p1.get('ff'))}/kast {f(p1.get('fg'))} m/s fra {f(p1.get('retn'))}, 0-iso {f(p1.get('iso0'))}–{f(p1.get('iso1'))} moh. "
                         f"I morgen: RR {f(p2.get('rr'))} mm, T topp {f(p2.get('Tt0'))}/{f(p2.get('Tt1'))}, vind {f(p2.get('ff'))}/kast {f(p2.get('fg'))} fra {f(p2.get('retn'))}, 0-iso {f(p2.get('iso1'))}.")
            sx = [s for s in P['stasjoner'] if s['data']]
            if sx:
                b = max(sx, key=lambda s: s.get('FGmax') or s.get('FFmax') or 0)
                o.append(f"Stasjoner siste døgn: T {f(mn(s['Tmin'] for s in sx))} til {f(mx(s['Tmax'] for s in sx))} °C, maks kast {f(b.get('FGmax'))} m/s ({b['navn']}, fra {f(b.get('retn'))}), maks RR {f(mx(s['RR'] for s in sx))} mm")
            if P['merknader']: o.append('Merknader: ' + ' | '.join(P['merknader']))
            if P['mangler']: o.append('Stasjoner uten data: ' + ', '.join(P['mangler']))
    if feil: o.append('\nFeil ved henting: ' + ' | '.join(feil))
    return '\n'.join(o)

if KORT: open(KORT, 'w', encoding='utf-8').write(kort())
html = open(MAL, encoding='utf-8').read().replace('/*__BRIEF__*/null', json.dumps(BRIEF, ensure_ascii=False, separators=(',', ':')))
open(UT, 'w', encoding='utf-8').write(html)
print(f"Skrev {UT} ({len(html)//1024} kB): {len(PROSJ)} prosjekt, {sum(len(p['merknader']) for p in PROSJ.values())} merknader, "
      f"{sum(len(v) for v in FV.values())} farevarsler (land), Varsom {'utstedt' if BRIEF['varsom_utstedt'] else 'ikke utstedt'}, tekst {'ja' if BRIEF['tekst'] else 'nei'}, feil {len(feil)}")
for x in feil: print('  FEIL', x)
