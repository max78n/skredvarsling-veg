#!/usr/bin/env python3
"""Lager kartdata til fanen Varslinger fra Prosjektinfo-data og bakgrunnsoppsettet.

Bruk: python3 lag_kartdata.py prosjektinfo/data.json config.json site/varsler/kart_data.js
- data.json: løsneområder (poly), skredløp (lop), delområde (del), skredpunkt, skredtårn og veglinje per strekning
  (samme grunnlag som Prosjektinfo, tabell 4/5 i skredvarslingsplanene).
- config.json: bare 'bakgrunn' og 'detalj' (bildefiler + grenser) leses. Nøkler i config.json skrives ikke ut.
Kjøres når geometri eller bakgrunnsbilder endres.
"""
import json, sys

info, cfg_fil, ut = sys.argv[1:4]
D = json.load(open(info, encoding='utf-8'))
C = json.load(open(cfg_fil, encoding='utf-8'))
r5 = lambda p: [round(p[0], 5), round(p[1], 5)]

strek = []
for s in D['strekninger']:
    dels, lop, rader = [], {}, []
    for r in s['rader']:
        if r['del'] not in dels: dels.append(r['del'])
        if r.get('lop'): lop.setdefault(r.get('lopId') or r['nr'], {'del': r['del'], 'l': [r5(p) for p in r['lop']]})
        rader.append({'nr': r['nr'], 'navn': r['navn'], 'del': r['del'], 'sp': r.get('skredpunkt'),
                      'z': [r.get('zmin'), r.get('zmax')], 'poly': [[r5(p) for p in ring] for ring in (r.get('poly') or [])]})
    # skredpunkt -> delområde(r) via løsneområdene som hører til
    sp = []
    for p in s['skredpunkt']:
        d = sorted({r['del'] for r in s['rader'] if r.get('skredpunkt') == p['navn']})
        sp.append({'navn': p['navn'], 'll': r5(p['ll']), 'del': d})
    strek.append({'id': s['id'], 'navn': s['navn'], 'del': dels, 'rader': rader, 'lop': list(lop.values()), 'sp': sp,
                  'taarn': [{'navn': t['navn'], 'll': r5(t['ll']), 'plan': t.get('status') != 'installert'} for t in s.get('taarn', [])],
                  'veg': [[r5(p) for p in l] for l in s.get('veglinje', [])]})

out = {'strekninger': strek, 'bakgrunn': C.get('bakgrunn', {}), 'detalj': C.get('detalj', {}),
       'kilde': 'Løsneområder og skredløp: SVV, sammenslått eksport 15.11.2026; delområder: tabell 4 i skredvarslingsplanene; bakgrunn © Kartverket'}
with open(ut, 'w', encoding='utf-8') as f:
    f.write('window.KART=' + json.dumps(out, ensure_ascii=False, separators=(',', ':')) + ';\n')
print(ut, sum(len(s['rader']) for s in strek), 'løsneområder,', len(strek), 'strekninger')
