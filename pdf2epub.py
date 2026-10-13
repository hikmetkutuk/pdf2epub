#!/usr/bin/env python3
"""
pdf2epub — Taranmış (ya da kötü OCR'lı) Türkçe roman/öykü PDF'lerini temiz, akışkan EPUB'a çevirir.

Kullanım:
    python pdf2epub.py kitap.pdf --title "Sarı Sıcak" --author "Yaşar Kemal"

Ayrıntılar için README.md dosyasına bakın.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def hata(msg):
    print('\nHATA: ' + msg, file=sys.stderr)
    sys.exit(1)


fitz = np = Image = None
structure = epub_writer = None


# ---------------------------------------------------------------- bağımlılıklar
def load_dependencies():
    global fitz, np, Image, structure, epub_writer
    try:
        import pymupdf as _fitz  # PyMuPDF >= 1.24
    except ImportError:
        try:
            import fitz as _fitz
        except ImportError:
            hata("PyMuPDF kurulu değil. Şunu çalıştırın:  pip install -r requirements.txt")
    try:
        import numpy as _np
        from PIL import Image as _Image
    except ImportError:
        hata("Pillow/numpy kurulu değil. Şunu çalıştırın:  pip install -r requirements.txt")

    import structure as _structure
    import epub_writer as _epub_writer

    fitz = _fitz
    np = _np
    Image = _Image
    structure = _structure
    epub_writer = _epub_writer


def tesseract_bul(elle=None):
    adaylar = []
    if elle:
        adaylar.append(elle)
    w = shutil.which('tesseract')
    if w:
        adaylar.append(w)
    adaylar += [
        r'C:\Program Files\Tesseract-OCR\tesseract.exe',
        r'C:\Program Files (x86)\Tesseract-OCR\tesseract.exe',
        os.path.expandvars(r'%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe'),
        '/opt/homebrew/bin/tesseract', '/usr/local/bin/tesseract', '/usr/bin/tesseract',
    ]
    for a in adaylar:
        if a and os.path.isfile(a):
            return a
    hata("Tesseract OCR bulunamadı. README.md içindeki kurulum adımlarını izleyin\n"
         "(ya da yolunu --tesseract ile verin).")


def tessdata_ayari(tess):
    """Türkçe dil dosyasını bul. Yoksa script klasöründeki tessdata/ klasörüne bakar."""
    yerel = os.path.join(HERE, 'tessdata')
    if os.path.isfile(os.path.join(yerel, 'tur.traineddata')):
        return ['--tessdata-dir', yerel]
    out = subprocess.run([tess, '--list-langs'], capture_output=True, text=True,
                         encoding='utf-8', errors='replace')
    if 'tur' in (out.stdout + out.stderr).split():
        return []
    hata("Tesseract'ta Türkçe dil paketi (tur) yok.\n"
         "Çözüm: tur.traineddata dosyasını indirip bu klasördeki 'tessdata' klasörüne koyun\n"
         "(README.md > 'Türkçe dil paketi').")


# ---------------------------------------------------------------- sayfa işleme
def sayfa_araligi(s, n):
    sec = set()
    if not s:
        return sec
    for parca in s.split(','):
        parca = parca.strip()
        if not parca:
            continue
        if re.fullmatch(r'-\d+', parca):
            k = int(parca)
            sec.add(n + k + 1)  # -1 = son sayfa
            continue
        if '-' in parca:
            a, b = parca.split('-', 1)
            a = int(a) if a else 1
            b = int(b) if b else n
            sec.update(range(a, b + 1))
        else:
            sec.add(int(parca))
    return sec


def egiklik_duzelt(im):
    small = im.resize((max(1, im.width // 4), max(1, im.height // 4)))
    best = (0.0, -1.0)
    for a in np.arange(-4, 4.01, 0.2):
        r = np.array(small.rotate(a, fillcolor=255, resample=Image.BILINEAR)) < 128
        s = r.sum(1).astype(float)
        v = float(((s[1:] - s[:-1]) ** 2).sum())
        if v > best[1]:
            best = (float(a), v)
    if abs(best[0]) < 0.35:  # küçük açıları döndürmek bulanıklaştırır
        return im
    return im.rotate(best[0], fillcolor=255, resample=Image.BICUBIC)


def cilt_yeri(im):
    """Çift sayfalı taramada iki sayfayı ayıran sütunu bul."""
    a = np.array(im.resize((im.width // 4, im.height // 4))) < 128
    h, w = a.shape
    lo, hi = int(w * 0.38), int(w * 0.62)
    band = a[int(h * 0.1):int(h * 0.9), lo:hi].mean(0)
    if band.max() > 0.5:          # koyu cilt gölgesi çizgisi
        c = lo + int(band.argmax())
    else:                          # beyaz cilt boşluğu: en az mürekkepli bölge
        k = 15
        sm = np.convolve(band, np.ones(k) / k, mode='same')
        c = lo + int(sm.argmin())
    return c * 4


def tarama_bilgisi(page):
    """(dpi, siyah_beyaz_mi). Taranmış sayfada resmin kendi çözünürlüğü (300-600);
    metne dönüştürülmüş/dijital sayfada 300 dpi (OCR için en uygun harf boyu)."""
    try:
        best, bpc = 0, 8
        for info in page.get_image_info():
            x0, y0, x1, y1 = info['bbox']
            if (x1 - x0) * (y1 - y0) > 0.5 * page.rect.width * page.rect.height:
                d = info['width'] / ((x1 - x0) / 72.0)
                if d > best:
                    best, bpc = d, info.get('bpc', 8)
        if best:
            return int(min(600, max(300, round(best / 50) * 50))), bpc == 1
    except Exception:
        pass
    return 300, False


def sayfalari_hazirla(doc, img_dir, dpi, atla, yeniden):
    os.makedirs(img_dir, exist_ok=True)
    adlar = []
    n = len(doc)
    for i in range(n):
        no = i + 1
        if no in atla:
            continue
        hazir = sorted(f for f in os.listdir(img_dir) if f.startswith(f'p-{no:04d}'))
        if hazir and not yeniden:
            adlar += [f[:-4] for f in hazir]
            continue
        d, bilevel = tarama_bilgisi(doc[i])
        pix = doc[i].get_pixmap(dpi=dpi or d, colorspace=fitz.csGRAY)
        im = Image.frombytes('L', (pix.width, pix.height), pix.samples)
        if im.width > im.height * 1.1:
            c = cilt_yeri(im)
            parcalar = [('a', im.crop((0, 0, c, im.height))), ('b', im.crop((c, 0, im.width, im.height)))]
        else:
            parcalar = [('', im)]
        for ek, p in parcalar:
            ad = f'p-{no:04d}{ek}'
            p = egiklik_duzelt(p)
            if bilevel:  # siyah-beyaz taramayı döndürme sonrası yeniden keskinleştir
                p = p.point(lambda v: 255 if v > 128 else 0)
            p.save(os.path.join(img_dir, ad + '.png'))
            adlar.append(ad)
        print(f'\r  sayfalar hazırlanıyor: {no}/{n}', end='', flush=True)
    print()
    return adlar


def ocr(adlar, img_dir, tsv_dir, tess, tess_args, is_sayisi, yeniden):
    os.makedirs(tsv_dir, exist_ok=True)
    env = dict(os.environ, OMP_THREAD_LIMIT='1')
    yapilacak = [a for a in adlar if yeniden or not os.path.isfile(os.path.join(tsv_dir, a + '.tsv'))]
    toplam = len(yapilacak)
    if not toplam:
        print('  OCR: önceki sonuçlar kullanılıyor')
        return
    bitti = [0]
    t0 = time.time()

    def tek(ad):
        subprocess.run([tess, os.path.join(img_dir, ad + '.png'), os.path.join(tsv_dir, ad),
                        '-l', 'tur', '--psm', '4'] + tess_args + ['tsv'],
                       capture_output=True, env=env)
        bitti[0] += 1
        gecen = time.time() - t0
        kalan = gecen / bitti[0] * (toplam - bitti[0])
        print(f'\r  OCR: {bitti[0]}/{toplam}  (kalan ~{int(kalan // 60)} dk {int(kalan % 60)} sn)   ',
              end='', flush=True)

    with ThreadPoolExecutor(max_workers=is_sayisi) as ex:
        list(ex.map(tek, yapilacak))
    print()


def kapak_kaydet(doc, no, yol):
    pix = doc[no - 1].get_pixmap(dpi=150)
    im = Image.frombytes('RGB', (pix.width, pix.height), pix.samples) if pix.n >= 3 else \
        Image.frombytes('L', (pix.width, pix.height), pix.samples)
    im.save(yol)


KUNYE = re.compile(r'ISBN|Sertifika|Baskı\s*:|\d\.\s*[Bb]as[ıi]m|Yayınları|Yayınevi|Yayıncılık|Kapak\s+(tasarımı|resmi|düzeni)|'
                   r'[Ee]ditör|Düzelti|Dizgi|Faks|Tel\s*:|e-posta|www\.|http|[Yy]ayın hakları|İÇİNDEKİLER|İçindekiler|'
                   r'Matbaa|Neşriyat')


def kunye_mi(tsv):
    """Künye, içindekiler, arka kapak gibi kitabın metni olmayan sayfaları tanır."""
    satirlar = [' '.join(w['t'] for w in d['words']) for d in structure.read_lines(tsv)]
    if not satirlar:
        return True
    metin = '\n'.join(satirlar)
    if len(KUNYE.findall(metin)) >= 1 and len(satirlar) < 45:
        return True
    toc = sum(1 for s in satirlar if re.search(r'\D\s*[•.·…e]*\s*\d{1,3}\s*$', s) and len(s) < 60)
    return toc >= 4 and toc >= 0.5 * len(satirlar)


# ---------------------------------------------------------------- ana akış
def main():
    ap = argparse.ArgumentParser(description='Taranmış Türkçe kitap PDF\'ini EPUB\'a çevirir.')
    ap.add_argument('pdf', help='PDF dosyası')
    ap.add_argument('--title', dest='title', help='Kitap adı (verilmezse dosya adından)')
    ap.add_argument('--baslik', dest='title', help=argparse.SUPPRESS)
    ap.add_argument('--author', dest='author', default='', help='Yazar')
    ap.add_argument('--yazar', dest='author', help=argparse.SUPPRESS)
    ap.add_argument('--translator', dest='translator', default='', help='Çevirmen')
    ap.add_argument('--cevirmen', dest='translator', help=argparse.SUPPRESS)
    ap.add_argument('--publisher', dest='publisher', default='', help='Yayınevi')
    ap.add_argument('--yayinevi', dest='publisher', help=argparse.SUPPRESS)
    ap.add_argument('--skip', dest='skip', default='',
                    help='Atlanacak PDF sayfaları, örn. "2-8,234" ya da "-1" (son sayfa)')
    ap.add_argument('--atla', dest='skip', help=argparse.SUPPRESS)
    ap.add_argument('--cover-page', dest='cover_page', type=int, default=1,
                    help='Kapak olarak kullanılacak PDF sayfası (varsayılan 1)')
    ap.add_argument('--kapak', dest='cover_page', type=int, help=argparse.SUPPRESS)
    ap.add_argument('--output', dest='output', help='Çıktı EPUB yolu (varsayılan: PDF ile aynı klasör)')
    ap.add_argument('--cikti', dest='output', help=argparse.SUPPRESS)
    ap.add_argument('--config', dest='config', help='Kitaba özel düzeltmeler içeren JSON dosyası (isteğe bağlı)')
    ap.add_argument('--ayar', dest='config', help=argparse.SUPPRESS)
    ap.add_argument('--dpi', type=int, default=0, help='Görüntü çözünürlüğü (varsayılan: otomatik)')
    ap.add_argument('--jobs', dest='jobs', type=int, default=max(1, (os.cpu_count() or 2) - 1),
                    help='Aynı anda kaç sayfa OCR edilsin')
    ap.add_argument('--is', dest='jobs', type=int, help=argparse.SUPPRESS)
    ap.add_argument('--tesseract', help='tesseract programının yolu (otomatik bulunamazsa)')
    ap.add_argument('--force', dest='force', action='store_true', help='Önceki ara sonuçları yok sayıp her şeyi baştan yap')
    ap.add_argument('--yeniden', dest='force', action='store_true', help=argparse.SUPPRESS)
    ap.add_argument('--verbose', dest='verbose', action='store_true', help='Ayrıntılı çıktı (kurtarılan satırlar vb.)')
    ap.add_argument('--ayrinti', dest='verbose', action='store_true', help=argparse.SUPPRESS)
    args = ap.parse_args()

    pdf = os.path.abspath(args.pdf)
    if not os.path.isfile(pdf):
        hata(f'Dosya bulunamadı: {pdf}')
    load_dependencies()

    kok = os.path.splitext(pdf)[0]
    calisma = kok + '_work'
    img_dir = os.path.join(calisma, 'pages')
    tsv_dir = os.path.join(calisma, 'ocr')
    if args.force and os.path.isdir(calisma):
        shutil.rmtree(calisma)

    tess = tesseract_bul(args.tesseract)
    tess_args = tessdata_ayari(tess)

    doc = fitz.open(pdf)
    n = len(doc)
    meta_title = (doc.metadata or {}).get('title') or ''
    baslik = args.title or (meta_title.strip() if len(meta_title.strip()) > 2 else
                            os.path.basename(kok).replace('_', ' '))
    atla = sayfa_araligi(args.skip, n)
    atla.add(args.cover_page)  # kapak resim olarak eklenir, metin olarak değil
    print(f'"{baslik}" — {n} sayfa')

    print('1/4 Sayfalar görüntüye çevriliyor')
    adlar = sayfalari_hazirla(doc, img_dir, args.dpi, atla, args.force)

    print('2/4 Türkçe OCR')
    ocr(adlar, img_dir, tsv_dir, tess, tess_args, args.jobs, args.force)

    print('3/4 Kitap yapısı kuruluyor (paragraf, bölüm, şiir, dipnot)')
    structure.IMG_DIR = img_dir
    structure.TESSERACT = tess
    structure.VERBOSE = args.verbose
    if tess_args:
        # boşluk kurtarma adımı da aynı dil klasörünü kullansın
        structure.TESSERACT_ARGS = tess_args
    files = [os.path.join(tsv_dir, a + '.tsv') for a in adlar]
    files = [f for f in files if os.path.isfile(f)]
    # baştaki ve sondaki künye / içindekiler / arka kapak sayfalarını ele
    bas, son = max(3, int(len(files) * 0.12)), max(2, int(len(files) * 0.05))
    atilan = [f for i, f in enumerate(files) if (i < bas or i >= len(files) - son) and kunye_mi(f)]
    if atilan:
        print('  metin dışı sayfalar atlandı (künye/içindekiler/arka kapak): ' +
              ', '.join(os.path.basename(f)[2:-4].lstrip('0') for f in atilan))
    files = [f for f in files if f not in atilan]
    structure.HEADERS[:] = structure.detect_headers(files)
    sayfalar = [p for p in (structure.page(f) for f in files) if p]
    bloklar = structure.build(structure.normalize(sayfalar))

    # İlk bölümden önceki künye/önsöz kırıntılarını at (azsa)
    ilk = next((i for i, b in enumerate(bloklar) if b[0] == 'chapter'), None)
    if ilk:
        once = [b for b in bloklar[:ilk] if b[0] == 'p']
        if len(once) < 25:
            print(f'  ilk bölümden önceki {len(once)} paragraf (künye vb.) atıldı')
            bloklar = bloklar[ilk:]
    # Kitap adını büyük harfle tekrarlayan kısa ön sayfa (iç kapak, yazar tanıtımı) bölüm sayılmasın
    chs = [i for i, b in enumerate(bloklar) if b[0] == 'chapter']
    if len(chs) >= 2:
        t0 = bloklar[chs[0]][1] if len(bloklar[chs[0]]) > 1 else None
        kisa = sum(1 for b in bloklar[chs[0]:chs[1]] if b[0] == 'p') <= 6
        if t0 and kisa and t0.isupper() and structure.norm_hdr(t0) in (
                structure.norm_hdr(baslik), *(structure.norm_hdr(bloklar[i][1] or '') for i in chs[1:])):
            print(f'  ön sayfa ("{t0}") bölüm olarak alınmadı')
            bloklar = bloklar[chs[1]:]
    with open(os.path.join(calisma, 'blocks.json'), 'w', encoding='utf-8') as f:
        json.dump(bloklar, f, ensure_ascii=False, indent=0)

    print('4/4 EPUB yazılıyor')
    kapak = os.path.join(calisma, 'cover.png')
    kapak_kaydet(doc, args.cover_page, kapak)
    cfg = dict(title=baslik, author=args.author, translator=args.translator, publisher=args.publisher,
               cover=kapak, out=os.path.abspath(args.output) if args.output else kok + '.epub')
    if args.config:
        with open(args.config, encoding='utf-8') as f:
            cfg.update(json.load(f))
    out, notlar, bolumler = epub_writer.run(cfg, bloklar)
    paragraf = sum(1 for b in bloklar if b[0] == 'p')
    siir = sum(1 for b in bloklar if b[0] == 'verse')
    print(f'\nTamam: {out}')
    print(f'  {bolumler} bölüm, {paragraf} paragraf, {siir} şiir bloğu, {notlar} dipnot')
    basliklar = [b[1] for b in bloklar if b[0] == 'chapter' and len(b) > 1 and b[1]]
    if basliklar:
        print('  bölümler: ' + ', '.join(basliklar[:30]) + (' ...' if len(basliklar) > 30 else ''))
    print(f'  (ara dosyalar: {calisma} — tekrar çalıştırınca OCR baştan yapılmaz)')


if __name__ == '__main__':
    main()
