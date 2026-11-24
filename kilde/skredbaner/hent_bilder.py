"""Henter hendelsesbildene (A1, G12 …) fra PowerPoint-fila og lagrer dem som site/bilder/dim/<etikett>.jpg.
Bruk: python3 hent_bilder.py bilder.pptx utmappe skredstorrelser.xlsx"""
import re, io, json, sys, os, openpyxl
from pptx import Presentation
from PIL import Image
P = Presentation(sys.argv[1]); ut = sys.argv[2]
os.makedirs(ut, exist_ok=True)
LAB = re.compile(r'^[A-Za-zÆØÅæøå]{1,4}\d{1,2}$')
def flat(shapes, tf=lambda x, y, w, h: (x, y, w, h)):
    for sh in shapes:
        if sh.shape_type == 6:
            x = sh._element.grpSpPr.xfrm
            gx, gy, gw, gh = tf(sh.left, sh.top, sh.width, sh.height)
            cox, coy, cex, cey = x.chOff.x, x.chOff.y, x.chExt.cx, x.chExt.cy
            def t2(a, b, c, d, gx=gx, gy=gy, gw=gw, gh=gh, cox=cox, coy=coy, cex=cex, cey=cey):
                sx, sy = gw / cex, gh / cey
                return gx + (a - cox) * sx, gy + (b - coy) * sy, c * sx, d * sy
            yield from flat(sh.shapes, t2)
        elif sh.left is not None:
            yield sh, tf(sh.left, sh.top, sh.width, sh.height)
xl = {str(r[0]).strip(): r for r in openpyxl.load_workbook(sys.argv[3]).active.iter_rows(min_row=2, values_only=True) if r[0]}
regid = lambda s: (re.search(r'(\d{4,})', str(s or '')) or [None, None])[1]
res, logg = {}, []
for si, s in enumerate(P.slides, 1):
    items = list(flat(s.shapes))
    def erbilde(sh):
        try: return sh.image is not None
        except Exception: return False
    pics = [(sh, b) for sh, b in items if erbilde(sh)]
    labs = [(sh.text_frame.text.strip(), b) for sh, b in items if sh.shape_type == 17 and sh.has_text_frame and LAB.match(sh.text_frame.text.strip())]
    urls = [(regid(sh.text_frame.text), b) for sh, b in items if sh.has_text_frame and 'regobs' in sh.text_frame.text.lower()]
    for lab, (x, y, w, h) in labs:
        cx, cy = x + w / 2, y + h / 2
        def d(p):
            px, py, pw, ph = p[1]
            dx = max(px - cx, 0, cx - (px + pw)); dy = max(py - cy, 0, cy - (py + ph)); return dx * dx + dy * dy
        pic = min(pics, key=d) if pics else None
        if not pic: logg.append(f'{lab}: ingen bilde på slide {si}'); continue
        px, py, pw, ph = pic[1]
        inn = [u for u, (ux, uy, uw, uh) in urls if px - 2e5 <= ux + uw / 2 <= px + pw + 2e5 and py <= uy + uh / 2 <= py + ph + 3e5]
        fasit = regid(xl.get(lab, [None] * 9)[8]) if lab in xl else None
        ok = (not inn or not fasit or fasit in inn)
        im = Image.open(io.BytesIO(pic[0].image.blob)).convert('RGB')
        W, H = im.size; c = pic[0]
        im = im.crop((int(W * c.crop_left), int(H * c.crop_top), int(W * (1 - c.crop_right)), int(H * (1 - c.crop_bottom))))
        im.thumbnail((1000, 1000)); navn = re.sub(r'[^\w]', '_', lab)
        im.save(f'{ut}/{navn}.jpg', quality=80, optimize=True)
        res[lab] = {'fil': f'{navn}.jpg', 'slide': si, 'urls': inn, 'xlsx': fasit, 'ok': ok}
        if not ok: logg.append(f'{lab} (slide {si}): regObs i bildet {inn} ≠ Excel {fasit}')
print(len(res), 'bilder;', 'mangler i pptx:', sorted(set(xl) - set(res)))
print('ikke i xlsx:', sorted(set(res) - set(xl)))
print('\n'.join(logg))
