#!/usr/bin/env python3
"""Engangsskript: lager kartbildene som sida trenger (Kartverket topograatone/topo).
- bakgrunn/*.jpg og detalj/*.jpg med nøyaktig de grensene som står i info.html
- fliser/*.js (flisepakker) etter samme plan som claude/vaerpakke/lag_fliser.py
Bruk: python3 lag_kartbilder.py config.json info.html utmappe
Trengs bare på nytt hvis geometri eller strekninger endres."""
import json, math, io, os, re, sys, time, base64, urllib.request
from concurrent.futures import ThreadPoolExecutor
from PIL import Image, ImageEnhance

CFG, INFO, UT = sys.argv[1:4]
UA = 'SVV-vaerpakke-veg/0.9 (kartbilder)'
cfg = json.load(open(CFG, encoding='utf-8'))
info = open(INFO, encoding='utf-8').read()

def get(u):
    for i in range(5):
        try:
            with urllib.request.urlopen(urllib.request.Request(u, headers={'User-Agent': UA}), timeout=60) as r: return r.read()
        except Exception as e:
            if '404' in str(e): return None
            time.sleep(1.5 * (i + 1))
    return None

def px(lon, lat, z):
    n = 256 * 2 ** z
    return (lon + 180) / 360 * n, (1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2 * n

def bilde(s, w, n, e, lag, z):
    x0, y0 = px(w, n, z); x1, y1 = px(e, s, z)
    tx0, ty0, tx1, ty1 = int(x0 // 256), int(y0 // 256), int(x1 // 256), int(y1 // 256)
    can = Image.new('RGB', ((tx1 - tx0 + 1) * 256, (ty1 - ty0 + 1) * 256), (214, 228, 238))
    jobb = [(tx, ty) for tx in range(tx0, tx1 + 1) for ty in range(ty0, ty1 + 1)]
    def hent(t):
        b = get(f'https://cache.kartverket.no/v1/wmts/1.0.0/{lag}/default/webmercator/{z}/{t[1]}/{t[0]}.png')
        return t, (Image.open(io.BytesIO(b)).convert('RGBA') if b else None)
    with ThreadPoolExecutor(8) as ex:
        for (tx, ty), im in ex.map(hent, jobb):
            if im: can.paste(im, ((tx - tx0) * 256, (ty - ty0) * 256), im)
    return can.crop((round(x0 - tx0 * 256), round(y0 - ty0 * 256), round(x1 - tx0 * 256), round(y1 - ty0 * 256)))

def zoom_for(s, w, n, e, maxpx, zmin):
    z = 16
    while z > zmin:
        x0, y0 = px(w, n, z); x1, y1 = px(e, s, z)
        if x1 - x0 <= maxpx and y1 - y0 <= maxpx: break
        z -= 1
    return z

def json_etter(nokkel):
    i = info.index(f'"{nokkel}": ') + len(nokkel) + 4
    return json.JSONDecoder().raw_decode(info[i:])[0]

# ---- bakgrunn ----
os.makedirs(f'{UT}/bakgrunn', exist_ok=True)
for k, b in json_etter('bakgrunn').items():
    (s, w), (n, e) = b['grenser']
    lag = 'topo' if k == 'norge' else 'topograatone'
    z = zoom_for(s, w, n, e, 1400 if k == 'norge' else 2000, 3)
    im = bilde(s, w, n, e, lag, z)
    if lag == 'topograatone': im = ImageEnhance.Contrast(im).enhance(1.35)
    im.save(f'{UT}/bakgrunn/{b["fil"]}', 'JPEG', quality=78, optimize=True, progressive=True)
    print('bakgrunn', b['fil'], im.size, 'z', z, flush=True)

# ---- detalj ----
os.makedirs(f'{UT}/detalj', exist_ok=True)
for k, L in json_etter('detalj').items():
    for b in L:
        (s, w), (n, e) = b['grenser']
        im = ImageEnhance.Contrast(bilde(s, w, n, e, 'topograatone', b['z'])).enhance(1.3)
        im.save(f'{UT}/detalj/{b["fil"]}', 'JPEG', quality=74, optimize=True, progressive=True)
        print('detalj', b['fil'], im.size, 'z', b['z'], flush=True)

# ---- flisepakker (samme plan som lag_fliser.py) ----
def tile(lon, lat, z):
    n = 2 ** z; lat = max(min(lat, 85), -85)
    return (lon + 180) / 360 * n, (1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2 * n
def fliser_for(P, z, buf):
    ut = set()
    for la, lo in P:
        dla = buf / 111320; dlo = buf / (111320 * math.cos(math.radians(la)))
        x0, y0 = tile(lo - dlo, la + dla, z); x1, y1 = tile(lo + dlo, la - dla, z)
        for x in range(int(x0), int(x1) + 1):
            for y in range(int(y0), int(y1) + 1): ut.add((z, x, y))
    return ut
BUF_A = {8: 30000, 9: 25000, 10: 18000, 11: 10000, 12: 6000, 13: 3000, 14: 1500, 15: 700}
BUF_B = {8: 30000, 9: 25000, 10: 15000, 11: 6000, 12: 3000, 13: 1500, 14: 600}
def punkterAB(s):
    A, B = [], []
    for d in s['delomr']:
        A.append((d['lat'], d['lon']))
        for p in d.get('polys', []): A += [tuple(c) for c in p['c']]
        for l in d.get('skredlop', []): A += [tuple(c) for c in l]
    for l in (s.get('veglinje') or {}).get('linjer', []): B += [tuple(c) for c in l]
    for st in s.get('stasjoner', []):
        if st.get('lat'): B.append((st['lat'], st['lon']))
    return A, B
plan = {}
for s in cfg['strekninger']:
    A, B = punkterAB(s); T = set()
    for z, b in BUF_A.items(): T |= fliser_for(A, z, b)
    for z, b in BUF_B.items(): T |= fliser_for(B, z, b)
    plan[s['id']] = sorted(T)
T = set()
for z in range(4, 8):
    x0, y0 = tile(3.5, 71.6, z); x1, y1 = tile(31.5, 57.6, z)
    T |= {(z, x, y) for x in range(int(x0), int(x1) + 1) for y in range(int(y0), int(y1) + 1)}
plan['norge'] = sorted(T)
def hent(t):
    z, x, y = t
    b = get(f'https://cache.kartverket.no/v1/wmts/1.0.0/topograatone/default/webmercator/{z}/{y}/{x}.png')
    if not b: return t, None
    im = Image.open(io.BytesIO(b)).convert('RGBA'); bg = Image.new('RGB', im.size, (214, 226, 234)); bg.paste(im, mask=im.split()[3])
    bg = ImageEnhance.Contrast(bg).enhance(1.3); o = io.BytesIO(); bg.save(o, 'JPEG', quality=70, optimize=True); return t, o.getvalue()
os.makedirs(f'{UT}/fliser', exist_ok=True)
for navn, liste in plan.items():
    if navn not in (cfg.get('fliser') or {}): continue
    idx = {}
    with ThreadPoolExecutor(10) as ex:
        for (z, x, y), b in ex.map(hent, liste):
            if b: idx[f'{z}/{x}/{y}'] = base64.b64encode(b).decode()
    open(f'{UT}/fliser/{navn}.js', 'w').write('FLISPAKKE(' + json.dumps(navn) + ',' + json.dumps(idx, separators=(',', ':')) + ');')
    print('fliser', navn, len(idx), 'av', cfg['fliser'][navn].get('n'), flush=True)
