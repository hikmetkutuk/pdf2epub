"""OCR çıktısından (tesseract TSV) kitap yapısını kurar: paragraf, şiir, dipnot, sahne ve bölüm."""

import contextlib
import csv
import difflib
import importlib
import os
import re
import statistics as st
import subprocess
import sys
import tempfile
from collections import Counter
from collections.abc import Iterable, Iterator
from typing import Any, TypeVar

T = TypeVar('T')
np: Any = importlib.import_module('numpy')
Image: Any = importlib.import_module('PIL.Image')

FOOTNOTE_PREFIX_PATTERNS = (
    re.compile(r'^\W{0,3}\(\s*[*9©]\s*[)Y]\s*'),
    re.compile(r'^\W{0,3}©\)\s*'),
    re.compile(r'^\W{0,3}\(\*\s*'),
    re.compile(r'^\W{0,3}\*\s*'),
    re.compile(r'^[\s|]*(?:\*{1,3}|†{1,2})\s*'),
)
MARK = re.compile(r"\(\s*\*\s*[)Y]?|(?<=[\w.,;:!?'’\"”)])(\*{1,3}|†{1,2})")
NOTE_ID = [0]
HEADER = re.compile(r"(\d+\s*$|^\d+\s*/)")
IMG_DIR = 'desk'
TESSERACT = 'tesseract'
TESSERACT_ARGS = []
LANG = 'tur'
VERBOSE = False
REFERENCE = {}
RESCUE_REFERENCE = {}
REF_MAX_CONF = 80
VOCAB = {}
REF_ODD_APOSTROPHE_SUFFIX = re.compile(r"['’][a-zçğıöşü]*(?:m|rn|nm)$")
NON_LETTER = r"[^A-Za-zÇĞİÖŞÜçğıöşü_]"
PAGE_NUM = re.compile(rf"{NON_LETTER}{{0,2}}[0-9]{{1,4}}{NON_LETTER}{{0,2}}")
END_PUNCT = tuple('.!?:…"”\'»')
EDGE_NOISE = ' \t\r\n\f\v|*©'
BREAK_CHARS = set(' \t\r\n\f\v*#∴•·.,:;©°+')
JUNK_TOKEN_CHARS = set('|İ![]{}')
BAD_GAP_CHARS = set('<>»«|—')
GAP_TRAILING_CHARS = set('.,!?;:"\'')
ODD_APOSTROPHE_SUFFIX = re.compile(r"\b[^\W\d_]+['’][bcçdfgğhjklmnprsştvyz]{3,}\b", re.IGNORECASE)
SUSPICIOUS_TEXT_FRAGMENTS = (
    'oı', 'ie', 'lcr', 'rnı', 'ıı', 'umı', 'tcı', 'teı', 'kcn', 'ktc', 'dck', 'eiy',
    'vann', 'eeli', 'dalzi', 'lci', 'tck', 'nıa', 'ysc', 'okyar', 'sorn', 'yanıyar',
    'pcn', 'ınaj', 'kraliyer', 'gcl', 'huyur', 'görscl', 'güvcrt', 'karavc', 'kümcs',
    'beccr', 'cialm', 'kişncy', 'elelen', 'lerele', 'geele', 'tavt',
)


def successive_pairs(iterable: Iterable[T]) -> Iterator[tuple[T, T]]:
    iterator = iter(iterable)
    try:
        previous = next(iterator)
    except StopIteration:
        return
    for current in iterator:
        yield previous, current
        previous = current


def read_lines(f):
    with open(f, encoding='utf-8') as handle:
        rows = list(csv.DictReader(handle, delimiter='\t', quoting=csv.QUOTE_NONE))
    L = {}
    for r in rows:
        if r['level'] != '5' or not (r['text'] or '').strip():
            continue
        k = (r['block_num'], r['par_num'], r['line_num'])
        x, y, w, h = map(int, (r['left'], r['top'], r['width'], r['height']))
        L.setdefault(k, []).append({'x': x, 'y': y, 'r': x + w, 'b': y + h, 't': r['text'], 'c': float(r['conf'])})
    out = []
    for ws in L.values():
        ws.sort(key=lambda w: w['x'])
        good = [w for w in ws if sum(ch.isalnum() for ch in w['t']) >= 2] or ws
        out.append({'words': ws, 'y': int(st.median(w['y'] for w in good)), 'b': int(st.median(w['b'] for w in good))})
    out.sort(key=lambda d: d['y'])
    return out


def finalize(d, words):
    d['words'] = words
    d['x'] = words[0]['x']
    d['r'] = words[-1]['r']
    d['t'] = ' '.join(w['t'] for w in words)
    d['conf'] = st.mean(w['c'] for w in words)
    d['h'] = d['b'] - d['y']
    return d


def is_break_line(t):
    return bool(t) and all(ch in BREAK_CHARS for ch in t) and ('*' in t or '∴' in t or len(t.strip()) <= 3)


def letters(t):
    return sum(ch.isalpha() for ch in t)


HEADERS = []


def tr_lower(t):
    return t.replace('I', 'ı').replace('İ', 'i').lower()


def norm_hdr(t):
    return re.sub(r'[\W\d_]+', '', tr_lower(t))


LOOSE_TITLE_MAP = str.maketrans({
    'â': 'a',
    'ç': 'c',
    'ğ': 'g',
    'ı': 'i',
    'î': 'i',
    'ö': 'o',
    'ş': 's',
    'û': 'u',
    'ü': 'u',
})


def loose_norm_title(t):
    return norm_hdr(t).translate(LOOSE_TITLE_MAP)


def loose_title_match(left, right):
    left_norm = loose_norm_title(left)
    right_norm = loose_norm_title(right)
    if not left_norm or not right_norm:
        return False
    if left_norm == right_norm:
        return True
    if abs(len(left_norm) - len(right_norm)) > max(3, 0.25 * len(right_norm)):
        return False
    return difflib.SequenceMatcher(None, left_norm, right_norm).ratio() >= 0.82


def norm_match_text(t):
    t = tr_lower(t).replace('’', "'").replace('‘', "'").replace('`', "'")
    return re.sub(r"[^0-9a-zçğıöşü]+", "", t)


def norm_word(word):
    return re.sub(r"[^0-9a-zçğıöşüâîû]", "", tr_lower(word))


def clean_reference_line(t):
    t = t.replace('\xad', '-')
    t = re.sub(r'\s+', ' ', t)
    return t.strip()


def text_words(t):
    return re.findall(r"[^\W\d_]+", tr_lower(t))


def suspicious_text_layer_word_count(t):
    count = 0
    for word in text_words(t):
        has_fragment = any(fragment in word for fragment in SUSPICIOUS_TEXT_FRAGMENTS)
        has_bad_lc = word.endswith('lc') or ('lc' in word and re.search(r'lc(?=[^aeıioöuü])', word))
        has_bad_dc = word.endswith('dc') or ('dc' in word and re.search(r'dc(?=[^aeıioöuü])', word))
        if has_fragment or has_bad_lc or has_bad_dc:
            count += 1
    return count


def text_badness(t):
    score = 0
    score += 3 * len(re.findall(r"[#|�]", t))
    score += 3 * len(re.findall(r"[^\W\d_]+\d+[^\W\d_]+", t))
    score += 4 * len(ODD_APOSTROPHE_SUFFIX.findall(tr_lower(t)))
    score += 3 * suspicious_text_layer_word_count(t)
    score += 2 * len(re.findall(r"\b\w+\?+\w+\b", t))
    score += abs(t.count('[') - t.count(']'))
    score += abs(t.count('(') - t.count(')'))
    return score


def build_vocab(files):
    counts = {}
    for f in files:
        for line in read_lines(f):
            for word in line['words']:
                if word['c'] >= 90:
                    normalized = norm_word(word['t'])
                    if len(normalized) >= 2:
                        counts[normalized] = counts.get(normalized, 0) + 1
    return counts


def reference_word_is_allowed(ocr_word, ref_word, ocr_norm, ref_norm, rescue=False):
    if rescue:
        return rescue_reference_word_is_allowed(ocr_word, ref_word, ocr_norm, ref_norm)
    if not reference_word_is_similar_enough(ocr_word, ocr_norm, ref_norm):
        return False
    ocr_known = VOCAB.get(ocr_norm, 0) >= 3
    ref_known = VOCAB.get(ref_norm, 0) >= 2
    if ocr_known and not ref_known:
        return False
    return not (reference_word_is_suspect(ref_word) and not ref_known)


def rescue_reference_word_is_allowed(ocr_word, ref_word, ocr_norm, ref_norm):
    if not reference_word_is_similar_enough(ocr_word, ocr_norm, ref_norm):
        return False
    if ocr_word['c'] >= 70 or not ocr_word_is_suspicious(ocr_word['t']):
        return False
    return not reference_word_is_suspect(ref_word)


def ocr_word_is_suspicious(text):
    lowered = tr_lower(text)
    return (
        text_badness(text) > 0
        or bool(re.search(r'[#|?\\]', text))
        or bool(re.search(r'[xw]', lowered))
    )


def reference_word_is_similar_enough(ocr_word, ocr_norm, ref_norm):
    if not (ocr_word['c'] < REF_MAX_CONF and ocr_norm and ref_norm):
        return False
    if difflib.SequenceMatcher(None, ocr_norm, ref_norm).ratio() < 0.5:
        return False
    return not (re.search(r'\d', ocr_norm) and re.search(r'\d', ref_norm))


def reference_word_is_suspect(ref_word):
    lowered = tr_lower(ref_word)
    if 'vv' in lowered or any(ch in ref_word for ch in '/|#\\'):
        return True
    if re.search(r'\d[^\W\d_]|[^\W\d_]\d', ref_word):
        return True
    if any(a.islower() and b.isupper() for a, b in successive_pairs(ref_word)):
        return True
    return bool(REF_ODD_APOSTROPHE_SUFFIX.search(lowered))


def apply_reference_word(ocr_word, ref_word, ocr_norm, ref_norm, rescue=False):
    if reference_word_is_allowed(ocr_word, ref_word, ocr_norm, ref_norm, rescue=rescue):
        ocr_word['t'] = ref_word
        ocr_word['c'] = max(ocr_word['c'], 60.0)


def apply_reference_footnote_marker(ocr_word, ref_word):
    marker = re.search(r'(\*{1,3})[.,;:!?)]*$', ref_word)
    if marker and '*' not in ocr_word['t'] and ocr_word['c'] < 95:
        ocr_word['t'] = re.sub(r'["”“\'’]?([.,;:!?)]*)$', marker.group(1) + r'\1', ocr_word['t'], count=1)


def apply_reference_text(lines, page_name):
    page_key = os.path.basename(page_name)[:-4]
    ref = REFERENCE.get(page_key) or RESCUE_REFERENCE.get(page_key)
    if not ref:
        return lines
    rescue = page_key not in REFERENCE
    words = [word for line in lines for word in line['words']]
    ocr_norms = [norm_word(word['t']) for word in words]
    ref_norms = [norm_word(text) for text in ref]
    matcher = difflib.SequenceMatcher(None, ocr_norms, ref_norms, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == 'equal':
            for offset in range(i2 - i1):
                apply_reference_footnote_marker(words[i1 + offset], ref[j1 + offset])
        elif tag == 'replace' and i2 - i1 == j2 - j1:
            for offset in range(i2 - i1):
                apply_reference_word(
                    words[i1 + offset],
                    ref[j1 + offset],
                    ocr_norms[i1 + offset],
                    ref_norms[j1 + offset],
                    rescue=rescue,
                )
    for line in lines:
        finalize(line, line['words'])
    return lines


def footnote_prefix_match(text):
    for pattern in FOOTNOTE_PREFIX_PATTERNS:
        match = pattern.match(text)
        if match:
            return match
    return None


def strip_footnote_prefix(text):
    match = footnote_prefix_match(text)
    return text[match.end():] if match else text


def first_word(text):
    match = re.search(r"[^\W\d_]+", text, re.UNICODE)
    return tr_lower(match.group(0)) if match else ''


def hyphen_continuation_conflicts(previous_text, ocr_text, ref_text):
    if not re.search(r"[^\W\d_]-$", previous_text.strip()):
        return False
    ocr_first = first_word(ocr_text)
    ref_first = first_word(ref_text)
    return bool(ocr_first and ref_first and ocr_first != ref_first)


def detect_headers(files):
    """Sayfaların tepesinde sık tekrarlanan satırları (koşan başlık) bul."""
    tops = []
    for f in files:
        L = read_lines(f)
        for d in L[:2]:
            t = norm_hdr(' '.join(w['t'] for w in d['words']))
            if 4 <= len(t) <= 60:
                tops.append(t)
    counts = Counter(tops)
    unique = sorted(counts, key=lambda item: -counts[item])
    found = []
    for t in unique:
        if any(difflib.SequenceMatcher(None, t, h).ratio() > 0.75 for h in found):
            continue
        if counts[t] < 2:
            break
        n = sum(counts[u] for u in unique if difflib.SequenceMatcher(None, t, u).ratio() > 0.75)
        if n >= max(5, 0.15 * len(files)):
            found.append(t)
    return found


def is_header(t):
    n = norm_hdr(t)
    return bool(n) and any(difflib.SequenceMatcher(None, n, h).ratio() > 0.7 for h in HEADERS)


def img_size(f):
    p = os.path.join(IMG_DIR, os.path.basename(f)[:-4] + '.png')
    try:
        with Image.open(p) as image:
            return image.size
    except OSError as exc:
        if VERBOSE:
            print(f'  sayfa görüntüsü okunamadı: {p} ({exc})', file=sys.stderr)
        return (2400, 3307)


def open_image_copy(path):
    with Image.open(path) as image:
        return image.copy()


def drop_page_numbers(lines, page_h):
    if lines and is_page_number_noise(lines[-1], page_h, at_bottom=True):
        lines = lines[:-1]
    if lines and is_page_number_noise(lines[0], page_h, at_bottom=False):
        lines = lines[1:]
    return lines


def is_page_number_noise(line, page_h, at_bottom):
    text = line['t'].strip()
    if not text:
        return False
    in_margin = line['y'] > page_h * 0.9 if at_bottom else line['y'] < page_h * 0.1
    if PAGE_NUM.fullmatch(text):
        return in_margin or (line['y'] > page_h * 0.8 if at_bottom else line['y'] < page_h * 0.2)
    if norm_hdr(text) == 'son':
        return False
    return in_margin and letters(text) <= 3 and len(text) <= 5


def drop_running_headers(lines, page_h):
    while lines and lines[0]['y'] < page_h * 0.15 and is_header(lines[0]['t']):
        lines = lines[1:]
    return lines


def line_spacing_after_first(lines):
    spacings = [b['y'] - a['y'] for a, b in successive_pairs(lines[1:])]
    return st.median(spacings) if spacings else 60


def body_bounds_for_title(lines, W):
    xs_ = sorted(d['x'] for d in lines[1:] if d['x'] > 8)
    rs_ = sorted(d['r'] for d in lines[1:])
    body_x = xs_[len(xs_) // 10] if xs_ else 0
    body_r = rs_[int(len(rs_) * 0.9)] if rs_ else W
    return body_x, body_r


def centered_title_line(line, body_x, body_r):
    return abs((line['x'] - body_x) - (body_r - line['r'])) < 0.12 * (body_r - body_x) and line['x'] - body_x > 60


def valid_chapter_title(text):
    stripped = text.strip()
    return letters(text) >= 2 or re.fullmatch(r'\d{1,4}\.?|[IVXLC]{1,6}\.?', stripped)


def centered_top_title(lines, W):
    body_x, body_r = body_bounds_for_title(lines, W)
    return centered_title_line(lines[0], body_x, body_r)


def is_mid_page_title_candidate(lines, page_h, line_gap, spacing):
    first = lines[0]
    return (
        len(lines) > 2
        and page_h * 0.18 <= first['y'] < page_h * 0.6
        and line_gap > 2.5 * spacing
    )


def is_valid_top_title(lines, W, allow_header=False):
    first = lines[0]
    return (
        centered_top_title(lines, W)
        and valid_chapter_title(first['t'])
        and len(first['t']) < 60
        and (allow_header or not is_header(first['t']))
    )


def detect_top_title(lines, page_h, W):
    if len(lines) <= 1:
        return None, lines
    line_gap = lines[1]['y'] - lines[0]['y']
    spacing = line_spacing_after_first(lines)
    first = lines[0]
    if is_mid_page_title_candidate(lines, page_h, line_gap, spacing) and is_valid_top_title(lines, W, allow_header=True):
        return first['t'], lines[1:]
    if first['y'] >= page_h * 0.18 or line_gap <= 2 * spacing:
        return None, lines
    title = first['t'] if centered_top_title(lines, W) and line_gap > 3.5 * spacing and not HEADER.search(first['t']) else None
    return title, lines[1:]


def estimate_text_bounds(lines):
    full = [d for d in lines if len(d['t']) > 40 and d['x'] > 8]
    if len(full) < 3:
        R = max((d['r'] for d in lines), default=0)
        B = min((d['x'] for d in lines if d['x'] > 8), default=0)
    else:
        hy = [d['r'] for d in lines if re.search(r'\w-$', d['t'])]
        if len(hy) >= 3:
            R = st.median(hy)
        else:
            m = st.median(d['r'] for d in full)
            rs = sorted(d['r'] for d in lines if d['r'] < m + 120)
            R = rs[int(len(rs) * 0.85)]
        xs = sorted(d['x'] for d in full)
        B = xs[len(xs) // 7]
    return R, B


def line_core(d):
    ws = [w for w in d['words'] if sum(ch.isalpha() for ch in w['t']) >= 2 and w['c'] >= 50]
    return (ws[0]['x'], ws[-1]['r']) if ws else (d['x'], d['r'])


def cluster_text_bounds(lines, R, B):
    # Kümelenme ile kenar: en az 3 satırın hizalandığı en sağ/en sol konum.
    cores = [line_core(d) for d in lines if len(d['t']) > 15]
    rs_ = sorted((c[1] for c in cores), reverse=True)
    for r0 in rs_:
        if sum(1 for r in rs_ if abs(r - r0) <= 12) >= 3:
            R = st.median([r for r in rs_ if abs(r - r0) <= 12])
            break
    xs_ = sorted(c[0] for c in cores if c[0] > 8)
    for x0 in xs_:
        if sum(1 for x in xs_ if abs(x - x0) <= 12) >= 3:
            B = st.median([x for x in xs_ if abs(x - x0) <= 12])
            break
    return R, B


def junk_word(w, R, B):
    outside_body = w['x'] > R + 30 or w['r'] < B - 15
    low_signal = sum(ch.isalpha() for ch in w['t']) < 2 or w['c'] < 50
    return outside_body and low_signal


def has_confident_neighbor(words, index, step):
    neighbor_index = index + step
    while 0 <= neighbor_index < len(words):
        neighbor = words[neighbor_index]
        if letters(neighbor['t']) >= 2:
            return neighbor['c'] >= 50
        neighbor_index += step
    return False


def keep_low_conf_word(word, words, index, B, R):
    if word['c'] >= 10:
        return True
    if letters(word['t']) < 4 or word['x'] < B - 15 or word['r'] > R + 30:
        return False
    if words and words[0]['t'].startswith('*') and has_confident_neighbor(words, index, 1):
        return True
    if index == len(words) - 1 and has_confident_neighbor(words, index, -1):
        return True
    return has_confident_neighbor(words, index, -1) and has_confident_neighbor(words, index, 1)


def classify_line(d, cleaned, B):
    t = d['t']
    if is_break_line(t):
        return 'break'
    if is_page_reference_text(t):
        return 'text'
    if cleaned and re.search(r'\w-$', cleaned[-1]['t']) and abs(d['x'] - B) < 60 and letters(t) >= 1 and t[:1].islower():
        return 'text'
    noisy_short = letters(t) <= 3 and d['conf'] < 75 and not re.fullmatch(r'\W*SON\W*', t)
    noisy = noisy_short or letters(t) < 2 or (d['conf'] < 45 and letters(t) < 6) or d['conf'] < 30
    return None if noisy else 'text'


def is_page_reference_text(text):
    return bool(re.fullmatch(r'[sS]\.?\s*[0-9]{1,4}\.?', text.strip()))


def clean_ocr_lines(lines, B, R):
    cleaned = []
    for d in lines:
        ws = [w for w in d['words'] if not junk_word(w, R, B)]
        ws = [w for w in ws if not all(ch in JUNK_TOKEN_CHARS for ch in w['t'])] or ws
        ws = [w for i, w in enumerate(ws) if keep_low_conf_word(w, ws, i, B, R)]
        if not ws:
            continue
        d = finalize(d, ws)
        if d['x'] < B - 15:
            d['x'] = B
        kind = classify_line(d, cleaned, B)
        if kind:
            d['kind'] = kind
            cleaned.append(d)
    return cleaned


def split_footnotes(lines, page_h, H):
    notes, body = [], []
    in_fn = False
    for d in lines:
        note_start = d['kind'] == 'text' and d['y'] > page_h * 0.45 and footnote_prefix_match(d['t'])
        if not in_fn and note_start and (d['h'] < H * 0.9 or d['t'].lstrip().startswith(('(', '©'))):
            in_fn = True
        if in_fn:
            if footnote_prefix_match(d['t']):
                notes.append([strip_footnote_prefix(d['t'])])
            elif notes:
                notes[-1].append(d['t'])
        else:
            body.append(d)
    return [join_lines(n) for n in notes], body


def word_char_width(line):
    words = [word for word in line.get('words', []) if letters(word['t']) >= 2]
    if not words:
        return (line['r'] - line['x']) / max(1, len(line['t']))
    width = sum(word['r'] - word['x'] for word in words)
    char_count = sum(len(word['t']) for word in words)
    return width / max(1, char_count)


def base_char_width(lines):
    widths = [word_char_width(line) for line in lines if line['kind'] == 'text' and len(line['t']) > 30]
    return st.median(widths) if widths else 0


def line_is_small_footnote(line, B, H, base_width):
    if line['kind'] != 'text':
        return False
    left_aligned = line['x'] - B < 0.2 * max(1, line.get('width', line['r'] - B))
    small_height = line['h'] < H * 0.85
    narrow_chars = base_width and len(line['t']) >= 8 and word_char_width(line) < 0.85 * base_width
    return left_aligned and (small_height or narrow_chars)


def trailing_body_end_before_notes(body):
    end = len(body)
    while end > 0 and body[end - 1]['kind'] == 'break' and len(body[end - 1]['t'].strip()) <= 2:
        end -= 1
    return end


def trailing_unmarked_note_start(body, end, page_h, B, H, base_width, spacing):
    index = end
    start = None
    while index > 1:
        line = body[index - 1]
        if not line_is_small_footnote(line, B, H, base_width):
            break
        index -= 1
        if line['y'] - body[index - 1]['y'] > 1.5 * spacing:
            start = index
            break
    if start is None or end - start > 6 or body[start]['y'] <= page_h * 0.6:
        return None
    return start


def recover_unmarked_footnotes(notes, body, page_h, B, H, base_width, spacing):
    if len(body) <= 6:
        return notes, body
    end = trailing_body_end_before_notes(body)
    start = trailing_unmarked_note_start(body, end, page_h, B, H, base_width, spacing)
    if start is None:
        return notes, body
    recovered = join_lines(line['t'] for line in body[start:end])
    return [recovered, *notes], body[:start]


def bind_footnotes(notes, body):
    note_ids = []
    for _ in notes:
        NOTE_ID[0] += 1
        note_ids.append(NOTE_ID[0])

    marker_index = 0
    for line in body:
        if line['kind'] != 'text' or not MARK.search(line['t']):
            continue

        def replace_marker(_match):
            nonlocal marker_index
            if marker_index < len(note_ids):
                note_id = note_ids[marker_index]
                marker_index += 1
                return f'⟦{note_id}⟧'
            return ''

        line['t'] = MARK.sub(replace_marker, line['t'])
    return list(zip(note_ids, notes))


def add_body_metrics(body, B, R):
    ys = [d['y'] for d in body]
    sp = [b - a for a, b in successive_pairs(ys)]
    med = st.median(sp) if sp else 70
    for i, d in enumerate(body):
        d['gap'] = i > 0 and (d['y'] - body[i - 1]['y']) > 1.75 * med
        d['rel'] = d['x'] - B
        d['short'] = d['r'] < R - 110
        d['width'] = R - B


def mark_date_lines(body, B, R):
    for i, d in enumerate(body):
        if not (
            d['kind'] == 'text'
            and d['x'] - B > 0.2 * (R - B)
            and abs(d['r'] - R) < 45
            and len(d['t']) < 60
            and not re.search(r'\w-$', d['t'])
        ):
            continue
        before_ok = i == 0 or d['gap']
        next_line = body[i + 1] if i + 1 < len(body) else None
        after_ok = next_line is None or next_line['gap'] or next_line['rel'] < 0.15 * (R - B)
        if before_ok and after_ok:
            d['kind'] = 'date'


def page(f):
    lines = [finalize(d, d['words']) for d in read_lines(f)]
    if not lines:
        return None
    W, page_h = img_size(f)
    lines = drop_running_headers(drop_page_numbers(lines, page_h), page_h)
    if not lines:
        return None
    title, lines = detect_top_title(lines, page_h, W)
    if not lines:
        return None
    R, B = cluster_text_bounds(lines, *estimate_text_bounds(lines))
    apply_reference_text(lines, f)
    lines = clean_ocr_lines(lines, B, R)
    hs = [d['h'] for d in lines if d['kind'] == 'text']
    H = st.median(hs) if hs else 60
    base_width = base_char_width(lines)
    line_spacings = [b['y'] - a['y'] for a, b in successive_pairs(lines)]
    spacing = st.median(line_spacings) if line_spacings else 60
    notes, body = split_footnotes(lines, page_h, H)
    notes, body = recover_unmarked_footnotes(notes, body, page_h, B, H, base_width, spacing)
    notes = bind_footnotes(notes, body)
    body = recover_gaps(f, body, B, R)
    add_body_metrics(body, B, R)
    mark_date_lines(body, B, R)
    first_y = body[0]['y'] if body else 0
    return {'name': f, 'body': body, 'notes': notes, 'R': R, 'B': B, 'first_y': first_y, 'title': title, 'page_h': page_h}


def gap_box(prev, cur, B, R):
    return (max(0, B - 40), prev['b'] + 2, R + 40, cur['y'] - 2)


def box_is_tall_enough(box):
    return box[3] - box[1] > 25


def run_gap_ocr(crop):
    fd, tmp = tempfile.mkstemp(suffix='.png')
    os.close(fd)
    crop.save(tmp)
    try:
        return subprocess.run(
            [TESSERACT, tmp, '-', '-l', LANG, '--psm', '6'] + TESSERACT_ARGS + ['tsv'],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            check=False,
        ).stdout
    finally:
        with contextlib.suppress(OSError):
            os.remove(tmp)


def group_tsv_words(tsv):
    grouped = {}
    rows = [row.split('\t') for row in tsv.splitlines()[1:]]
    for row in rows:
        if len(row) == 12 and row[0] == '5' and row[11].strip():
            grouped.setdefault((row[2], row[3], row[4]), []).append((float(row[10]), row[11]))
    return grouped


def candidate_gap_lines(grouped, prev):
    candidates = []
    for words in grouped.values():
        words = [(conf, text) for conf, text in words if re.search(r'\w', text)] or words
        good = [text for conf, text in words if conf >= 55 and not any(ch in BAD_GAP_CHARS for ch in text)]
        if good and len(good) == len(words):
            candidates.append(' '.join(good))
        elif gap_word_continues(prev, words):
            candidates.append(words[0][1].lower())
    return candidates


def gap_word_continues(prev, words):
    return (
        re.search(r'\w-$', prev['t'])
        and words
        and short_lower_word(words[0][1])
    )


def short_lower_word(text):
    text = text.lower().rstrip(''.join(GAP_TRAILING_CHARS))
    return 1 <= len(text) <= 4 and all('a' <= ch <= 'z' or ch in 'çğıöşü' for ch in text)


def normalize_gap_line(line, prev):
    if re.search(r'\w-$', prev['t']) and 1 <= letters(line) <= 3:
        return line.split(' ')[0].lower()
    return line


def useful_gap_line(line, prev):
    return letters(line) >= 4 or (re.search(r'\w-$', prev['t']) and letters(line) >= 1 and line[:1].islower())


def recovered_line_bounds(img, box):
    crop = img.crop(box).convert('L')
    ink = np.array(crop) < 128
    cols = np.nonzero(ink.sum(0) > 0)[0]
    if not len(cols):
        return None
    return box[0] + int(cols[0]), box[0] + int(cols[-1])


def make_recovered_line(line, prev, cur, B, R, img, box):
    line = normalize_gap_line(line, prev)
    if not useful_gap_line(line, prev):
        return None
    recovered = {
        'words': [],
        'y': (prev['y'] + cur['y']) // 2,
        'b': (prev['b'] + cur['y']) // 2,
        'x': B,
        'r': R,
        't': line,
        'conf': 80,
        'h': prev['h'],
        'kind': 'text',
    }
    bounds = recovered_line_bounds(img, box)
    if bounds:
        recovered['x'], recovered['r'] = bounds
    return recovered


def recover_gap_lines(prev, cur, B, R, img):
    box = gap_box(prev, cur, B, R)
    if not box_is_tall_enough(box):
        return []
    crop = img.crop(box)
    ink = (np.array(crop.convert('L')) < 128).mean(1) > 0.004
    run = best_run = 0
    for value in ink:
        run = run + 1 if value else 0
        best_run = max(best_run, run)
    if best_run < 0.4 * prev['h']:
        return []
    tsv = run_gap_ocr(crop)
    candidates = candidate_gap_lines(group_tsv_words(tsv), prev)
    return [line for line in (make_recovered_line(c, prev, cur, B, R, img, box) for c in candidates) if line]


def recover_gaps(f, body, B, R):
    if len(body) < 4:
        return body
    ys = [d['y'] for d in body]
    med = st.median(b - a for a, b in successive_pairs(ys))
    img = open_image_copy(os.path.join(IMG_DIR, os.path.basename(f)[:-4] + '.png'))
    out = [body[0]]
    for prev, cur in successive_pairs(body):
        if cur['y'] - prev['y'] > 1.6 * med:
            for recovered in recover_gap_lines(prev, cur, B, R, img):
                if VERBOSE:
                    print('  atlanan satır kurtarıldı:', os.path.basename(f), recovered['t'], file=sys.stderr)
                out.append(recovered)
        out.append(cur)
    return out


def join_lines(parts):
    s = ''
    for p in parts:
        p = re.sub(r'(\w)-[\s.,*|]+$', r'\1-', p.strip())
        if not s:
            s = p
        elif re.search(r'\w-$', s) and p[:1].islower():
            s = merge_hyphenated_parts(s[:-1], p)
        else:
            s += ' ' + p
    return s


def merge_hyphenated_parts(left, right):
    if re.search(r'[aeıioöuü]$', left) and re.match(r'[ıiuü]k\b', right):
        return left + 'l' + right
    return left + right


def clean_text(s):
    strip_noise = EDGE_NOISE.replace('*', '')
    s = s.replace('“', '"').replace('”', '"').replace('„', '"').replace("''", '"')
    s = re.sub(r'\s+([,.;:!?])', r'\1', s)
    s = re.sub(r'\s{2,}', ' ', s)
    s = re.sub(r'^[-—–]\s*', '– ', s)
    s = re.sub(r'\s[-—]\s', ' – ', s)
    s = s.rstrip(strip_noise)
    s = s.lstrip(strip_noise)
    s = re.sub(r'(?<=\w)/$', '*', s)
    s = re.sub(r'^[a-zı] (?=[A-ZÇĞİÖŞÜ])', '', s)
    s = re.sub(r'([.!?…":]) (?:[A-ZÇĞİÖŞÜ]{1,2}|[a-zı][A-ZÇĞİÖŞÜ])$', r'\1', s)
    s = re.sub(r'(\w)-\.$', r'\1.', s)
    return s.strip()


def has_unclosed_span(text):
    return text.count('[') > text.count(']') or text.count('(') > text.count(')')


def open_ratio(lines):
    """Cümle sonu noktalamasıyla bitmeyen satır oranı (şiir dizeleri çoğu zaman açık biter)."""
    op = [d for d in lines if not re.search(r'[.!?:…"”»)]\W*$', d['t'])]
    return len(op) / max(1, len(lines))


def page_widths(pages):
    return [p['R'] - p['B'] for p in pages if len(p['body']) > 15]


def page_needs_width_fix(page, width):
    return abs((page['R'] - page['B']) - width) > 0.05 * width


def right_edge_is_more_stable(page):
    left_count = sum(1 for d in page['body'] if abs(d['x'] - page['B']) < 25)
    right_count = sum(1 for d in page['body'] if abs(d['r'] - page['R']) < 25)
    return right_count > left_count


def shift_left_edge(page, width):
    new_left = page['R'] - width
    for d in page['body']:
        d['rel'] = d['rel'] + (page['B'] - new_left)
    page['B'] = new_left


def set_right_edge(page, width):
    page['R'] = page['B'] + width


def update_line_widths(page, width):
    for d in page['body']:
        d['width'] = width


def update_short_flags(page):
    for d in page['body']:
        d['short'] = d['r'] < page['R'] - 110


def normalize_page_width(page, width):
    if page_needs_width_fix(page, width):
        if right_edge_is_more_stable(page):
            shift_left_edge(page, width)
        else:
            set_right_edge(page, width)
        update_short_flags(page)
    update_line_widths(page, width)


def normalize(pages):
    """Sağ kenarı kitap genelindeki metin genişliğine göre düzelt (şiir ağırlıklı sayfalar için)."""
    widths = page_widths(pages)
    if widths:
        width = st.median(widths)
        for page in pages:
            normalize_page_width(page, width)


def is_repeated_page_title(line, current_title):
    if not current_title or line.get('kind') != 'text':
        return False
    text = line.get('t', '')
    return len(text) <= 80 and loose_title_match(text, current_title)


def remove_repeated_page_title(body, current_title):
    if not body or not is_repeated_page_title(body[0], current_title):
        return body
    body = [dict(line) for line in body[1:]]
    if body:
        body[0]['gap'] = False
    return body


def page_starts_chapter(page, page_title):
    return bool(page_title) or (page['first_y'] > page['page_h'] * 0.35 and len(page['body']) > 3)


def body_for_items(page, page_title, current_title):
    if page_title:
        return page['body']
    return remove_repeated_page_title(page['body'], current_title)


def append_body_items(items, body, chapter_start):
    definition_rows = definition_list_rows(body)
    if definition_rows:
        items.append({'kind': 'deflist', 'rows': definition_rows})
        return
    for i, d in enumerate(body):
        items.append({**d, 'page_first': i == 0, 'chap': chapter_start})


def append_note_items(items, notes):
    for note_id, note in notes:
        items.append({'kind': 'note', 't': clean_text(note), 'id': note_id})


def side_label_match(text):
    return re.match(r'^(Üç\s+[^\d:]{2,20}:)\s+(.+)$', text)


def side_label_group_line(line):
    text = line.get('t', '')
    return line.get('rel', 0) > 140 or re.match(r'^[>.\s-]*[0-9]+\.', text) or len(text) < 45


def first_side_label_group_index(body, label_index):
    start = label_index
    cursor = label_index - 1
    while cursor >= 0 and side_label_group_line(body[cursor]):
        start = cursor
        if body[cursor].get('gap'):
            break
        cursor -= 1
    return start


def last_side_label_group_index(body, label_index):
    end = label_index + 1
    while end < len(body) and not body[end].get('gap') and side_label_group_line(body[end]):
        end += 1
    return end


def side_label_list_line(source, text, gap=False):
    line = dict(source)
    line['t'] = re.sub(r'^[>.\s-]+', '', text).replace('farklıolan', 'farklı olan')
    line['gap'] = gap
    line['rel'] = max(line.get('rel', 0), 180)
    line['verse'] = True
    line['manual_verse'] = True
    return line


def recover_side_label_lists(body):
    out = []
    index = 0
    while index < len(body):
        match = side_label_match(body[index].get('t', ''))
        if not match or body[index].get('rel', 0) > 80:
            out.append(body[index])
            index += 1
            continue
        start = first_side_label_group_index(body, index)
        end = last_side_label_group_index(body, index)
        previous_group_count = index - start
        if previous_group_count:
            del out[-previous_group_count:]
        label, rest = match.groups()
        out.append(side_label_list_line(body[start], label, gap=body[start].get('gap', False)))
        for line in body[start:index]:
            out.append(side_label_list_line(line, line['t']))
        out.append(side_label_list_line(body[index], rest))
        for line in body[index + 1:end]:
            out.append(side_label_list_line(line, line['t']))
        index = end
    return out


def build_items(pages):
    items = []
    current_title = None
    for pg in pages:
        page_title = pg.get('title')
        chapter_start = page_starts_chapter(pg, page_title)
        if chapter_start:
            if page_title:
                current_title = page_title
            items.append({'kind': 'chapter', 'title': page_title})
        body = recover_side_label_lists(body_for_items(pg, page_title, current_title))
        append_body_items(items, body, chapter_start)
        append_note_items(items, pg['notes'])
    return items


def largest_word_gap(words):
    gaps = [(words[i + 1]['x'] - words[i]['r'], i, words[i + 1]['x']) for i in range(len(words) - 1)]
    return max(gaps, default=(0, None, None))


def likely_definition_gap(line):
    words = line.get('words') or []
    if len(words) < 2:
        return None
    gap, index, column = largest_word_gap(words)
    threshold = max(70, 0.08 * line['width'])
    if gap < threshold or index is None:
        return None
    left_text = ' '.join(w['t'] for w in words[:index + 1])
    right_text = ' '.join(w['t'] for w in words[index + 1:])
    if letters(left_text) < 2 or letters(right_text) < 2:
        return None
    if not (line['x'] + 0.18 * line['width'] < column < line['x'] + 0.65 * line['width']):
        return None
    return column


def definition_column(lines):
    columns = [column for column in (likely_definition_gap(line) for line in lines) if column is not None]
    if len(columns) < 4:
        return None
    column = st.median(columns)
    clustered = sum(1 for value in columns if abs(value - column) <= 45)
    if clustered < max(4, int(0.6 * len(columns))):
        return None
    return column


def split_definition_line(line, column):
    words = line.get('words') or []
    split_at = next((i for i, word in enumerate(words) if word['x'] >= column - 45), None)
    if split_at is None or split_at == 0:
        return None
    term = clean_text(' '.join(w['t'] for w in words[:split_at]))
    desc = clean_text(' '.join(w['t'] for w in words[split_at:]))
    if letters(term) < 1 or letters(desc) < 2:
        return None
    return [term, desc]


def is_definition_continuation(line, column):
    return abs(line['x'] - column) <= 70 or line['x'] >= column - 35


def append_definition_continuation(rows, text):
    rows[-1][1] = clean_text(join_lines([rows[-1][1], text]))


def definition_list_rows(lines):
    text_lines = [line for line in lines if line['kind'] == 'text']
    column = definition_column(text_lines)
    if column is None:
        return None

    rows = []
    split_count = 0
    for line in text_lines:
        row = split_definition_line(line, column)
        if row:
            rows.append(row)
            split_count += 1
        elif rows and is_definition_continuation(line, column):
            append_definition_continuation(rows, line['t'])
        else:
            return None

    if len(rows) < 4 or split_count < 4:
        return None
    return rows


def base_verse_candidate(d):
    return (d['rel'] > 60 and not re.match(r"^\W{0,2}[-—–]", d['t'])
            and not re.match(r"^[\"“”'‘«]", d['t'])
            and not re.search(r'\w-$', d['t']))


def paragraph_indent(seq):
    ind = sorted(d['rel'] for d in seq if 40 < d['rel'] < 400)
    return ind[len(ind) // 2] if ind else 100


def starts_regular_paragraph(d, nxt):
    return (
        nxt is not None
        and (
            (not nxt['gap'] and not nxt['page_first'] and nxt['rel'] < 40)
            or (nxt['page_first'] and nxt['rel'] < 40 and not d['short'])
        )
    )


def verse_candidates(seq):
    candidates = []
    for j, d in enumerate(seq):
        d['verse'] = bool(d.get('manual_verse'))
        nxt = seq[j + 1] if j + 1 < len(seq) else None
        candidates.append(base_verse_candidate(d) and not starts_regular_paragraph(d, nxt))
    return candidates


def candidate_runs(seq, candidates):
    j = 0
    while j < len(seq):
        if not candidates[j]:
            j += 1
            continue
        k = j
        while k + 1 < len(seq) and candidates[k + 1]:
            k += 1
        yield seq[j:k + 1]
        j = k + 1


def mark_indented_verses(seq):
    # şiir: girintili + kısa + diyalog değil, en az 2 ardışık
    para_indent = paragraph_indent(seq)
    for run in candidate_runs(seq, verse_candidates(seq)):
        deep = all(d['rel'] > 1.6 * para_indent for d in run)
        if (deep and len(run) >= 2) or (len(run) >= 3 and open_ratio(run) >= 0.3):
            for d in run:
                d['verse'] = True


def is_short_verse_line(d):
    return (d['r'] - (d['x'] - d['rel'])) < 0.9 * d['width'] and not re.search(r'\w-$', d['t']) \
        and not re.match(r"^\W{0,2}[-—–\"“”'‘«]", d['t'])


def mark_short_verse_group(group):
    if not 2 <= len(group) <= 16:
        return
    widths = [(d['r'] - (d['x'] - d['rel'])) / d['width'] for d in group]
    if all(is_short_verse_line(d) for d in group) and (st.mean(widths) < 0.75 or open_ratio(group) >= 0.3):
        for d in group:
            d['verse'] = True


def mark_short_verses(items):
    # Şiir (2): boşluklarla çevrili, bütün satırları kısa olan blok (girintisiz ninni/türkü dizilişi).
    group = []
    prev_closed = True
    for it in items:
        if it['kind'] != 'text':
            mark_short_verse_group(group)
            group.clear()
            prev_closed = True
            continue
        if it['gap']:
            mark_short_verse_group(group)
            group.clear()
            prev_closed = True
        if prev_closed:
            group.append(it)
            prev_closed = False
        elif group:
            group.append(it)
    # son grup: arkasından boşluk gelmedikçe şiir sayma
    group.clear()


def mark_chapter_numbers(seq):
    # Yeni grup yalnızca boşluktan sonra başlar; boşluksuz paragraflar grubu bozar.
    for d in seq:
        if d['chap'] and d['rel'] > 150 and not d['gap'] and re.fullmatch(r"[-\s]*[0-9IJl]{1,3}[.\-\s]*", d['t']):
            d['kind'] = 'chapnum'


def prepare_text_items(items):
    seq = [it for it in items if it['kind'] == 'text']
    mark_indented_verses(seq)
    mark_short_verses(items)
    mark_chapter_numbers(seq)


class BlockBuilder:
    def __init__(self):
        self.blocks: list = []
        self.cur: list = []
        self.prev = None
        self.verse_buf: list = []
        self.in_epigraph = False

    def flush(self):
        if self.cur:
            self.blocks.append(('p', clean_text(join_lines(self.cur))))
        self.cur = []

    def flush_verse(self, cls):
        if self.verse_buf:
            self.blocks.append(('verse', [clean_text(v) for v in self.verse_buf], cls))
        self.verse_buf = []

    def verse_class(self):
        return 'epigraph' if self.in_epigraph else 'verse'

    def append_break(self):
        if self.blocks and self.blocks[-1][0] not in ('break', 'chapter'):
            self.blocks.append(('break',))

    def start_chapter(self, title):
        self.flush()
        self.flush_verse('verse')
        self.blocks.append(('chapter', title))
        self.in_epigraph = True
        self.prev = None

    def add_note(self, text, note_id=None):
        self.flush()
        self.flush_verse(self.verse_class())
        self.blocks.append(('note', text, note_id))
        self.prev = None

    def add_date(self, text):
        self.flush()
        self.flush_verse('verse')
        if self.blocks and self.blocks[-1][0] == 'break':
            self.blocks.pop()
        self.blocks.append(('date', clean_text(text)))
        self.prev = None

    def add_definition_list(self, rows):
        self.flush()
        self.flush_verse(self.verse_class())
        self.blocks.append(('deflist', rows))
        self.prev = None

    def add_break(self):
        self.flush()
        self.flush_verse(self.verse_class())
        self.append_break()
        self.prev = None

    def add_end(self):
        self.flush()
        self.flush_verse('verse')
        self.blocks.append(('end',))

    def merge_hyphenated_line(self, item, text):
        if self.prev is not None and re.search(r'\w-$', self.prev['t']) and text[:1].islower():
            item['gap'] = False
            item['rel'] = 0
        if self.prev is not None and has_unclosed_span(self.prev['t']):
            item['gap'] = False
            item['rel'] = 0

    def is_continued_verse_gap(self, item):
        return item['gap'] and self.prev is not None and item.get('verse') and self.prev.get('verse')

    def keep_verse_gap(self, item, text):
        if self.is_continued_verse_gap(item):
            self.flush_verse('verse')
            self.verse_buf.append(text)
            self.prev = item

    def absorb_verse_gap(self, item):
        has_verse_neighbor = self.prev is not None and (item.get('verse') or self.prev.get('verse'))
        if item['gap'] and has_verse_neighbor and not self.in_epigraph:
            self.flush()
            self.flush_verse('verse')
            item['gap'] = False

    def close_paragraph_gap(self, item):
        if item['gap'] and self.prev is not None:
            self.flush()
            self.flush_verse(self.verse_class())
            if self.in_epigraph:
                self.in_epigraph = False
            else:
                self.append_break()

    def close_epigraph_if_needed(self, item):
        if self.in_epigraph and not (item['rel'] > 150):
            self.in_epigraph = False
            self.flush_verse('epigraph')

    def is_verse_line(self, item):
        return item['verse'] or self.in_epigraph

    def add_verse_line(self, item, text):
        self.flush()
        self.verse_buf.append(text)
        self.prev = item

    def starts_new_paragraph(self, item):
        return (
            not self.cur
            or item['rel'] > 60
            or (self.prev is not None and self.prev['short'] and self.prev['t'].rstrip(' |*').endswith(END_PUNCT))
        )

    def add_paragraph_line(self, item, text):
        self.flush_verse('verse')
        if self.starts_new_paragraph(item):
            self.flush()
            self.cur = [text]
        else:
            self.cur.append(text)
        self.prev = item

    def add_text(self, item):
        text = item['t']
        self.merge_hyphenated_line(item, text)
        if re.fullmatch(r'\W*SON\W*', text):
            self.add_end()
        elif self.is_continued_verse_gap(item):
            self.keep_verse_gap(item, text)
        else:
            self.absorb_verse_gap(item)
            self.close_paragraph_gap(item)
            self.close_epigraph_if_needed(item)
            self.add_flowing_text(item, text)

    def add_flowing_text(self, item, text):
        if self.is_verse_line(item):
            self.add_verse_line(item, text)
        else:
            self.add_paragraph_line(item, text)

    def add_item(self, item):
        kind = item['kind']
        if kind == 'chapter':
            self.start_chapter(item.get('title'))
        elif kind == 'note':
            self.add_note(item['t'], item.get('id'))
        elif kind == 'date':
            self.add_date(item['t'])
        elif kind == 'deflist':
            self.add_definition_list(item['rows'])
        elif kind == 'break':
            self.add_break()
        elif kind != 'chapnum':
            self.add_text(item)

    def finish(self):
        self.flush()
        self.flush_verse('verse')
        return self.blocks


def build(pages):
    """Bütün sayfaları bloklara dönüştürür: chapter / p / verse / deflist / break / note."""
    items = build_items(pages)
    prepare_text_items(items)
    builder = BlockBuilder()
    for item in items:
        builder.add_item(item)
    return builder.finish()
