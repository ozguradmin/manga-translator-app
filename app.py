import streamlit as st
import google.generativeai as genai
from PIL import Image, ImageDraw, ImageFont
import io
import os
import re
import time
import textwrap
import zipfile
import fitz  # PyMuPDF
import rarfile
import base64
import json


# --- API Anahtar Listesi ---
API_KEYS = st.secrets["API_KEYS"]

st.set_page_config(layout="wide")
st.title("Manga Okuma ve Otomatik Çeviri (Gemini)")

if 'current_api_key_index' not in st.session_state:
    st.session_state.current_api_key_index = 0
if 'logs' not in st.session_state:
    st.session_state.logs = []
if 'page_states' not in st.session_state:
    st.session_state.page_states = {}

st.sidebar.title("Log Kayıtları")
log_area = st.sidebar.empty()
def add_log(message):
    st.session_state.logs.append(message)
    log_area.text_area("Log Mesajları", "\n".join(st.session_state.logs), height=250)

# --- Gemini API Key Cycling ---
def configure_gemini(key_index):
    try:
        selected_key = API_KEYS[key_index]
        genai.configure(api_key=selected_key)
        model = genai.GenerativeModel('gemini-1.5-pro-latest')
        add_log(f"Gemini API yapılandırıldı. Anahtar Index: {key_index} (***{selected_key[-4:]})")
        return model
    except Exception as e:
        add_log(f"HATA: API Anahtarı Index {key_index} ile yapılandırma başarısız: {e}")
        return None

def call_gemini_with_retry(model, content, max_retries=len(API_KEYS) + 2, initial_delay=3):
    current_model = model
    delay = initial_delay
    for attempt in range(max_retries):
        if current_model is None:
            add_log("HATA: Geçerli bir Gemini modeli yok. API çağrısı yapılamıyor.")
            return None
        try:
            response = current_model.generate_content(content)
            add_log("API çağrısı başarılı.")
            return response
        except Exception as e:
            if '429' in str(e):
                add_log(f"429 Hatası (Anahtar Index: {st.session_state.current_api_key_index}). Detay: {e}")
                st.session_state.current_api_key_index = (st.session_state.current_api_key_index + 1) % len(API_KEYS)
                add_log(f"Sıradaki API anahtarına geçiliyor: Index {st.session_state.current_api_key_index}. {delay}sn bekleniyor...")
                time.sleep(delay)
                current_model = configure_gemini(st.session_state.current_api_key_index)
                delay = min(delay * 1.5, 15)
                continue
            else:
                add_log(f"API çağrısı sırasında beklenmeyen hata (Anahtar Index: {st.session_state.current_api_key_index}): {e}")
                return None
    add_log(f"HATA: API çağrısı {max_retries} denemeden sonra başarısız oldu.")
    return None

model = configure_gemini(st.session_state.current_api_key_index)

# --- Dosya Yükleme ve Sayfa Çıkarma ---
def extract_images_from_file(uploaded_file):
    images = []
    filename = uploaded_file.name.lower()
    if filename.endswith('.pdf'):
        pdf_bytes = uploaded_file.read()
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        for page in doc:
            pix = page.get_pixmap()
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            images.append(img)
    elif filename.endswith('.zip') or filename.endswith('.cbz'):
        with zipfile.ZipFile(uploaded_file) as archive:
            for name in sorted(archive.namelist()):
                if name.lower().endswith(('.jpg', '.jpeg', '.png')):
                    img = Image.open(io.BytesIO(archive.read(name))).convert("RGB")
                    images.append(img)
    elif filename.endswith('.rar') or filename.endswith('.cbr'):
        with rarfile.RarFile(uploaded_file) as archive:
            for name in sorted(archive.namelist()):
                if name.lower().endswith(('.jpg', '.jpeg', '.png')):
                    img = Image.open(io.BytesIO(archive.read(name))).convert("RGB")
                    images.append(img)
    elif filename.endswith(('.jpg', '.jpeg', '.png')):
        img = Image.open(uploaded_file).convert("RGB")
        images.append(img)
    return images

# --- Font Boyutu Hesaplama ---
def get_optimal_font_size(draw, text, box_width, box_height, font_path="manga_font.ttf", max_font_size=100, min_font_size=8, padding=0):
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
    return best_font, best_size, best_wrapped

# --- Görsel Yükleme ---
uploaded_file = st.file_uploader("Bir manga dosyası veya görsel yükleyin (PDF, ZIP, CBZ, CBR, JPG, PNG)", type=["pdf", "zip", "cbz", "cbr", "jpg", "jpeg", "png"])

if uploaded_file:
    images = extract_images_from_file(uploaded_file)
    st.session_state.page_states = {}
    page_placeholders = []
    for idx, img in enumerate(images):
        st.session_state.page_states[idx] = {'status': 'pending', 'img': img, 'translated_img': None, 'log': ''}
        page_placeholders.append(st.empty())

    st.success(f"{len(images)} sayfa yüklendi. Çeviri işlemi başlatılıyor...")

    for idx, page in st.session_state.page_states.items():
        img = page['img']
        placeholder = page_placeholders[idx]
        if page['status'] == 'done':
            buffered = io.BytesIO()
            page['translated_img'].save(buffered, format="PNG")
            img_str = base64.b64encode(buffered.getvalue()).decode()
            placeholder.markdown(f"<img src='data:image/png;base64,{img_str}' style='display:block;margin:0;padding:0;border:none;width:100%;'>", unsafe_allow_html=True)
            continue
        prompt_detection = (
            "Bu görseldeki konuşma balonları veya mantıksal olarak bağlantılı metin grupları gibi metin bloklarını tespit et. "
            "Her blok için: "
            "1. İçindeki tüm metinleri (satır sonlarını koruyarak veya boşlukla birleştirerek) tek bir string olarak 'text' anahtarıyla birleştir. "
            "2. Tüm metin bloğunu çevreleyen tek bir sınırlayıcı kutuyu [ymin, xmin, ymax, xmax] formatında (0-1000 arası normalize edilmiş) 'box' anahtarıyla ver. "
            "Sonucu bir JSON listesi olarak döndür. Örneğin: "
            "[{'text': 'WHAT DOES IT\nMEAN TO BE\nHUMAN...?', 'box': [100, 780, 210, 970]}]"
        )
        response_detection = call_gemini_with_retry(model, [prompt_detection, img])
        text_response = response_detection.text.strip() if response_detection else ""
        text_response = re.sub(r"^```json\s*|^```\s*|\s*```$", "", text_response, flags=re.MULTILINE).strip()
        if not text_response:
            page['status'] = 'error'
            page['log'] = 'Gemini API yanıtı boş geldi.'
            add_log('UYARI: Gemini API yanıtı boş geldi, JSON ayrıştırma yapılmadı.')
            buffered = io.BytesIO()
            img.save(buffered, format="PNG")
            img_str = base64.b64encode(buffered.getvalue()).decode()
            placeholder.markdown(f"<img src='data:image/png;base64,{img_str}' style='display:block;margin:0;padding:0;border:none;width:100%;'>", unsafe_allow_html=True)
            placeholder.markdown('<div style="position:relative;top:-60px;left:0;width:100%;height:60px;background:rgba(255,0,0,0.2);text-align:center;font-size:18px;">Hata: Gemini API yanıtı boş geldi.</div>', unsafe_allow_html=True)
            continue
        cleaned_json_str = re.sub(r",\s*([}\]])", r"\1", text_response)
        try:
            detected_items = json.loads(cleaned_json_str)
        except Exception as e:
            page['status'] = 'error'
            page['log'] = f'JSON ayrıştırma hatası: {e}'
            add_log(f'HATA: JSON ayrıştırma hatası: {e}\nYanıt: {cleaned_json_str}')
            buffered = io.BytesIO()
            img.save(buffered, format="PNG")
            img_str = base64.b64encode(buffered.getvalue()).decode()
            placeholder.markdown(f"<img src='data:image/png;base64,{img_str}' style='display:block;margin:0;padding:0;border:none;width:100%;'>", unsafe_allow_html=True)
            placeholder.markdown(f'<div style="position:relative;top:-60px;left:0;width:100%;height:60px;background:rgba(255,0,0,0.2);text-align:center;font-size:18px;">Hata: JSON ayrıştırma hatası: {e}</div>', unsafe_allow_html=True)
            continue
        all_texts = [item.get('text', '') for item in detected_items]
        joined_text = '\n---\n'.join(all_texts)
        prompt_translation = f"Aşağıdaki metin bloklarını Türkçeye çevir. Her blok arasını --- ile ayırdım, sen de çeviride blokları aynı sırayla --- ile ayırarak döndür:\n\n{joined_text}"
        response_translation = call_gemini_with_retry(model, prompt_translation)
        if response_translation is None:
            page['status'] = 'error'
            page['log'] = 'Çeviri başarısız.'
            buffered = io.BytesIO()
            img.save(buffered, format="PNG")
            img_str = base64.b64encode(buffered.getvalue()).decode()
            placeholder.markdown(f"<img src='data:image/png;base64,{img_str}' style='display:block;margin:0;padding:0;border:none;width:100%;'>", unsafe_allow_html=True)
            placeholder.markdown('<div style="position:relative;top:-60px;left:0;width:100%;height:60px;background:rgba(255,0,0,0.2);text-align:center;font-size:18px;">Hata: Çeviri başarısız.</div>', unsafe_allow_html=True)
            continue
        translated_text = response_translation.text.strip()
        translated_blocks = [b.strip() for b in translated_text.split('---')]
        processed_img = img.convert("RGBA")
        for i, item in enumerate(detected_items):
            box = item.get('box')
            cleaned_translation = translated_blocks[i] if i < len(translated_blocks) else "Çeviri hatası"
            ymin, xmin, ymax, xmax = box
            left = xmin * img.width / 1000
            top = ymin * img.height / 1000
            right = xmax * img.width / 1000
            bottom = ymax * img.height / 1000
            overlay = Image.new("RGBA", processed_img.size, (255,255,255,0))
            overlay_draw = ImageDraw.Draw(overlay)
            overlay_draw.rectangle([left, top, right, bottom], fill=(255,255,255,180))
            processed_img = Image.alpha_composite(processed_img, overlay)
            draw = ImageDraw.Draw(processed_img)
            text_box_width = right - left
            text_box_height = bottom - top
            font_path = "CCComicrazy.ttf"
            font, font_size, wrapped = get_optimal_font_size(draw, cleaned_translation, text_box_width, text_box_height, font_path)
            bbox = draw.multiline_textbbox((0, 0), wrapped, font=font, spacing=4)
            text_width = bbox[2] - bbox[0]
            text_height = bbox[3] - bbox[1]
            text_x = left + (text_box_width - text_width) / 2
            text_y = top + (text_box_height - text_height) / 2
            draw.multiline_text((text_x, text_y), wrapped, fill=(0,0,0,255), font=font, spacing=4, align="center")
        page['translated_img'] = processed_img.convert("RGB")
        page['status'] = 'done'
        page['log'] = 'Çeviri tamamlandı.'
        buffered = io.BytesIO()
        page['translated_img'].save(buffered, format="PNG")
        img_str = base64.b64encode(buffered.getvalue()).decode()
        placeholder.markdown(f"<img src='data:image/png;base64,{img_str}' style='display:block;margin:0;padding:0;border:none;width:100%;'>", unsafe_allow_html=True)

    # --- İNDİRME BUTONU (her zaman en altta ve her zaman göster, PDF olarak) ---
    def create_pdf_of_translated_pages():
        from PIL import Image
        pdf_pages = []
        for idx, page in st.session_state.page_states.items():
            if page['status'] == 'done' and page['translated_img'] is not None:
                img = page['translated_img']
                if img.mode != 'RGB':
                    img = img.convert('RGB')
                pdf_pages.append(img)
        if not pdf_pages:
            return None
        pdf_buffer = io.BytesIO()
        pdf_pages[0].save(pdf_buffer, format="PDF", save_all=True, append_images=pdf_pages[1:])
        pdf_buffer.seek(0)
        return pdf_buffer

    done_count = sum(1 for page in st.session_state.page_states.values() if page['status'] == 'done' and page['translated_img'] is not None)
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
