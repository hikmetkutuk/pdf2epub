"""Şiir kitabı kipi: her satır bir dize, her şiir bir bölüm, iki dilli (İngilizce/Türkçe) sayfalar."""
import difflib
import itertools
import re
import statistics as st

import structure

EN_WORDS = frozenset(['the', 'and', 'of', 'to', 'in', 'my', 'you', 'is', 'with', 'her', 'his', 'your', 'our', 'that', 'this', 'for', 'not', 'are', 'was', 'be', 'but', 'all', 'as', 'at', 'on', 'we', 'from'])
TR_WORDS = frozenset(['bir', 've', 'bu', 'ile', 'de', 'da', 'ne', 'gibi', 'her', 'için', 'daha', 'ama', 'çok', 'kadar', 'sonra', 'ben', 'sen', 'o', 'değil', 'mi', 'var', 'yok', 'içinde', 'ona', 'onu', 'bana', 'sana'])
POEM_NUMBER = re.compile(r'^\W*(\d{1,3}|[IVXL]{1,6})\W{0,2}$')
SUPERSCRIPTS = '¹²³⁴⁵⁶⁷⁸⁹⁰'  # \w sayıldıkları için başlık sonundaki dipnot işareti olarak ayrıca atılır
NOTE_START = re.compile(r'^[\s|]*(\d{1,2}|[ıIl])\s+(?=\S)')
MAX_TITLE = 70
TOKEN = re.compile(r'⟦\d+⟧')
OCR_DIGITS = re.compile(r"(?<=[^\W\d_])(\d{1,2})$|(?<=[.,;:!?'’\"”])[!|]?(\d{1,2})$")
JUNK_TAIL = re.compile(r'(?:[?®|_]+|!{2})$')  # işaretin OCR'da bıraktığı artık karakterler
STRAY_MARK = re.compile(r'(?:[!|]?\d{1,2}|[-_]+)$')
SEP = r"[-_\xad:;,]*"
STRICT_MARKS = (  # (desen, sayısal mı): OCR'ın yapışık okuduğu üst simge işaretleri
    re.compile(rf"(?P<w>.*[^\W\d_])(?P<n>\d{{1,2}}){SEP}$"),                       # biri4, kıçlarınızda9:
    re.compile(r"(?P<w>.*[.,;:!?'’\"”])(?P<n>\d{1,2}|\d[lIı])[-_\xad]*$"),          # bavulundan.13, diye?2I
    re.compile(rf"(?P<w>.*[a-zçğıöşü])(?P<n>S|[lIıi][OoI\d]){SEP}$"),                # deS, ağlamaklılO
    re.compile(r"(?P<w>.*[,;:])(?P<n>\?)$"),                                         # üzerine,?
    re.compile(rf"(?P<w>.*[a-zçğıöşü])(?P<n>ll|ıı|ii|II){SEP}$"),                          # asaletinill (11)
)
LOOSE_MARKS = (  # yalnızca eksik işaret sayısını tamamlamak için
    re.compile(r"(?P<w>.*[a-zçğıöşü])(?P<n>[A-Z])$"),                               # deniB
    re.compile(rf"(?P<w>.*[a-zçğıöşü])(?P<n>[ilıI]{{1,2}}){SEP}$"),                  # bastımil, Gazl
)
GLYPH_NUMBERS = {'S': 8, '?': 7, 'lO': 10, 'iO': 10, 'ıO': 10, 'IO': 10, 'io': 10, 'ıo': 10,
                 'll': 11, 'ıı': 11, 'ii': 11, 'II': 11}


def guess_lang(lines):
    words = [w for line in lines if line['kind'] == 'text' for w in re.findall(r"[^\W\d_]+", line['t'].lower())]
    en = sum(w in EN_WORDS for w in words)
    tr = sum(w in TR_WORDS for w in words) + sum(1 for w in words if re.search('[ıöüşğç]', w)) * 0.5
    if en == tr:
        return None
    return 'en' if en > tr else 'tr'


def clean_title(text):
    text = TOKEN.sub('', text).strip()
    while text and (not text[-1].isalnum() or text[-1] == '_' or text[-1] in SUPERSCRIPTS):
        text = text[:-1]
    return text.strip()


def raw_line_texts(pg):
    """Ham OCR satırları: {y: metin}. Yapı aşaması baştaki dipnot numarasını sildiği için buradan okunur."""
    return {line['y']: ' '.join(w['t'] for w in line['words']) for line in structure.read_lines(pg['name'])}


def raw_text_at(raw, y):
    near = min(raw, key=lambda ry: abs(ry - y), default=None)
    return raw[near] if near is not None and abs(near - y) < 8 else ''


def small_note_start(pg):
    """Sayfa altındaki, gövdeden belirgin biçimde küçük puntolu satırların başlangıç dizini (yoksa None)."""
    body, low = pg['body'], pg['page_h'] * 0.6
    heights = [d['h'] for d in body if d['kind'] == 'text' and d['y'] < low]
    if len(heights) < 4:
        return None
    limit = st.median(heights) * 0.9
    start = len(body)
    while start > 1 and body[start - 1]['y'] > low and body[start - 1]['h'] < limit:
        start -= 1
    if start == len(body):
        return None
    # dipnot bloğu gövdeden bir boşlukla ayrılır; boşluktan önceki küçük satırlar dizedir
    gap = next((i for i in range(start, len(body)) if body[i].get('gap')), None)
    return gap


def note_number(match, previous):
    """Dipnot numarası; '1' yerine okunan ı/I/l harfleri öncekinin ardışığı olarak alınır."""
    token = match.group(1)
    return int(token) if token.isdigit() else previous + 1


def split_unnumbered(lines, target=0):
    """Numarası okunamayan dipnotları ayırır: sağ kenara varmayan, noktayla biten satır notu bitirir.
    Metindeki işaret sayısı (target) daha fazlaysa, en kısa satırlar da ek sınır olarak alınır."""
    right = max(d['r'] for d in lines)
    ends = {i for i, d in enumerate(lines[:-1])
            if d['r'] < right * 0.9 and d['t'].rstrip().endswith(('.', '_', '…'))}
    spare = sorted((i for i in range(len(lines) - 1) if i not in ends), key=lambda i: lines[i]['r'])
    while target and len(ends) + 1 < target and spare:
        ends.add(spare.pop(0))
    while target and len(ends) + 1 > target:  # işaretten az dipnot var: en az kesin sınırlar kaldırılır
        ends.remove(max(ends, key=lambda i: lines[i]['r']))
    notes, current = [], []
    for i, d in enumerate(lines):
        current.append(d['t'])
        if i in ends:
            notes.append((0, current))
            current = []
    if current:
        notes.append((0, current))
    return notes


def read_notes(pg, start, target=0):
    """[(numara, [satırlar])]; numara ham OCR'daki baştaki rakamdan okunur, okunamadıysa 0."""
    raw, notes = raw_line_texts(pg), []
    run = pg['body'][start:]
    numbered = [bool(NOTE_START.match(raw_text_at(raw, d['y']))) for d in run]
    if not numbered[0] or not any(numbered):  # ilk dipnotun numarası okunamadıysa sıra güvenilmez
        return split_unnumbered(run, target)
    for d in run:
        match = NOTE_START.match(raw_text_at(raw, d['y']))
        if match:
            notes.append((note_number(match, notes[-1][0] if notes else 0), [d['t']]))
        elif not notes:
            notes.append((0, [d['t']]))
        else:
            notes[-1][1].append(d['t'])
    return notes


def raw_note_lines(pg):
    """Sayfa gövdesinin altındaki ham OCR satırları, dipnot ayrıştırıcısının beklediği biçimde."""
    last = pg['body'][-1]['y'] if pg['body'] else 0
    lines = []
    for line in structure.read_lines(pg['name']):
        text = ' '.join(w['t'] for w in line['words'])
        if last + 20 < line['y'] < pg['page_h'] * 0.96 and not re.fullmatch(r'\W*\d{0,3}\W*', text):
            lines.append({'t': NOTE_START.sub('', text), 'y': line['y'], 'r': line['words'][-1]['r'], 'kind': 'text'})
    return lines


def resplit_recovered_notes(pg, markers):
    """Yapı aşamasının tek parça birleştirdiği dipnotlar, işaret sayısıyla tutmuyorsa ham satırlardan yeniden bölünür."""
    if not pg['notes'] or len(pg['notes']) >= len(markers):
        return None
    lines = raw_note_lines(pg)
    if not lines:
        return None
    pg['notes'].clear()
    pg['body'].extend(lines)
    return len(pg['body']) - len(lines)


def number_span(markers):
    nums = [m['number'] for m in markers if m['number']]
    return max(nums) - min(nums) + 1 if nums and max(nums) - min(nums) <= 15 else 0


def ocr_markers(pg, limit):
    """OCR satırlarında okunan, sonda yapışık rakamlı işaretler (PDF katmanında kaybolmuş olabilir)."""
    found = []
    for d in pg['body']:
        match = OCR_DIGITS.search(d['t']) if d['kind'] == 'text' and d['y'] / pg['page_h'] < limit else None
        if match and d['t'].split():
            found.append({'y': d['y'] / pg['page_h'], 'index': 999, 'word': d['t'].split()[-1], 'number': int(next(g for g in match.groups() if g))})
    return found


def title_marker(pg, loose_all):
    """Başlıktaki işaret (örn. "Yakıcı Gaz¹"): tek gevşek aday ilk satırdaysa tek dipnotun işaretidir."""
    first = pg['body'][0]['y'] / pg['page_h'] if pg['body'] else None
    near = [m for m in loose_all if first is not None and abs(m['y'] - first) < 0.02]
    return near if pg['first_y'] > pg['page_h'] * 0.2 and len(near) == 1 else []


def page_markers(doc, pg, start):
    """(işaretler, dipnot sayısı): dipnot sayısı doğal bölmeden ve numara aralığından çıkar; işaret sayısı
    ondan azsa gevşek adaylarla tamamlanır."""
    limit = pg['body'][start]['y'] / pg['page_h'] if start is not None else 1.0
    strict = [m for m in layer_markers(doc, pg['number']) if m['y'] < limit]
    if start is None:
        return strict, 0
    count = max(len(read_notes(pg, start)), number_span(strict))
    loose_all = [m for m in layer_markers(doc, pg['number'], loose=True) if m['y'] < limit]
    if not strict and (title := title_marker(pg, loose_all)):
        return title, 1
    return fill_markers(strict, loose_all + ocr_markers(pg, limit), count), count


def split_small_notes(pg, doc):
    """Sayfa altındaki numaralı çevirmen dipnotlarını dipnota çevirir ve metindeki işaretlere bağlar."""
    start = resplit_recovered_notes(pg, layer_markers(doc, pg['number']))
    if start is None:
        start = small_note_start(pg)
    markers, count = page_markers(doc, pg, start)
    notes = read_notes(pg, start, max(len(markers), count)) if start is not None else []
    if notes and not (any(n for n, _ in notes) or markers):
        notes = []  # işaret de numara da yoksa küçük satırlar dipnot değildir
    loose = [note_id for note_id, _ in pg['notes']]
    if notes:
        del pg['body'][start:]
    ids = {}
    for number, lines in notes:
        structure.NOTE_ID[0] += 1
        pg['notes'].append((structure.NOTE_ID[0], structure.clean_text(structure.join_lines(lines))))
        if number:
            ids[number] = structure.NOTE_ID[0]
        else:
            loose.append(structure.NOTE_ID[0])
    link_note_markers(pg['body'], ids, loose, markers, pg['page_h'])


def token_marker(token, nxt, prev, loose=False, last=False):
    """Bir sözcükteki dipnot işareti: (işaretten önceki sözcük, numara ya da None) ya da None.
    Gevşek kipte "il" gibi sonekler yalnızca satırın son sözcüğünde işaret sayılır."""
    if not loose:
        patterns = STRICT_MARKS
    elif last:
        patterns = LOOSE_MARKS
    else:
        patterns = LOOSE_MARKS[:1]
    for pattern in patterns:
        match = pattern.fullmatch(token)
        if match:
            digits = match.group('n')
            return match.group('w'), int(digits) if digits.isdigit() else GLYPH_NUMBERS.get(digits)
    if not loose and re.fullmatch(r'\d{1,2}', token) and prev and re.search(r"[!?.,;:]$", prev) and nxt:
        return prev, int(token)  # "cücükler! 16 Wellington": ayrı yazılmış işaret
    if not loose and last and prev and re.fullmatch(r'(?:ll|ıı|ii|II|U)[-_\xad]*', token):
        return prev, GLYPH_NUMBERS.get(token.strip('-_\xad'))  # "sevenlerden ıı": ayrı okunmuş işaret
    return None


def line_markers(text, y, loose):
    tokens = text.split()
    found = []
    for i, token in enumerate(tokens):
        prev = tokens[i - 1] if i else ''
        nxt = tokens[i + 1] if i + 1 < len(tokens) else ''
        marker = token_marker(token, nxt, prev, loose, last=i == len(tokens) - 1)
        if marker:
            found.append({'y': y, 'index': i, 'word': marker[0], 'number': marker[1]})
    return found


def layer_markers(doc, number, loose=False):
    """PDF metin katmanındaki dipnot işaretleri, okuma sırasıyla."""
    page = doc[number - 1]
    found = []
    for block in page.get_text('dict')['blocks']:
        for line in block.get('lines', []):
            if max(s['size'] for s in line['spans']) > 8:
                text = ''.join(s['text'] for s in line['spans'])
                found += line_markers(text, line['bbox'][1] / page.rect.height, loose)
    return sorted(found, key=lambda m: (m['y'], m['index']))


def consistent(markers):
    """Bilinen numaralar sıra numarasıyla aynı farkta artıyorsa (örn. 14,15,16,?,18) dizi tutarlıdır."""
    offsets = {m['number'] - i for i, m in enumerate(markers) if m['number']}
    return len(offsets) <= 1


def fill_markers(strict, loose_all, count):
    """Kesin işaretler dipnot sayısından azsa, numara dizisini tutarlı kılan gevşek adaylarla tamamlar.
    Tek tutarlı seçim yoksa kesin işaretlerle yetinilir (eşleşmezse bağ kurulmaz)."""
    need = count - len(strict)
    if need <= 0:
        return strict
    taken = {(m['y'], m['index']) for m in strict}
    extras = [m for m in loose_all if (m['y'], m['index']) not in taken]
    key = lambda m: (m['y'], m['index'])
    options = [sorted(strict + list(c), key=key) for c in itertools.combinations(extras, need)]
    options = [o for o in options if consistent(o)]
    numbered = [o for o in options if all(m['number'] for m in o)]  # numarası okunmuş adaylar tercih edilir
    for pool in (numbered, options):
        if len(pool) == 1:
            return pool[0]
    return strict


def nearest_line(body, y):
    return min((d for d in body if d['kind'] == 'text'), key=lambda d: abs(d['y'] - y), default=None)


def word_key(token):
    return re.sub(r'[\W\d_]+$', '', token.lower())


def best_token(lines, word):
    """Aday satırlar arasında, PDF katmanındaki sözcüğe en çok benzeyen (satır, sözcük dizini, benzerlik)."""
    key = word_key(word)
    best = (None, 0, 0.0)
    for line in lines:
        for i, token in enumerate(line['t'].split()):
            ratio = difflib.SequenceMatcher(None, word_key(token), key).ratio()
            if ratio > best[2]:
                best = (line, i, ratio)
    return best


def place_marker(body, y, word, note_id):
    """İşareti, PDF katmanındaki konuma en yakın birkaç satır içinde sözcüğe en çok benzeyen sözcüğün sonuna koyar."""
    near = sorted((d for d in body if d['kind'] == 'text'), key=lambda d: abs(d['y'] - y))[:3]
    line, index, ratio = best_token(near, word)
    if line is None or ratio < 0.5:
        return
    tokens = line['t'].split()
    tokens[index] = JUNK_TAIL.sub('', STRAY_MARK.sub('', tokens[index])) + f'⟦{note_id}⟧'
    line['t'] = ' '.join(tokens)


def link_note_markers(body, ids, loose, markers, page_h):
    """Numarası bilinen dipnotlar işaret numarasıyla; bilinmeyenler, işaret sayısı dipnot sayısına
    eşitse sırayla bağlanır. Sayılar tutmuyorsa yanlış bağ kurmamak için bırakılır."""
    for number, note_id in sorted(ids.items()):
        pattern = re.compile(rf'(?<=[^\W\d_]|[.,;:!?\'’"”]){number}$')
        line = next((d for d in body if d['kind'] == 'text' and pattern.search(d['t'])), None)
        if line is not None:
            line['t'] = pattern.sub(f'⟦{note_id}⟧', line['t'])
        elif number == 1 and body and re.search(r'[!¹1lıI]$', body[0]['t']):  # başlıktaki işaret
            body[0]['t'] = re.sub(r'[!¹1lıI]$', '', body[0]['t']) + f'⟦{note_id}⟧'
    if not loose or len(markers) != len(loose):
        return
    for marker, note_id in zip(markers, loose):
        place_marker(body, marker['y'] * page_h, marker['word'], note_id)


def poem_number(pg):
    """Sayfa numarası sanılıp atılan şiir numarasını (örn. '10.') ham OCR satırlarından kurtarır."""
    first_y = pg['body'][0]['y'] if pg['body'] else pg['page_h']
    for line in structure.read_lines(pg['name']):
        text = ' '.join(w['t'] for w in line['words'])
        if line['y'] < first_y - 20 and line['y'] > pg['page_h'] * 0.1 and POEM_NUMBER.match(text):
            return clean_title(text)
    return None


def first_stanza_line(body):
    return clean_title(body[0]['t']) if body else ''


def is_title_line(body):
    """İlk satır, ardından boşluk gelen kısa, cümle gibi bitmeyen bir satırsa başlıktır."""
    if len(body) < 2 or not body[1].get('gap') or len(body[0]['t']) > MAX_TITLE:
        return False
    text = body[0]['t'].rstrip()
    return len(text.split()) <= 6 or not text.endswith(('.', ',', ';', ':', '?', '!'))


def split_title(pg):
    """(başlık, numara_mı) döndürür ve başlık satırını gövdeden çıkarır."""
    number = poem_number(pg)
    if number:
        return number, True
    body = pg['body']
    if is_title_line(body):
        return clean_title(body.pop(0)['t']), False
    return None, False


def chapter_label(title, numbered, body):
    """Numaralı şiirde "10. Bright cap and streamers", başlıksız şiirde ilk dize."""
    if not title:
        return first_stanza_line(body)
    return f'{title}. {first_stanza_line(body)}' if numbered else title


def prepare_section_page(pg, state):
    """Bölüm ara sayfası ("Kutsal Görev (1904)"). Hemen ardından gelen ikinci dildeki ara sayfa alt başlık olur."""
    raw = raw_line_texts(pg)  # yıl satırı "(1907)" sayfa numarası sanılıp gövdeden atılmış olabilir
    lines = [raw[y] for y in sorted(raw) if pg['page_h'] * 0.1 < y < pg['page_h'] * 0.85]
    text = ' '.join(clean_title(line) if i == 0 else line for i, line in enumerate(lines))
    if state.get('section_only'):
        pg['title'] = None
        pg['body'] = [{'kind': 'subhead', 't': text, 'y': 0}]
    else:
        pg['title'] = text
        pg['body'] = []
        pg['section'] = True
        state['section'] = section_key(text)
    state['section_only'] = True


def mark_verse(body):
    for d in body:
        if d['kind'] == 'text':
            d['verse'] = d['manual_verse'] = True


def prepare_page(pg, state, doc):
    split_small_notes(pg, doc)
    body = pg['body']
    lang = guess_lang(body) or state.get('lang')
    starts = pg['first_y'] > pg['page_h'] * 0.2
    pg['first_y'] = 0
    pg['title'] = None
    if starts and len(body) <= 2:
        prepare_section_page(pg, state)
        return
    state['section_only'] = False
    if starts:
        title_tokens = TOKEN.findall(body[0]['t']) if body else []
        title, numbered = split_title(pg)
        if lang != 'tr' and section_key(title) != state.get('section'):  # bölümle aynı adlı tek şiir ayrı bölüm olmaz
            pg['title'] = chapter_label(title, numbered, pg['body'])
        elif title and not numbered:
            body.insert(0, {'kind': 'subhead', 't': title + ''.join(title_tokens), 'y': 0})
    mark_verse(pg['body'])
    pg['body'].insert(0, {'kind': 'lang', 't': '', 'lang': lang, 'y': 0})
    pg['lang'] = lang
    state['lang'] = lang


NUMBERED = re.compile(r'^(\d{1,3})\.\s*(.*)$')


def section_key(title):
    return re.sub(r'[\W\d_]+', '', (title or '').lower())


def fill_poem_numbers(pages):
    """Ara sayfalar arasındaki şiir dizisinde OCR'ın düşürdüğü numaraları ardışıklıktan tamamlar."""
    runs, run = [], []
    for pg in pages:
        if pg.get('section'):
            runs.append(run)
            run = []
        elif pg.get('title'):
            run.append(pg)
    runs.append(run)
    for run in runs:
        found = [(i, int(m.group(1))) for i, pg in enumerate(run) if (m := NUMBERED.match(pg['title']))]
        if not found or len({n - i for i, n in found}) != 1:
            continue
        start = found[0][1] - found[0][0]
        for i, pg in enumerate(run):
            if not NUMBERED.match(pg['title']) and start + i >= 1:
                pg['title'] = f'{start + i}. {pg["title"]}'


def group_languages(pages):
    """Her şiirin (iki bölüm başlığı arasındaki sayfaların) İngilizce sayfalarını Türkçe sayfalardan önce toplar."""
    start = 0
    for i in range(1, len(pages) + 1):
        if i == len(pages) or pages[i].get('title'):
            run = pages[start:i]
            pages[start:i] = [pg for pg in run if pg.get('lang') != 'tr'] + [pg for pg in run if pg.get('lang') == 'tr']
            start = i


def prepare(pages, doc, prose_pages=frozenset()):
    state = {}
    for pg in pages:
        match = re.search(r'p-(\d+)', pg['name'])
        pg['number'] = int(match.group(1)) if match else 0
        if pg['number'] not in prose_pages:
            prepare_page(pg, state, doc)
    fill_poem_numbers(pages)
    group_languages(pages)
