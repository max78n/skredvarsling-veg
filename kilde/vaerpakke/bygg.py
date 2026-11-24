#!/usr/bin/env python3
"""Bygger vaerpakke_veg.html med innebygde data (v0.8, med arkiv og datovelger).
Kilder: MET Frost, Skred AS (InfluxDB), NVE seNorge/GTS, MET Locationforecast, Varsom.
Arkiv: døgnsammendrag (06–06 UTC) fra sesongstart, lagret som gzip+base64-JSON. Uten arkiv hentes hele sesongen.
Bruk: python3 bygg.py config.json mal.html ut.html [--arkiv-inn arkiv.b64] [--arkiv-ut arkiv.b64] [--json data.json]
--json skriver de innebygde dataene (offentlig config + BAKED) til en JSON-fil, brukt av morgenbrief/lag_brief.py.
Hemmeligheter leses fra miljøvariablene FROST_CLIENT_ID, SKREDAS_TOKEN og WYSSEN_KEY (GitHub Secrets),
og bare hvis de mangler, fra config.json. De bygges aldri inn i HTML eller data.json."""
import json, sys, os, re, time, math, base64, gzip, csv, io, datetime as dt
import urllib.request, urllib.parse, urllib.error
from concurrent.futures import ThreadPoolExecutor

pos = [a for i, a in enumerate(sys.argv[1:], 1) if not a.startswith('--') and not sys.argv[i - 1].startswith(('--arkiv', '--json'))]
opt = lambda k: sys.argv[sys.argv.index(k) + 1] if k in sys.argv else None
CFG = pos[0] if len(pos) > 0 else 'config.json'
MAL = pos[1] if len(pos) > 1 else 'mal.html'
UT = pos[2] if len(pos) > 2 else 'vaerpakke_veg.html'
ARK_INN, ARK_UT, JSON_UT = opt('--arkiv-inn'), opt('--arkiv-ut'), opt('--json')
UA = 'SVV-vaerpakke-veg/0.9 (stedsspesifikk skredvarsling)'
cfg = json.load(open(CFG, encoding='utf-8'))
# hemmeligheter fra miljøet (GitHub Secrets)
if os.environ.get('FROST_CLIENT_ID'): cfg['frost_client_id'] = os.environ['FROST_CLIENT_ID']
if os.environ.get('SKREDAS_TOKEN'): cfg.setdefault('skredas', {})['token'] = os.environ['SKREDAS_TOKEN']
if os.environ.get('WYSSEN_KEY'): cfg.setdefault('wyssen', {})['key'] = os.environ['WYSSEN_KEY']
NOW = dt.datetime.now(dt.timezone.utc)
now = NOW.replace(minute=0, second=0, microsecond=0)
iso = lambda t: t.strftime('%Y-%m-%dT%H:%M:%SZ')
dag06 = lambda t: (t - dt.timedelta(hours=6)).date() + dt.timedelta(days=1)   # dato = døgnets slutt kl. 06 UTC
D0 = (NOW - dt.timedelta(hours=6)).date()   # siste avsluttede seNorge-døgn (slutter kl. 06 UTC denne datoen)
SESONG = dt.date.fromisoformat(cfg.get('sesongstart', '2026-10-01'))

def get(url, auth=None, forsok=4, data=None, headers=None, raw=False, timeout=90):
    for i in range(forsok):
        req = urllib.request.Request(url, data=data, headers={'User-Agent': UA, 'Accept': 'application/json', **(headers or {})})
        if auth: req.add_header('Authorization', 'Basic ' + base64.b64encode((auth + ':').encode()).decode())
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                b = r.read(); return b.decode('utf-8') if raw else json.loads(b)
        except urllib.error.HTTPError as e:
            if e.code < 500 or i == forsok - 1: raise
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            if i == forsok - 1: raise
        time.sleep(2 * (i + 1))

def utm33(lat, lon):
    a, f = 6378137.0, 1 / 298.257222101
    k0, lon0, E0 = 0.9996, math.radians(15), 500000
    n = f / (2 - f); A = a / (1 + n) * (1 + n**2 / 4 + n**4 / 64)
    al = [n/2 - 2*n**2/3 + 5*n**3/16, 13*n**2/48 - 3*n**3/5, 61*n**3/240]
    phi, lam = math.radians(lat), math.radians(lon) - lon0
    e = 2 * math.sqrt(n) / (1 + n)
    t = math.sinh(math.atanh(math.sin(phi)) - e * math.atanh(e * math.sin(phi)))
    xi, eta = math.atan2(t, math.cos(lam)), math.atanh(math.sin(lam) / math.sqrt(1 + t*t))
    E = E0 + k0 * A * (eta + sum(al[j] * math.cos(2*(j+1)*xi) * math.sinh(2*(j+1)*eta) for j in range(3)))
    N = k0 * A * (xi + sum(al[j] * math.sin(2*(j+1)*xi) * math.cosh(2*(j+1)*eta) for j in range(3)))
    return round(E), round(N)

# ---------------- arkiv ----------------
def les_arkiv(p):
    if not p or not os.path.exists(p): return None
    t = open(p, 'rb').read().strip()
    try: return json.loads(gzip.decompress(base64.b64decode(t)))
    except Exception:
        try: return json.loads(t)
        except Exception: return None
def skriv_arkiv(a, p):
    open(p, 'w').write(base64.b64encode(gzip.compress(json.dumps(a, separators=(',', ':')).encode(), 9)).decode())

TILBAKE = cfg.get('dager_tilbake')
ARK = ({} if TILBAKE else les_arkiv(ARK_INN)) or {}
ARK.setdefault('gts', {}); ARK.setdefault('gtsveg', {}); ARK.setdefault('frostdag', {}); ARK.setdefault('varsom', {}); ARK.setdefault('meta', {})
siste = dt.date.fromisoformat(ARK['til']) if ARK.get('til') else None
FRA = max(SESONG, siste - dt.timedelta(days=8)) if siste else SESONG     # hentes på nytt fra denne datoen
if TILBAKE: FRA = D0 - dt.timedelta(days=TILBAKE + 3)                    # + 3 døgn for 72 t-terskler
print(f'Arkiv: {"til " + ARK["til"] if siste else "tomt"} – henter fra {FRA} til {D0}')

# ---------------- Frost ----------------
FID = cfg.get('frost_client_id')
FEL = ['air_temperature', 'sum(precipitation_amount PT1H)', 'wind_speed', 'max(wind_speed_of_gust PT1H)',
       'wind_from_direction', 'surface_snow_thickness']
KEY = dict(zip(FEL, ['T', 'RR', 'FF', 'FG', 'DD', 'HS']))

def frost_meta(sns):
    q = urllib.parse.urlencode({'ids': ','.join(sns), 'fields': 'id,name,masl,geometry'})
    out = {}
    for s in get('https://frost.met.no/sources/v0.jsonld?' + q, FID).get('data', []):
        c = s.get('geometry', {}).get('coordinates', [None, None])
        out[s['id']] = {'lat': c[1], 'lon': c[0], 'moh': s.get('masl'), 'frostnavn': s.get('name')}
    return out

def frost_timer(sn, fra_t, til_t):
    """Timesverdier {t: {T,RR,...}} for perioden, hentet i bolker på 20 døgn."""
    def hent(els, a, b):
        q = urllib.parse.urlencode({'sources': sn, 'elements': ','.join(els), 'referencetime': f'{iso(a)}/{iso(b)}'})
        return get('https://frost.met.no/observations/v0.jsonld?' + q, FID, timeout=180).get('data', [])
    timer = {}
    a = fra_t
    while a < til_t:
        b = min(til_t, a + dt.timedelta(days=20))
        try: data = hent(FEL, a, b)
        except urllib.error.HTTPError as e:
            if e.code not in (404, 412): raise
            data = []
            for el in FEL:
                try: data += hent([el], a, b)
                except urllib.error.HTTPError as e2:
                    if e2.code not in (404, 412): raise
        for d in data:
            t = dt.datetime.fromisoformat(d['referenceTime'].replace('Z', '+00:00'))
            if t.minute: continue
            row = timer.setdefault(t, {})
            for o in d['observations']:
                k = KEY.get(o['elementId'])
                if k and (k not in row or o.get('timeResolution') == 'PT1H'): row[k] = o['value']
        a = b
    return timer

def skredas_timer(st, fra_t, til_t):
    sa = cfg['skredas']
    flux = (f'from(bucket: "{st["bucket"]}") |> range(start: {iso(fra_t)}, stop: {iso(til_t)}) '
            f'|> filter(fn: (r) => r._measurement == "{st["measurement"]}") '
            '|> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")')
    txt = get(sa['url'], data=flux.encode(), raw=True, timeout=180,
              headers={'Authorization': 'Token ' + sa['token'], 'Accept': 'application/csv', 'Content-type': 'application/vnd.flux'})
    recs = []
    for blokk in [b for b in txt.replace('\r\n', '\n').split('\n\n') if b.strip()]:
        recs += list(csv.DictReader(io.StringIO(blokk)))
    recs.sort(key=lambda r: r.get('_time') or '')
    f = lambda x: float(x) if x not in (None, '') else None
    grupper = {}
    for rc in recs:
        if not rc.get('_time'): continue
        t = dt.datetime.fromisoformat(rc['_time'].replace('Z', '+00:00'))
        k = (t - dt.timedelta(seconds=1)).replace(minute=0, second=0, microsecond=0) + dt.timedelta(hours=1)
        grupper.setdefault(k, []).append(rc)
    timer = {}
    for k, xs in grupper.items():
        def agg(key, fn):
            v = [f(x.get(key)) for x in xs if f(x.get(key)) is not None]
            return round(fn(v), 2) if v else None
        row = {'T': agg('air_temperature', lambda v: v[-1]), 'FF': agg('wind_speed', lambda v: sum(v) / len(v)),
               'FG': agg('wind_speed_of_gust', max), 'DD': agg('wind_from_direction', lambda v: v[-1]),
               'SFl': agg('snow_drift_low_cumsum', sum), 'SFh': agg('snow_drift_high_cumsum', sum)}
        timer[k] = {a: b for a, b in row.items() if b is not None}
    return timer

def wyssen_timer(st, fra_t, til_t):
    """Wyssen-tårn via SVV-proxy (Respons). Nøkkel i cfg['wyssen'], aldri i publisert HTML."""
    wy = cfg['wyssen']
    q = urllib.parse.urlencode({'stations': st['wid'], 'parameters': 'air_temperature,wind_speed,wind_from_direction',
                                'from': iso(fra_t), 'to': iso(til_t)})
    d = get(wy['url'] + '?' + q, headers={'X-API-KEY': wy['key']}, timeout=180)
    grupper = {}
    for stn in d.get('stations', []):
        for par, ser in stn.get('series', {}).items():
            for x in ser.get('data', []):
                if x.get('value') is None: continue
                t = dt.datetime.fromtimestamp(x['timestamp'] / 1000, dt.timezone.utc)
                k = (t - dt.timedelta(seconds=1)).replace(minute=0, second=0, microsecond=0) + dt.timedelta(hours=1)
                grupper.setdefault(k, {}).setdefault(par, []).append(x['value'])
    timer = {}
    for k, g in grupper.items():
        row = {}
        if g.get('air_temperature'): row['T'] = round(g['air_temperature'][-1], 1)
        if g.get('wind_speed'): row['FF'] = round(sum(g['wind_speed']) / len(g['wind_speed']), 1)
        if g.get('wind_from_direction'):
            sx = sum(math.sin(math.radians(v)) for v in g['wind_from_direction']); cx = sum(math.cos(math.radians(v)) for v in g['wind_from_direction'])
            row['DD'] = round(math.degrees(math.atan2(sx, cx)) % 360)
        if row: timer[k] = row
    return timer

def dognverdier(timer):
    """06–06 UTC-døgn fra timesverdier. Timeverdi ved t gjelder timen før t."""
    dager = {}
    for t, r in timer.items():
        dager.setdefault(dag06(t - dt.timedelta(minutes=1)).isoformat(), []).append((t, r))
    out = {}
    for d, xs in dager.items():
        xs.sort(key=lambda x: x[0])
        v = lambda k: [r[k] for _, r in xs if r.get(k) is not None]
        T, RR, FF, FG, HS, SFl, SFh = v('T'), v('RR'), v('FF'), v('FG'), v('HS'), v('SFl'), v('SFh')
        o = {'n': len(xs)}
        if len(T) >= 12: o.update(Tmin=min(T), Tmax=max(T), Tm=round(sum(T) / len(T), 1))
        if len(RR) >= 18: o['RR'] = round(sum(RR), 1)
        if len(FF) >= 12: o.update(FFmax=round(max(FF), 1), FFm=round(sum(FF) / len(FF), 1))
        if FG: o['FGmax'] = round(max(FG), 1)
        if HS: o['HS'] = HS[-1]
        if len(SFl) >= 12: o['SFl'] = round(sum(SFl)); o['SFh'] = round(sum(SFh)) if SFh else None
        out[d] = o
    return out

def stasjon(s):
    fra_t = min(dt.datetime.combine(FRA - dt.timedelta(days=1), dt.time(6), dt.timezone.utc), now - dt.timedelta(hours=73))
    til_t = now + dt.timedelta(hours=1)
    if s.get('kilde') == 'skredas': timer = skredas_timer(s, fra_t, til_t)
    elif s.get('kilde') == 'wyssen': timer = wyssen_timer(s, fra_t, til_t)
    else: timer = frost_timer(s['sn'], fra_t, til_t)
    rows = []
    for k in range(72):
        t = now - dt.timedelta(hours=71 - k); r = {'t': iso(t)}; r.update(timer.get(t, {})); rows.append(r)
    sist = max((r['t'] for r in rows if len(r) > 1), default=None)
    return {'rows': rows, 'siste': sist, 'elementer': sorted({k for r in rows for k in r if k != 't'}), 'dag': dognverdier(timer)}

# ---------------- seNorge / GTS ----------------
def gts(lat, lon, temaer, fra, til):
    E0, N0 = utm33(lat, lon)
    s, e = fra.isoformat(), til.isoformat()
    for dE, dN in [(0,0),(0,1000),(-1000,0),(1000,0),(0,-1000),(-1000,1000),(1000,1000),(-1000,-1000),(1000,-1000)]:
        E, N = E0 + dE, N0 + dN
        try:
            get(f'https://gts.nve.no/api/GridTimeSeries/{E}/{N}/{e}/{e}/tm.json'); break
        except urllib.error.HTTPError as ex:
            if ex.code == 400: continue
            raise
    else:
        raise RuntimeError('ingen landrute innen 1 km')
    serie, moh = {}, None
    for th in temaer:
        j = get(f'https://gts.nve.no/api/GridTimeSeries/{E}/{N}/{s}/{e}/{th}.json', timeout=180)
        nod = j.get('NoDataValue'); moh = j.get('Altitude')
        dd, mm, yy = j['StartDate'].split(' ')[0].split('.'); d0 = dt.date(int(yy), int(mm), int(dd))
        for i, v in enumerate(j['Data']):
            dato = d0 + dt.timedelta(days=i)
            if dato > D0: break
            serie.setdefault(dato.isoformat(), {})[th] = None if v == nod else v
    return {'E': E, 'N': N, 'flyttet_m': [dE, dN], 'moh': moh, 'serie': serie}

# ---------------- Locationforecast ----------------
def lf(lat, lon, alt):
    q = f'lat={lat:.4f}&lon={lon:.4f}' + (f'&altitude={round(alt)}' if alt is not None else '')
    j = get('https://api.met.no/weatherapi/locationforecast/2.0/complete?' + q)
    rows = []
    for x in j['properties']['timeseries']:
        n1 = x['data'].get('next_1_hours')
        if not n1: continue
        i, n = x['data']['instant']['details'], n1['details']
        rows.append({'t': x['time'], 'T': i.get('air_temperature'), 'FF': i.get('wind_speed'), 'FG': i.get('wind_speed_of_gust'),
                     'DD': i.get('wind_from_direction'), 'RR': n.get('precipitation_amount'), 'RRmax': n.get('precipitation_amount_max')})
        if len(rows) == 66: break
    lang = []   # 6-timers serie over hele horisonten (MEPS, deretter ECMWF)
    for x in j['properties']['timeseries']:
        n6 = x['data'].get('next_6_hours')
        if not n6 or int(x['time'][11:13]) % 6: continue
        i, n = x['data']['instant']['details'], n6['details']
        lang.append({'t': x['time'], 'T': i.get('air_temperature'), 'FF': i.get('wind_speed'), 'FG': i.get('wind_speed_of_gust'),
                     'DD': i.get('wind_from_direction'), 'RR': n.get('precipitation_amount'), 'RRmax': n.get('precipitation_amount_max'),
                     'Tx': n.get('air_temperature_max'), 'Tn': n.get('air_temperature_min')})
    return j['properties']['meta']['updated_at'], rows, lang

def prog_losne(d):
    upd, top, lang = lf(d['lat'], d['lon'], d['topp'])
    if d.get('bunn') is not None:
        _, bun, lb = lf(d['lat'], d['lon'], d['bunn'])
        for x, y in zip(top, bun): x['Tb'] = y['T']
        tb = {y['t']: y['T'] for y in lb}
        for x in lang: x['Tb'] = tb.get(x['t'])
    return {'upd': upd, 'rows': top, 'lang': lang}

# ---------------- Varsom ----------------
VURL = 'https://api01.nve.no/hydrology/forecast/avalanche/v6.3.0/api/AvalancheWarningByCoordinates/Detail'
def varsom(lat, lon, fra, til):
    out = {}
    for w in get(f'{VURL}/{lat:.4f}/{lon:.4f}/1/{fra.isoformat()}/{til.isoformat()}', timeout=300):
        d = (w.get('ValidFrom') or '')[:10]
        out[d] = {'region': w.get('RegionName'), 'publisert': w.get('PublishTime'),
                  'faregrad': w.get('DangerLevel'), 'hovedtekst': w.get('MainText'),
                  'utstedt': bool(w.get('AvalancheProblems')) or (w.get('DangerLevel') not in (None, '0', 0)),
                  'problemer': [{'type': p.get('AvalancheProblemTypeName'), 'arsak': p.get('AvalCauseName'), 'eksp': p.get('ValidExpositions'),
                                 'h1': p.get('ExposedHeight1'), 'h2': p.get('ExposedHeight2'), 'fill': p.get('ExposedHeightFill'),
                                 'storrelse': p.get('DestructiveSizeExtName'), 'utlosning': p.get('AvalTriggerSimpleName'),
                                 'sannsynlighet': p.get('AvalProbabilityName'), 'utbredelse': p.get('AvalPropagationName'),
                                 'sens': (p.get('AvalTriggerSensitivityName') or '').strip(), 'ptype': p.get('AvalancheProblemTypeName')}
                                for p in (w.get('AvalancheProblems') or [])]}
    return out

# ---------------- MEPS-kart (MET Norway, CC BY 4.0) ----------------
MEPS_SKALA = {   # (grenser, farger) – verdier under første grense er gjennomsiktige
 'rr3':  ([0.2, 0.5, 1, 2, 4, 6, 10, 15, 20], ['#c6ecff','#8fd3ff','#4fb0f5','#1f7fe0','#2fb34a','#8fd12a','#f2d21a','#f28c1a','#d7301f']),
 'rr24': ([1, 2, 4, 6, 10, 15, 20, 25, 30, 40, 50], ['#c6ecff','#8fd3ff','#4fb0f5','#1f7fe0','#1b9e3e','#5cc22c','#b5dd2a','#f2d21a','#f2a11a','#e8601c','#c41f2a']),
 'sf24': ([1, 2, 4, 6, 10, 15, 20, 25, 30, 40], ['#e5dcff','#c9b8ff','#a990f7','#8a68ec','#6a45d9','#d36ee0','#f05aa8','#e8601c','#c41f2a','#7a0f17']),
 'ff':   ([5.5, 8, 10.8, 13.9, 17.2, 20.8, 24.5, 28.5, 32.7], ['#d9f2c7','#9fd98a','#5cb85c','#f2d21a','#f29a1a','#e8601c','#c41f2a','#8e1a6e','#3d0a5c']),
 'fg':   ([10.8, 13.9, 17.2, 20.8, 24.5, 28.5, 32.7, 40], ['#d9f2c7','#9fd98a','#f2d21a','#f29a1a','#e8601c','#c41f2a','#8e1a6e','#3d0a5c']),
 'tw0':  ([0.01, 200, 400, 600, 800, 1000, 1200, 1400, 1600, 2000], ['#2b4f9e','#3f73c4','#5f9bd9','#8cc2e8','#bfe2ef','#f2efc4','#f5cf86','#ef9f5a','#dd6a3f','#b7342a']),
}
MEPS_PROD = {'rr3': ('Nedbør 3 t', 'mm', 0.78), 'ff': ('Middelvind 10 m', 'm/s', 0.72), 'fg': ('Vindkast maks 3 t', 'm/s', 0.72),
             'tw0': ('Snøfallsgrense ≈ våttemp. 0 °C', 'moh.', 0.55), 'rr24': ('Nedbør døgn', 'mm', 0.78),
             'sf24': ('Snøfall døgn', 'mm vannekv.', 0.78), 'fg24': ('Vindkast maks døgn', 'm/s', 0.72)}

def meps_kart(ut='kart'):
    import numpy as np, netCDF4
    from PIL import Image
    from pyproj import Transformer
    mc = cfg.get('meps')
    if not mc: return None
    kat = get(mc['katalog'], raw=True, headers={'Accept': 'text/html'})
    kjor = sorted(set(re.findall(r'meps_det_2_5km_(\d{8}T\d{2}Z)\.ncml', kat)))
    if not kjor: raise RuntimeError('fant ingen MEPS-kjøringer i katalogen')
    ds = None
    for k in reversed(kjor[-3:]):           # nyeste komplette kjøring
        try:
            d = netCDF4.Dataset(mc['dods'] + f'meps_det_2_5km_{k}.ncml')
            r0 = mc['regioner']['S']
            v = d['precipitation_amount_acc'][-1, 0, r0['j'][0]:r0['j'][0] + 5, r0['i'][0]:r0['i'][0] + 5]
            if np.ma.count(v) == 0: d.close(); continue
            ds, kjoring = d, k; break
        except Exception: continue
    if ds is None: raise RuntimeError('ingen komplett MEPS-kjøring')
    tid = [dt.datetime.fromtimestamp(float(t), dt.timezone.utc) for t in ds['time'][:]]
    nt = len(tid); steg3 = list(range(3, nt, 3))
    xg = ds['x'][:]; yg = ds['y'][:]
    lcc = Transformer.from_crs('EPSG:4326', mc['proj'], always_xy=True)
    merc2ll = Transformer.from_crs('EPSG:3857', 'EPSG:4326', always_xy=True)
    ll2merc = Transformer.from_crs('EPSG:4326', 'EPSG:3857', always_xy=True)
    ll_fra_lcc = Transformer.from_crs(mc['proj'], 'EPSG:4326', always_xy=True)

    def farge(a, prod, alpha):
        g, f = MEPS_SKALA['fg' if prod == 'fg24' else prod]
        rgb = np.array([[int(c[i:i + 2], 16) for i in (1, 3, 5)] for c in f], dtype=np.uint8)
        idx = np.digitize(a, g) - 1
        out = np.zeros(a.shape + (4,), np.uint8)
        ok = (idx >= 0) & np.isfinite(a)
        out[ok, :3] = rgb[np.clip(idx[ok], 0, len(f) - 1)]
        out[ok, 3] = int(255 * alpha)
        return out

    os.makedirs(os.path.join(ut, 'meps'), exist_ok=True)
    filer, meta = [], {'kjoring': kjoring, 'start': iso(tid[0]), 'steg': [iso(tid[s]) for s in steg3], 'regioner': {}, 'dager': [],
                       'prod': {p: {'navn': n, 'enhet': e, 'grenser': MEPS_SKALA['fg' if p == 'fg24' else p][0],
                                    'farger': MEPS_SKALA['fg' if p == 'fg24' else p][1]} for p, (n, e, _) in MEPS_PROD.items()},
                       'vind': {}}
    # døgn 06–06 UTC innenfor kjøringen
    dager = []
    e = tid[0].replace(hour=6, minute=0, second=0, microsecond=0)
    if e <= tid[0]: e += dt.timedelta(days=1)
    while e <= tid[-1]:
        ks = max(0, tid.index(e - dt.timedelta(hours=24))) if (e - dt.timedelta(hours=24)) in tid else 0
        dager.append((ks, tid.index(e))); e += dt.timedelta(days=1)
    meta['dager'] = [{'fra': iso(tid[a]), 'til': iso(tid[b]), 'dato': tid[b].date().isoformat()} for a, b in dager]

    for rk, r in mc['regioner'].items():
        i0, i1 = r['i']; j0, j1 = r['j']
        X = xg[i0:i1]; Y = yg[j0:j1]
        les = lambda n, sl=slice(None): np.ma.filled(ds[n][sl, 0, j0:j1, i0:i1].astype('f4'), np.nan)
        acc = les('precipitation_amount_acc'); sacc = les('snowfall_amount_acc')
        ff = les('wind_speed'); dd = les('wind_direction'); fg = les('wind_speed_of_gust'); tw = les('altitude_of_isoTprimW_equal_0')
        # målgitter i Web Mercator (Leaflet strekker bildet lineært i mercator)
        bx = np.concatenate([X, X, np.full(len(Y), X[0]), np.full(len(Y), X[-1])])
        by = np.concatenate([np.full(len(X), Y[0]), np.full(len(X), Y[-1]), Y, Y])
        blon, blat = ll_fra_lcc.transform(bx, by)
        mx, my = ll2merc.transform(blon, blat)
        W = int(len(X) * 2.2); H = int(W * (my.max() - my.min()) / (mx.max() - mx.min()))
        gx = np.linspace(mx.min(), mx.max(), W); gy = np.linspace(my.max(), my.min(), H)
        GX, GY = np.meshgrid(gx, gy)
        glon, glat = merc2ll.transform(GX, GY)
        lx, ly = lcc.transform(glon, glat)
        fi = (lx - X[0]) / 2500.0; fj = (ly - Y[0]) / 2500.0
        inn = (fi >= 0) & (fi <= len(X) - 1) & (fj >= 0) & (fj <= len(Y) - 1)
        fi = np.clip(fi, 0, len(X) - 1.001); fj = np.clip(fj, 0, len(Y) - 1.001)
        ia = fi.astype(int); ja = fj.astype(int); wx = fi - ia; wy = fj - ja
        def resamp(a):
            v = (a[ja, ia] * (1 - wx) * (1 - wy) + a[ja, ia + 1] * wx * (1 - wy) + a[ja + 1, ia] * (1 - wx) * wy + a[ja + 1, ia + 1] * wx * wy)
            v[~inn] = np.nan; return v
        sw_lon, sw_lat = merc2ll.transform(mx.min(), my.min()); ne_lon, ne_lat = merc2ll.transform(mx.max(), my.max())
        meta['regioner'][rk] = {'navn': r['navn'], 'grenser': [[round(sw_lat, 5), round(sw_lon, 5)], [round(ne_lat, 5), round(ne_lon, 5)]]}
        def lagre(arr, prod, navn):
            img = Image.fromarray(farge(resamp(arr), prod, MEPS_PROD[prod][2]), 'RGBA')
            img = img.quantize(colors=32, method=Image.Quantize.FASTOCTREE)
            fil = f'meps/{rk}_{navn}.png'; img.save(os.path.join(ut, fil), optimize=True); filer.append(fil)
        for n, s in enumerate(steg3):
            lagre(acc[s] - acc[s - 3], 'rr3', f'rr3_{n:02d}')
            lagre(ff[s], 'ff', f'ff_{n:02d}')
            lagre(np.nanmax(fg[s - 2:s + 1], axis=0), 'fg', f'fg_{n:02d}')
            lagre(tw[s], 'tw0', f'tw0_{n:02d}')
        for n, (a, b) in enumerate(dager):
            lagre(acc[b] - acc[a], 'rr24', f'rr24_{n}')
            lagre(sacc[b] - sacc[a], 'sf24', f'sf24_{n}')
            lagre(np.nanmax(fg[a + 1:b + 1], axis=0), 'fg24', f'fg24_{n}')
        # vindpiler (grovt gitter)
        k = mc.get('vindpil_steg', 7)
        jj, ii = np.meshgrid(np.arange(k // 2, len(Y), k), np.arange(k // 2, len(X), k), indexing='ij')
        plon, plat = ll_fra_lcc.transform(X[ii], Y[jj])
        meta['vind'][rk] = {'ll': [[round(float(a), 2), round(float(b), 2)] for a, b in zip(plat.ravel(), plon.ravel())],
                            'steg': [[(int(round(float(dd[s][j, i]))) % 360 if np.isfinite(dd[s][j, i]) else -1) for j, i in zip(jj.ravel(), ii.ravel())] +
                                     [(int(round(float(ff[s][j, i]) * 10)) if np.isfinite(ff[s][j, i]) else -1) for j, i in zip(jj.ravel(), ii.ravel())] for s in steg3]}
    ds.close()
    open('kart_filer.txt', 'w').write('\n'.join('kart/' + f for f in sorted(filer)) + '\n')
    meta['filer'] = len(filer); meta['hentet'] = iso(NOW)
    return meta

# ---------------- terskler ----------------
def terskelserie(gser, prows, tdef):
    """gser: {dato: {rr,tm,sd,sdfsw,qsw,windSpeed10m24h06}} (seNorge, vegpunkt). prows: LF-timer i vegpunkt.
    Returnerer {dato: {enkelt:[{v,pass}], komb:{verdier,pass}, kilde, timer}} for alle døgn + D+1, D+2."""
    serie = {}
    for d, x in gser.items():
        serie[dt.date.fromisoformat(d)] = {'rr': x.get('rr'), 'nysno': x.get('sdfsw'), 'tm': x.get('tm'), 'sd': x.get('sd'),
                                           'qsw': x.get('qsw'), 'vind': x.get('windSpeed10m24h06'), 'kilde': 'obs'}
    if not serie: return {}
    dmax = max(serie)
    agg = {}
    for r in prows or []:
        t = dt.datetime.fromisoformat(r['t'].replace('Z', '+00:00')); k = dag06(t)
        if k <= dmax: continue
        a = agg.setdefault(k, {'rr': 0, 'nysno': 0, 'T': [], 'FF': [], 'timer': 0})
        a['timer'] += 1; rr = r.get('RR') or 0; a['rr'] += rr
        if r.get('T') is not None:
            a['T'].append(r['T'])
            if r['T'] <= 0.5: a['nysno'] += rr
        if r.get('FF') is not None: a['FF'].append(r['FF'])
    for k in sorted(agg)[:2]:
        a = agg[k]
        serie[k] = {'rr': round(a['rr'], 1), 'nysno': round(a['nysno'], 1), 'tm': round(sum(a['T']) / len(a['T']), 1) if a['T'] else None,
                    'sd': None, 'qsw': None, 'vind': round(sum(a['FF']) / len(a['FF']), 1) if a['FF'] else None, 'kilde': 'prog', 'timer': a['timer']}
    datoer = sorted(serie); idx = {d: i for i, d in enumerate(datoer)}
    def roll(dato, key, n, fn=sum):
        i = idx[dato]; vals = [serie[x][key] for x in datoer[max(0, i - n + 1):i + 1]]
        if len(vals) < n or any(v is None for v in vals): return None
        return round(fn(vals), 1)
    def var(dato, v):
        m = re.match(r'(nedbor|nysno)_(\d+)t_(mm|cm)', v)
        if m: return roll(dato, 'rr' if m.group(1) == 'nedbor' else 'nysno', int(m.group(2)) // 24)
        if v == 'temp_24t_C': return serie[dato]['tm']
        if v == 'temp_72t_C': return roll(dato, 'tm', 3, lambda x: sum(x) / len(x))
        if v == 'temp_maks_dogn_72t_C': return roll(dato, 'tm', 3, max)
        if v == 'snodybde_cm': return serie[dato]['sd']
        if v == 'endring_snodybde_72t_cm':
            i = idx[dato]
            if i < 3 or serie[dato]['sd'] is None or serie[datoer[i - 3]]['sd'] is None: return None
            return round(serie[dato]['sd'] - serie[datoer[i - 3]]['sd'], 1)
        if v == 'vann_ut_av_snodekke_72t_mm': return roll(dato, 'qsw', 3)
        if v == 'vind_24t_ms': return serie[dato]['vind']
        if v == 'vind_72t_ms': return roll(dato, 'vind', 3, lambda x: sum(x) / len(x))
        return None
    cmp = lambda x, op, t: None if x is None else (x >= t if op == '>=' else x < t)
    deler = re.findall(r'(\w+)\s*(>=|<)\s*([-\d.]+)', tdef['komb']['regel']); kop = tdef['komb']['operator']
    out = {}
    for dato in datoer:
        e = [{'v': var(dato, t['variabel'])} for t in tdef['enkelt']]
        for x, t in zip(e, tdef['enkelt']): x['pass'] = cmp(x['v'], t['operator'], t['verdi'])
        kv = [(var(dato, a), cmp(var(dato, a), o, float(b))) for a, o, b in deler]; ps = [p for _, p in kv]
        kp = None if any(p is None for p in ps) and not (kop == 'ELLER' and any(ps)) else (any(ps) if kop == 'ELLER' else all(ps))
        out[dato.isoformat()] = {'enkelt': e, 'komb': {'verdier': [v for v, _ in kv], 'pass': kp}, 'kilde': serie[dato]['kilde'],
                                 'timer': serie[dato].get('timer')}
    return out

# ---------------- kjør ----------------
start = time.time()
B = {'created': iso(NOW), 'd0': D0.isoformat(), 'frost': {}, 'frostmeta': {}, 'prog': {}, 'varsom': {}, 'terskel': {}, 'feil': [], 'status': {}}
progveg = {}
sns = [s['sn'] for r in cfg['strekninger'] for s in r['stasjoner'] if s.get('sn')]
stasjoner = [s for r in cfg['strekninger'] for s in r['stasjoner'] if s.get('sn') or s.get('kilde') in ('skredas', 'wyssen')]
if not FID: B['feil'].append('Frost: mangler FROST_CLIENT_ID (GitHub Secret)')
try: B['frostmeta'] = frost_meta(sns)
except Exception as e: B['feil'].append(f'frost-meta: {e}')

jobs = [('stasjon', s.get('sn') or s['id'], s) for s in stasjoner]
for r in cfg['strekninger']:
    d0 = r['delomr'][0]
    jobs.append(('varsom', r['id'], (d0['lat'], d0['lon'])))
    for d in r['delomr']:
        k = r['id'] + '|' + d['navn']
        jobs += [('gts', k, d), ('prog', k, d)]
        if d.get('veg'): jobs += [('gtsveg', k, d['veg']), ('progveg', k, d['veg'])]

def run(job):
    typ, key, a = job
    try:
        if typ == 'stasjon': return typ, key, stasjon(a), None
        if typ == 'gts': return typ, key, gts(a['lat'], a['lon'], ['rr', 'tm', 'sd', 'sdfsw'], FRA, D0), None
        if typ == 'gtsveg': return typ, key, gts(a['lat'], a['lon'], ['rr', 'tm', 'sd', 'sdfsw', 'qsw', 'windSpeed10m24h06'], FRA, D0), None
        if typ == 'prog': return typ, key, prog_losne(a), None
        if typ == 'progveg':
            upd, rows, _ = lf(a['lat'], a['lon'], a.get('moh')); return typ, key, {'upd': upd, 'rows': rows}, None
        if typ == 'varsom': return typ, key, varsom(a[0], a[1], max(FRA, D0 - dt.timedelta(days=400)), D0 + dt.timedelta(days=2)), None
    except Exception as e:
        return typ, key, None, f'{typ} {key}: {e}'

ok = {'stasjon': set(), 'gts': set(), 'gtsveg': set(), 'prog': set(), 'progveg': set(), 'varsom': set()}
with ThreadPoolExecutor(4) as ex:
    for typ, key, val, err in ex.map(run, jobs):
        if err: B['feil'].append(err); continue
        ok[typ].add(key)
        if typ == 'stasjon':
            B['frost'][key] = {k: val[k] for k in ('rows', 'siste', 'elementer')}
            ARK['frostdag'].setdefault(key, {}).update(val['dag'])
        elif typ in ('gts', 'gtsveg'):
            ARK[typ].setdefault(key, {}).update(val['serie'])
            ARK['meta'][typ + ':' + key] = {k: val[k] for k in ('E', 'N', 'flyttet_m', 'moh')}
        elif typ == 'prog': B['prog'][key] = val
        elif typ == 'progveg': progveg[key] = val
        elif typ == 'varsom': ARK['varsom'].setdefault(key, {}).update(val)

# rydd arkiv: bare datoer fra sesongstart
for grp in ('gts', 'gtsveg', 'frostdag', 'varsom'):
    for k in ARK[grp]:
        ARK[grp][k] = {d: v for d, v in sorted(ARK[grp][k].items()) if d >= (FRA.isoformat() if TILBAKE else SESONG.isoformat())}
ARK['til'] = D0.isoformat(); ARK['fra'] = (D0 - dt.timedelta(days=TILBAKE)).isoformat() if TILBAKE else SESONG.isoformat(); ARK['oppdatert'] = iso(NOW)

for r in cfg['strekninger']:
    if not cfg.get('terskel_aktiv', True) or not r.get('terskel'): continue
    for d in r['delomr']:
        k = r['id'] + '|' + d['navn']
        try: B['terskel'][k] = terskelserie(ARK['gtsveg'].get(k, {}), (progveg.get(k) or {}).get('rows'), r['terskel'])
        except Exception as e: B['feil'].append(f'terskel {k}: {e}')

# ---------------- skyggevarsel (regionalt varsel -> aktsomhet per skredpunkt) ----------------
SK_STAB = {'Naturlig': 'Svært dårlig', 'Svært lett å løse ut': 'Dårlig', 'Lett å løse ut': 'Dårlig',
           'Vanskelig å løse ut': 'Moderat', 'Svært vanskelig å løse ut': 'God'}   # som i Varsom-normaliseringen 2025/26
SK_UTB = {'Få bratte heng': 0, 'Noen bratte heng': 1, 'Mange bratte heng': 2}
SK_SMAT = {'Svært dårlig': [3, 4, 5], 'Dårlig': [2, 3, 4], 'Moderat': [1, 2, 2], 'God': [1, 1, 2]}
SK_RMAT = {2: {5: [0, 2, 3, 3], 4: [0, 1, 2, 3], 3: [0, 0, 1, 2], 2: [0, 0, 0, 1], 1: [0, 0, 0, 0]},
           1: {5: [0, 3, 3, 3], 4: [0, 1, 3, 3], 3: [0, 1, 1, 3], 2: [0, 0, 0, 3], 1: [0, 0, 0, 0]}}
SK_DIR = ['N', 'NØ', 'Ø', 'SØ', 'S', 'SV', 'V', 'NV']
def skyggevarsel(sk, dager):
    par = cfg['skygge']['parametre']; bz = par['hoyde_buffer_m']; tol = par['retning_toleranse_grader']
    adiff = lambda a, b: abs((a - b + 180) % 360 - 180)
    ut = {}
    for dato, w in sorted(dager.items()):
        res = {k: [0, []] for k in sk['enheter']}
        for p in w.get('problemer', []):
            eksp = [i for i, c in enumerate(str(p.get('eksp') or '')) if c == '1']
            h1, h2, fill = p.get('h1'), p.get('h2'), p.get('fill')
            lo, hi = -1e4, 1e4
            if h1 is not None:
                if fill == 1: lo = h1
                elif fill == 2: hi = h1
                elif fill in (3, 4): lo, hi = min(h1, h2 or h1), max(h1, h2 or h1)
            stab = SK_STAB.get(p.get('sens')); ub = SK_UTB.get(p.get('utbredelse'))
            try: D = int(str(p.get('storrelse') or '')[0])
            except ValueError: D = None
            if stab is None or ub is None or not D: continue
            S = SK_SMAT[stab][ub]
            rel = 'K' if str(p.get('ptype') or p.get('type') or '').startswith('Våt snø') else 'M'
            htxt = '' if h1 is None else (f'over {h1}' if fill == 1 else f'under {h1}' if fill == 2 else f'{min(h1, h2 or h1)}–{max(h1, h2 or h1)}')
            vin = {'h': htxt, 'eksp': ', '.join(SK_DIR[i] for i in eksp), 'sens': p.get('sens'), 'utb': p.get('utbredelse'), 'str': p.get('storrelse')}
            treff = {}
            for z0, z1, a, en, nr in sk['rader']:
                if z1 + bz < lo or z0 - bz > hi: continue
                if any(adiff(a, i * 45) <= tol for i in eksp): treff.setdefault(en, []).append(nr)
            for en, nrs in treff.items():
                e = sk['enheter'][en]
                if not e['mat']: res[en][1].append([p.get('ptype') or p.get('type'), S, D, rel, None, None, None, nrs, vin]); continue
                spes, K = e['mat'][rel][D - 1]
                if spes == 'UL': res[en][1].append([p.get('ptype'), S, D, rel, 'UL', None, None, nrs, vin]); continue
                K = int(str(K)[1]); nv = SK_RMAT[sk['modell']][S][K]
                res[en][1].append([p.get('ptype'), S, D, rel, spes, K, nv, nrs, vin]); res[en][0] = max(res[en][0], nv)
        ut[dato] = {'region': w.get('region'), 'utstedt': w.get('utstedt'), 'enheter': res}
    return ut
B['skygge'] = {}
for r in cfg['strekninger']:
    sk = (cfg.get('skygge') or {}).get('strekninger', {}).get(r['id'])
    if not sk: continue
    try: B['skygge'][r['id']] = skyggevarsel(sk, ARK['varsom'].get(r['id'], {}))
    except Exception as e: B['feil'].append(f'skyggevarsel {r["id"]}: {type(e).__name__}: {e}')


# ---------------- webkamera (offentlige bilder bakes inn som filer) ----------------
def webkamera(ut='kart/kamera'):
    from PIL import Image
    os.makedirs(ut, exist_ok=True); res = {}; filer = []
    def en(k):
        req = urllib.request.Request(k['u'], headers={'User-Agent': UA})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read(); lm = r.headers.get('Last-Modified')
        im = Image.open(io.BytesIO(data)).convert('RGB')
        if im.width > 1280: im = im.resize((1280, round(im.height * 1280 / im.width)))
        f = f"{ut}/{k['id']}.jpg"; im.save(f, quality=80)
        endret = dt.datetime.strptime(lm, '%a, %d %b %Y %H:%M:%S GMT').replace(tzinfo=dt.timezone.utc).isoformat() if lm else None
        return k['id'], {'fil': f, 'hentet': iso(NOW), 'endret': endret}
    ks = [k for v in (cfg.get('kamera') or {}).values() for k in v if not k.get('intern')]
    with ThreadPoolExecutor(6) as ex:
        for k, fut in [(k, ex.submit(en, k)) for k in ks]:
            try: i, v = fut.result(); res[i] = v; filer.append(v['fil'])
            except Exception as e: B['feil'].append(f"webkamera {k['n']}: {type(e).__name__}: {e}")
    if filer:
        gml = open('kart_filer.txt').read().split() if os.path.exists('kart_filer.txt') else []
        gml = [x for x in gml if not x.startswith('kart/kamera/')]
        open('kart_filer.txt', 'w').write('\n'.join(gml + sorted(filer)) + '\n')
    return res

# ---------------- meteogram (yr-stil) for utvalgte punkter i skredvarslingsplanene ----------------
def meteogram_pkt(p):
    q = f"lat={p['lat']:.4f}&lon={p['lon']:.4f}&altitude={round(p['moh'])}"
    j = get('https://api.met.no/weatherapi/locationforecast/2.0/complete?' + q)
    ts = j['properties']['timeseries']
    def r(v, n=1): return None if v is None else round(v, n)
    t1 = {k: [] for k in ('t', 'T', 'RR', 'RRx', 'FF', 'FG', 'DD', 'S')}
    for x in ts:
        n1 = x['data'].get('next_1_hours')
        if not n1: break
        i, n = x['data']['instant']['details'], n1['details']
        for k, v in (('t', x['time'][:13]), ('T', r(i.get('air_temperature'))), ('RR', r(n.get('precipitation_amount'))),
                     ('RRx', r(n.get('precipitation_amount_max'))), ('FF', r(i.get('wind_speed'))), ('FG', r(i.get('wind_speed_of_gust'))),
                     ('DD', r(i.get('wind_from_direction'), 0)), ('S', (n1.get('summary') or {}).get('symbol_code'))):
            t1[k].append(v)
    t6 = {k: [] for k in ('t', 'T', 'Tx', 'Tn', 'RR', 'RRx', 'FF', 'FG', 'DD', 'S')}
    for x in ts:
        n6 = x['data'].get('next_6_hours')
        if not n6 or int(x['time'][11:13]) % 6: continue
        i, n = x['data']['instant']['details'], n6['details']
        for k, v in (('t', x['time'][:13]), ('T', r(i.get('air_temperature'))), ('Tx', r(n.get('air_temperature_max'))), ('Tn', r(n.get('air_temperature_min'))),
                     ('RR', r(n.get('precipitation_amount'))), ('RRx', r(n.get('precipitation_amount_max'))), ('FF', r(i.get('wind_speed'))),
                     ('FG', r(i.get('wind_speed_of_gust'))), ('DD', r(i.get('wind_from_direction'), 0)), ('S', (n6.get('summary') or {}).get('symbol_code'))):
            t6[k].append(v)
    return {'upd': j['properties']['meta']['updated_at'], 't1': t1, 't6': t6}

def meteogrammer():
    res = {}
    pk = [(vid, i, p) for vid, L in (cfg.get('meteogram') or {}).items() for i, p in enumerate(L)]
    with ThreadPoolExecutor(4) as ex:
        for (vid, i, p), fut in [(x, ex.submit(meteogram_pkt, x[2])) for x in pk]:
            try: res.setdefault(vid, {})[str(i)] = fut.result()
            except Exception as e: B['feil'].append(f"meteogram {vid} {p['n']}: {type(e).__name__}: {e}")
    return res

try: B['meps'] = meps_kart()
except Exception as e: B['meps'] = None; B['feil'].append(f'MEPS-kart: {type(e).__name__}: {e}')
try: B['kamera'] = webkamera()
except Exception as e: B['kamera'] = {}; B['feil'].append(f'webkamera: {type(e).__name__}: {e}')
try: B['mgram'] = meteogrammer()
except Exception as e: B['mgram'] = {}; B['feil'].append(f'meteogram: {type(e).__name__}: {e}')
B['arkiv'] = {'fra': ARK['fra'], 'til': ARK['til'], 'gts': ARK['gts'], 'gtsveg': ARK['gtsveg'], 'frostdag': ARK['frostdag'],
              'varsom': ARK['varsom'], 'meta': ARK['meta']}
ndel = sum(len(r['delomr']) for r in cfg['strekninger']); nveg = sum(1 for r in cfg['strekninger'] for d in r['delomr'] if d.get('veg'))
tomme = [k for k, v in B['frost'].items() if not v['siste']]
B['status'] = {
    'frost': {'ok': len(ok['stasjon']), 'tot': len(stasjoner), 'tomme': tomme,
              'siste': max((v['siste'] for v in B['frost'].values() if v['siste']), default=None)},
    'gts': {'ok': len(ok['gts']) + len(ok['gtsveg']), 'tot': ndel + nveg, 'siste_dogn': D0.isoformat()},
    'prog': {'ok': len(ok['prog']) + len(ok['progveg']), 'tot': ndel + nveg,
             'modell': max((v['upd'] for v in list(B['prog'].values()) + list(progveg.values())), default=None)},
    'varsom': {'ok': len(ok['varsom']), 'tot': len(cfg['strekninger']),
               'utstedt': any(w.get('utstedt') for v in ARK['varsom'].values() for d, w in v.items() if d >= D0.isoformat())},
    'terskel': {'ok': sum(1 for v in B['terskel'].values() if v), 'tot': sum(len(r['delomr']) for r in cfg['strekninger'] if r.get('terskel'))},
    'arkiv': {'fra': ARK['fra'], 'til': ARK['til'], 'hentet_fra': FRA.isoformat()},
    'meps': {'kjoring': B['meps']['kjoring'], 'filer': B['meps']['filer']} if B.get('meps') else None,
    'sekunder': round(time.time() - start)}

cfg_pub = {k: v for k, v in cfg.items() if k not in ('frost_client_id', 'skredas', 'wyssen', 'skygge')}
cfg_pub['skygge'] = {'parametre': (cfg.get('skygge') or {}).get('parametre'), 'enheter': {k: {e: {'navn': x['navn'], 'del': x['del'], 'merk': x['merk']} for e, x in v['enheter'].items()} for k, v in (cfg.get('skygge') or {}).get('strekninger', {}).items()}}
html = open(MAL, encoding='utf-8').read()
html = html.replace('/*__CONFIG__*/null', json.dumps(cfg_pub, ensure_ascii=False, separators=(',', ':')))
html = html.replace('/*__BAKED__*/null', json.dumps(B, ensure_ascii=False, separators=(',', ':')))
open(UT, 'w', encoding='utf-8').write(html)
if JSON_UT: json.dump({'config': cfg_pub, 'baked': B}, open(JSON_UT, 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
if ARK_UT and not TILBAKE: skriv_arkiv(ARK, ARK_UT)
st = B['status']
print(f"Skrev {UT} ({len(html)//1024} kB): frost {st['frost']['ok']}/{st['frost']['tot']} (tomme {len(tomme)}), gts {st['gts']['ok']}/{st['gts']['tot']}, "
      f"prog {st['prog']['ok']}/{st['prog']['tot']}, varsom {st['varsom']['ok']}/{st['varsom']['tot']}, terskel {st['terskel']['ok']}/{st['terskel']['tot']}, "
      f"arkiv {ARK['fra']}–{ARK['til']} (hentet fra {FRA}), feil {len(B['feil'])}, {st['sekunder']} s")
for f in B['feil']: print('  FEIL', f)
# avslutningskode for arbeidsflyten: 2 = både Frost og prognose feilet helt (ikke publiser)
if st['frost']['ok'] == 0 and st['prog']['ok'] == 0: sys.exit(2)
