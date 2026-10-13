"""OCR çıktısından (tesseract TSV) kitap yapısını kurar: paragraf, şiir, dipnot, sahne ve bölüm."""
import csv, glob, re, statistics as st, json, sys

FN_START = re.compile(r'^\W{0,3}(\(\s*[\*9©]\s*[\)Y]|©\)|\(\*)\s*')
MARK = re.compile(r"\(\s*\*\s*[\)Y]?")
HEADER = re.compile(r"(\d+\s*$|^\d+\s*/)")
IMG_DIR = 'desk'
TESSERACT = 'tesseract'
TESSERACT_ARGS = []
LANG = 'tur'
VERBOSE = False
PAGE_NUM = re.compile(r"[\W\d]{0,2}\d{1,4}[\W\d]{0,2}")
END_PUNCT = tuple('.!?:…"”\'»')


def read_lines(f):
    rows = list(csv.DictReader(open(f, encoding='utf-8'), delimiter='\t', quoting=csv.QUOTE_NONE))
    L = {}
    for r in rows:
        if r['level'] != '5' or not (r['text'] or '').strip():
            continue
        k = (r['block_num'], r['par_num'], r['line_num'])
        x, y, w, h = map(int, (r['left'], r['top'], r['width'], r['height']))
        L.setdefault(k, []).append(dict(x=x, y=y, r=x + w, b=y + h, t=r['text'], c=float(r['conf'])))
    out = []
    for ws in L.values():
        ws.sort(key=lambda w: w['x'])
        good = [w for w in ws if sum(ch.isalnum() for ch in w['t']) >= 2] or ws
        out.append(dict(words=ws, y=int(st.median(w['y'] for w in good)), b=int(st.median(w['b'] for w in good))))
    out.sort(key=lambda d: d['y'])
    return out


def finalize(d, words):
    d['words'] = words
    d['x'] = words[0]['x']; d['r'] = words[-1]['r']
    d['t'] = ' '.join(w['t'] for w in words)
    d['conf'] = st.mean(w['c'] for w in words)
    d['h'] = d['b'] - d['y']
    return d


def is_break_line(t):
    return bool(re.fullmatch(r"[\s\*#∴•·.,:;©°+]+", t)) and ('*' in t or '∴' in t or len(t.strip()) <= 3)


def letters(t):
    return sum(ch.isalpha() for ch in t)


HEADERS = []


def tr_lower(t):
    return t.replace('I', 'ı').replace('İ', 'i').lower()


def norm_hdr(t):
    return re.sub(r'[\W\d_]+', '', tr_lower(t))


def detect_headers(files):
    """Sayfaların tepesinde sık tekrarlanan satırları (koşan başlık) bul."""
    import difflib
    tops = []
    for f in files:
        L = read_lines(f)
        for d in L[:2]:
            t = norm_hdr(' '.join(w['t'] for w in d['words']))
            if 4 <= len(t) <= 60:
                tops.append(t)
    found = []
    for t in tops:
        if any(difflib.SequenceMatcher(None, t, h).ratio() > 0.75 for h in found):
            continue
        n = sum(difflib.SequenceMatcher(None, t, u).ratio() > 0.75 for u in tops)
        if n >= max(5, 0.15 * len(files)):
            found.append(t)
    return found


def is_header(t):
    import difflib
    n = norm_hdr(t)
    return bool(n) and any(difflib.SequenceMatcher(None, n, h).ratio() > 0.7 for h in HEADERS)


def img_size(f):
    import os
    from PIL import Image
    p = os.path.join(IMG_DIR, os.path.basename(f)[:-4] + '.png')
    try:
        return Image.open(p).size
    except Exception:
        return (2400, 3307)


def page(f):
    lines = [finalize(d, d['words']) for d in read_lines(f)]
    if not lines:
        return None
    W, page_h = img_size(f)
    # sayfa numarası (alt ya da üst)
    if lines and PAGE_NUM.fullmatch(lines[-1]['t'].strip()) and lines[-1]['y'] > page_h * 0.8:
        lines = lines[:-1]
    if lines and PAGE_NUM.fullmatch(lines[0]['t'].strip()) and lines[0]['y'] < page_h * 0.2:
        lines = lines[1:]
    if not lines:
        return None
    # üst bilgi
    while lines and lines[0]['y'] < page_h * 0.15 and is_header(lines[0]['t']):
        lines = lines[1:]
    if not lines:
        return None
    title = None
    sp0 = [b['y'] - a['y'] for a, b in zip(lines[1:], lines[2:])]
    lsp = st.median(sp0) if sp0 else 60
    if len(lines) > 1 and lines[0]['y'] < page_h * 0.18 and lines[1]['y'] - lines[0]['y'] > 2 * lsp:
        # üstte tek başına satır: ya üst bilgi ya da (ortalanmışsa ve büyük boşluk varsa) bölüm başlığı
        xs_ = sorted(d['x'] for d in lines[1:] if d['x'] > 8); rs_ = sorted(d['r'] for d in lines[1:])
        body_x = xs_[len(xs_) // 10] if xs_ else 0; body_r = rs_[int(len(rs_) * 0.9)] if rs_ else W
        l0 = lines[0]
        centered = abs((l0['x'] - body_x) - (body_r - l0['r'])) < 0.12 * (body_r - body_x) and l0['x'] - body_x > 60
        if centered and lines[1]['y'] - lines[0]['y'] > 3.5 * lsp and not HEADER.search(l0['t']):
            title = l0['t']
        lines = lines[1:]
    full = [d for d in lines if len(d['t']) > 40 and d['x'] > 8]
    if len(full) < 3:
        R = max((d['r'] for d in lines), default=0); B = min((d['x'] for d in lines if d['x'] > 8), default=0)
    else:
        hy = [d['r'] for d in lines if re.search(r'\w-$', d['t'])]
        if len(hy) >= 3:
            R = st.median(hy)
        else:
            m = st.median(d['r'] for d in full)
            rs = sorted(d['r'] for d in lines if d['r'] < m + 120); R = rs[int(len(rs) * 0.85)]
        xs = sorted(d['x'] for d in full); B = xs[len(xs) // 7]
    # kümelenme ile kenar: en az 3 satırın hizalandığı en sağ/en sol konum
    def core(d):
        ws = [w for w in d['words'] if sum(ch.isalpha() for ch in w['t']) >= 2 and w['c'] >= 50]
        return (ws[0]['x'], ws[-1]['r']) if ws else (d['x'], d['r'])
    cores = [core(d) for d in lines if len(d['t']) > 15]
    rs_ = sorted((c[1] for c in cores), reverse=True)
    for r0 in rs_:
        if sum(1 for r in rs_ if abs(r - r0) <= 12) >= 3:
            R = st.median([r for r in rs_ if abs(r - r0) <= 12]); break
    xs_ = sorted(c[0] for c in cores if c[0] > 8)
    for x0 in xs_:
        if sum(1 for x in xs_ if abs(x - x0) <= 12) >= 3:
            B = st.median([x for x in xs_ if abs(x - x0) <= 12]); break
    cleaned = []
    for d in lines:
        def junk(w):
            return sum(ch.isalpha() for ch in w['t']) < 2 or w['c'] < 50
        ws = [w for w in d['words'] if not ((w['x'] > R + 30 or w['r'] < B - 15) and junk(w))]
        ws = [w for w in ws if not re.fullmatch(r"[|İ!\[\]{}]+", w['t'])] or ws
        ws = [w for w in ws if w['c'] >= 10]  # güven 0: süsleme/çizgi çöpü
        if not ws:
            continue
        d = finalize(d, ws)
        if d['x'] < B - 15:
            d['x'] = B
        t = d['t']
        if is_break_line(t):
            d['kind'] = 'break'
        elif cleaned and re.search(r'\w-$', cleaned[-1]['t']) and abs(d['x'] - B) < 60 and letters(t) >= 1 and t[:1].islower():
            d['kind'] = 'text'
        elif (letters(t) <= 3 and d['conf'] < 75 and not re.fullmatch(r'\W*SON\W*', t)) or letters(t) < 2 or (d['conf'] < 45 and letters(t) < 6) or (d['conf'] < 30):
            continue  # gürültü
        else:
            d['kind'] = 'text'
        cleaned.append(d)
    lines = cleaned
    hs = [d['h'] for d in lines if d['kind'] == 'text']
    H = st.median(hs) if hs else 60
    # dipnotlar
    notes, body = [], []
    in_fn = False
    for d in lines:
        if not in_fn and d['kind'] == 'text' and d['y'] > page_h * 0.45 and FN_START.match(d['t']) and (d['h'] < H * 0.9 or d['t'].lstrip().startswith(('(', '©'))):
            in_fn = True
        if in_fn:
            if FN_START.match(d['t']):
                notes.append([FN_START.sub('', d['t'])])
            elif notes:
                notes[-1].append(d['t'])
        else:
            body.append(d)
    notes = [join_lines(n) for n in notes]
    # boşluklar: önce atlanmış satır var mı diye boşluğu yeniden OCR'la
    body = recover_gaps(f, body, B, R)
    ys = [d['y'] for d in body]
    sp = [b - a for a, b in zip(ys, ys[1:])]
    med = st.median(sp) if sp else 70
    for i, d in enumerate(body):
        d['gap'] = i > 0 and (d['y'] - body[i - 1]['y']) > 1.75 * med
        d['rel'] = d['x'] - B
        d['short'] = d['r'] < R - 110
        d['width'] = R - B
    first_y = body[0]['y'] if body else 0
    return dict(name=f, body=body, notes=notes, R=R, B=B, first_y=first_y, title=title, page_h=page_h)


def recover_gaps(f, body, B, R):
    import subprocess, os
    from PIL import Image
    if len(body) < 4:
        return body
    ys = [d['y'] for d in body]
    med = st.median(b - a for a, b in zip(ys, ys[1:]))
    img = None
    out = [body[0]]
    for prev, d in zip(body, body[1:]):
        if d['y'] - prev['y'] > 1.6 * med:
            if img is None:
                img = Image.open(os.path.join(IMG_DIR, os.path.basename(f)[:-4] + '.png'))
            box = (max(0, B - 40), prev['b'] + 2, R + 40, d['y'] - 2)
            if box[3] - box[1] > 25:
                crop = img.crop(box)
                import tempfile
                fd, tmp = tempfile.mkstemp(suffix='.png'); os.close(fd)
                crop.save(tmp)
                tsv = subprocess.run([TESSERACT, tmp, '-', '-l', LANG, '--psm', '6'] + TESSERACT_ARGS + ['tsv'], capture_output=True,
                                     text=True, encoding='utf-8', errors='replace').stdout
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                rows = [r.split('\t') for r in tsv.splitlines()[1:]]
                glines = {}
                for r in rows:
                    if len(r) == 12 and r[0] == '5' and r[11].strip():
                        glines.setdefault((r[2], r[3], r[4]), []).append((float(r[10]), r[11]))
                cand_lines = []
                for ws in glines.values():
                    ws = [(c, t) for c, t in ws if re.search(r'\w', t)] or ws  # salt sembol çöplerini at
                    good = [t for c, t in ws if c >= 55 and not re.search(r'[<>»«|—]', t)]
                    # bütün kelimeler güvenilir olmalı (süs/çizgi çöpünü ele)
                    if good and len(good) == len(ws):
                        cand_lines.append(' '.join(good))
                    elif re.search(r'\w-$', prev['t']) and ws and re.fullmatch(r"[a-zçğıöşü]{1,4}[.,!?;:\"'.]*", ws[0][1].lower()):
                        cand_lines.append(ws[0][1].lower())
                for ln in cand_lines:
                    if re.search(r'\w-$', prev['t']) and 1 <= letters(ln) <= 3:
                        ln = ln.split(' ')[0].lower()
                    if letters(ln) >= 4 or (re.search(r'\w-$', prev['t']) and letters(ln) >= 1 and ln[:1].islower()):
                        # girinti: piksel konumu bilinmiyor -> tireyle bitiyorsa satır içi devam say
                        nd = dict(words=[], y=(prev['y'] + d['y']) // 2, b=(prev['b'] + d['y']) // 2,
                                  x=B, r=R, t=ln, conf=80, h=prev['h'], kind='text')
                        cr = img.crop(box).convert('L')
                        import numpy as np
                        a = np.array(cr) < 128
                        cols = np.where(a.sum(0) > 0)[0]
                        if len(cols):
                            nd['x'] = box[0] + int(cols[0]); nd['r'] = box[0] + int(cols[-1])
                        if VERBOSE:
                            print('  atlanan satır kurtarıldı:', os.path.basename(f), ln, file=sys.stderr)
                        out.append(nd)
        out.append(d)
    return out


def join_lines(parts):
    s = ''
    for p in parts:
        p = re.sub(r'(\w)-[\s.,*|]+$', r'\1-', p.strip())
        if not s:
            s = p
        elif re.search(r'\w-$', s) and p[:1].islower():
            s = s[:-1] + p
        else:
            s += ' ' + p
    return s


def clean_text(s):
    s = s.replace('“', '"').replace('”', '"').replace('„', '"').replace("''", '"')
    s = re.sub(r'\s+([,.;:!?])', r'\1', s)
    s = re.sub(r'\s{2,}', ' ', s)
    s = re.sub(r'^[-—–]\s*', '– ', s)
    s = re.sub(r'\s[-—]\s', ' – ', s)
    s = re.sub(r"[\s|*©]+$", '', s)
    s = re.sub(r"^[©|*\s]+", '', s)
    s = re.sub(r'^[a-zı] (?=[A-ZÇĞİÖŞÜ])', '', s)
    s = re.sub(r'([.!?…":]) (?:[A-ZÇĞİÖŞÜ]{1,2}|[a-zı][A-ZÇĞİÖŞÜ])$', r'\1', s)
    s = re.sub(r'(\w)-\.$', r'\1.', s)
    return s.strip()


def open_ratio(lines):
    """Cümle sonu noktalamasıyla bitmeyen satır oranı (şiir dizeleri çoğu zaman açık biter)."""
    op = [d for d in lines if not re.search(r'[.!?:…"”»)]\W*$', d['t'])]
    return len(op) / max(1, len(lines))


def normalize(pages):
    """Sağ kenarı kitap genelindeki metin genişliğine göre düzelt (şiir ağırlıklı sayfalar için)."""
    ws = [p['R'] - p['B'] for p in pages if len(p['body']) > 15]
    if not ws:
        return pages
    W = st.median(ws)
    for p in pages:
        if abs((p['R'] - p['B']) - W) > 0.05 * W:
            # hangi kenar güvenilir? daha çok satırın hizalandığı kenar
            nb = sum(1 for d in p['body'] if abs(d['x'] - p['B']) < 25)
            nr = sum(1 for d in p['body'] if abs(d['r'] - p['R']) < 25)
            if nr > nb:
                newB = p['R'] - W
                for d in p['body']:
                    d['rel'] = d['rel'] + (p['B'] - newB)
                p['B'] = newB
            else:
                p['R'] = p['B'] + W
            for d in p['body']:
                d['short'] = d['r'] < p['R'] - 110
        for d in p['body']:
            d['width'] = W
    return pages


def build(pages):
    """Bütün sayfaları bloklara dönüştürür: chapter / p / verse / break / note."""
    blocks = []
    cur = None  # açık paragraf (liste)

    def flush():
        nonlocal cur
        if cur:
            blocks.append(('p', clean_text(join_lines(cur))))
        cur = None

    items = []
    for pg in pages:
        chapter_start = bool(pg.get('title')) or (pg['first_y'] > pg['page_h'] * 0.35 and len(pg['body']) > 3)
        if chapter_start:
            items.append(dict(kind='chapter', title=pg.get('title')))
        for i, d in enumerate(pg['body']):
            d = dict(d); d['page_first'] = i == 0; d['chap'] = chapter_start
            items.append(d)
        for n in pg['notes']:
            items.append(dict(kind='note', t=clean_text(n)))

    texts = [it for it in items if it['kind'] == 'text']
    # şiir: girintili + kısa + diyalog değil, en az 2 ardışık
    seq = [it for it in items if it['kind'] == 'text']
    def base_cand(d):
        return (d['rel'] > 60 and not re.match(r"^\W{0,2}[-—–]", d['t'])
                and not re.match(r"^[\"“”'‘«]", d['t'])
                and not re.search(r'\w-$', d['t']))
    ind = sorted(d['rel'] for d in seq if 40 < d['rel'] < 400)
    para_indent = ind[len(ind) // 2] if ind else 100
    cands = []
    for j, d in enumerate(seq):
        d['verse'] = False
        nxt = seq[j + 1] if j + 1 < len(seq) else None
        para_start = nxt is not None and not nxt['gap'] and not nxt['page_first'] and nxt['rel'] < 40
        if nxt is not None and nxt['page_first'] and nxt['rel'] < 40 and not d['short']:
            para_start = True
        cands.append(base_cand(d) and not para_start)
    # şiir: en az 3 ardışık aday satır (kıta boşlukları dahil)
    j = 0
    while j < len(seq):
        if not cands[j]:
            j += 1; continue
        k = j
        while k + 1 < len(seq) and cands[k + 1]:
            k += 1
        run = seq[j:k + 1]
        deep = all(d['rel'] > 1.6 * para_indent for d in run)
        if (deep and len(run) >= 2) or (len(run) >= 3 and open_ratio(run) >= 0.3):
            for d in run:
                d['verse'] = True
        j = k + 1
    # şiir (2): boşluklarla çevrili, bütün satırları kısa olan blok (girintisiz ninni/türkü dizilişi)
    group = []
    def close_group():
        if 2 <= len(group) <= 16:
            def short_line(d):
                return (d['r'] - (d['x'] - d['rel'])) < 0.9 * d['width'] and not re.search(r'\w-$', d['t']) \
                    and not re.match(r"^\W{0,2}[-—–\"“”'‘«]", d['t'])
            widths = [(d['r'] - (d['x'] - d['rel'])) / d['width'] for d in group]
            if all(short_line(d) for d in group) and (st.mean(widths) < 0.75 or open_ratio(group) >= 0.3):
                for d in group:
                    d['verse'] = True
        group.clear()
    prev_closed = True
    for it in items:
        if it['kind'] != 'text':
            close_group(); prev_closed = True; continue
        if it['gap']:
            close_group(); prev_closed = True
        if prev_closed:
            group.append(it); prev_closed = False
        elif group:
            group.append(it)
    # son grup: arkasından boşluk gelmedikçe şiir sayma
    group.clear()
    # yeni grup yalnızca boşluktan sonra başlar; boşluksuz paragraflar grubu bozar
    for j, d in enumerate(seq):
        if d['chap'] and d['rel'] > 150 and not d['gap'] and re.fullmatch(r"[-\s]*[0-9IJl]{1,3}[.\-\s]*", d['t']):
            d['kind'] = 'chapnum'

    prev = None
    verse_buf = []

    def flush_verse(cls):
        nonlocal verse_buf
        if verse_buf:
            blocks.append(('verse', [clean_text(v) for v in verse_buf], cls))
        verse_buf = []

    in_epigraph = False
    for it in items:
        k = it['kind']
        if k == 'chapter':
            flush(); flush_verse('verse'); blocks.append(('chapter', it.get('title'))); in_epigraph = True; prev = None; continue
        if k == 'note':
            blocks.append(('note', it['t'])); continue
        if k == 'chapnum':
            continue
        if k == 'break':
            flush(); flush_verse('epigraph' if in_epigraph else 'verse')
            if blocks and blocks[-1][0] not in ('break', 'chapter'):
                blocks.append(('break',))
            prev = None; continue
        t = it['t']
        if prev is not None and re.search(r'\w-$', prev['t']) and t[:1].islower():
            it['gap'] = False; it['rel'] = 0
        if re.fullmatch(r'\W*SON\W*', t):
            flush(); flush_verse('verse'); blocks.append(('end',)); continue
        if it['gap'] and prev is not None and it.get('verse') and prev.get('verse'):
            flush_verse('verse'); verse_buf.append(t); prev = it; continue
        if it['gap'] and prev is not None and (it.get('verse') or prev.get('verse')) and not in_epigraph:
            flush(); flush_verse('verse')
            it['gap'] = False
        if it['gap'] and prev is not None:
            flush(); flush_verse('epigraph' if in_epigraph else 'verse')
            if in_epigraph:
                in_epigraph = False
            elif blocks and blocks[-1][0] not in ('break', 'chapter'):
                blocks.append(('break',))
        if in_epigraph and not (it['rel'] > 150):
            in_epigraph = False; flush_verse('epigraph')
        if it['verse'] or in_epigraph:
            flush(); verse_buf.append(t); prev = it; continue
        flush_verse('verse')
        newpara = (cur is None or it['rel'] > 60 or
                   (prev is not None and prev['short'] and prev['t'].rstrip(' |*').endswith(END_PUNCT)))
        if newpara:
            flush(); cur = [t]
        else:
            cur.append(t)
        prev = it
    flush(); flush_verse('verse')
    return blocks
