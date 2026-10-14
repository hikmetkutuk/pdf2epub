"""blocks.json -> EPUB 3 (reflowable, dipnotlu, kapaklı)."""

import datetime
import html
import importlib
import io
import re
import uuid
import zipfile
from collections import Counter

CFG = {}
TITLE = AUTHOR = TRANSLATOR = PUBLISHER = ''
OEBPS_DIR = 'OEBPS/'
LETTER_OR_APOSTROPHE = r"(?:[^\W\d_]|')"
try:
    UTC = datetime.UTC
except AttributeError:
    UTC = datetime.timezone.utc  # noqa: UP017 - Python 3.9 compatibility fallback.

# OCR düzeltmeleri (tam kelime)
FIXES = {
    'caha': 'daha', 'dâha': 'daha', 'sonma': 'sonra', 'böyie': 'böyle', 'simdi': 'şimdi', 'Simdi': 'Şimdi',
    'zamar': 'zaman', 'baska': 'başka', 'arısında': 'arasında', 'arasındâ': 'arasında', 'yainız': 'yalnız',
    'olduğuny': 'olduğunu', 'altn': 'altın', 'onjarı': 'onları', 'arlık': 'artık', 'karsı': 'karşı',
    'değii': 'değil', 'degildi': 'değildi', 'degil': 'değil', 'çocuj': 'çocuk', 'dyni': 'ayni', 'âyni': 'ayni',
    'güclü': 'güçlü', 'insar': 'insan', 'bunlarn': 'bunlar', 's0nunda': 'sonunda', 'yüzbası': 'yüzbaşı',
    'peşirden': 'peşinden', 'yznına': 'yanına', 'noöbetçi': 'nöbetçi', 'zavall': 'zavallı', 'seyden': 'şeyden',
    'soguk': 'soğuk', 'sâdece': 'sadece', 'yakit': 'vakit', 'bozkir': 'bozkır', 'başlamıstı': 'başlamıştı',
    'yardim': 'yardım', 'gördügü': 'gördüğü', 'bağııdı': 'bağırdı', 'anlıyot': 'anlıyor', 'işıl': 'ışıl',
    'bakışli': 'bakışlı', 'ortasırda': 'ortasında', 'düsen': 'düşen', 'ölay': 'olay', 
    'çalısıyor': 'çalışıyor', 'kınş': 'kış', 'akşine': 'aksine', 'meydân': 'meydan', 'itaal': 'itaat',
    'adamun': 'adamın', 'bulamiyordu': 'bulamıyordu', 's0luk': 'soluk', 'zözden': 'gözden', 'kaçmal': 'kaçmak',
    'meslekdasları': 'meslekdaşları', 
    'savaşlan': 'savaştan', 'yâpacağı': 'yapacağı', 'unutut': 'unutur', 'boişevik': 'bolşevik', 'çocugu': 'çocuğu', 'çocuklan': 'çocuklar',
    'Diger': 'Diğer', 'diger': 'diğer', 'degildir': 'değildir', 'degilmiş': 'değilmiş',
    'degiştirmiş': 'değiştirmiş', 'bilecegim': 'bileceğim', 'oldugundan': 'olduğundan',
    'dalmadıgını': 'dalmadığını', 'bulamadıgını': 'bulamadığını', 'aldıgındaysa': 'aldığındaysa',
    'ıçın': 'için', 'Tbe': 'The', 'tbe': 'the',
}
REGEX_FIXES = [

    (r"\(Çevirenin not\)", "(Çevirenin notu)"),

    (r"\.\.\s*\.", "..."),
    (r"\$", "ş"),
    (r"\b([0-9]{1,2}) Mar[iı](?=,?\s+(Pazartesi|Salı|Çarşamba|Perşembe|Cuma|Cumartesi|Pazar)\b)", r"\1 Mart"),
    (r"(?<=\w)\?(?=(?:i|ı|in|ın|un|ün|yle|de|da|te|ta)\b)", "'"),
    (r"\bTbe\b", "The"),
    (r"\btbe\b", "the"),
    (r"\bibe\b", "the"),
    (r"\bbis\b", "his"),
    (r"(?<!\w)#be\b", "the"),
    (r"(?<!\w)#he\b", "the"),
    (r"\b191#te\b", "1914'te"),
    (r"(?<!\w)#ünde\b", "4'ünde"),
    (r"(?<!\w)#(?=[a-zçğıöşü]{2,}\b)", "n"),
    (r"(\w) (ğ\w)", r"\1\2"), (r"\bdogup\b", "doğup"),
]
OLD_MARK = re.compile(r"\(\s*\*\s*[\)Y]")
TOKEN = re.compile(r"⟦(\d+)⟧")
QUOTE_SUFFIX = re.compile(r'(?<=[^\W\d_])["”](?=[^\W\d_]+\b)')
APOSTROPHE_SUFFIXES = ('de', 'da', 'den', 'dan', 'nin', 'ın', 'in', 'un', 'ün', 'ye', 'ya', 'yle', 'te', 'ta', 'ten', 'tan')


PROPER = set()


def hyphen_fix(s):
    # satır sonu tiresinden sonra büyük harf: OCR çoğunlukla 'ğ'yi Ş/B okuyor
    def rep(m):
        a, w = m.group(1), m.group(2)
        if a.endswith(('di', 'ldi')) and w in ('Ama',):
            return m.group(0)
        if w in PROPER or w.split("'")[0] in PROPER:
            return a + '-' + w
        if w[0] in 'ŞB':
            return a + 'ğ' + w[1:]
        return a + w[0].lower() + w[1:]
    return re.sub(rf"([^\W\d_]+)- ([A-ZÇĞİÖŞÜ]{LETTER_OR_APOSTROPHE}*)", rep, s)


def fix_translation_brackets(s):
    # OCR often reads square brackets around translated titles as (, ), | or final l.
    translated = r"[A-ZÇĞİÖŞÜ][^()[\]{}|]{2,80}"
    s = re.sub(rf"\(({translated})\|", r"[\1]", s)
    s = re.sub(rf"\|({translated})\|", r"[\1]", s)
    s = re.sub(rf"\|({translated})\)", r"[\1]", s)
    s = re.sub(rf"\|({translated})\]", r"[\1]", s)
    s = re.sub(rf"\(({translated})\]", r"[\1]", s)
    return s


def quote_as_apostrophe(s):
    """Kesme işareti yerine okunmuş tırnakları düzelt; kapanan alıntı tırnaklarını koru."""
    out, last = [], 0
    for match in QUOTE_SUFFIX.finditer(s):
        if not quote_has_apostrophe_suffix(s, match.end()):
            continue
        open_quote = is_closing_quote(s[:match.start()])
        out.append(s[last:match.start()])
        out.append(match.group(0) if open_quote else "'")
        last = match.end()
    out.append(s[last:])
    return ''.join(out)


def quote_has_apostrophe_suffix(text, start):
    match = re.match(r"[^\W\d_]+", text[start:])
    return bool(match and match.group(0) in APOSTROPHE_SUFFIXES)


def is_closing_quote(before):
    return has_unclosed_quote(before) or quote_opens_current_word(before)


def has_unclosed_quote(text):
    return (text.count('"') + text.count('“') - text.count('”')) % 2 == 1


def quote_opens_current_word(text):
    match = re.search(r"[^\W\d_]*$", text)
    if not match:
        return False
    word_start = len(text.rstrip()) - len(match.group(0))
    return word_start > 0 and text[word_start - 1] in '"“'


def fix(s):
    for a, b in CFG.get('regex_fixes', []):
        s = re.sub(a, b, s)
    for a, b in CFG.get('fixes', {}).items():
        s = re.sub(r'(?<!\w)' + re.escape(a) + r'(?!\w)', b, s)
    s = fix_question_mark_apostrophes(s)
    s = re.sub(r'\bk1\b', 'ki', s)
    s = re.sub(r'(?<=[a-zçğıöşü])[01](?=[a-zçğıöşü])', lambda m: 'o' if m.group(0) == '0' else 'ı', s)
    s = re.sub(r'\b1(?=[a-zçğıöşü]{2,})', '', s)
    s = hyphen_fix(s)
    s = re.sub(r'([.!?…]) [a-zı]{1,2}$', r'\1', s)
    def rep(m):
        w = m.group(0)
        r = FIXES.get(w)
        return r if r else w
    s = re.sub(rf"{LETTER_OR_APOSTROPHE}+", rep, s)
    for a, b in REGEX_FIXES:
        s = re.sub(a, b, s)
    s = quote_as_apostrophe(s)
    s = fix_translation_brackets(s)
    return s


def fix_question_mark_apostrophes(s):
    letter = r"[^\W\d_]"
    suffix = r"(?:n?[ıiuü]n|[dt][ae]n?|y[ae]|l[ae]|[ıiuü]m|s[ıiuü]n)"
    proper = r"([A-ZÇĞİÖŞÜ][A-Za-zÇĞİÖŞÜçğıöşü]{1,30})"
    s = re.sub(rf"\b{proper}\?({suffix}\b)", r"\1'\2", s)
    return re.sub(rf"(?<={letter})\?(?={letter})", '', s)


def esc(s):
    return html.escape(s, quote=False)


def page_xhtml(title, body, cls=''):
    return f'''<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="tr" lang="tr">
<head><meta charset="utf-8"/><title>{esc(title)}</title><link rel="stylesheet" type="text/css" href="style.css"/></head>
<body{f' class="{cls}"' if cls else ''}>
{body}
</body></html>'''


CSS = '''
body { margin: 0 4%; font-family: serif; line-height: 1.45; }
h1 { font-size: 1.4em; text-align: center; margin: 3em 0 1.5em; font-weight: normal; letter-spacing: 0.08em; }
p { margin: 0; text-indent: 1.5em; text-align: justify; hyphens: auto; -webkit-hyphens: auto; }
p.date { text-align: right; text-indent: 0; font-style: italic; margin: 1.6em 0 0.6em; }
p.date + p { text-indent: 0; }
p.first, .break + p, h1 + p, .epigraph + p, .verse + p { text-indent: 0; }
.break { text-align: center; margin: 1.2em 0; text-indent: 0; letter-spacing: 0.5em; }
.verse { margin: 0.9em 0 0.9em 2em; font-style: italic; }
.verse p, .epigraph p { text-indent: 0; text-align: left; }
.epigraph { margin: 1em 10% 2.2em; font-style: italic; text-align: center; }
.epigraph p { text-align: center; }
.end { text-align: center; margin-top: 2em; text-indent: 0; letter-spacing: 0.2em; }
a.noteref { text-decoration: none; font-size: 0.75em; vertical-align: super; line-height: 0; }
aside.footnote, .notes p { font-size: 0.85em; text-indent: 0; margin: 0.4em 0; }
.notes { margin-top: 2.5em; border-top: 1px solid #999; padding-top: 0.6em; }
.definition-list { width: 100%; border-collapse: collapse; margin: 1em 0; }
.definition-list th, .definition-list td { padding: 0 0 0.15em 0; text-align: left; vertical-align: top; }
.definition-list th { width: 9em; padding-right: 1em; font-weight: normal; white-space: nowrap; }
.definition-list td { hyphens: auto; -webkit-hyphens: auto; }
.title-page { text-align: center; }
.title-page h1 { font-size: 1.8em; margin-top: 30%; }
.title-page p { text-align: center; text-indent: 0; margin: 0.4em 0; }
.cover { margin: 0; padding: 0; text-align: center; }
.cover img { max-width: 100%; max-height: 100%; }
'''


def load_image(path):
    try:
        image_module = importlib.import_module('PIL.Image')
    except ImportError as exc:
        raise RuntimeError("Pillow kurulu değil. Şunu çalıştırın: pip install -r requirements.txt") from exc
    return image_module.open(path)


def setup_book(cfg, blocks):
    global CFG, TITLE, AUTHOR, TRANSLATOR, PUBLISHER
    CFG = cfg
    TITLE = cfg.get('title') or 'Kitap'
    AUTHOR = cfg.get('author', '')
    TRANSLATOR = cfg.get('translator', '')
    PUBLISHER = cfg.get('publisher', '')
    PROPER.clear()
    alltxt = ' '.join(b[1] for b in blocks if b[0] == 'p')
    caps = Counter(re.findall(r"(?<=[a-zçğıöşü,;] )([A-ZÇĞİÖŞÜ][a-zçğıöşü]+)", alltxt))
    PROPER.update(w for w, n in caps.items() if n >= 2)


def split_chapters(blocks):
    chapters, cur, titles = [], None, []
    for b in blocks:
        if b[0] == 'chapter':
            cur = []
            chapters.append(cur)
            titles.append(b[1] if len(b) > 1 else None)
            continue
        if cur is None:
            cur = []
            chapters.append(cur)
            titles.append(None)
        cur.append(b)
    return chapters, titles


def fallback_chapter_label(chapter, ci):
    for block in chapter:
        if block[0] == 'verse' and block[1]:
            return fix(block[1][0])
        if block[0] == 'p' and block[1].strip():
            return fix(block[1].strip()[:60])
    return f'Metin {ci + 1}'


def note_linker(note_numbers):
    def link(match):
        note_id = int(match.group(1))
        if note_id not in note_numbers:
            return ''
        number = note_numbers[note_id]
        return f'<a epub:type="noteref" class="noteref" id="r{note_id}" href="#n{note_id}">{number}</a>'
    return link


def append_paragraph(parts, text, note_numbers):
    text = esc(fix(OLD_MARK.sub('', text)))
    parts.append('<p>' + TOKEN.sub(note_linker(note_numbers), text) + '</p>')


def append_verse(parts, block, note_numbers):
    cls = 'epigraph' if block[2] == 'epigraph' else 'verse'
    link = note_linker(note_numbers)
    lines = ''.join(f'<p>{TOKEN.sub(link, esc(fix(line)))}</p>' for line in block[1])
    parts.append(f'<div class="{cls}">{lines}</div>')


def append_definition_list(parts, block):
    rows = []
    for term, desc in block[1]:
        rows.append(f'<tr><th scope="row">{esc(fix(term))}</th><td>{esc(fix(desc))}</td></tr>')
    parts.append('<table class="definition-list"><tbody>' + ''.join(rows) + '</tbody></table>')


def append_break(parts):
    if parts and not parts[-1].startswith('<div class="break"'):
        parts.append('<div class="break">* * *</div>')


def append_note(notes_html, block, note_numbers, referenced_notes):
    if len(block) < 3 or block[2] not in note_numbers:
        return
    note_id = block[2]
    note_no = note_numbers[note_id]
    note = esc(fix(block[1]))
    label = f'<a href="#r{note_id}">{note_no}.</a>' if note_id in referenced_notes else f'{note_no}.'
    notes_html.append(
        f'<aside epub:type="footnote" id="n{note_id}" class="footnote">'
        f'<p>{label} {note}</p></aside>'
    )


def render_chapter_blocks(chapter, parts, notes_html, note_numbers, referenced_notes):
    for block in chapter:
        kind = block[0]
        if kind == 'p':
            append_paragraph(parts, block[1], note_numbers)
        elif kind == 'verse':
            append_verse(parts, block, note_numbers)
        elif kind == 'deflist':
            append_definition_list(parts, block)
        elif kind == 'break':
            append_break(parts)
        elif kind == 'end':
            parts.append('<p class="end">SON</p>')
        elif kind == 'date':
            parts.append(f'<p class="date">{esc(fix(block[1]))}</p>')
        elif kind == 'note':
            append_note(notes_html, block, note_numbers, referenced_notes)


def remove_trailing_breaks(body):
    while body.count('<div class="break">* * *</div>\n<p class="end">') or body.endswith('<div class="break">* * *</div>'):
        body = re.sub(r'<div class="break">\* \* \*</div>\n?(<p class="end">|$)', r'\1', body)
    return body


def render_chapter(ci, chapter, title, nav_label, note_numbers, referenced_notes):
    parts = [f'<h1 id="b{ci + 1}">{esc(title)}</h1>'] if title else []
    notes_html = []
    render_chapter_blocks(chapter, parts, notes_html, note_numbers, referenced_notes)
    body = '\n'.join(parts)
    body = remove_trailing_breaks(body)
    if notes_html:
        body += '\n<section class="notes" epub:type="footnotes">\n' + '\n'.join(notes_html) + '\n</section>'
    return f'chapter{ci + 1}.xhtml', nav_label, page_xhtml(nav_label, body)


def collect_note_links(blocks):
    note_numbers = {}
    referenced_notes = set()
    for block in blocks:
        if block[0] == 'note' and len(block) > 2 and block[2] is not None:
            note_numbers[block[2]] = len(note_numbers) + 1
        if block[0] == 'p':
            referenced_notes.update(int(match) for match in TOKEN.findall(block[1]))
        elif block[0] == 'verse':
            for line in block[1]:
                referenced_notes.update(int(match) for match in TOKEN.findall(line))
    return note_numbers, referenced_notes


def render_chapters(chapters, titles, note_numbers, referenced_notes):
    files = []
    for ci, chapter in enumerate(chapters):
        title = titles[ci]
        nav_label = title or fallback_chapter_label(chapter, ci)
        files.append(render_chapter(ci, chapter, title, nav_label, note_numbers, referenced_notes))
    return files


def cover_bytes():
    im = load_image(CFG['cover']).convert('RGB')
    im.thumbnail((1400, 2100))
    buf = io.BytesIO()
    im.save(buf, 'JPEG', quality=85)
    return buf.getvalue()


def build_title_page():
    author = f'<p>{esc(AUTHOR)}</p>' if AUTHOR else ''
    translator = f'<p>Çeviren: {esc(TRANSLATOR)}</p>' if TRANSLATOR else ''
    publisher = f'<p>{esc(PUBLISHER)}</p>' if PUBLISHER else ''
    return page_xhtml(TITLE, f'''<div class="title-page"><h1>{esc(TITLE)}</h1>
{author}<p>&#160;</p>{translator}{publisher}</div>''')


def build_cover_page():
    return page_xhtml('Kapak', '<div class="cover"><img src="cover.jpg" alt="Kapak"/></div>', 'cover')


def build_nav(files):
    return page_xhtml('İçindekiler', '<nav epub:type="toc" id="toc"><h1>İçindekiler</h1><ol>' +
                      ''.join(f'<li><a href="{f}">{esc(t)}</a></li>' for f, t, _ in files) + '</ol></nav>' +
                      '<nav epub:type="landmarks" hidden=""><ol><li><a epub:type="cover" href="cover.xhtml">Kapak</a></li>'
                      f'<li><a epub:type="bodymatter" href="{files[0][0]}">Metin</a></li></ol></nav>')


def build_ncx(uid, files):
    nav_points = ''.join(
        f'<navPoint id="np{i}" playOrder="{i + 1}"><navLabel><text>{esc(t)}</text></navLabel><content src="{f}"/></navPoint>'
        for i, (f, t, _) in enumerate(files)
    )
    return f'''<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1"><head><meta name="dtb:uid" content="{uid}"/></head>
<docTitle><text>{esc(TITLE)}</text></docTitle><navMap>{nav_points}</navMap></ncx>'''


def build_manifest_and_spine(files):
    manifest = ['<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
                '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
                '<item id="css" href="style.css" media-type="text/css"/>',
                '<item id="cover-img" href="cover.jpg" media-type="image/jpeg" properties="cover-image"/>',
                '<item id="cover" href="cover.xhtml" media-type="application/xhtml+xml"/>',
                '<item id="title" href="title.xhtml" media-type="application/xhtml+xml"/>']
    spine = ['<itemref idref="cover" linear="yes"/>', '<itemref idref="title"/>']
    for i, (f, _, _) in enumerate(files):
        manifest.append(f'<item id="c{i + 1}" href="{f}" media-type="application/xhtml+xml"/>')
        spine.append(f'<itemref idref="c{i + 1}"/>')
    return manifest, spine


def build_opf(uid, now, manifest, spine):
    aut = (f'<dc:creator id="aut">{esc(AUTHOR)}</dc:creator>\n'
           f'<meta refines="#aut" property="role" scheme="marc:relators">aut</meta>\n') if AUTHOR else ''
    trl = (f'<dc:contributor id="trl">{esc(TRANSLATOR)}</dc:contributor>\n'
           f'<meta refines="#trl" property="role" scheme="marc:relators">trl</meta>\n') if TRANSLATOR else ''
    pub = f'<dc:publisher>{esc(PUBLISHER)}</dc:publisher>\n' if PUBLISHER else ''
    return f'''<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid" xml:lang="tr">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
<dc:identifier id="uid">{uid}</dc:identifier>
<dc:title>{esc(TITLE)}</dc:title>
{aut}{trl}{pub}
<dc:language>tr</dc:language>
<meta property="dcterms:modified">{now}</meta>
<meta name="cover" content="cover-img"/>
</metadata>
<manifest>{''.join(manifest)}</manifest>
<spine toc="ncx">{''.join(spine)}</spine>
</package>'''


def build_container():
    return f'''<?xml version="1.0" encoding="utf-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
<rootfiles><rootfile full-path="{OEBPS_DIR}content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>'''


def write_epub(out, files, content):
    with zipfile.ZipFile(out, 'w') as z:
        z.writestr(zipfile.ZipInfo('mimetype'), 'application/epub+zip', compress_type=zipfile.ZIP_STORED)
        z.writestr('META-INF/container.xml', content['container'], zipfile.ZIP_DEFLATED)
        z.writestr(OEBPS_DIR + 'content.opf', content['opf'], zipfile.ZIP_DEFLATED)
        z.writestr(OEBPS_DIR + 'nav.xhtml', content['nav'], zipfile.ZIP_DEFLATED)
        z.writestr(OEBPS_DIR + 'toc.ncx', content['ncx'], zipfile.ZIP_DEFLATED)
        z.writestr(OEBPS_DIR + 'style.css', CSS, zipfile.ZIP_DEFLATED)
        z.writestr(OEBPS_DIR + 'cover.jpg', content['cover'], zipfile.ZIP_STORED)
        z.writestr(OEBPS_DIR + 'cover.xhtml', content['cover_page'], zipfile.ZIP_DEFLATED)
        z.writestr(OEBPS_DIR + 'title.xhtml', content['title_page'], zipfile.ZIP_DEFLATED)
        for f, _, x in files:
            z.writestr(OEBPS_DIR + f, x, zipfile.ZIP_DEFLATED)


def html_member_names(z):
    return [name for name in z.namelist() if name.startswith(OEBPS_DIR) and name.endswith(('.xhtml', '.html'))]


def html_ids_by_name(z, names):
    ids_by_name = {}
    for name in names:
        text = z.read(name).decode('utf-8')
        ids_by_name[name] = set(re.findall(r'\bid="([^"]+)"', text))
    return ids_by_name


def internal_html_hrefs(text):
    for href in re.findall(r'\bhref="([^"]+)"', text):
        if href.startswith(('http:', 'https:', 'mailto:')):
            continue
        target_file, _, target_id = href.partition('#')
        if target_file and not target_file.endswith(('.xhtml', '.html')):
            continue
        yield href, target_file, target_id


def broken_href(href_parts, source_name, html_set, ids_by_name):
    _href, target_file, target_id = href_parts
    target_name = OEBPS_DIR + target_file if target_file else source_name
    missing_file = target_file and target_name not in html_set
    missing_id = target_id and target_id not in ids_by_name.get(target_name, set())
    return missing_file or missing_id


def validate_epub_links(out):
    with zipfile.ZipFile(out) as z:
        html_names = html_member_names(z)
        ids_by_name = html_ids_by_name(z, html_names)
        html_set = set(html_names)
        broken = []
        for name in html_names:
            text = z.read(name).decode('utf-8')
            broken.extend(f'{name}: {href}' for href, *parts in internal_html_hrefs(text)
                          if broken_href((href, *parts), name, html_set, ids_by_name))
    if broken:
        raise RuntimeError('EPUB içinde bozuk bağlantı var: ' + ', '.join(broken[:10]))


def build_epub_content(files):
    uid = 'urn:uuid:' + str(uuid.uuid5(uuid.NAMESPACE_URL, TITLE + AUTHOR))
    now = datetime.datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')
    manifest, spine = build_manifest_and_spine(files)
    return {
        'container': build_container(),
        'opf': build_opf(uid, now, manifest, spine),
        'nav': build_nav(files),
        'ncx': build_ncx(uid, files),
        'cover': cover_bytes(),
        'cover_page': build_cover_page(),
        'title_page': build_title_page(),
    }


def run(cfg, blocks):
    setup_book(cfg, blocks)
    chapters, titles = split_chapters(blocks)
    note_numbers, referenced_notes = collect_note_links(blocks)
    files = render_chapters(chapters, titles, note_numbers, referenced_notes)
    content = build_epub_content(files)
    out = CFG.get('out', 'kitap.epub')
    write_epub(out, files, content)
    validate_epub_links(out)
    return out, len(note_numbers), len(files)
