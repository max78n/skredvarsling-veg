#!/usr/bin/env python3
"""Importerer historiske aktsomhetsnivå (sesongoversikter i Excel) til arkivet for fanen Skredvarsel.

Bruk: python3 importer_historikk.py site/varsler <fil.xlsx> [...]
Gjenkjenner:
- «…_aktsomhetsniva_….xlsx» med fanen «Alle år (langformat)» (Sesong, Dato, Skredløp, Nivå): ett dagsnivå per delområde.
  «Alle løp (samlet)» gjelder alle delområder i skredvarslingsplanen.
- «Rv13_Vikafjellet_sesongoversikt_….xlsx» med fanene «Varsel – alle» og «Aktsomhet per dag»: ett arkivinnslag per
  utsendt varsel (gyldig frå → erstatta), dagsnivå per delområde, og skredproblem per skredløp som tekst.
Innslagene merkes hist=True (ingen originalvarsel). Kjøres på nytt uten duplikater (id = hash av prosjekt+tid).
"""
import sys, os, re, json, hashlib, datetime as dt
from collections import defaultdict
import openpyxl
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hent_varsler as H

OSLO = H.OSLO
NIV = {'grøn': 0, 'grønt': 0, 'gul': 1, 'gult': 1, 'oransje': 2, 'raud': 3, 'rødt': 3, 'rød': 3}
DAG = ['Mandag', 'Tirsdag', 'Onsdag', 'Torsdag', 'Fredag', 'Lørdag', 'Søndag']
PLAN_DEL = {'rv15': ['Skoresvingane', 'Skjeringsdalen', 'Grasdalen', 'Breidalen']}
RV13_DEL = {'Elvakroken': 'Kvassdalen', 'Halsabakkane': 'Kvassdalen', 'Kvassdalen': 'Kvassdalen', 'Svaet': 'Kvassdalen',
            'Skjelingavatnet': 'Skjelingavatnet – Ygnisdalshamrane', 'Ygnisdalshamrane': 'Skjelingavatnet – Ygnisdalshamrane',
            'Skredene': 'Skredene'}
SONE_DEL = {'Kvassdalen': 'Kvassdalen', 'Skjelingavatnet-Ygnisdalen': 'Skjelingavatnet – Ygnisdalshamrane', 'Skredene': 'Skredene'}


def niv(x):
    if x is None: return None
    if isinstance(x, (int, float)): return int(x) - 1 if 1 <= x <= 4 else None
    return NIV.get(str(x).strip().lower())


def dato(x):
    if isinstance(x, dt.datetime): return x
    m = re.match(r'(\d{1,2})\.(\d{1,2})\.(\d{4})(?:\s+(\d{1,2}):(\d{2}))?', str(x or ''))
    if not m: return None
    d, mo, y, h, mi = m.groups()
    return dt.datetime(int(y), int(mo), int(d), int(h or 0), int(mi or 0))


def lbl(d): return f'{DAG[d.weekday()]} {d.day}.{d.month}'


def iso(d): return d.replace(tzinfo=OSLO).isoformat()


def innslag(prosjekt, start, slutt, slots, soner, nivaa, tekst=None, tittel=None, kilde=''):
    vid = hashlib.sha1(f'hist:{prosjekt}:{start.isoformat()}'.encode()).hexdigest()[:12]
    return {'id': vid, 'prosjekt': prosjekt, 'strekning': H.NAVN[prosjekt], 'tittel': tittel or f'Skredvarsel for {H.NAVN[prosjekt]}',
            'gyldig_fra': iso(start), 'neste': iso(slutt), 'mottatt': None, 'utarbeidet': '', 'kontrollert': '',
            'tekst': tekst or {}, 'kilde': kilde, 'soner': soner, 'slots': [[lbl(s), f'{s.hour:02d}'] for s in slots],
            'tider': [iso(s) for s in slots], 'nivaa': nivaa, 'maks': max((x for r in nivaa for x in r if x is not None), default=None),
            'tiltak': {}, 'meteogram': None, 'hist': True}


def langformat(fil, prosjekt):
    wb = openpyxl.load_workbook(fil, read_only=True, data_only=True)
    ws = wb['Alle år (langformat)']
    dager = defaultdict(dict)
    for r in ws.iter_rows(min_row=5, values_only=True):
        _, sesong, d, lop, n = (list(r) + [None] * 5)[:5]
        d = dato(d); n = niv(n)
        if d is None or n is None or not lop: continue
        if 'samlet' in str(lop).lower():
            for p in PLAN_DEL.get(prosjekt, [str(lop)]): dager[d][p] = n
        else:
            dager[d][str(lop).strip()] = n
    ut = []
    for d in sorted(dager):
        z = dager[d]; soner = list(z)
        ut.append(innslag(prosjekt, d, d + dt.timedelta(days=1), [d], soner, [[z[s]] for s in soner],
                          tittel=f'Aktsomhetsnivå {H.NAVN[prosjekt]}', kilde='historikk:' + os.path.basename(fil)))
    return ut


def vikafjellet(fil):
    wb = openpyxl.load_workbook(fil, read_only=True, data_only=True)
    pd = {}
    rr = list(wb['Aktsomhet per dag'].iter_rows(values_only=True)); hode = rr[0]
    kol = [(i, h.replace(' (nivå)', '')) for i, h in enumerate(hode) if h and h.endswith('(nivå)')]
    for r in rr[1:]:
        d = dato(r[0])
        if d: pd[d.date()] = {navn: niv(r[i]) for i, navn in kol}
    prob = defaultdict(lambda: defaultdict(list))
    for r in list(wb['Skredproblem – detaljert'].iter_rows(values_only=True))[2:]:
        if not r[1]: continue
        lop, ret, zmin, zmax, sp, lag, stab = r[7], r[8], r[9], r[10], r[11], r[12], r[13]
        if not sp or str(sp).lower() == 'ingen': continue
        t = f'{sp} ({ret}, {zmin}–{zmax} moh.' + (f'; svakt lag: {lag}' if lag else '') + (f'; stabilitet: {stab}' if stab else '') + ')'
        if t not in prob[r[1]][lop]: prob[r[1]][lop].append(t)
    rp = list(wb['Aktsomhet per skredløp'].iter_rows(values_only=True)); h2 = rp[1]
    PL = {x[0]: {h2[i]: niv(x[i]) for i in range(2, len(h2))} for x in rp[2:] if x[0]}
    ut = []
    for r in list(wb['Varsel – alle'].iter_rows(values_only=True))[2:]:
        if not r[0] or not str(r[0]).startswith('VIK'): continue
        start, til, ers = dato(r[3]), dato(r[4]), dato(r[5])
        slutt = ers or til
        if not start or not slutt: continue
        slots = [start]; d = dt.datetime.combine(start.date() + dt.timedelta(days=1), dt.time())
        while d < slutt: slots.append(d); d += dt.timedelta(days=1)
        soner = [n for _, n in kol]
        nivaa = [[(pd.get(s.date()) or {}).get(z) for s in slots] for z in soner]
        # dager uten dagsnivå: bruk varselets nivå per skredløp (høyeste i delområdet)
        lops = PL.get(r[0], {})
        for zi, z in enumerate(soner):
            alt = [n for l, n in lops.items() if n is not None and RV13_DEL.get(l) == SONE_DEL.get(z)]
            for si in range(len(slots)):
                if nivaa[zi][si] is None and alt: nivaa[zi][si] = max(alt)
        tekst = {}
        if prob.get(r[0]):
            tekst['Skredproblem'] = '\n'.join(f'{l}: ' + '; '.join(v) for l, v in prob[r[0]].items())
        ut.append(innslag('rv13', start, slutt, slots, soner, nivaa, tekst, kilde=f'historikk:{os.path.basename(fil)}:{r[0]}'))
    return ut


def main():
    ut_dir, filer = sys.argv[1], sys.argv[2:]
    arkiv = H.last_arkiv(ut_dir); kjent = {v['id'] for v in arkiv['varsler']}; nye = 0
    for f in filer:
        b = os.path.basename(f).lower()
        if 'vikafjellet' in b and 'sesongoversikt' in b: liste = vikafjellet(f)
        else:
            p = 'rv15' if 'stryn' in b else 'e134' if 'haukeli' in b else 'rv13' if 'vika' in b else None
            if not p: print('ukjent fil', f); continue
            liste = langformat(f, p)
        for v in liste:
            if v['id'] not in kjent: arkiv['varsler'].append(v); kjent.add(v['id']); nye += 1
        print(f, len(liste), 'innslag')
    H.skriv(arkiv, ut_dir)
    print(nye, 'nye, totalt', len(arkiv['varsler']))


if __name__ == '__main__':
    main()
