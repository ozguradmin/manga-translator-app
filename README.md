# Manga Otomatik Çeviri Uygulaması (Streamlit)

Bu uygulama, PDF/CBZ/CBR/ZIP/JPG/PNG formatındaki manga dosyalarını yükleyip, sayfa sayfa otomatik olarak Türkçeye çevirir ve çevrilen sayfaları PDF olarak indirmenizi sağlar.

## Özellikler
- PDF, ZIP, CBZ, CBR, JPG, PNG desteği
- Sayfalar alt alta, birleşik ve mobil uyumlu
- Her sayfa çevrilir çevrilmez anında gösterim
- Metin kutusu yerine şeffaf beyaz arka plan
- Minimum font boyutu 8
- Tüm çevrilen sayfaları PDF olarak indir butonu (her zaman görünür, kaç sayfa çevrildiği yazıyor)
- API key cycling ve log paneli

## Kurulum
1. Gerekli paketleri yükleyin:
   ```bash
   pip install -r requirements.txt
   ```
2. (Varsa) `CCComicrazy.ttf` gibi özel font dosyanızı `fonts` klasörüne ekleyin:
   - `github/fonts/CCComicrazy.ttf` şeklinde olmalı.
   - Kodda font yolu olarak `fonts/CCComicrazy.ttf` kullanılır.

## Çalıştırma
```bash
streamlit run app.py
```
veya
```bash
python -m streamlit run app.py
```

## Deploy (Streamlit Cloud)
1. Bu klasörü bir GitHub reposuna yükleyin.
2. [https://streamlit.io/cloud](https://streamlit.io/cloud) adresinden "New app" ile repoyu seçin ve deploy edin.

## Notlar
- API anahtarlarınızı kodun başındaki `API_KEYS` listesine ekleyin.
- Büyük dosyalarda çeviri işlemi uzun sürebilir.
- Tüm çevrilen sayfaları PDF olarak indirebilirsiniz.
- Font dosyanız yoksa varsayılan font kullanılır, ancak manga için özel font önerilir. 