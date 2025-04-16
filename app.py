import streamlit as st
import google.generativeai as genai
from PIL import Image, ImageDraw, ImageFont, ImageOps
import io
import json
import os
import re # Regex modülünü import et
import time
import textwrap
import zipfile
import fitz  # PyMuPDF
import rarfile
import base64
import tempfile

# --- API Anahtar Listesi ---
API_KEYS = st.secrets["API_KEYS"]

# --- Sayfa ve Başlık Ayarları ---
st.set_page_config(layout="wide")
st.title("Manga Okuma ve Otomatik Çeviri (Gemini)")

# --- Oturum Durumu Başlatma ---
if 'current_api_key_index' not in st.session_state:
    st.session_state.current_api_key_index = 0
if 'logs' not in st.session_state:
    st.session_state.logs = []
if 'page_states' not in st.session_state:
    st.session_state.page_states = {}  # {page_idx: {'status': 'pending'/'done'/'error', 'img': img, 'translated_img': img, 'log': ...}}

# --- Kenar Çubuğu Ayarları ---
st.sidebar.title("Ayarlar ve Loglar")

# API Anahtarı Girişi (Varsayılan olarak ilk anahtarı gösterir, ancak ana mantık listeyi kullanır)
# Bu alan şimdilik sadece bilgilendirme amaçlı veya geçici anahtar eklemek için kullanılabilir.
api_key_display = st.sidebar.text_input(
    "Aktif API Anahtarı (Başlangıç)",
    value=API_KEYS[st.session_state.current_api_key_index][:8] + "...", # Anahtarın sadece başını göster
    disabled=True, # Şimdilik değiştirmeyi kapatalım
    help="API anahtarı listesi kod içinde tanımlıdır ve hız limitine takıldıkça otomatik değişir."
)

# Log Alanı
st.sidebar.subheader("Log Kayıtları")
log_area = st.sidebar.empty()

def add_log(message):
    """Log listesine mesaj ekler ve alanı günceller."""
    st.session_state.logs.append(message)
    log_area.text_area("Log Mesajları", "\n".join(st.session_state.logs), height=250)

# --- Gemini Yapılandırma Fonksiyonu ---
def configure_gemini(key_index):
    """Belirtilen index'teki API anahtarı ile Gemini'yi yapılandırır."""
    try:
        selected_key = API_KEYS[key_index]
        genai.configure(api_key=selected_key)
        model = genai.GenerativeModel('gemini-1.5-pro-latest')
        add_log(f"Gemini API yapılandırıldı. Anahtar Index: {key_index} (***{selected_key[-4:]})")
        # Aktif anahtar gösterimini güncelle (opsiyonel)
        # api_key_display.text_input("Aktif API Anahtarı", value=selected_key[:8] + "...", disabled=True, key=f"api_disp_{key_index}")
        return model
    except Exception as e:
        add_log(f"HATA: API Anahtarı Index {key_index} ile yapılandırma başarısız: {e}")
        return None

# --- API Çağrısı Yardımcı Fonksiyonu (Retry ve Key Cycling ile) ---
def call_gemini_with_retry(model, content, max_retries=len(API_KEYS) + 2, initial_delay=1):
    """Gemini API'yi çağırır, 429 hatasında anahtar değiştirerek ve bekleyerek tekrar dener."""
    current_model = model
    delay = initial_delay
    for attempt in range(max_retries):
        if current_model is None:
            add_log("HATA: Geçerli bir Gemini modeli yok. API çağrısı yapılamıyor.")
            return None
        try:
            response = current_model.generate_content(content)
            add_log("API çağrısı başarılı.")
            return response # Başarılı olursa yanıtı döndür
        except Exception as e:
            if '429' in str(e):
                add_log(f"429 Hatası (Anahtar Index: {st.session_state.current_api_key_index}). Detay: {e}")
                st.session_state.current_api_key_index = (st.session_state.current_api_key_index + 1) % len(API_KEYS)
                add_log(f"Sıradaki API anahtarına geçiliyor: Index {st.session_state.current_api_key_index}. {delay}sn bekleniyor...")
                time.sleep(delay)
                current_model = configure_gemini(st.session_state.current_api_key_index)
                delay = min(delay * 1.5, 15) # Gecikmeyi biraz artır
                continue # Yeni anahtarla tekrar dene
            else:
                add_log(f"API çağrısı sırasında beklenmeyen hata (Anahtar Index: {st.session_state.current_api_key_index}): {e}")
                return None # Diğer hatalarda None döndür
    add_log(f"HATA: API çağrısı {max_retries} denemeden sonra başarısız oldu.")
    return None

# --- İlk Model Yapılandırması ---
model = configure_gemini(st.session_state.current_api_key_index)

# --- Yardımcı Fonksiyon: Optimal Font Boyutunu Bulma (İyileştirildi v2) ---
def get_optimal_font_size(draw, text, box_width, box_height, font_path="manga_font.ttf", max_font_size=100, min_font_size=8, padding=0):
    """Verilen kutuya metni sığdıracak en büyük font boyutunu ve wrap edilmiş halini döndürür."""
    add_log(f"Font Boyutu Hesaplama Başladı: Kutu({box_width:.0f}x{box_height:.0f}), MaxSize={max_font_size}, MinSize={min_font_size}, İçPadding={padding}")
    best_font = None
    best_size = min_font_size
    best_wrapped = text
    for font_size in range(max_font_size, min_font_size - 1, -1):
        try:
            font = ImageFont.truetype(font_path, font_size)
        except IOError:
            try:
                font = ImageFont.load_default(size=font_size)
            except Exception:
                font = ImageFont.load_default()
        # Satır uzunluğunu fonta ve kutuya göre tahmini ayarla
        max_chars_per_line = max(1, int(box_width // (font_size * 0.6)))
        wrapped = textwrap.fill(text, width=max_chars_per_line)
        bbox = draw.multiline_textbbox((0, 0), wrapped, font=font, spacing=4)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        if text_width <= box_width - 2 * padding and text_height <= box_height - 2 * padding:
            best_font = font
            best_size = font_size
            best_wrapped = wrapped
            break
    if best_font is None:
        best_font = ImageFont.truetype(font_path, min_font_size) if font_path else ImageFont.load_default()
        best_size = min_font_size
        max_chars_per_line = max(1, int(box_width // (min_font_size * 0.6)))
        best_wrapped = textwrap.fill(text, width=max_chars_per_line)
        add_log(f"UYARI: Kutu çok küçük! Font minimumda ({min_font_size}).")
    add_log(f"Font Boyutu Hesaplama Bitti: Son Boyut={best_size}")
    return best_font, best_size, best_wrapped

# --- Gemini API'ye gönderilecek görseli yeniden boyutlandıran fonksiyon ---
from PIL import ImageOps
MAX_API_IMAGE_SIZE = 1000
MIN_ASPECT_RATIO = 0.6   # Çok dar ise padding uygula
MAX_ASPECT_RATIO = 1.8   # Çok uzun ise padding uygula

def resize_for_api(img):
    img_api = img.copy()
    aspect_ratio = img_api.width / img_api.height
    if aspect_ratio < MIN_ASPECT_RATIO:
        # Çok dar, yanlara padding ekle
        new_width = int(img_api.height * MIN_ASPECT_RATIO)
        result = Image.new("RGB", (new_width, img_api.height), (255,255,255))
        result.paste(img_api, ((new_width - img_api.width)//2, 0))
        img_api = result
    elif aspect_ratio > MAX_ASPECT_RATIO:
        # Çok uzun, üst-alt padding ekle
        new_height = int(img_api.width / MAX_ASPECT_RATIO)
        result = Image.new("RGB", (img_api.width, new_height), (255,255,255))
        result.paste(img_api, (0, (new_height - img_api.height)//2))
        img_api = result
    # Sonra boyutlandır
    if img_api.width > MAX_API_IMAGE_SIZE or img_api.height > MAX_API_IMAGE_SIZE:
        img_api.thumbnail((MAX_API_IMAGE_SIZE, MAX_API_IMAGE_SIZE))
    return img_api

# --- Dosya Yükleme ve Sayfa Çıkarma ---
def extract_images_from_file(uploaded_file):
    image_paths = []
    filename = uploaded_file.name.lower()
    if filename.endswith('.pdf'):
        pdf_bytes = uploaded_file.read()
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        for page in doc:
            pix = page.get_pixmap()
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            temp = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
            img.save(temp, format="PNG")
            temp.close()
            image_paths.append(temp.name)
    elif filename.endswith('.zip') or filename.endswith('.cbz'):
        with zipfile.ZipFile(uploaded_file) as archive:
            for name in sorted(archive.namelist()):
                if name.lower().endswith(('.jpg', '.jpeg', '.png')):
                    img = Image.open(io.BytesIO(archive.read(name))).convert("RGB")
                    temp = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
                    img.save(temp, format="PNG")
                    temp.close()
                    image_paths.append(temp.name)
    elif filename.endswith('.rar') or filename.endswith('.cbr'):
        try:
            with zipfile.ZipFile(uploaded_file) as archive:
                for name in sorted(archive.namelist()):
                    if name.lower().endswith(('.jpg', '.jpeg', '.png')):
                        img = Image.open(io.BytesIO(archive.read(name))).convert("RGB")
                        temp = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
                        img.save(temp, format="PNG")
                        temp.close()
                        image_paths.append(temp.name)
            return image_paths
        except Exception:
            uploaded_file.seek(0)
            try:
                with rarfile.RarFile(uploaded_file) as archive:
                    for name in sorted(archive.namelist()):
                        if name.lower().endswith(('.jpg', '.jpeg', '.png')):
                            img = Image.open(io.BytesIO(archive.read(name))).convert("RGB")
                            temp = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
                            img.save(temp, format="PNG")
                            temp.close()
                            image_paths.append(temp.name)
                return image_paths
            except Exception as e:
                st.error("CBR dosyası açılamadı. Dosya bozuk olabilir veya sunucuda RAR desteği yok. Hata: " + str(e))
                return []
    elif filename.endswith(('.jpg', '.jpeg', '.png')):
        img = Image.open(uploaded_file).convert("RGB")
        temp = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
        img.save(temp, format="PNG")
        temp.close()
        image_paths.append(temp.name)
    return image_paths

# --- Görsel Yükleme ---
uploaded_file = st.file_uploader(
    "Bir manga dosyası veya görsel yükleyin (PDF, ZIP, CBZ, CBR, JPG, PNG)",
    type=["pdf", "zip", "cbz", "cbr", "jpg", "jpeg", "png"],
    label_visibility="visible",
    help="Buraya dosya sürükleyip bırakabilir veya dosya seçebilirsiniz."
)

if uploaded_file:
    image_paths = extract_images_from_file(uploaded_file)
    st.session_state.page_states = {}
    page_placeholders = []
    for idx, img_path in enumerate(image_paths):
        st.session_state.page_states[idx] = {'status': 'pending', 'img_path': img_path, 'translated_img_path': None, 'log': ''}
        page_placeholders.append(st.empty())

    st.success(f"{len(image_paths)} sayfa yüklendi. Çeviri işlemi başlatılıyor...")

    # --- Sayfa Sayfa Çeviri ---
    for idx, page in st.session_state.page_states.items():
        img = Image.open(page['img_path'])
        placeholder = page_placeholders[idx]
        if page['status'] == 'done':
            placeholder.markdown(f"### Sayfa {idx+1}")
            placeholder.image(page['translated_img_path'], use_container_width=True)
            continue
        # --- Gemini ile metin tespiti ve çeviri ---
        img_api = resize_for_api(img)
        prompt_detection = (
            "Bu görseldeki konuşma balonları veya mantıksal olarak bağlantılı metin grupları gibi metin bloklarını tespit et. "
            "Her blok için: "
            "1. İçindeki tüm metinleri (satır sonlarını koruyarak veya boşlukla birleştirerek) tek bir string olarak 'text' anahtarıyla birleştir. "
            "2. Tüm metin bloğunu çevreleyen tek bir sınırlayıcı kutuyu [ymin, xmin, ymax, xmax] formatında (0-1000 arası normalize edilmiş) 'box' anahtarıyla ver. "
            "Sonucu bir JSON listesi olarak döndür. Örneğin: "
            "[{'text': 'WHAT DOES IT\nMEAN TO BE\nHUMAN...?', 'box': [100, 780, 210, 970]}]"
        )
        response_detection = call_gemini_with_retry(model, [prompt_detection, img_api])
        text_response = response_detection.text.strip() if response_detection else ""
        text_response = re.sub(r"^```json\s*|^```\s*|\s*```$", "", text_response, flags=re.MULTILINE).strip()
        if not text_response:
            page['status'] = 'error'
            page['log'] = 'Gemini API yanıtı boş geldi.'
            add_log('UYARI: Gemini API yanıtı boş geldi, JSON ayrıştırma yapılmadı.')
            placeholder.markdown(f"### Sayfa {idx+1}")
            placeholder.image(img, use_container_width=True)
            placeholder.markdown('<div style="position:relative;top:-60px;left:0;width:100%;height:60px;background:rgba(255,0,0,0.2);text-align:center;font-size:18px;">Hata: Gemini API yanıtı boş geldi.</div>', unsafe_allow_html=True)
            continue
        cleaned_json_str = re.sub(r",\s*([}\]])", r"\1", text_response)
        try:
            detected_items = json.loads(cleaned_json_str)
        except Exception as e:
            page['status'] = 'error'
            page['log'] = f'JSON ayrıştırma hatası: {e}'
            add_log(f'HATA: JSON ayrıştırma hatası: {e}\nYanıt: {cleaned_json_str}')
            placeholder.markdown(f"### Sayfa {idx+1}")
            placeholder.image(img, use_container_width=True)
            placeholder.markdown(f'<div style="position:relative;top:-60px;left:0;width:100%;height:60px;background:rgba(255,0,0,0.2);text-align:center;font-size:18px;">Hata: JSON ayrıştırma hatası: {e}</div>', unsafe_allow_html=True)
            continue
        # Toplu çeviri
        all_texts = [item.get('text', '') for item in detected_items]
        joined_text = '\n---\n'.join(all_texts)
        prompt_translation = f"Aşağıdaki metin bloklarını Türkçeye çevir. Her blok arasını --- ile ayırdım, sen de çeviride blokları aynı sırayla --- ile ayırarak döndür:\n\n{joined_text}"
        response_translation = call_gemini_with_retry(model, prompt_translation)
        if response_translation is None:
            page['status'] = 'error'
            page['log'] = 'Çeviri başarısız.'
            placeholder.markdown(f"### Sayfa {idx+1}")
            placeholder.image(img, use_container_width=True)
            placeholder.markdown('<div style="position:relative;top:-60px;left:0;width:100%;height:60px;background:rgba(255,0,0,0.2);text-align:center;font-size:18px;">Hata: Çeviri başarısız.</div>', unsafe_allow_html=True)
            continue
        translated_text = response_translation.text.strip()
        translated_blocks = [b.strip() for b in translated_text.split('---')]
        processed_img = img.convert("RGBA")
        draw = ImageDraw.Draw(processed_img)
        font_path = "CCComicrazy.ttf"
        for i, item in enumerate(detected_items):
            box = item.get('box')
            cleaned_translation = translated_blocks[i] if i < len(translated_blocks) else "Çeviri hatası"
            ymin, xmin, ymax, xmax = box
            left = xmin * img.width / 1000
            top = ymin * img.height / 1000
            right = xmax * img.width / 1000
            bottom = ymax * img.height / 1000
            rect_radius = 0  # Border-radius sıfırlandı
            # Şeffaf overlay oluştur
            overlay = Image.new("RGBA", processed_img.size, (255,255,255,0))
            overlay_draw = ImageDraw.Draw(overlay)
            overlay_draw.rectangle([left, top, right, bottom], fill=(255,255,255,180))
            processed_img = Image.alpha_composite(processed_img, overlay)
            draw = ImageDraw.Draw(processed_img)
            text_box_width = right - left
            text_box_height = bottom - top
            font, font_size, wrapped = get_optimal_font_size(draw, cleaned_translation, text_box_width, text_box_height, font_path)
            bbox = draw.multiline_textbbox((0, 0), wrapped, font=font, spacing=4)
            text_width = bbox[2] - bbox[0]
            text_height = bbox[3] - bbox[1]
            text_x = left + (text_box_width - text_width) / 2
            text_y = top + (text_box_height - text_height) / 2
            draw.multiline_text((text_x, text_y), wrapped, fill=(0,0,0,255), font=font, spacing=4, align="center")
        # Çevrilen görseli tekrar temp dosyaya kaydet
        temp_trans = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
        processed_img.convert("RGB").save(temp_trans, format="PNG")
        temp_trans.close()
        page['translated_img_path'] = temp_trans.name
        page['status'] = 'done'
        page['log'] = 'Çeviri tamamlandı.'
        # Çevrilen sayfayı hemen göster (base64 ile birleşik gösterim)
        buffered = io.BytesIO()
        processed_img.save(buffered, format="PNG")
        img_str = base64.b64encode(buffered.getvalue()).decode()
        placeholder.markdown(f"<img src='data:image/png;base64,{img_str}' style='display:block;margin:0;padding:0;border:none;width:100%;'>", unsafe_allow_html=True)

    # Henüz çevrilmeyen sayfalar için overlay göster (base64 ile)
    for idx, page in st.session_state.page_states.items():
        if page['status'] == 'pending':
            page_placeholders[idx].markdown(f"### Sayfa {idx+1}")
            img = Image.open(page['img_path'])
            buffered = io.BytesIO()
            img.save(buffered, format="PNG")
            img_str = base64.b64encode(buffered.getvalue()).decode()
            page_placeholders[idx].markdown(f"<img src='data:image/png;base64,{img_str}' style='display:block;margin:0;padding:0;border:none;width:100%;'>", unsafe_allow_html=True)
            page_placeholders[idx].markdown('<div style="position:relative;top:-60px;left:0;width:100%;height:60px;background:rgba(255,255,255,0.7);text-align:center;font-size:24px;">Henüz çevrilmedi, lütfen bekleyin...</div>', unsafe_allow_html=True)
        elif page['status'] == 'error':
            page_placeholders[idx].markdown(f"### Sayfa {idx+1}")
            img = Image.open(page['img_path'])
            buffered = io.BytesIO()
            img.save(buffered, format="PNG")
            img_str = base64.b64encode(buffered.getvalue()).decode()
            page_placeholders[idx].markdown(f"<img src='data:image/png;base64,{img_str}' style='display:block;margin:0;padding:0;border:none;width:100%;'>", unsafe_allow_html=True)
            page_placeholders[idx].markdown(f'<div style="position:relative;top:-60px;left:0;width:100%;height:60px;background:rgba(255,0,0,0.2);text-align:center;font-size:18px;">Hata: {page["log"]}</div>', unsafe_allow_html=True)

    # --- İNDİRME BUTONU (her zaman en altta ve her zaman göster, PDF olarak) ---
    def create_pdf_of_translated_pages():
        from PIL import Image
        pdf_pages = []
        for idx, page in st.session_state.page_states.items():
            if page['status'] == 'done' and page['translated_img_path'] is not None:
                img = Image.open(page['translated_img_path'])
                if img.mode != 'RGB':
                    img = img.convert('RGB')
                pdf_pages.append(img)
        if not pdf_pages:
            return None
        pdf_buffer = io.BytesIO()
        pdf_pages[0].save(pdf_buffer, format="PDF", save_all=True, append_images=pdf_pages[1:])
        pdf_buffer.seek(0)
        return pdf_buffer

    done_count = sum(1 for page in st.session_state.page_states.values() if page['status'] == 'done' and page['translated_img_path'] is not None)
    total_count = len(st.session_state.page_states)
    st.markdown(f"<div style='font-size:13px;color:#888;margin-bottom:4px;'>Çevrilen: {done_count} / Toplam: {total_count}</div>", unsafe_allow_html=True)
    pdf_buffer = create_pdf_of_translated_pages()
    if pdf_buffer:
        st.download_button(
            label=f"Çevrilen sayfaları indir (PDF)",
            data=pdf_buffer,
            file_name="cevrilen_sayfalar.pdf",
            mime="application/pdf"
        )
    else:
        st.download_button(
            label="Çevrilen sayfaları indir (PDF)",
            data=b"",
            file_name="cevrilen_sayfalar.pdf",
            mime="application/pdf",
            disabled=True
        )

# İlk log mesajını göster (API anahtarı bekleniyor veya uygulama hazır)
if not st.session_state.logs:
    if not API_KEYS:
         add_log("Uygulama başlatıldı. API anahtarları bekleniyor...")
    else:
         add_log("Uygulama hazır. Görsel bekleniyor...") 
