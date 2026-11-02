"""Oyun metni kipi: konuşmacı adı, diyalog ve sahne yönergesi ayrı bloklar olur (A. Çehov, Martı gibi)."""
import collections
import re
import statistics as st
import unicodedata

import structure

LOW_CONF = 75
LAYER_TOLERANCE = 0.015
ACT = re.compile(r'^\W*[IVXL1]+\W*\s*Perde\W*$', re.IGNORECASE)
SPEAKER = re.compile(r"^[A-ZÇĞİÖŞÜÂÎÛ][A-ZÇĞİÖŞÜÂÎÛ.'’-]*(?: [A-ZÇĞİÖŞÜÂÎÛ][A-ZÇĞİÖŞÜÂÎÛ.'’-]*){0,3}$")
SPEAKER_REL = 45      # konuşmacı adı sol kenara yakındır
DIRECTION_REL = 140   # sahne yönergesi diyalogdan da içeridedir
ROMAN = ((10, 'X'), (9, 'IX'), (5, 'V'), (4, 'IV'), (1, 'I'))


def roman(number):
    out = ''
    for value, symbol in ROMAN:
        while number >= value:
            out += symbol
            number -= value
    return out


def layer_lines(page):
    """PDF metin katmanındaki satırlar: [(sayfa oranı olarak y, metin)]."""
    lines = []
    for block in page.get_text('dict')['blocks']:
        for line in block.get('lines', []):
            text = ''.join(s['text'] for s in line['spans']).replace('\xad', '').strip()
            if text:
                lines.append((line['bbox'][1] / page.rect.height, text))
    return lines


def mean_conf(line):
    return st.mean(w['c'] for w in line['words'])


def layer_words(line, text):
    """Metin katmanındaki satırı, OCR satırının sınırları içine yayılmış sözcüklere çevirir."""
    tokens = text.split()
    left, right = line['words'][0]['x'], line['words'][-1]['r']
    step = (right - left) / max(1, len(tokens))
    return [{'x': int(left + i * step), 'r': int(left + (i + 1) * step), 'y': line['words'][0]['y'],
             'b': line['words'][0]['b'], 't': token, 'c': 96.0} for i, token in enumerate(tokens)]


def nearest_layer_line(layer, frac):
    """Metin katmanında, verilen sayfa oranına en yakın satır (tolerans içindeyse)."""
    near = min(layer, key=lambda item: abs(item[0] - frac), default=None)
    return near if near is not None and abs(near[0] - frac) < LAYER_TOLERANCE else None


def patch_page_lines(lines, layer, height):
    for line in lines:
        if mean_conf(line) >= LOW_CONF:
            continue
        near = nearest_layer_line(layer, line['y'] / height)
        if near is not None:
            line['words'] = layer_words(line, near[1])
    return lines


def patch_read_lines(doc):
    """Güveni düşük OCR satırlarını (örn. "Şşş..." için "9ŞŞ...") metin katmanındaki satırla değiştirir."""
    original = structure.read_lines

    def patched(path):
        lines = original(path)
        match = re.search(r'p-(\d+)', path)
        if match:
            lines = patch_page_lines(lines, layer_lines(doc[int(match.group(1)) - 1]), structure.img_size(path)[1])
        return lines

    structure.read_lines = patched


class PlayBuilder:
    def __init__(self):
        self.blocks = []
        self.kind = None      # 'dialogue' ya da 'direction': biriken satırların türü
        self.buffer = []
        self.acts = 0

    def flush(self):
        if self.buffer:
            text = structure.clean_text(structure.join_lines(self.buffer))
            self.blocks.append((self.kind, text))
        self.buffer, self.kind = [], None

    def direction_open(self):
        text = ' '.join(self.buffer)
        return self.kind == 'direction' and text.count('(') > text.count(')')

    def add_line(self, kind, text):
        continuing = self.kind == kind and (kind == 'dialogue' or self.direction_open() or not text.startswith('('))
        if not continuing:
            self.flush()
            self.kind = kind
        self.buffer.append(text)

    def start_chapter(self, title):
        self.flush()
        self.blocks.append(('chapter', title))

    def start_act(self):
        self.acts += 1
        self.start_chapter(f'{roman(self.acts)}. Perde')

    def add_speaker(self, name):
        self.flush()
        self.blocks.append(('speaker', structure.clean_text(name)))

    def add_notes(self, notes):
        self.flush()
        for note_id, text in notes:
            self.blocks.append(('note', structure.clean_text(text), note_id))


def classify(line):
    text = line['t']
    if ACT.match(text):
        return 'act'
    if line['rel'] < SPEAKER_REL and SPEAKER.match(text):
        return 'speaker'
    if line['rel'] >= DIRECTION_REL or text.startswith('('):
        return 'direction'
    return 'dialogue'


TOKEN = re.compile(r'⟦(\d+)⟧')
STRAY_QUOTE = re.compile(r'(?<=[^\W\d_])[”"*•°](?=[^\W\d_]|\s|$)')


def unbalanced_quote(text, match):
    """Satırda eşi bulunmayan kapanış tırnağı, OCR'ın dipnot yıldızı yerine okuduğu işarettir."""
    char = match.group(0)
    if char in '*•°':
        return True
    if char == '”':
        return text.count('”') > text.count('“')
    return text.count('"') % 2 == 1


def link_stray_markers(page):
    """İşareti OCR'da tırnak/yıldız olarak bozulmuş dipnotları, bağlanmamış dipnot sayısı kadar aday varsa sırayla bağlar."""
    used = {int(i) for d in page['body'] for i in TOKEN.findall(d['t'])}
    free = [note_id for note_id, _ in page['notes'] if note_id not in used]
    spots = [(d, m) for d in page['body'] for m in STRAY_QUOTE.finditer(d['t']) if unbalanced_quote(d['t'], m)]
    if not free or len(spots) != len(free):
        return
    for (line, match), note_id in reversed(list(zip(spots, free))):
        line['t'] = line['t'][:match.start()] + f'⟦{note_id}⟧' + line['t'][match.end():]


def add_page(builder, page):
    title = page.get('title')
    if not builder.blocks and not (title and ACT.match(title)):
        builder.start_chapter('Kişiler')  # oyunun başındaki kişiler listesi
    if title and ACT.match(title):
        builder.start_act()
    elif title and SPEAKER.match(title) and not builder.blocks:
        builder.start_chapter(title.title())
    link_stray_markers(page)
    for line in page['body']:
        kind = classify(line)
        if kind == 'act':
            builder.start_act()
        elif kind == 'speaker':
            builder.add_speaker(line['t'])
        else:
            builder.add_line('direction' if builder.direction_open() else kind, line['t'])
    builder.add_notes(page['notes'])


def name_key(name):
    """Aksan ve büyük/küçük harf farkını yok sayan konuşmacı anahtarı (MÂŞA = MAŞA)."""
    decomposed = unicodedata.normalize('NFD', name.replace('İ', 'I').replace('ı', 'i'))
    return ''.join(ch for ch in decomposed if not unicodedata.combining(ch)).upper()


def unify_speakers(blocks):
    """OCR'ın farklı okuduğu aynı konuşmacı adlarını en sık görülen yazılışa birleştirir."""
    counts = collections.Counter(b[1] for b in blocks if b[0] == 'speaker')
    best = {}
    for name, count in counts.items():
        key = name_key(name)
        if key not in best or count > counts[best[key]]:
            best[key] = name
    return [('speaker', best[name_key(b[1])]) if b[0] == 'speaker' else b for b in blocks]


def build(pages):
    builder = PlayBuilder()
    for page in pages:
        add_page(builder, page)
    builder.flush()
    return unify_speakers(builder.blocks)
