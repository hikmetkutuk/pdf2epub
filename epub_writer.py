"""blocks.json -> EPUB 3 (reflowable, dipnotlu, kapaklı)."""
import json, re, zipfile, uuid, html, datetime, io
from PIL import Image

CFG = {}
TITLE = AUTHOR = TRANSLATOR = PUBLISHER = ''
CHAPTER_TITLES = ["Birinci Bölüm", "İkinci Bölüm", "Üçüncü Bölüm", "Dördüncü Bölüm", "Beşinci Bölüm"]

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
    
}
REGEX_FIXES = [

    (r"\(Çevirenin not\)", "(Çevirenin notu)"),

    (r"\.\.\s*\.", "..."),
    (r"\$", "ş"),
    (r"(\w) (ğ\w)", r"\1\2"), (r"\bdogup\b", "doğup"),
]
MARK = re.compile(r"\(\s*\*\s*[\)Y]")


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
    return re.sub(r"(\w+)- ([A-ZÇĞİÖŞÜ][\w']*)", rep, s)


def fix(s):
    for a, b in CFG.get('regex_fixes', []):
        s = re.sub(a, b, s)
    for a, b in CFG.get('fixes', {}).items():
        s = re.sub(r'(?<![\w])' + re.escape(a) + r'(?![\w])', b, s)
    s = re.sub(r'(?<=[a-zçğıöşü])[01](?=[a-zçğıöşü])', lambda m: 'o' if m.group(0) == '0' else 'ı', s)
    s = re.sub(r'\b1(?=[a-zçğıöşü]{2,})', '', s)
    s = hyphen_fix(s)
    s = re.sub(r'([.!?…]) [a-zı]{1,2}$', r'\1', s)
    def rep(m):
        w = m.group(0)
        r = FIXES.get(w)
        return r if r else w
    s = re.sub(r"[\wÂâÎîÛû']+", rep, s)
    for a, b in REGEX_FIXES:
        s = re.sub(a, b, s)
    return s


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
.title-page { text-align: center; }
.title-page h1 { font-size: 1.8em; margin-top: 30%; }
.title-page p { text-align: center; text-indent: 0; margin: 0.4em 0; }
.cover { margin: 0; padding: 0; text-align: center; }
.cover img { max-width: 100%; max-height: 100%; }
'''


def run(cfg, blocks):
    global CFG, TITLE, AUTHOR, TRANSLATOR, PUBLISHER
    CFG = cfg
    TITLE = cfg.get('title') or 'Kitap'
    AUTHOR = cfg.get('author', '')
    TRANSLATOR = cfg.get('translator', '')
    PUBLISHER = cfg.get('publisher', '')
    PROPER.clear()
    alltxt = ' '.join(b[1] for b in blocks if b[0] == 'p')
    from collections import Counter
    caps = Counter(re.findall(r"(?<=[a-zçğıöşü,;] )([A-ZÇĞİÖŞÜ][a-zçğıöşü]+)", alltxt))
    PROPER.update(w for w, n in caps.items() if n >= 2)
    chapters, cur, titles = [], None, []
    for b in blocks:
        if b[0] == 'chapter':
            cur = []; chapters.append(cur); titles.append(b[1] if len(b) > 1 else None); continue
        if cur is None:
            cur = []; chapters.append(cur); titles.append(None)
        cur.append(b)

    note_no = 0
    files = []
    for ci, ch in enumerate(chapters):
        fname = f'chapter{ci + 1}.xhtml'
        title = titles[ci] or (CHAPTER_TITLES[ci] if ci < len(CHAPTER_TITLES) else f'{ci + 1}. Bölüm')
        parts = [f'<h1 id="b{ci + 1}">{esc(title)}</h1>']
        pending = []  # (part_index, ) markers waiting for notes
        notes_html = []
        chap_notes = []
        for b in ch:
            k = b[0]
            if k == 'p':
                t = esc(fix(b[1]))
                pieces = MARK.split(t)
                out = pieces[0]
                for p in pieces[1:]:
                    tok = f'@@MARK{len(pending)}@@'
                    pending.append(None)
                    out += tok + p
                parts.append(f'<p>{out}</p>')
            elif k == 'verse':
                cls = 'epigraph' if b[2] == 'epigraph' else 'verse'
                parts.append(f'<div class="{cls}">' + ''.join(f'<p>{esc(fix(l))}</p>' for l in b[1]) + '</div>')
            elif k == 'break':
                if parts and not parts[-1].startswith('<div class="break"'):
                    parts.append('<div class="break">* * *</div>')
            elif k == 'end':
                parts.append('<p class="end">SON</p>')
            elif k == 'note':
                note_no += 1
                chap_notes.append(note_no)
                notes_html.append(f'<aside epub:type="footnote" id="n{note_no}" class="footnote"><p><a href="#r{note_no}">{note_no}.</a> {esc(fix(b[1]))}</p></aside>')
        body = '\n'.join(parts)
        if len(chap_notes) != len(pending):
            print('UYARI: bölüm', ci + 1, 'işaret', len(pending), 'not', len(chap_notes))
        for i, n in enumerate(chap_notes[:len(pending)]):
            pending[i] = n
        for i, n in enumerate(pending):
            rep = f'<a epub:type="noteref" class="noteref" id="r{n}" href="#n{n}">{n}</a>' if n else ''
            body = body.replace(f'@@MARK{i}@@', rep)
        while body.count('<div class="break">* * *</div>\n<p class="end">') or body.endswith('<div class="break">* * *</div>'):
            body = re.sub(r'<div class="break">\* \* \*</div>\n?(<p class="end">|$)', r'\1', body)
        if notes_html:
            body += '\n<section class="notes" epub:type="footnotes">\n' + '\n'.join(notes_html) + '\n</section>'
        files.append((fname, title, page_xhtml(title, body)))

    # kapak
    im = Image.open(CFG['cover']).convert('RGB')
    im.thumbnail((1400, 2100))
    buf = io.BytesIO(); im.save(buf, 'JPEG', quality=85); cover = buf.getvalue()

    uid = 'urn:uuid:' + str(uuid.uuid5(uuid.NAMESPACE_URL, TITLE + AUTHOR))
    now = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    title_page = page_xhtml(TITLE, f'''<div class="title-page"><h1>{esc(TITLE)}</h1>
<p>{esc(AUTHOR)}</p><p>&#160;</p>''' + (f'<p>Çeviren: {esc(TRANSLATOR)}</p>' if TRANSLATOR else '') + f'''<p>{esc(PUBLISHER)}</p></div>''')
    cover_page = page_xhtml('Kapak', '<div class="cover"><img src="cover.jpg" alt="Kapak"/></div>', 'cover')

    nav = page_xhtml('İçindekiler', '<nav epub:type="toc" id="toc"><h1>İçindekiler</h1><ol>' +
                     ''.join(f'<li><a href="{f}">{esc(t)}</a></li>' for f, t, _ in files) + '</ol></nav>' +
                     '<nav epub:type="landmarks" hidden=""><ol><li><a epub:type="cover" href="cover.xhtml">Kapak</a></li>'
                     f'<li><a epub:type="bodymatter" href="{files[0][0]}">Metin</a></li></ol></nav>')
    ncx = f'''<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1"><head><meta name="dtb:uid" content="{uid}"/></head>
<docTitle><text>{esc(TITLE)}</text></docTitle><navMap>''' + ''.join(
        f'<navPoint id="np{i}" playOrder="{i + 1}"><navLabel><text>{esc(t)}</text></navLabel><content src="{f}"/></navPoint>'
        for i, (f, t, _) in enumerate(files)) + '</navMap></ncx>'

    manifest = ['<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
                '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
                '<item id="css" href="style.css" media-type="text/css"/>',
                '<item id="cover-img" href="cover.jpg" media-type="image/jpeg" properties="cover-image"/>',
                '<item id="cover" href="cover.xhtml" media-type="application/xhtml+xml"/>',
                '<item id="title" href="title.xhtml" media-type="application/xhtml+xml"/>']
    spine = ['<itemref idref="cover" linear="yes"/>', '<itemref idref="title"/>']
    for i, (f, t, _) in enumerate(files):
        manifest.append(f'<item id="c{i + 1}" href="{f}" media-type="application/xhtml+xml"/>')
        spine.append(f'<itemref idref="c{i + 1}"/>')
    trl = (f'<dc:contributor id="trl">{esc(TRANSLATOR)}</dc:contributor>\n'
           f'<meta refines="#trl" property="role" scheme="marc:relators">trl</meta>\n') if TRANSLATOR else ''
    opf = f'''<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid" xml:lang="tr">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
<dc:identifier id="uid">{uid}</dc:identifier>
<dc:title>{esc(TITLE)}</dc:title>
<dc:creator id="aut">{esc(AUTHOR)}</dc:creator>
<meta refines="#aut" property="role" scheme="marc:relators">aut</meta>
{trl}<dc:publisher>{esc(PUBLISHER)}</dc:publisher>
<dc:language>tr</dc:language>
<meta property="dcterms:modified">{now}</meta>
<meta name="cover" content="cover-img"/>
</metadata>
<manifest>{''.join(manifest)}</manifest>
<spine toc="ncx">{''.join(spine)}</spine>
</package>'''
    container = '''<?xml version="1.0" encoding="utf-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>'''

    out = CFG.get('out', 'kitap.epub')
    with zipfile.ZipFile(out, 'w') as z:
        z.writestr(zipfile.ZipInfo('mimetype'), 'application/epub+zip', compress_type=zipfile.ZIP_STORED)
        z.writestr('META-INF/container.xml', container, zipfile.ZIP_DEFLATED)
        z.writestr('OEBPS/content.opf', opf, zipfile.ZIP_DEFLATED)
        z.writestr('OEBPS/nav.xhtml', nav, zipfile.ZIP_DEFLATED)
        z.writestr('OEBPS/toc.ncx', ncx, zipfile.ZIP_DEFLATED)
        z.writestr('OEBPS/style.css', CSS, zipfile.ZIP_DEFLATED)
        z.writestr('OEBPS/cover.jpg', cover, zipfile.ZIP_STORED)
        z.writestr('OEBPS/cover.xhtml', cover_page, zipfile.ZIP_DEFLATED)
        z.writestr('OEBPS/title.xhtml', title_page, zipfile.ZIP_DEFLATED)
        for f, t, x in files:
            z.writestr('OEBPS/' + f, x, zipfile.ZIP_DEFLATED)
    return out, note_no, len(files)
