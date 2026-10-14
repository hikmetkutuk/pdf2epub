#!/usr/bin/env python3
"""
pdf2epub — Taranmış (ya da kötü OCR'lı) Türkçe roman/öykü PDF'lerini temiz, akışkan EPUB'a çevirir.

Kullanım:
    python pdf2epub.py kitap.pdf --title "Sarı Sıcak" --author "Yaşar Kemal"

Ayrıntılar için README.md dosyasına bakın.
"""

import argparse
import importlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, NoReturn

for stream in (sys.stdout, sys.stderr):
    reconfigure = getattr(stream, 'reconfigure', None)
    if reconfigure:
        reconfigure(encoding='utf-8')

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def fail(msg) -> NoReturn:
    print('\nHATA: ' + msg, file=sys.stderr)
    sys.exit(1)


fitz: Any = None
np: Any = None
Image: Any = None
structure: Any = None
epub_writer: Any = None


# ---------------------------------------------------------------- bağımlılıklar
def load_dependencies():
    global fitz, np, Image, structure, epub_writer
    try:
        _fitz = importlib.import_module('pymupdf')  # PyMuPDF >= 1.24
    except ImportError:
        try:
            _fitz = importlib.import_module('fitz')
        except ImportError:
            fail("PyMuPDF kurulu değil. Şunu çalıştırın:  pip install -r requirements.txt")
    try:
        _np = importlib.import_module('numpy')
        _Image = importlib.import_module('PIL.Image')
    except ImportError:
        fail("Pillow/numpy kurulu değil. Şunu çalıştırın:  pip install -r requirements.txt")

    _structure = importlib.import_module('structure')
    _epub_writer = importlib.import_module('epub_writer')

    fitz = _fitz
    np = _np
    Image = _Image
    structure = _structure
    epub_writer = _epub_writer


def find_tesseract(manual_path=None):
    adaylar = []
    if manual_path:
        adaylar.append(manual_path)
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
    fail("Tesseract OCR bulunamadı. README.md içindeki kurulum adımlarını izleyin\n"
         "(ya da yolunu --tesseract ile verin).")


def tessdata_args(tess):
    """Türkçe dil dosyasını bul. Yoksa script klasöründeki tessdata/ klasörüne bakar."""
    yerel = os.path.join(HERE, 'tessdata')
    if os.path.isfile(os.path.join(yerel, 'tur.traineddata')):
        return ['--tessdata-dir', yerel]
    out = subprocess.run([tess, '--list-langs'], capture_output=True, text=True,
                         encoding='utf-8', errors='replace', check=False)
    if 'tur' in (out.stdout + out.stderr).split():
        return []
    fail("Tesseract'ta Türkçe dil paketi (tur) yok.\n"
         "Çözüm: tur.traineddata dosyasını indirip bu klasördeki 'tessdata' klasörüne koyun\n"
         "(README.md > 'Türkçe dil paketi').")


# ---------------------------------------------------------------- sayfa işleme
def parse_relative_page(part, page_count):
    return page_count + int(part) + 1  # -1 = son sayfa


def parse_page_interval(part, page_count):
    start, end = part.split('-', 1)
    start = int(start) if start else 1
    end = int(end) if end else page_count
    return range(start, end + 1)


def parse_page_selector(part, page_count):
    if re.fullmatch(r'-\d+', part):
        return [parse_relative_page(part, page_count)]
    if '-' in part:
        return parse_page_interval(part, page_count)
    return [int(part)]


def parse_page_range(value, page_count):
    selected = set()
    for part in (value or '').split(','):
        part = part.strip()
        if part:
            selected.update(parse_page_selector(part, page_count))
    return selected


def deskew_image(im):
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


def binding_split_x(im):
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


def scan_info(page):
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
    except (AttributeError, KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
        print(f'  uyarı: sayfa tarama bilgisi okunamadı ({exc})', file=sys.stderr)
    return 300, False


def existing_page_names(img_dir, no):
    return sorted(f for f in os.listdir(img_dir) if f.startswith(f'p-{no:04d}'))


def render_page_image(page, dpi):
    d, bilevel = scan_info(page)
    pix = page.get_pixmap(dpi=dpi or d, colorspace=fitz.csGRAY)
    im = Image.frombytes('L', (pix.width, pix.height), pix.samples)
    return im, bilevel


def split_page_image(im):
    if im.width <= im.height * 1.1:
        return [('', im)]
    c = binding_split_x(im)
    return [('a', im.crop((0, 0, c, im.height))), ('b', im.crop((c, 0, im.width, im.height)))]


def save_page_part(img_dir, no, ek, im, bilevel):
    ad = f'p-{no:04d}{ek}'
    im = deskew_image(im)
    if bilevel:  # siyah-beyaz taramayı döndürme sonrası yeniden keskinleştir
        im = im.point(lambda v: 255 if v > 128 else 0)
    im.save(os.path.join(img_dir, ad + '.png'))
    return ad


def prepare_pages(doc, img_dir, dpi, skip_pages, force):
    os.makedirs(img_dir, exist_ok=True)
    adlar = []
    n = len(doc)
    for i in range(n):
        no = i + 1
        if no in skip_pages:
            continue
        hazir = existing_page_names(img_dir, no)
        if hazir and not force:
            adlar += [f[:-4] for f in hazir]
            continue
        im, bilevel = render_page_image(doc[i], dpi)
        parcalar = split_page_image(im)
        for ek, p in parcalar:
            adlar.append(save_page_part(img_dir, no, ek, p, bilevel))
        print(f'\r  sayfalar hazırlanıyor: {no}/{n}', end='', flush=True)
    print()
    return adlar


def ocr(adlar, img_dir, tsv_dir, tess, tess_args, is_sayisi, yeniden):
    os.makedirs(tsv_dir, exist_ok=True)
    env = {**os.environ, 'OMP_THREAD_LIMIT': '1'}
    yapilacak = [a for a in adlar if yeniden or not os.path.isfile(os.path.join(tsv_dir, a + '.tsv'))]
    toplam = len(yapilacak)
    if not toplam:
        print('  OCR: önceki sonuçlar kullanılıyor')
        return
    bitti = [0]
    t0 = time.time()

    def run_one(ad):
        subprocess.run([tess, os.path.join(img_dir, ad + '.png'), os.path.join(tsv_dir, ad),
                        '-l', 'tur', '--psm', '4'] + tess_args + ['tsv'],
                       capture_output=True, env=env, check=False)
        bitti[0] += 1
        gecen = time.time() - t0
        kalan = gecen / bitti[0] * (toplam - bitti[0])
        print(f'\r  OCR: {bitti[0]}/{toplam}  (kalan ~{int(kalan // 60)} dk {int(kalan % 60)} sn)   ',
              end='', flush=True)

    with ThreadPoolExecutor(max_workers=is_sayisi) as ex:
        list(ex.map(run_one, yapilacak))
    print()


def save_cover(doc, no, yol):
    pix = doc[no - 1].get_pixmap(dpi=150)
    im = Image.frombytes('RGB', (pix.width, pix.height), pix.samples) if pix.n >= 3 else \
        Image.frombytes('L', (pix.width, pix.height), pix.samples)
    im.save(yol)


KUNYE = re.compile(r'ISBN|Sertifika|Baskı\s*:|\d\.\s*[Bb]as[ıi]m|Yayınları|Yayınevi|Yayıncılık|Kapak\s+(tasarımı|resmi|düzeni)|'
                   r'[Ee]ditör|Düzelti|Dizgi|Faks|Tel\s*:|e-posta|www\.|http|[Yy]ayın hakları|İÇİNDEKİLER|İçindekiler|'
                   r'Matbaa|Neşriyat')


def is_front_or_back_matter(tsv):
    """Künye, içindekiler, arka kapak gibi kitabın metni olmayan sayfaları tanır."""
    satirlar = [' '.join(w['t'] for w in d['words']) for d in structure.read_lines(tsv)]
    if not satirlar:
        return True
    metin = '\n'.join(satirlar)
    if len(KUNYE.findall(metin)) >= 1 and len(satirlar) < 45:
        return True
    toc = sum(1 for s in satirlar if re.search(r'\D\s*[•.·…e]*\s*\d{1,3}\s*$', s) and len(s) < 60)
    return toc >= 4 and toc >= 0.5 * len(satirlar)


def build_parser():
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
    return ap


def prepare_paths(args):
    pdf = os.path.abspath(args.pdf)
    if not os.path.isfile(pdf):
        fail(f'Dosya bulunamadı: {pdf}')

    root = os.path.splitext(pdf)[0]
    work_dir = root + '_work'
    paths = {
        'pdf': pdf,
        'root': root,
        'work_dir': work_dir,
        'img_dir': os.path.join(work_dir, 'pages'),
        'tsv_dir': os.path.join(work_dir, 'ocr'),
    }
    if args.force and os.path.isdir(work_dir):
        shutil.rmtree(work_dir)
    return paths


def book_title(args, doc, root):
    meta_title = (doc.metadata or {}).get('title') or ''
    if args.title:
        return args.title
    if len(meta_title.strip()) > 2:
        return meta_title.strip()
    return os.path.basename(root).replace('_', ' ')


def image_and_ocr(doc, args, paths, title, tess, tess_args):
    n = len(doc)
    skip = parse_page_range(args.skip, n)
    skip.add(args.cover_page)  # kapak resim olarak eklenir, metin olarak değil
    print(f'"{title}" — {n} sayfa')

    print('1/4 Sayfalar görüntüye çevriliyor')
    names = prepare_pages(doc, paths['img_dir'], args.dpi, skip, args.force)

    print('2/4 Türkçe OCR')
    ocr(names, paths['img_dir'], paths['tsv_dir'], tess, tess_args, args.jobs, args.force)
    return names


def text_files_from_ocr(names, tsv_dir):
    return [f for f in (os.path.join(tsv_dir, name + '.tsv') for name in names) if os.path.isfile(f)]


def filter_non_text_pages(files):
    bas, son = max(3, int(len(files) * 0.12)), max(2, int(len(files) * 0.05))
    dropped = [f for i, f in enumerate(files) if (i < bas or i >= len(files) - son) and is_front_or_back_matter(f)]
    if dropped:
        print('  metin dışı sayfalar atlandı (künye/içindekiler/arka kapak): ' +
              ', '.join(os.path.basename(f)[2:-4].lstrip('0') for f in dropped))
    return [f for f in files if f not in dropped]


def page_number_from_file(path):
    match = re.search(r'p-(\d+)', os.path.basename(path))
    return int(match.group(1)) if match else None


def pdf_reference_lines(doc, files):
    references = {}
    for path in files:
        page_no = page_number_from_file(path)
        if not page_no or page_no > len(doc):
            continue
        lines = [structure.clean_reference_line(line) for line in doc[page_no - 1].get_text().splitlines()]
        lines = [line for line in lines if line]
        if lines:
            references[os.path.basename(path)] = lines
    return references


def build_blocks_from_files(files, img_dir, tess, tess_args, verbose, doc=None):
    structure.IMG_DIR = img_dir
    structure.TESSERACT = tess
    structure.VERBOSE = verbose
    if tess_args:
        # boşluk kurtarma adımı da aynı dil klasörünü kullansın
        structure.TESSERACT_ARGS = tess_args

    files = filter_non_text_pages(files)
    structure.REFERENCE_LINES = pdf_reference_lines(doc, files) if doc is not None else {}
    structure.HEADERS[:] = structure.detect_headers(files)
    pages = [p for p in (structure.page(f) for f in files) if p]
    structure.normalize(pages)
    return structure.build(pages)


def drop_short_preface(blocks):
    first = next((i for i, b in enumerate(blocks) if b[0] == 'chapter'), None)
    if not first:
        return blocks
    before = [b for b in blocks[:first] if b[0] == 'p']
    if len(before) < 25:
        print(f'  ilk bölümden önceki {len(before)} paragraf (künye vb.) atıldı')
        return blocks[first:]
    return blocks


def drop_duplicate_title_page(blocks, title):
    chapters = [i for i, b in enumerate(blocks) if b[0] == 'chapter']
    if len(chapters) < 2:
        return blocks

    first_title = blocks[chapters[0]][1] if len(blocks[chapters[0]]) > 1 else None
    short_first = sum(1 for b in blocks[chapters[0]:chapters[1]] if b[0] == 'p') <= 6
    later_titles = (structure.norm_hdr(blocks[i][1] or '') for i in chapters[1:])
    known_titles = (structure.norm_hdr(title), *later_titles)
    if first_title and short_first and first_title.isupper() and structure.norm_hdr(first_title) in known_titles:
        print(f'  ön sayfa ("{first_title}") bölüm olarak alınmadı')
        return blocks[chapters[1]:]
    return blocks


def build_book_structure(doc, names, paths, tess, tess_args, args, title):
    print('3/4 Kitap yapısı kuruluyor (paragraf, bölüm, şiir, dipnot)')
    files = text_files_from_ocr(names, paths['tsv_dir'])
    blocks = build_blocks_from_files(files, paths['img_dir'], tess, tess_args, args.verbose, doc)
    blocks = drop_short_preface(blocks)
    blocks = drop_duplicate_title_page(blocks, title)
    with open(os.path.join(paths['work_dir'], 'blocks.json'), 'w', encoding='utf-8') as f:
        json.dump(blocks, f, ensure_ascii=False, indent=0)
    return blocks


def epub_config(args, paths, title, cover):
    cfg = {
        'title': title,
        'author': args.author,
        'translator': args.translator,
        'publisher': args.publisher,
        'cover': cover,
        'out': os.path.abspath(args.output) if args.output else paths['root'] + '.epub',
        'verbose': args.verbose,
    }
    if args.config:
        with open(args.config, encoding='utf-8') as f:
            cfg.update(json.load(f))
    return cfg


def write_output(doc, args, paths, title, blocks):
    print('4/4 EPUB yazılıyor')
    cover = os.path.join(paths['work_dir'], 'cover.png')
    save_cover(doc, args.cover_page, cover)
    out, notes, chapters = epub_writer.run(epub_config(args, paths, title, cover), blocks)
    paragraphs = sum(1 for b in blocks if b[0] == 'p')
    verses = sum(1 for b in blocks if b[0] == 'verse')
    titles = [b[1] for b in blocks if b[0] == 'chapter' and len(b) > 1 and b[1]]

    print(f'\nTamam: {out}')
    print(f'  {chapters} bölüm, {paragraphs} paragraf, {verses} şiir bloğu, {notes} dipnot')
    if titles:
        print('  bölümler: ' + ', '.join(titles[:30]) + (' ...' if len(titles) > 30 else ''))
    print(f'  (ara dosyalar: {paths["work_dir"]} — tekrar çalıştırınca OCR baştan yapılmaz)')


# ---------------------------------------------------------------- ana akış
def main():
    args = build_parser().parse_args()
    paths = prepare_paths(args)
    pdf = os.path.abspath(args.pdf)
    load_dependencies()

    tess = find_tesseract(args.tesseract)
    tess_args = tessdata_args(tess)

    doc = fitz.open(pdf)
    title = book_title(args, doc, paths['root'])
    names = image_and_ocr(doc, args, paths, title, tess, tess_args)
    blocks = build_book_structure(doc, names, paths, tess, tess_args, args, title)
    write_output(doc, args, paths, title, blocks)


if __name__ == '__main__':
    main()
