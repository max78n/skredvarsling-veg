#!/usr/bin/env python3
"""Henter utsendte skredvarsel fra Gmail (IMAP) og lager data til fanen Varslinger.

Bruk:
  GitHub/IMAP:  python3 hent_varsler.py --ut site/varsler
                (miljøvariabler: GMAIL_BRUKER, GMAIL_APP_PASSORD, VARSEL_AVSENDER [, GMAIL_MAPPE])
  Test lokalt:  python3 hent_varsler.py --ut site/varsler --fil varsel1.html varsel2.eml

Skriver:
  <ut>/varsler.json      arkiv (fasit, leses inn igjen ved neste kjøring)
  <ut>/varsler_data.js   window.VARSLER = {...} for fanen (lastes med <script>)
  <ut>/v/<id>.html       originalvarselet slik det ble sendt
Bare e-post fra VARSEL_AVSENDER (kommaseparert liste) tas med.
"""
import argparse, datetime as dt, email, email.policy, hashlib, html, imaplib, json, os, re, sys
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from zoneinfo import ZoneInfo

OSLO = ZoneInfo('Europe/Oslo')
PROSJ = {'E134': 'e134', 'RV13': 'rv13', 'RV15': 'rv15', 'FV53': 'tyin', 'RV85': 'rv85',
         'FV7570': 'beisfjord', 'RV94': 'rv94', 'E69': 'e69'}
NAVN = {'e134': 'E134 Haukelifjell', 'rv13': 'Rv13 Vikafjellet', 'rv15': 'Rv15 Strynefjellet', 'tyin': 'Fv53 Tyin',
        'rv85': 'Rv85 Gullesfjord', 'beisfjord': 'Fv7570 Beisfjorden', 'rv94': 'Rv94 Hammerfest', 'e69': 'E69 Nordkapp'}
NIVA = ['Grønt', 'Gult', 'Oransje', 'Rødt']


# ---------- JS-objekt (DATA = {...}) til JSON ----------
def js_til_json(s):
    """Gjør et enkelt JS-objektliteral om til JSON: kommentarer, '…'-strenger, nøkler uten anførselstegn, hengekomma."""
    ut, i, n = [], 0, len(s)
    while i < n:
        c = s[i]
        if s.startswith('/*', i):
            i = s.find('*/', i + 2); i = n if i < 0 else i + 2; continue
        if s.startswith('//', i):
            i = s.find('\n', i); i = n if i < 0 else i; continue
        if c in '\'"':
            j, buf = i + 1, []
            while j < n and s[j] != c:
                if s[j] == '\\' and j + 1 < n:
                    buf.append(s[j + 1]); j += 2; continue
                buf.append(s[j]); j += 1
            ut.append(json.dumps(''.join(buf), ensure_ascii=False)); i = j + 1; continue
        m = re.match(r'[A-Za-z_$][\w$]*', s[i:])
        if m:
            w = m.group(0); k = i + len(w)
            while k < n and s[k] in ' \t\r\n': k += 1
            ut.append(json.dumps(w) if k < n and s[k] == ':' else w); i += len(w); continue
        ut.append(c); i += 1
    t = ''.join(ut)
    t = re.sub(r',(\s*[}\]])', r'\1', t)
    return json.loads(t)


def finn_data(src):
    m = re.search(r'var\s+DATA\s*=\s*\{', src)
    if not m: return None
    i, dybde, start = m.end() - 1, 0, m.end() - 1
    q = None
    while i < len(src):
        c = src[i]
        if q:
            if c == '\\': i += 2; continue
            if c == q: q = None
        elif c in '\'"': q = c
        elif src.startswith('/*', i): i = src.find('*/', i) + 2; continue
        elif c == '{': dybde += 1
        elif c == '}':
            dybde -= 1
            if dybde == 0: return js_til_json(src[start:i + 1])
        i += 1
    return None


# ---------- tekstfelt fra HTML ----------
class Tekst(HTMLParser):
    def __init__(self):
        super().__init__(); self.st = []; self.h1 = ''; self.meta = ''; self.felt = {}; self.over = ''; self.cap = None; self.buf = []
    def handle_starttag(self, tag, a):
        a = dict(a); kl = a.get('class') or ''
        if tag in ('h1', 'h2', 'h3'): self.cap = tag; self.buf = []
        elif tag == 'p' and ('skv-meta' in kl or 'skv-txt' in kl): self.cap = 'meta' if 'skv-meta' in kl else 'txt'; self.buf = []
        elif tag == 'br' and self.cap: self.buf.append('\n')
    def handle_endtag(self, tag):
        if not self.cap: return
        t = re.sub(r'[ \t]+', ' ', ''.join(self.buf)).strip()
        if self.cap == 'h1' and tag == 'h1': self.h1 = t
        elif self.cap in ('h2', 'h3') and tag == self.cap: self.over = t
        elif self.cap == 'meta' and tag == 'p': self.meta = t
        elif self.cap == 'txt' and tag == 'p':
            if self.over: self.felt[self.over] = (self.felt.get(self.over, '') + '\n' + t).strip()
        else: return
        self.cap = None
    def handle_data(self, d):
        if self.cap: self.buf.append(d)



# ---------- e-postformatet fra varslingsverktøyet (tabeller, ingen DATA-blokk) ----------
NIVNAVN = {'grønt': 0, 'gult': 1, 'oransje': 2, 'rødt': 3}

class Tre(HTMLParser):
    """Enkel DOM: hvert element = {'tag','a','barn','far'}; tekst lagres som str i 'barn'."""
    TOMME = {'br', 'img', 'meta', 'hr', 'input', 'link'}
    def __init__(self):
        super().__init__(convert_charrefs=True); self.rot = {'tag': 'rot', 'a': {}, 'barn': [], 'far': None}; self.cur = self.rot
    def handle_starttag(self, tag, a):
        e = {'tag': tag, 'a': dict(a), 'barn': [], 'far': self.cur}; self.cur['barn'].append(e)
        if tag not in self.TOMME: self.cur = e
    def handle_endtag(self, tag):
        c = self.cur
        while c is not self.rot and c['tag'] != tag: c = c['far']
        if c is not self.rot: self.cur = c['far']
    def handle_data(self, d): self.cur['barn'].append(d)

def alle(e, tag):
    for b in e['barn']:
        if isinstance(b, dict):
            if b['tag'] == tag: yield b
            yield from alle(b, tag)

def tekst(e, li=False):
    ut = []
    def gaa(x):
        for b in x['barn']:
            if isinstance(b, str): ut.append(b)
            elif b['tag'] == 'br': ut.append('\n')
            elif b['tag'] == 'li': ut.append('\n• '); gaa(b)
            elif b['tag'] == 'p': ut.append('\n'); gaa(b); ut.append('\n')
            else: gaa(b)
    gaa(e)
    t = re.sub(r'[ \t ]+', ' ', ''.join(ut))
    return re.sub(r'\n\s*\n+', '\n', '\n'.join(l.strip() for l in t.split('\n'))).strip()

def celler(tr):  # direkte td/th i raden
    return [b for b in tr['barn'] if isinstance(b, dict) and b['tag'] in ('td', 'th')]

def rader_i(tab):
    ut = []
    def gaa(x):
        for b in x['barn']:
            if isinstance(b, dict):
                if b['tag'] == 'tr': ut.append(b)
                elif b['tag'] in ('tbody', 'thead'): gaa(b)
    gaa(tab); return ut

DAG = re.compile(r'(mandag|tirsdag|onsdag|torsdag|fredag|lørdag|søndag)\s+\d{1,2}\.\d{1,2}', re.I)

def tolk_epost(src):
    t = Tre(); t.feed(src)
    ut = {'soner': [], 'slots': [], 'nivaa': [], 'tekst': {}, 'tiltak': {}, 'meta': {}}
    # tittel
    for p in alle(t.rot, 'p'):
        x = tekst(p)
        if x.lower().startswith('skredvarsel for'): ut['tittel'] = x; break
    # nøkkel: verdi (celle som slutter på ':' etterfulgt av verdicelle)
    for tr in alle(t.rot, 'tr'):
        c = celler(tr)
        for i in range(len(c) - 1):
            k = tekst(c[i])
            if k.endswith(':') and len(k) < 30: ut['meta'][k[:-1].strip()] = tekst(c[i + 1])
    # aktsomhetstabell
    for tab in alle(t.rot, 'table'):
        rr = rader_i(tab)
        if len(rr) < 3 or not any(DAG.search(tekst(c)) for c in celler(rr[0])): continue
        kol = []  # kolonne -> dag
        for c in celler(rr[0]):
            kol += [tekst(c) if DAG.search(tekst(c)) else None] * int(c['a'].get('colspan') or 1)
        tid = celler(rr[1]); idx = []
        for i, c in enumerate(tid):
            x = tekst(c)
            if re.fullmatch(r'\d{1,2}', x) and i < len(kol) and kol[i]: idx.append(i); ut['slots'].append([kol[i], x.zfill(2)])
        for tr in rr[2:]:
            c = celler(tr)
            if len(c) <= max(idx or [0]): continue
            navn = tekst(c[0])
            niv = [NIVNAVN.get(tekst(c[i]).lower()) for i in idx]
            if navn and all(n is not None for n in niv): ut['soner'].append(navn); ut['nivaa'].append(niv)
        break
    # underoverskrift (grå rad #cacaca) -> innhold (lys rad #eaeaea)
    rows = list(alle(t.rot, 'tr')); sist = None
    for tr in rows:
        bg = (tr['a'].get('bgcolor') or '').lower()
        if bg == '#cacaca': sist = tekst(tr)
        elif bg == '#eaeaea' and sist:
            img = next(alle(tr, 'img'), None); a = next(alle(tr, 'a'), None)
            if sist.lower() == 'meteogram' and img is not None:
                ut['meteogram'] = {'img': img['a'].get('src'), 'href': a['a'].get('href') if a else None}
            elif sist.lower() != 'anbefalt aktsomhetsnivå':
                ut['tekst'][sist] = tekst(tr)
            sist = None
    # tiltakstabell (Nivå | Typisk tiltaksnivå | Trafikkavvikling | Drift)
    for tab in alle(t.rot, 'table'):
        rr = rader_i(tab)
        if not rr or [tekst(c).lower() for c in celler(rr[0])][:2] != ['nivå', 'typisk tiltaksnivå']: continue
        hode = [tekst(c) for c in celler(rr[0])]
        for tr in rr[1:]:
            c = [tekst(x) for x in celler(tr)]
            if c and c[0].lower() in NIVNAVN: ut['tiltak'][str(NIVNAVN[c[0].lower()])] = dict(zip(hode[1:], c[1:]))
        break
    return ut

def tid(s):
    m = re.search(r'(\d{1,2})\.(\d{1,2})\.(\d{4})\s*(?:kl\.?\s*)?(\d{1,2})[:.](\d{2})', s or '')
    if not m: return None
    d, mo, y, h, mi = map(int, m.groups())
    return dt.datetime(y, mo, d, h, mi, tzinfo=OSLO).isoformat()


def slot_tider(data, gyldig):
    """Tidspunkt (ISO) for hver kolonne. Dagetiketten har ikke år; årsskifte håndteres."""
    if not gyldig: return []
    g = dt.datetime.fromisoformat(gyldig); ut = []
    for dag, kl in data.get('slots', []):
        m = re.search(r'(\d{1,2})\.(\d{1,2})', dag)
        if not m: ut.append(None); continue
        d, mo = int(m.group(1)), int(m.group(2)); y = g.year + (1 if mo < g.month - 6 else 0)
        ut.append(dt.datetime(y, mo, d, int(kl), tzinfo=OSLO).isoformat())
    return ut


def tolk(src, mottatt=None, kilde=''):
    data = finn_data(src)
    if data:  # malformatet (skredvarsel_web.html)
        p = Tekst(); p.feed(src)
        tittel, meta, felt = p.h1, p.meta, p.felt
        gyldig = tid(re.search(r'Gyldig fra[^·]*', meta).group(0)) if 'Gyldig fra' in meta else None
        neste = tid(re.search(r'Neste varsel[^·]*', meta).group(0)) if 'Neste varsel' in meta else None
        pers = dict(re.findall(r'(Utarbeidet|Kontrollert) av ([^·]+)', meta))
        utarb, kontr = pers.get('Utarbeidet', ''), pers.get('Kontrollert', '')
        soner, slots, lev, tiltak, meteo = data.get('zones', []), data.get('slots', []), data.get('levels') or [], {}, None
    else:  # e-postformatet
        e = tolk_epost(src); m = e['meta']
        tittel, felt = e.get('tittel', ''), e['tekst']
        gyldig, neste = tid(m.get('Gyldig fra')), tid(m.get('Neste varsel innen'))
        utarb, kontr = m.get('Utarbeidet av', ''), m.get('Kontrollert av', '')
        soner, slots, lev, tiltak, meteo = e['soner'], e['slots'], e['nivaa'], e['tiltak'], e.get('meteogram')
    mk = re.search(r'\b(E|Rv|Fv)\.?\s*(\d+)', tittel, re.I)
    kode = (mk.group(1) + mk.group(2)).upper() if mk else ''
    v = {'prosjekt': PROSJ.get(kode, ''), 'strekning': NAVN.get(PROSJ.get(kode, ''), tittel.replace('Skredvarsel for', '').strip()),
         'tittel': tittel, 'gyldig_fra': gyldig, 'neste': neste, 'mottatt': mottatt,
         'utarbeidet': (utarb or '').strip(), 'kontrollert': (kontr or '').strip(), 'tekst': felt, 'kilde': kilde,
         'soner': soner, 'slots': slots, 'tider': slot_tider({'slots': slots}, gyldig), 'nivaa': lev,
         'maks': max((x for r in lev for x in r), default=None), 'tiltak': tiltak, 'meteogram': meteo}
    if not lev: v['feil'] = 'Fant ikke aktsomhetstabellen – bare originalen vises'
    return v


def html_fra_epost(raw):
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    del_ = msg.get_body(preferencelist=('html',))
    return msg, (del_.get_content() if del_ else None)


def avsender_ok(msg, tillatt):
    fra = (msg.get('From') or '').lower()
    return not tillatt or any(a in fra for a in tillatt)


# ---------- arkiv ----------
def last_arkiv(ut):
    f = os.path.join(ut, 'varsler.json')
    if os.path.exists(f):
        with open(f, encoding='utf-8') as fh: return json.load(fh)
    return {'varsler': []}


def legg_til(arkiv, ut, v, src, nokkel):
    vid = hashlib.sha1(nokkel.encode()).hexdigest()[:12]
    if any(x['id'] == vid for x in arkiv['varsler']): return False
    v['id'] = vid
    os.makedirs(os.path.join(ut, 'v'), exist_ok=True)
    with open(os.path.join(ut, 'v', vid + '.html'), 'w', encoding='utf-8') as fh: fh.write(src)
    arkiv['varsler'].append(v); return True


def skriv(arkiv, ut, innebygd=False):
    arkiv['varsler'].sort(key=lambda x: (x.get('gyldig_fra') or '', x.get('mottatt') or ''), reverse=True)
    arkiv['hentet'] = dt.datetime.now(OSLO).isoformat(timespec='minutes')
    with open(os.path.join(ut, 'varsler.json'), 'w', encoding='utf-8') as fh: json.dump(arkiv, fh, ensure_ascii=False, indent=1)
    side = dict(arkiv)
    if innebygd:  # forhåndsvisning uten egne filer: legg originalene inn i data
        side = json.loads(json.dumps(arkiv))
        for x in side['varsler']:
            with open(os.path.join(ut, 'v', x['id'] + '.html'), encoding='utf-8') as fh: x['html'] = fh.read()
    with open(os.path.join(ut, 'varsler_data.js'), 'w', encoding='utf-8') as fh:
        fh.write('window.VARSLER=' + json.dumps(side, ensure_ascii=False).replace('</', '<\\/') + ';\n')


def hent_imap(arkiv, ut, tillatt):
    bruker, pw = os.environ['GMAIL_BRUKER'], os.environ['GMAIL_APP_PASSORD']
    mappe = os.environ.get('GMAIL_MAPPE', 'INBOX')
    kjent = {x.get('kilde') for x in arkiv['varsler']}
    nye = 0
    with imaplib.IMAP4_SSL('imap.gmail.com') as im:
        im.login(bruker, pw); im.select('"' + mappe + '"', readonly=True)
        krit = ['ALL'] if not tillatt else (['FROM', '"' + tillatt[0] + '"'] if len(tillatt) == 1 else ['ALL'])
        _, ids = im.uid('SEARCH', None, *krit)
        for uid in ids[0].split():
            _, h = im.uid('FETCH', uid, '(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)])')
            mid = email.message_from_bytes(h[0][1]).get('Message-ID', '').strip() or ('uid:' + uid.decode())
            if mid in kjent: continue
            _, d = im.uid('FETCH', uid, '(RFC822)')
            msg, src = html_fra_epost(d[0][1])
            if not src or not avsender_ok(msg, tillatt) or 'skredvarsel' not in src.lower(): continue
            try: mott = parsedate_to_datetime(msg['Date']).astimezone(OSLO).isoformat(timespec='minutes')
            except Exception: mott = None
            v = tolk(src, mott, mid)
            if legg_til(arkiv, ut, v, src, mid): nye += 1
    return nye


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ut', required=True)
    ap.add_argument('--fil', nargs='*', help='lokale .html/.eml-filer i stedet for IMAP')
    ap.add_argument('--innebygd', action='store_true', help='legg originalene inn i varsler_data.js (forhåndsvisning)')
    a = ap.parse_args()
    os.makedirs(a.ut, exist_ok=True)
    arkiv = last_arkiv(a.ut)
    tillatt = [x.strip().lower() for x in os.environ.get('VARSEL_AVSENDER', '').split(',') if x.strip()]
    if a.fil is not None:
        nye = 0
        for f in a.fil:
            raw = open(f, 'rb').read()
            if f.lower().endswith('.eml'):
                msg, src = html_fra_epost(raw); mott = parsedate_to_datetime(msg['Date']).astimezone(OSLO).isoformat(timespec='minutes')
            else:
                src, mott = raw.decode('utf-8'), None
            v = tolk(src, mott, 'fil:' + os.path.basename(f))
            nye += legg_til(arkiv, a.ut, v, src, 'fil:' + hashlib.sha1(raw).hexdigest())
    else:
        if not tillatt: sys.exit('VARSEL_AVSENDER mangler: oppgi avsenderadressen(e) til varslingsverktøyet')
        nye = hent_imap(arkiv, a.ut, tillatt)
    skriv(arkiv, a.ut, a.innebygd)
    print(f'{nye} nye varsel, {len(arkiv["varsler"])} totalt')


if __name__ == '__main__':
    main()
