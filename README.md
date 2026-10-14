# pdf2epub: Taranmış Türkçe kitapları temiz EPUB'a çevirme

Taranmış (ya da içindeki metni bozuk) roman ve öykü PDF'lerini, telefonda ve e-okuyucuda rahat okunan **akışkan EPUB**'a çevirir.

Neler yapar:

- Sayfaları kendi Türkçe OCR'ıyla baştan okur.
- PDF'in içinde hazır metin katmanı varsa önce kalitesini ölçer. Güvenilirse yalnızca OCR'ın emin olmadığı kelimeleri ve kaybolan dipnot yıldızlarını düzeltmek için kullanır; güvenilmezse hiç kullanmaz.
- Çift sayfalı taramaları ortadan böler, eğik taranmış sayfaları düzeltir.
- Satırları gerçek paragraflara birleştirir, satır sonu tirelerini kaldırır.
- Sayfa numaralarını ve sayfa başlarındaki kitap adını (üst bilgiyi) atar.
- Künye, içindekiler ve arka kapak sayfalarını kendiliğinden ayıklar.
- Bölüm ve öykü başlıklarını bulur, tıklanabilir içindekiler oluşturur.
- Şiir, türkü ve ninnileri dize dize, italik olarak korur.
- Sahne geçişlerini "* * *" ile gösterir.
- Dipnotları tıklanabilir dipnot yapar ve EPUB içindeki bağlantıları yazma sonunda kontrol eder.
- Günlük ve mektuplarda sağa yaslı tarih satırlarını korur.
- Resim ya da faksimile gibi OCR'ı anlamsız çıkan sayfaları atlar.
- İlk sayfayı kapak olarak ekler.

---

## 1. Kurulum (bir kez yapılır)

Üç şey gerekiyor: **Python**, **Tesseract OCR (Türkçe dil paketiyle)** ve birkaç Python kütüphanesi.

### Windows

1. **Python**: https://www.python.org/downloads/ adresinden indirip kurun.
   Kurulumun ilk ekranındaki **"Add python.exe to PATH"** kutusunu mutlaka işaretleyin.
2. **Tesseract**: https://github.com/UB-Mannheim/tesseract/wiki adresinden Windows kurulum dosyasını (`tesseract-ocr-w64-setup-...exe`) indirin.
   Kurulum sırasında **"Additional language data"** bölümünü açıp **Turkish** dilini işaretleyin.
   (İşaretlemeyi unutursanız aşağıdaki "Türkçe dil paketi" bölümüne bakın.)
3. **Kütüphaneler**: Bu klasörde adres çubuğuna `cmd` yazıp Enter'a basın. Açılan pencerede şunu çalıştırın:
   ```
   pip install -r requirements.txt
   ```

### Mac

1. Homebrew yoksa kurun: https://brew.sh (sayfadaki tek satırlık komutu Terminal'e yapıştırın).
2. Terminal'de şunları çalıştırın:
   ```
   brew install python tesseract tesseract-lang
   cd <bu klasörün yolu>
   pip3 install -r requirements.txt
   ```
   (`tesseract-lang` Türkçe dahil tüm dil paketlerini kurar.)

### Türkçe dil paketi

Program açılırken "Türkçe dil paketi (tur) yok" derse:

1. https://github.com/tesseract-ocr/tessdata/raw/main/tur.traineddata dosyasını indirin.
2. Bu klasörün içinde `tessdata` adında bir klasör açıp dosyayı oraya koyun:
   ```
   pdf2epub/
     pdf2epub.py
     tessdata/
       tur.traineddata
   ```

Program bu klasörü kendisi bulur.

---

## 2. Kullanım

### En kolay yol (Windows)

PDF dosyasını sürükleyip **`convert.bat`** dosyasının üzerine bırakın. EPUB, PDF'in yanına aynı adla oluşur.

### Komut satırından

```
python pdf2epub.py "Kitabım.pdf" --title "Sarı Sıcak" --author "Yaşar Kemal"
```

(Mac'te `python` yerine `python3` yazın.)

Çevirmen ve yayınevi de verilebilir:

```
python pdf2epub.py cengiz.pdf --title "Cengiz Han'a Küsen Bulut" --author "Cengiz Aytmatov" --translator "Emine Işınsu" --publisher "Ötüken Neşriyat"
```

Program 4 adımda ilerler ve kalan süreyi gösterir. 200 sayfalık bir kitap bilgisayara göre yaklaşık **5-15 dakika** sürer; en uzun adım OCR'dır. İşlem bitince özet yazar:

```
Tamam: C:\Kitaplar\Sarı Sıcak.epub
  22 bölüm, 2850 paragraf, 4 şiir bloğu, 0 dipnot
  bölümler: Sarı Sıcak, Bebek, Yatak, Dükkancı, ...
```

Bölüm listesine bakmak, her şeyin yolunda olup olmadığını anlamanın en hızlı yoludur.

### Seçenekler

| Seçenek | Ne işe yarar |
|---|---|
| `--title "..."` | Kitap adı (verilmezse dosya adı kullanılır) |
| `--author "..."` | Yazar |
| `--translator "..."` | Çevirmen |
| `--publisher "..."` | Yayınevi |
| `--skip "2-6,230"` | Bu PDF sayfalarını kitaba alma. `-1` son sayfa, `-2` sondan ikinci demektir |
| `--cover-page 1` | Kapak resmi olarak kullanılacak PDF sayfası (varsayılan 1) |
| `--output path.epub` | EPUB'ın kaydedileceği yer |
| `--config corrections.json` | Kitaba özel yazım düzeltmeleri (aşağıya bakın) |
| `--dpi 400` | Görüntü çözünürlüğü (varsayılan: otomatik) |
| `--jobs 4` | Aynı anda kaç sayfa OCR edilsin (varsayılan: işlemci sayısı - 1) |
| `--force` | Ara dosyaları silip her şeyi baştan yapar |
| `--verbose` | Ayrıntılı bilgi gösterir (örneğin kurtarılan satırlar) |

---

## 3. İpuçları

**Ara dosyalar:** Program, PDF'in yanına `BookName_work` klasörü açar. İçinde `pages/`, `ocr/`, `blocks.json` ve `cover.png` bulunur. Bir ayarı değiştirip tekrar çalıştırdığınızda OCR yeniden yapılmaz, sonuç saniyeler içinde gelir. EPUB'dan memnunsanız bu klasörü silebilirsiniz.

**Fazladan ya da eksik bir sayfa varsa:** Ön söz, yazar tanıtımı ya da reklam sayfası gibi istemediğiniz sayfaları `--skip` ile çıkarın. Ardından tekrar çalıştırın; tekrar çalıştırmak hızlıdır.

**Sık tekrarlanan bir OCR hatası varsa:** Bir metin dosyası oluşturun (klasördeki `sample_corrections.json` örnek olarak kullanılabilir):

```json
{
  "fixes": {"San-Özek": "Sarı-Özek", "böyie": "böyle"},
  "regex_fixes": [["\\bSa[nr]ı?\\s*-\\s*Özek", "Sarı-Özek"]]
}
```

`fixes` tam kelime değiştirir. `regex_fixes` düzenli ifade kullanır; bunu bilmiyorsanız sadece `fixes` yeterli. Sonra `--config corrections.json` ile çalıştırın.

**Kindle'a göndermek:** EPUB'ı "Send to Kindle" (uygulama ya da e-posta) ile gönderebilirsiniz, Amazon kendisi dönüştürür.

---

## 4. Sınırlar

- Taranmış kitaplarda OCR çok iyidir ama kusursuz değildir. Kitap başına birkaç düzine kelime hatası kalabilir.
- Şiir tespiti girinti ve satır uzunluğuna bakar. Çok kısa diyaloglu bir sayfa nadiren şiir sanılabilir, uzun dizeli bir şiir de düzyazı sanılabilir.
- Resimler, tablolar ve iki sütunlu düzen (dergi, ders kitabı) için tasarlanmadı. Hedef roman ve öykü kitaplarıdır.
- Bölüm başlığı kitapta yazmıyorsa (bölümler sadece boşlukla ayrılıyorsa) "Birinci Bölüm", "İkinci Bölüm" diye adlandırılır.

---

## Dosyalar

- `pdf2epub.py`: Ana program (sayfa hazırlama, OCR, akış).
- `structure.py`: OCR çıktısından kitap yapısını kurar (paragraf, bölüm, şiir, dipnot, sahne).
- `epub_writer.py`: EPUB 3 dosyasını yazar (içindekiler, kapak, dipnotlar, Türkçe yazım düzeltmeleri).
- `convert.bat`: Windows'ta sürükle-bırak ile çalıştırma.
- `sample_corrections.json`: Kitaba özel düzeltme dosyası örneği.
- `saramago_corrections.json`, `woolf_diary_corrections.json`: Genel koda gömülmeyen kitap özel düzeltme örnekleri.
- `requirements.txt`: Gerekli Python kütüphaneleri.
