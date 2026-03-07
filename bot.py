"""
🏫 ПроУрок — Telegram-бот помощник для учителей
Демо-версия для конференции по ИИ в образовании
Лимит: 10 запросов + 2 генерации документов
"""

import os
import io
import json
import logging
import tempfile
import re
import base64
import subprocess
import speech_recognition as sr
from pydub import AudioSegment
from datetime import datetime
import PyPDF2
from docx import Document as DocxDocument

import requests
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import numpy as np

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)
from anthropic import Anthropic

from docx import Document
from docx.shared import Pt, Inches, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

from pptx import Presentation
from pptx.util import Inches as PptxInches, Pt as PptxPt, Emu
from pptx.dml.color import RGBColor as PptxRGB
from pptx.enum.text import PP_ALIGN
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.chart import XL_CHART_TYPE

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
client = Anthropic(api_key=ANTHROPIC_API_KEY)

# ─── Название бота ────────────────────────────────────────────
BOT_NAME = "ПроУрок"
BOT_VERSION = "1.0"
SELLING_BOT_URL = "https://t.me/SELLING_BOT"  # TODO: заменить на реальный username

# ─── Лимиты для демо ─────────────────────────────────────────
LIMIT_QUERIES = 10      # текстовые запросы, фото, голос, документы
LIMIT_GENERATIONS = 2   # генерации файлов: PPTX, Word, Excel

# Счётчики по user_id (в памяти, сбросятся при перезапуске)
user_limits = {}  # {user_id: {"queries": 0, "generations": 0}}

def get_limits(user_id):
    """Получить счётчики пользователя."""
    if user_id not in user_limits:
        user_limits[user_id] = {"queries": 0, "generations": 0}
    return user_limits[user_id]

def check_query_limit(user_id):
    """Проверить лимит запросов. Возвращает True если можно."""
    limits = get_limits(user_id)
    return limits["queries"] < LIMIT_QUERIES

def check_generation_limit(user_id):
    """Проверить лимит генераций. Возвращает True если можно."""
    limits = get_limits(user_id)
    return limits["generations"] < LIMIT_GENERATIONS

def use_query(user_id):
    """Использовать 1 запрос."""
    limits = get_limits(user_id)
    limits["queries"] += 1

def use_generation(user_id):
    """Использовать 1 генерацию."""
    limits = get_limits(user_id)
    limits["generations"] += 1

def get_limit_status(user_id):
    """Статус лимитов пользователя."""
    limits = get_limits(user_id)
    q_left = max(0, LIMIT_QUERIES - limits["queries"])
    g_left = max(0, LIMIT_GENERATIONS - limits["generations"])
    return q_left, g_left

LIMIT_MESSAGE = (
    "🔒 **Демо-доступ исчерпан!**\n\n"
    "Вы использовали все бесплатные запросы.\n\n"
    "✨ Чтобы продолжить пользоваться помощником «Учительская» "
    "без ограничений, перейдите по ссылке:\n\n"
    f"👉 {SELLING_BOT_URL}\n\n"
    "Там вы сможете:\n"
    "• Узнать о тарифах и возможностях\n"
    "• Подключить полную версию\n"
    "• Задать вопросы по работе бота"
)

GENERATION_LIMIT_MESSAGE = (
    "🔒 **Лимит генерации документов исчерпан!**\n\n"
    "Вы использовали {used} из {total} генераций (презентации, Word, Excel).\n\n"
    "Текстовые запросы ещё доступны: осталось {q_left}.\n\n"
    "✨ Для безлимитного создания документов:\n"
    f"👉 {SELLING_BOT_URL}"
)

# ─── История диалогов ─────────────────────────────────────────
user_conversations = {}
MAX_HISTORY = 20

# ─── Системный промпт (УНИВЕРСАЛЬНЫЙ) ────────────────────────
SYSTEM_PROMPT = f"""Ты — профессиональный помощник учителя. Твоё имя — {BOT_NAME}.

Ты помогаешь учителям ЛЮБЫХ предметов: математика, русский язык, литература, история, обществознание, физика, химия, биология, география, английский язык, информатика, ИЗО, музыка, физкультура, технология, ОБЖ и другие.

ТВОИ ВОЗМОЖНОСТИ:
1. Создание планов уроков (1-11 класс, любой предмет)
2. Генерация тестов, контрольных работ, викторин
3. Подготовка конспектов и методических материалов
4. Создание рабочих листов и дидактических карточек
5. Помощь с рабочими программами по ФГОС
6. Подготовка к ОГЭ/ЕГЭ по любому предмету
7. Создание презентаций PowerPoint с диаграммами
8. Создание таблиц и документов Word
9. Анализ фотографий учебников, тетрадей, заданий
10. Идеи для интерактивных уроков и внеклассных мероприятий

ПРАВИЛА:
- Отвечай на русском языке
- Адаптируй сложность под указанный класс и предмет
- Учитывай требования ФГОС
- Будь точен в фактах, датах, формулах
- Если предмет не указан — уточни или дай универсальный ответ

ФОРМАТ ДЛЯ ДОКУМЕНТОВ:
- [WORD_DOC] — Word-документ
- [EXCEL_DOC] — Excel-таблица
- [PPTX_DOC] — презентация PowerPoint

ФОРМАТ ДЛЯ ПРЕЗЕНТАЦИЙ (при маркере [PPTX_DOC]):

[SLIDE]
ЗАГОЛОВОК: Название слайда
ПОДЗАГОЛОВОК: Подзаголовок (если есть)
СОДЕРЖИМОЕ:
- Пункт 1
- Пункт 2
ЗАМЕТКИ: Заметки для учителя
[/SLIDE]

ДИАГРАММЫ В ПРЕЗЕНТАЦИЯХ:
Если на слайде нужна диаграмма, используй формат:
[CHART]
ТИП: bar (или pie, или line)
НАЗВАНИЕ: Название диаграммы
МЕТКИ: Метка1, Метка2, Метка3
ЗНАЧЕНИЯ: 100, 200, 300
ЕДИНИЦЫ: единица измерения
[/CHART]

КАРТИНКИ В ПРЕЗЕНТАЦИЯХ:
Если на слайде нужна картинка, укажи:
[IMAGE]
ЗАПРОС: search query in english
ОПИСАНИЕ: Описание картинки
[/IMAGE]

Создавай 8-12 слайдов. Добавляй диаграммы и картинки где уместно (2-4 на презентацию).
Каждый пункт на слайде — полное предложение с фактами, минимум 10-15 слов.
НЕ пиши коротких фраз из 2-3 слов!

РЕКОМЕНДУЕМЫЕ ИСТОЧНИКИ:
- resh.edu.ru, yaklass.ru, infourok.ru
- fipi.ru (для ОГЭ/ЕГЭ)
- Учебники из федерального перечня
"""


# ─── Вспомогательные функции ──────────────────────────────────

def get_history(user_id):
    if user_id not in user_conversations:
        user_conversations[user_id] = []
    return user_conversations[user_id]

def add_to_history(user_id, role, content):
    history = get_history(user_id)
    history.append({"role": role, "content": content})
    if len(history) > MAX_HISTORY:
        user_conversations[user_id] = history[-MAX_HISTORY:]

def ask_claude(user_id, message, image_data=None):
    """Отправляет запрос к Claude. Поддерживает текст и изображения."""
    if image_data:
        content = [
            {"type": "image", "source": {"type": "base64", "media_type": image_data['mime'], "data": image_data['base64']}},
            {"type": "text", "text": message}
        ]
        add_to_history(user_id, "user", message)
    else:
        content = message
        add_to_history(user_id, "user", message)
    
    history = get_history(user_id)
    
    if image_data:
        messages = [{"role": "user", "content": content}]
    else:
        messages = history
    
    try:
        response = client.messages.create(
            model="claude-3-haiku-20240307",
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=messages
        )
        answer = response.content[0].text
        add_to_history(user_id, "assistant", answer)
        return answer
    except Exception as e:
        logger.error(f"Ошибка Claude API: {e}")
        return f"⚠️ Произошла ошибка при обращении к ИИ: {str(e)}"


# ─── Загрузка картинок из интернета ──────────────────────────

def download_image(query):
    """Скачивает картинку из Wikimedia Commons или Unsplash."""
    try:
        search_url = "https://commons.wikimedia.org/w/api.php"
        search_params = {
            "action": "query",
            "list": "search",
            "srsearch": query,
            "srnamespace": "6",
            "srlimit": "3",
            "format": "json",
        }
        resp = requests.get(search_url, params=search_params, timeout=10)
        data = resp.json()
        
        if "query" in data and data["query"]["search"]:
            page_title = data["query"]["search"][0]["title"]
            info_params = {
                "action": "query",
                "titles": page_title,
                "prop": "imageinfo",
                "iiprop": "url|mime",
                "format": "json",
            }
            resp2 = requests.get(search_url, params=info_params, timeout=10)
            data2 = resp2.json()
            
            if "query" in data2 and "pages" in data2["query"]:
                for page_id, page_data in data2["query"]["pages"].items():
                    if "imageinfo" in page_data:
                        img_url = page_data["imageinfo"][0]["url"]
                        mime = page_data["imageinfo"][0].get("mime", "")
                        if "image" in mime:
                            img_resp = requests.get(img_url, timeout=15)
                            if img_resp.status_code == 200:
                                return io.BytesIO(img_resp.content)
    except Exception as e:
        logger.warning(f"Не удалось загрузить картинку для '{query}': {e}")
    
    try:
        unsplash_url = f"https://source.unsplash.com/800x500/?{query}"
        resp = requests.get(unsplash_url, timeout=15, allow_redirects=True)
        if resp.status_code == 200 and len(resp.content) > 1000:
            return io.BytesIO(resp.content)
    except Exception as e:
        logger.warning(f"Unsplash ошибка для '{query}': {e}")
    
    return None


# ─── Генерация диаграмм с matplotlib ─────────────────────────

def create_chart_image(chart_type, title, labels, values, units="", theme_colors=None):
    """Создаёт диаграмму и возвращает как BytesIO."""
    if theme_colors is None:
        theme_colors = ['#4A90D9', '#50C878', '#FF6B6B', '#FFD93D', '#6C5CE7',
                        '#A8E6CF', '#FF8A80', '#82B1FF', '#B388FF', '#F48FB1']
    
    plt.rcParams['font.size'] = 14
    plt.rcParams['figure.facecolor'] = 'white'
    
    fig, ax = plt.subplots(figsize=(8, 5))
    
    if chart_type == 'bar':
        colors = theme_colors[:len(labels)]
        bars = ax.bar(labels, values, color=colors, edgecolor='white', linewidth=0.5)
        for bar, val in zip(bars, values):
            ax.text(bar.get_x() + bar.get_width()/2., bar.get_height() + max(values)*0.02,
                    f'{val:g}', ha='center', va='bottom', fontsize=12, fontweight='bold')
        ax.set_ylabel(units, fontsize=12)
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.spines['left'].set_color('#CCCCCC')
        ax.spines['bottom'].set_color('#CCCCCC')
        ax.tick_params(colors='#666666')
        if any(len(str(l)) > 8 for l in labels):
            plt.xticks(rotation=30, ha='right')
    
    elif chart_type == 'pie':
        colors = theme_colors[:len(labels)]
        wedges, texts, autotexts = ax.pie(
            values, labels=labels, autopct='%1.1f%%', colors=colors,
            startangle=90, pctdistance=0.8, labeldistance=1.15,
            wedgeprops={'edgecolor': 'white', 'linewidth': 2}
        )
        for text in autotexts:
            text.set_fontsize(11)
            text.set_fontweight('bold')
        for text in texts:
            text.set_fontsize(10)
    
    elif chart_type == 'line':
        ax.plot(labels, values, color=theme_colors[0], linewidth=3, marker='o',
                markersize=8, markerfacecolor=theme_colors[1], markeredgecolor='white', markeredgewidth=2)
        ax.fill_between(range(len(labels)), values, alpha=0.1, color=theme_colors[0])
        ax.set_ylabel(units, fontsize=12)
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.spines['left'].set_color('#CCCCCC')
        ax.spines['bottom'].set_color('#CCCCCC')
        ax.tick_params(colors='#666666')
        ax.grid(axis='y', alpha=0.3, color='#CCCCCC')
        if any(len(str(l)) > 8 for l in labels):
            plt.xticks(rotation=30, ha='right')
    
    ax.set_title(title, fontsize=16, fontweight='bold', pad=15, color='#1E293B')
    plt.tight_layout()
    
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=150, bbox_inches='tight',
                facecolor='white', edgecolor='none')
    plt.close(fig)
    buf.seek(0)
    return buf


# ─── Цветовые палитры (универсальные) ────────────────────────

THEMES = {
    "blue": {
        "bg_dark": PptxRGB(26, 54, 93),
        "bg_light": PptxRGB(240, 244, 250),
        "accent": PptxRGB(74, 144, 217),
        "accent2": PptxRGB(80, 200, 120),
        "text_light": PptxRGB(255, 255, 255),
        "text_dark": PptxRGB(30, 41, 59),
        "subtitle": PptxRGB(180, 200, 230),
        "chart_colors": ['#4A90D9', '#50C878', '#FF6B6B', '#FFD93D', '#6C5CE7'],
    },
    "green": {
        "bg_dark": PptxRGB(34, 87, 60),
        "bg_light": PptxRGB(242, 248, 244),
        "accent": PptxRGB(80, 200, 120),
        "accent2": PptxRGB(74, 144, 217),
        "text_light": PptxRGB(255, 255, 255),
        "text_dark": PptxRGB(30, 45, 35),
        "subtitle": PptxRGB(180, 220, 195),
        "chart_colors": ['#22573C', '#50C878', '#A8E6CF', '#4A90D9', '#FFD93D'],
    },
    "warm": {
        "bg_dark": PptxRGB(93, 52, 40),
        "bg_light": PptxRGB(250, 245, 240),
        "accent": PptxRGB(217, 144, 74),
        "accent2": PptxRGB(200, 100, 80),
        "text_light": PptxRGB(255, 255, 255),
        "text_dark": PptxRGB(60, 40, 35),
        "subtitle": PptxRGB(230, 200, 180),
        "chart_colors": ['#D9904A', '#C86450', '#FFD93D', '#A8E6CF', '#6C5CE7'],
    },
}

def choose_theme(topic):
    topic_lower = topic.lower()
    if any(w in topic_lower for w in ['матем', 'физик', 'информ', 'геометр', 'алгебр', 'программ']):
        return THEMES["blue"]
    elif any(w in topic_lower for w in ['биолог', 'природ', 'эколог', 'ботаник', 'зоолог', 'географ']):
        return THEMES["green"]
    elif any(w in topic_lower for w in ['истор', 'литератур', 'искусств', 'музык', 'культур']):
        return THEMES["warm"]
    return THEMES["blue"]  # По умолчанию — синяя


# ─── Парсинг слайдов ─────────────────────────────────────────

def parse_chart(text):
    charts = []
    pattern = r'\[CHART\](.*?)\[/CHART\]'
    matches = re.findall(pattern, text, re.DOTALL)
    for match in matches:
        chart = {'type': 'bar', 'title': '', 'labels': [], 'values': [], 'units': ''}
        for line in match.strip().split('\n'):
            line = line.strip()
            if line.startswith('ТИП:'):
                chart['type'] = line[4:].strip().lower()
            elif line.startswith('НАЗВАНИЕ:'):
                chart['title'] = line[9:].strip()
            elif line.startswith('МЕТКИ:'):
                chart['labels'] = [l.strip() for l in line[6:].split(',')]
            elif line.startswith('ЗНАЧЕНИЯ:'):
                try:
                    chart['values'] = [float(v.strip().replace(' ', '')) for v in line[9:].split(',')]
                except ValueError:
                    chart['values'] = []
            elif line.startswith('ЕДИНИЦЫ:'):
                chart['units'] = line[8:].strip()
        if chart['labels'] and chart['values']:
            charts.append(chart)
    return charts

def parse_image(text):
    images = []
    pattern = r'\[IMAGE\](.*?)\[/IMAGE\]'
    matches = re.findall(pattern, text, re.DOTALL)
    for match in matches:
        img = {'query': '', 'description': ''}
        for line in match.strip().split('\n'):
            line = line.strip()
            if line.startswith('ЗАПРОС:'):
                img['query'] = line[7:].strip()
            elif line.startswith('ОПИСАНИЕ:'):
                img['description'] = line[9:].strip()
        if img['query']:
            images.append(img)
    return images

def parse_slides(content):
    slides = []
    parts = content.split('[SLIDE]')
    for part in parts:
        if '[/SLIDE]' not in part:
            continue
        slide_text = part.split('[/SLIDE]')[0].strip()
        slide = {'title': '', 'subtitle': '', 'content': [], 'notes': '', 'charts': [], 'images': []}
        
        slide['charts'] = parse_chart(slide_text)
        slide['images'] = parse_image(slide_text)
        
        clean_text = re.sub(r'\[CHART\].*?\[/CHART\]', '', slide_text, flags=re.DOTALL)
        clean_text = re.sub(r'\[IMAGE\].*?\[/IMAGE\]', '', clean_text, flags=re.DOTALL)
        
        lines = clean_text.split('\n')
        current_section = None
        for line in lines:
            stripped = line.strip()
            if stripped.startswith('ЗАГОЛОВОК:'):
                slide['title'] = stripped[10:].strip()
            elif stripped.startswith('ПОДЗАГОЛОВОК:'):
                slide['subtitle'] = stripped[13:].strip()
            elif stripped.startswith('СОДЕРЖИМОЕ:'):
                current_section = 'content'
            elif stripped.startswith('ЗАМЕТКИ:'):
                slide['notes'] = stripped[8:].strip()
                current_section = 'notes'
            elif current_section == 'content' and stripped:
                if stripped.startswith('- ') or stripped.startswith('• '):
                    slide['content'].append(stripped[2:])
                else:
                    slide['content'].append(stripped)
            elif current_section == 'notes' and stripped:
                slide['notes'] += ' ' + stripped
        
        if slide['title']:
            slides.append(slide)
    return slides

def create_slides_from_text(title, content):
    slides = [{'title': title, 'subtitle': 'Учебный материал', 'content': [], 'notes': '', 'charts': [], 'images': []}]
    clean_lines = re.sub(r'\[CHART\].*?\[/CHART\]', '', content, flags=re.DOTALL)
    clean_lines = re.sub(r'\[IMAGE\].*?\[/IMAGE\]', '', clean_lines, flags=re.DOTALL).split('\n')
    
    current_slide = None
    for line in clean_lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith('# ') or stripped.startswith('## '):
            heading = stripped.lstrip('#').strip()
            current_slide = {'title': heading, 'subtitle': '', 'content': [], 'notes': '', 'charts': [], 'images': []}
            slides.append(current_slide)
        elif current_slide is not None:
            if stripped.startswith('- ') or stripped.startswith('• '):
                current_slide['content'].append(stripped[2:])
            elif len(stripped) > 5:
                current_slide['content'].append(stripped)
    return slides if len(slides) > 1 else [{'title': title, 'subtitle': '', 'content': [content[:200]], 'notes': '', 'charts': [], 'images': []}]


# ─── Создание PowerPoint ─────────────────────────────────────

def add_slide_number(slide, num, theme, slide_width, slide_height):
    bg = slide.background
    fill = bg.fill
    fill.solid()
    fill.fore_color.rgb = theme['bg_light']
    
    bar = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, PptxInches(0.3), slide_height)
    bar.fill.solid()
    bar.fill.fore_color.rgb = theme['bg_dark']
    bar.line.fill.background()
    
    accent = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, PptxInches(0.3), 0, PptxInches(0.05), slide_height)
    accent.fill.solid()
    accent.fill.fore_color.rgb = theme['accent']
    accent.line.fill.background()
    
    num_shape = slide.shapes.add_shape(MSO_SHAPE.OVAL, PptxInches(0.65), PptxInches(0.35), PptxInches(0.55), PptxInches(0.55))
    num_shape.fill.solid()
    num_shape.fill.fore_color.rgb = theme['bg_dark']
    num_shape.line.fill.background()
    ntf = num_shape.text_frame
    ntf.paragraphs[0].text = str(num)
    ntf.paragraphs[0].font.size = PptxPt(18)
    ntf.paragraphs[0].font.bold = True
    ntf.paragraphs[0].font.color.rgb = theme['text_light']
    ntf.paragraphs[0].alignment = PP_ALIGN.CENTER


def create_pptx_presentation(title, content):
    slides_data = parse_slides(content)
    if not slides_data:
        slides_data = create_slides_from_text(title, content)
    
    all_charts = parse_chart(content)
    all_images = parse_image(content)
    
    theme = choose_theme(title)
    prs = Presentation()
    prs.slide_width = PptxInches(13.333)
    prs.slide_height = PptxInches(7.5)
    slide_width = prs.slide_width
    slide_height = prs.slide_height

    # === ТИТУЛЬНЫЙ СЛАЙД ===
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    bg = slide.background
    fill = bg.fill
    fill.solid()
    fill.fore_color.rgb = theme['bg_dark']

    shape = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, slide_width, PptxInches(0.12))
    shape.fill.solid()
    shape.fill.fore_color.rgb = theme['accent']
    shape.line.fill.background()

    deco = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, PptxInches(0.8), PptxInches(1.5), PptxInches(0.12), PptxInches(3))
    deco.fill.solid()
    deco.fill.fore_color.rgb = theme['accent']
    deco.line.fill.background()

    first_title = slides_data[0]['title'] if slides_data else title
    txBox = slide.shapes.add_textbox(PptxInches(1.3), PptxInches(1.8), PptxInches(10), PptxInches(2))
    tf = txBox.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.text = first_title
    p.font.size = PptxPt(44)
    p.font.bold = True
    p.font.color.rgb = theme['text_light']
    p.font.name = "Georgia"

    if slides_data and slides_data[0].get('subtitle'):
        p2 = tf.add_paragraph()
        p2.text = slides_data[0]['subtitle']
        p2.font.size = PptxPt(22)
        p2.font.color.rgb = theme['subtitle']
        p2.font.name = "Calibri"
        p2.space_before = PptxPt(20)

    txBox2 = slide.shapes.add_textbox(PptxInches(1.3), PptxInches(5.5), PptxInches(8), PptxInches(0.8))
    tf2 = txBox2.text_frame
    p3 = tf2.paragraphs[0]
    p3.text = f"Подготовлено в «{BOT_NAME}»  •  {datetime.now().strftime('%d.%m.%Y')}"
    p3.font.size = PptxPt(14)
    p3.font.color.rgb = theme['subtitle']
    p3.font.name = "Calibri"

    if slides_data and slides_data[0].get('images'):
        img_data = download_image(slides_data[0]['images'][0]['query'])
        if img_data:
            try:
                slide.shapes.add_picture(img_data, PptxInches(8.5), PptxInches(1.5), PptxInches(4), PptxInches(4))
            except:
                pass

    shape2 = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, slide_height - PptxInches(0.12), slide_width, PptxInches(0.12))
    shape2.fill.solid()
    shape2.fill.fore_color.rgb = theme['accent']
    shape2.line.fill.background()

    # === СЛАЙДЫ С СОДЕРЖИМЫМ ===
    content_slides = slides_data[1:] if len(slides_data) > 1 else slides_data

    for i, sdata in enumerate(content_slides):
        slide = prs.slides.add_slide(prs.slide_layouts[6])
        add_slide_number(slide, i + 1, theme, slide_width, slide_height)

        has_visual = bool(sdata.get('charts') or sdata.get('images'))
        content_width = PptxInches(6) if has_visual else PptxInches(11)

        title_box = slide.shapes.add_textbox(PptxInches(1.5), PptxInches(0.3), PptxInches(11), PptxInches(0.8))
        ttf = title_box.text_frame
        ttf.word_wrap = True
        tp = ttf.paragraphs[0]
        tp.text = sdata['title']
        tp.font.size = PptxPt(30)
        tp.font.bold = True
        tp.font.color.rgb = theme['text_dark']
        tp.font.name = "Georgia"

        line = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, PptxInches(1.5), PptxInches(1.15), PptxInches(2.5), PptxInches(0.04))
        line.fill.solid()
        line.fill.fore_color.rgb = theme['accent']
        line.line.fill.background()

        if sdata['content']:
            content_box = slide.shapes.add_textbox(PptxInches(1.5), PptxInches(1.5), content_width, PptxInches(5))
            ctf = content_box.text_frame
            ctf.word_wrap = True
            for j, item in enumerate(sdata['content']):
                p = ctf.paragraphs[0] if j == 0 else ctf.add_paragraph()
                p.text = f"  •  {item}"
                p.font.size = PptxPt(16)
                p.font.color.rgb = theme['text_dark']
                p.font.name = "Calibri"
                p.space_after = PptxPt(10)

        if sdata.get('charts'):
            chart_data = sdata['charts'][0]
            chart_img = create_chart_image(
                chart_data['type'], chart_data['title'],
                chart_data['labels'], chart_data['values'],
                chart_data.get('units', ''), theme.get('chart_colors')
            )
            if chart_img:
                slide.shapes.add_picture(chart_img, PptxInches(7.5), PptxInches(1.3), PptxInches(5.3), PptxInches(3.5))

        elif sdata.get('images'):
            img_data = download_image(sdata['images'][0]['query'])
            if img_data:
                try:
                    pic = slide.shapes.add_picture(img_data, PptxInches(7.8), PptxInches(1.3), PptxInches(4.8), PptxInches(3.5))
                    cap_box = slide.shapes.add_textbox(PptxInches(7.8), PptxInches(4.9), PptxInches(4.8), PptxInches(0.5))
                    cap_tf = cap_box.text_frame
                    cap_p = cap_tf.paragraphs[0]
                    cap_p.text = sdata['images'][0].get('description', '')
                    cap_p.font.size = PptxPt(10)
                    cap_p.font.italic = True
                    cap_p.font.color.rgb = PptxRGB(140, 140, 140)
                    cap_p.alignment = PP_ALIGN.CENTER
                except:
                    pass

        if sdata.get('notes'):
            notes_slide = slide.notes_slide
            notes_slide.notes_text_frame.text = sdata['notes']

        footer = slide.shapes.add_textbox(PptxInches(0.7), PptxInches(6.95), PptxInches(12), PptxInches(0.35))
        fp = footer.text_frame.paragraphs[0]
        fp.text = f"{BOT_NAME}  •  {sdata['title'][:50]}"
        fp.font.size = PptxPt(9)
        fp.font.color.rgb = PptxRGB(170, 170, 170)
        fp.font.name = "Calibri"

    # === ФИНАЛЬНЫЙ СЛАЙД ===
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    bg = slide.background
    fill = bg.fill
    fill.solid()
    fill.fore_color.rgb = theme['bg_dark']

    shape = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, slide_width, PptxInches(0.12))
    shape.fill.solid()
    shape.fill.fore_color.rgb = theme['accent']
    shape.line.fill.background()

    txBox = slide.shapes.add_textbox(PptxInches(1), PptxInches(2.2), PptxInches(11.333), PptxInches(2.5))
    tf = txBox.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.text = "Спасибо за внимание!"
    p.font.size = PptxPt(44)
    p.font.bold = True
    p.font.color.rgb = theme['text_light']
    p.font.name = "Georgia"
    p.alignment = PP_ALIGN.CENTER

    p2 = tf.add_paragraph()
    p2.text = f"Создано с помощью «{BOT_NAME}»"
    p2.font.size = PptxPt(18)
    p2.font.color.rgb = theme['subtitle']
    p2.font.name = "Calibri"
    p2.alignment = PP_ALIGN.CENTER
    p2.space_before = PptxPt(30)

    shape2 = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, slide_height - PptxInches(0.12), slide_width, PptxInches(0.12))
    shape2.fill.solid()
    shape2.fill.fore_color.rgb = theme['accent']
    shape2.line.fill.background()

    buffer = io.BytesIO()
    prs.save(buffer)
    buffer.seek(0)
    return buffer


# ─── Word документ ────────────────────────────────────────────

def create_word_document(title, content):
    doc = Document()
    style = doc.styles['Normal']
    style.font.name = 'Times New Roman'
    style.font.size = Pt(12)

    heading = doc.add_heading(title, level=0)
    heading.alignment = WD_ALIGN_PARAGRAPH.CENTER
    for run in heading.runs:
        run.font.color.rgb = RGBColor(26, 54, 93)

    date_para = doc.add_paragraph()
    date_para.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    dr = date_para.add_run(f"Дата: {datetime.now().strftime('%d.%m.%Y')}")
    dr.font.size = Pt(10)
    dr.font.color.rgb = RGBColor(128, 128, 128)
    doc.add_paragraph()

    for line in content.split('\n'):
        stripped = line.strip()
        if not stripped:
            doc.add_paragraph()
        elif stripped.startswith('## '):
            h = doc.add_heading(stripped[3:], level=2)
            for r in h.runs: r.font.color.rgb = RGBColor(26, 54, 93)
        elif stripped.startswith('### '):
            h = doc.add_heading(stripped[4:], level=3)
            for r in h.runs: r.font.color.rgb = RGBColor(74, 144, 217)
        elif stripped.startswith('# '):
            h = doc.add_heading(stripped[2:], level=1)
            for r in h.runs: r.font.color.rgb = RGBColor(26, 54, 93)
        elif stripped.startswith('- ') or stripped.startswith('• '):
            doc.add_paragraph(stripped[2:], style='List Bullet')
        else:
            p = doc.add_paragraph()
            parts = stripped.split('**')
            for idx, part in enumerate(parts):
                if part:
                    run = p.add_run(part)
                    if idx % 2 == 1: run.bold = True

    buffer = io.BytesIO()
    doc.save(buffer)
    buffer.seek(0)
    return buffer


# ─── Excel документ ───────────────────────────────────────────

def create_excel_document(title, content):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = title[:31]

    hfont = Font(name='Arial', size=12, bold=True, color='FFFFFF')
    hfill = PatternFill(start_color='1A365D', end_color='1A365D', fill_type='solid')
    halign = Alignment(horizontal='center', vertical='center', wrap_text=True)
    cfont = Font(name='Arial', size=11)
    calign = Alignment(vertical='center', wrap_text=True)
    border = Border(left=Side(style='thin'), right=Side(style='thin'),
                    top=Side(style='thin'), bottom=Side(style='thin'))

    lines = [l.strip() for l in content.split('\n') if l.strip()]
    table_lines = [l for l in lines if '|' in l]

    if table_lines:
        row_num = 0
        for line in table_lines:
            if all(c in '-| ' for c in line): continue
            row_num += 1
            cells = [c.strip() for c in line.split('|') if c.strip()]
            for col_idx, value in enumerate(cells, 1):
                cell = ws.cell(row=row_num, column=col_idx, value=value)
                cell.border = border
                if row_num == 1:
                    cell.font, cell.fill, cell.alignment = hfont, hfill, halign
                else:
                    cell.font, cell.alignment = cfont, calign

    for col in ws.columns:
        max_len = max((len(str(c.value or '')) for c in col), default=10)
        ws.column_dimensions[col[0].column_letter].width = min(max_len + 4, 50)

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer


# ─── Определение типа документа ───────────────────────────────

def detect_document_request(text):
    if '[PPTX_DOC]' in text:
        parts = text.split('[PPTX_DOC]', 1)
        content = parts[1].strip() if len(parts) > 1 else text
        title = "Презентация"
        for line in content.split('\n'):
            s = line.strip()
            if s.startswith('ЗАГОЛОВОК:'):
                title = s[10:].strip(); break
            elif s.startswith('#'):
                title = s.lstrip('#').strip(); break
            elif s and not s.startswith('['):
                title = s[:60]; break
        return ('pptx', title, content)
    elif '[WORD_DOC]' in text:
        parts = text.split('[WORD_DOC]', 1)
        content = parts[1].strip() if len(parts) > 1 else text
        title = "Учебный материал"
        for line in content.split('\n'):
            if line.strip().startswith('#'):
                title = line.strip().lstrip('#').strip(); break
            elif line.strip():
                title = line.strip()[:60]; break
        return ('word', title, content)
    elif '[EXCEL_DOC]' in text:
        parts = text.split('[EXCEL_DOC]', 1)
        content = parts[1].strip() if len(parts) > 1 else text
        title = "Таблица"
        for line in content.split('\n'):
            if line.strip() and '|' not in line:
                title = line.strip()[:31]; break
        return ('excel', title, content)
    return (None, '', '')


# ─── Постоянная клавиатура ────────────────────────────────────

PERSISTENT_KEYBOARD = ReplyKeyboardMarkup(
    [[KeyboardButton("📋 Меню"), KeyboardButton("📽 Презентация")],
     [KeyboardButton("📄 Word"), KeyboardButton("📊 Excel")],
     [KeyboardButton("🗑 Очистить"), KeyboardButton("❓ Помощь")]],
    resize_keyboard=True, is_persistent=True
)


# ─── Вспомогательная: отправка ответа с проверкой лимитов ────

async def send_limited_reply(update, context, user_id, answer):
    """Обрабатывает ответ Claude: если документ — проверяет лимит генераций, иначе просто отправляет текст."""
    doc_type, doc_title, doc_content = detect_document_request(answer)

    if doc_type:
        # Это генерация документа — проверяем лимит
        if not check_generation_limit(user_id):
            q_left, g_left = get_limit_status(user_id)
            msg = GENERATION_LIMIT_MESSAGE.format(
                used=LIMIT_GENERATIONS, total=LIMIT_GENERATIONS, q_left=q_left
            )
            await update.message.reply_text(msg, parse_mode='Markdown')
            return
        
        use_generation(user_id)
        q_left, g_left = get_limit_status(user_id)

        if doc_type == 'pptx':
            await update.message.reply_text("📽 Презентация готова!")
            buffer = create_pptx_presentation(doc_title, doc_content)
            filename = f"{doc_title[:30].replace(' ', '_')}.pptx"
            await update.message.reply_document(document=buffer, filename=filename, caption=f"📽 {doc_title}")
        elif doc_type == 'word':
            clean = answer.replace('[WORD_DOC]', '').strip()
            if len(clean) > 4000: clean = clean[:4000] + "...\n📄 Полный текст в документе."
            await update.message.reply_text(clean)
            buffer = create_word_document(doc_title, doc_content)
            await update.message.reply_document(document=buffer, filename=f"{doc_title[:30].replace(' ', '_')}.docx", caption=f"📄 {doc_title}")
        elif doc_type == 'excel':
            clean = answer.replace('[EXCEL_DOC]', '').strip()
            if len(clean) > 4000: clean = clean[:4000] + "...\n📊 Данные в таблице."
            await update.message.reply_text(clean)
            buffer = create_excel_document(doc_title, doc_content)
            await update.message.reply_document(document=buffer, filename=f"{doc_title[:30].replace(' ', '_')}.xlsx", caption=f"📊 {doc_title}")

        # Уведомление об остатке
        status = f"\n📊 Осталось: запросов — {q_left}, генераций — {g_left}"
        await update.message.reply_text(status)
    else:
        # Обычный текстовый ответ
        if len(answer) > 4000:
            for i in range(0, len(answer), 4000):
                await update.message.reply_text(answer[i:i+4000])
        else:
            await update.message.reply_text(answer)


# ─── Обработчики Telegram ────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    q_left, g_left = get_limit_status(user_id)

    keyboard = [
        [InlineKeyboardButton("📝 План урока", callback_data="plan"),
         InlineKeyboardButton("📋 Тест/Контрольная", callback_data="test")],
        [InlineKeyboardButton("📊 Таблица (Excel)", callback_data="table"),
         InlineKeyboardButton("📖 Конспект (Word)", callback_data="summary")],
        [InlineKeyboardButton("📽 Презентация (PPT)", callback_data="pptx"),
         InlineKeyboardButton("💡 Идеи для урока", callback_data="ideas")],
        [InlineKeyboardButton("🎯 Подготовка к ЕГЭ", callback_data="ege"),
         InlineKeyboardButton("📚 Найти материалы", callback_data="sources")],
    ]
    welcome = (
        f"🏫 **Добро пожаловать в «{BOT_NAME}»!**\n\n"
        "Я — ИИ-помощник для учителей **любого предмета**.\n\n"
        "**Что я умею:**\n"
        "📝 Планы уроков и конспекты\n"
        "📋 Тесты и контрольные работы\n"
        "📽 Презентации с диаграммами и картинками\n"
        "📊 Таблицы Excel  •  📄 Документы Word\n"
        "📸 Анализ фото учебников и заданий\n"
        "🎙 Голосовые запросы\n"
        "🎯 Подготовка к ОГЭ/ЕГЭ\n"
        "📄 Чтение документов (Word, PDF, Excel)\n\n"
        "📸 Отправьте фото с подписью\n"
        "🎙 Или запишите голосовое сообщение!\n\n"
        f"🆓 **Демо-доступ:** {q_left} запросов + {g_left} генерации документов\n\n"
        "Выберите действие или напишите свой вопрос:"
    )
    await update.message.reply_text(welcome, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    await update.message.reply_text("⬇️ Кнопки быстрого доступа:", reply_markup=PERSISTENT_KEYBOARD)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    q_left, g_left = get_limit_status(user_id)
    help_text = (
        f"🏫 **«{BOT_NAME}» — Справка**\n\n"
        "**Команды:**\n"
        "/start — Меню  •  /help — Справка\n"
        "/clear — Очистить историю\n"
        "/pptx — Презентация  •  /word — Word  •  /excel — Excel\n"
        "/status — Проверить остаток запросов\n\n"
        "**📸 Фото:** Отправьте фото с подписью\n"
        "**🎙 Голос:** Запишите голосовое сообщение\n"
        "**📄 Файлы:** Отправьте Word, PDF, Excel\n\n"
        "**Примеры:**\n"
        "• «Презентация: Второая мировая война, 9 класс, 10 слайдов»\n"
        "• «Тест: Квадратные уравнения, 8 класс, 15 вопросов»\n"
        "• «План урока: Фотосинтез, 6 класс»\n"
        "• Фото учебника + «Создай тест»\n\n"
        f"📊 **Остаток:** запросов — {q_left}, генераций — {g_left}"
    )
    await update.message.reply_text(help_text, parse_mode='Markdown')


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать остаток лимитов."""
    user_id = update.effective_user.id
    q_left, g_left = get_limit_status(user_id)
    limits = get_limits(user_id)

    if q_left == 0 and g_left == 0:
        await update.message.reply_text(LIMIT_MESSAGE, parse_mode='Markdown')
    else:
        status = (
            f"📊 **Ваш демо-доступ:**\n\n"
            f"💬 Запросов использовано: {limits['queries']} из {LIMIT_QUERIES}\n"
            f"📄 Генераций использовано: {limits['generations']} из {LIMIT_GENERATIONS}\n\n"
            f"Осталось: **{q_left}** запросов, **{g_left}** генераций"
        )
        await update.message.reply_text(status, parse_mode='Markdown')


async def clear_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_conversations[update.effective_user.id] = []
    await update.message.reply_text("🗑 История очищена!")


async def pptx_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not check_query_limit(user_id):
        await update.message.reply_text(LIMIT_MESSAGE, parse_mode='Markdown')
        return
    if not check_generation_limit(user_id):
        q_left, _ = get_limit_status(user_id)
        await update.message.reply_text(
            GENERATION_LIMIT_MESSAGE.format(used=LIMIT_GENERATIONS, total=LIMIT_GENERATIONS, q_left=q_left),
            parse_mode='Markdown'
        )
        return
    if not context.args:
        await update.message.reply_text("📽 Укажите тему: `/pptx Вулканы 5 класс`", parse_mode='Markdown')
        return
    topic = ' '.join(context.args)
    await update.message.reply_text("⏳ Создаю презентацию с диаграммами и картинками...")
    prompt = (
        f"Создай подробную и развёрнутую презентацию по теме: {topic}\n"
        "ОБЯЗАТЕЛЬНО начни ответ с маркера [PPTX_DOC].\n"
        "Каждый слайд оформляй строго в формате [SLIDE]...[/SLIDE].\n"
        "ВАЖНО: на каждом слайде должно быть 5-7 РАЗВЁРНУТЫХ пунктов содержимого!\n"
        "Каждый пункт — полное предложение с фактами, цифрами, примерами.\n"
        "НЕ ПИШИ коротких фраз из 2-3 слов! Минимум 10-15 слов на пункт.\n"
        "Добавь 2-3 диаграммы [CHART]...[/CHART] с реальными числовыми данными.\n"
        "Добавь 2-3 картинки [IMAGE]...[/IMAGE] с запросами на английском.\n"
        "Создай ровно 10 подробных слайдов."
    )
    use_query(user_id)
    answer = ask_claude(user_id, prompt)
    await send_limited_reply(update, context, user_id, answer)


async def word_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not check_query_limit(user_id):
        await update.message.reply_text(LIMIT_MESSAGE, parse_mode='Markdown')
        return
    if not check_generation_limit(user_id):
        q_left, _ = get_limit_status(user_id)
        await update.message.reply_text(
            GENERATION_LIMIT_MESSAGE.format(used=LIMIT_GENERATIONS, total=LIMIT_GENERATIONS, q_left=q_left),
            parse_mode='Markdown'
        )
        return
    if not context.args:
        await update.message.reply_text("📄 Укажите тему: `/word Климат России 8 класс`", parse_mode='Markdown')
        return
    topic = ' '.join(context.args)
    await update.message.reply_text("⏳ Создаю документ Word...")
    prompt = f"Создай учебный материал: {topic}\nМаркер [WORD_DOC] в начале. Заголовки и структура."
    use_query(user_id)
    answer = ask_claude(user_id, prompt)
    await send_limited_reply(update, context, user_id, answer)


async def excel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not check_query_limit(user_id):
        await update.message.reply_text(LIMIT_MESSAGE, parse_mode='Markdown')
        return
    if not check_generation_limit(user_id):
        q_left, _ = get_limit_status(user_id)
        await update.message.reply_text(
            GENERATION_LIMIT_MESSAGE.format(used=LIMIT_GENERATIONS, total=LIMIT_GENERATIONS, q_left=q_left),
            parse_mode='Markdown'
        )
        return
    if not context.args:
        await update.message.reply_text("📊 Укажите тему: `/excel Страны Европы`", parse_mode='Markdown')
        return
    topic = ' '.join(context.args)
    await update.message.reply_text("⏳ Создаю таблицу Excel...")
    prompt = f"Создай таблицу: {topic}\nМаркер [EXCEL_DOC]. Столбцы через |"
    use_query(user_id)
    answer = ask_claude(user_id, prompt)
    await send_limited_reply(update, context, user_id, answer)


BUTTON_PROMPTS = {
    "plan": "📝 **План урока**\n\nНапишите тему, предмет и класс:\n«План урока: Фотосинтез, биология, 6 класс»",
    "test": "📋 **Тест**\n\nНапишите тему, предмет, класс, кол-во вопросов:\n«Тест: Дроби, математика, 5 класс, 20 вопросов»",
    "table": "📊 **Таблица**\n\nНапишите тему:\n«Таблица: сравнение литературных героев»\nИли: /excel тема",
    "summary": "📖 **Конспект**\n\nНапишите тему:\n«Конспект: Причастный оборот, 7 класс»\nИли: /word тема",
    "pptx": "📽 **Презентация**\n\nНапишите тему:\n«Презентация: Великая Отечественная война, 9 класс»\nИли: /pptx тема",
    "ege": "🎯 **ОГЭ/ЕГЭ**\n\nУкажите предмет и тему:\n«ЕГЭ: разбор задания 13, русский язык»",
    "ideas": "💡 **Идеи для урока**\n\nУкажите предмет и тему:\n«Идеи для урока по теме Электричество, физика, 8 класс»",
    "sources": "📚 **Материалы**\n\nУкажите тему:\n«Найди материалы по теме Реформы Петра I»",
}

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data in BUTTON_PROMPTS:
        await query.message.reply_text(BUTTON_PROMPTS[query.data], parse_mode='Markdown')


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка документов: Word, PDF, Excel, TXT."""
    user_id = update.effective_user.id

    if not check_query_limit(user_id):
        await update.message.reply_text(LIMIT_MESSAGE, parse_mode='Markdown')
        return

    doc = update.message.document
    caption = update.message.caption or ""
    filename = doc.file_name or "file"
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ""

    supported = ['docx', 'doc', 'pdf', 'xlsx', 'xls', 'txt', 'csv']
    if ext not in supported:
        await update.message.reply_text(
            f"⚠️ Формат .{ext} не поддерживается.\n"
            "Поддерживаемые: Word (.docx), PDF, Excel (.xlsx), текст (.txt, .csv)"
        )
        return

    await update.message.chat.send_action('typing')
    await update.message.reply_text(f"📄 Читаю файл «{filename}»...")

    try:
        file = await doc.get_file()
        file_path = f"/tmp/doc_{user_id}.{ext}"
        await file.download_to_drive(file_path)

        extracted_text = ""

        if ext == 'docx':
            try:
                docx_doc = DocxDocument(file_path)
                paragraphs = [p.text for p in docx_doc.paragraphs if p.text.strip()]
                for table in docx_doc.tables:
                    for row in table.rows:
                        row_text = ' | '.join(cell.text.strip() for cell in row.cells)
                        if row_text.strip():
                            paragraphs.append(row_text)
                extracted_text = '\n'.join(paragraphs)
            except Exception as e:
                logger.error(f"Ошибка чтения docx: {e}")

        elif ext == 'pdf':
            try:
                with open(file_path, 'rb') as f:
                    reader = PyPDF2.PdfReader(f)
                    pages = []
                    for page in reader.pages:
                        text = page.extract_text()
                        if text:
                            pages.append(text)
                    extracted_text = '\n'.join(pages)
            except Exception as e:
                logger.error(f"Ошибка чтения PDF: {e}")

        elif ext in ['xlsx', 'xls']:
            try:
                wb = openpyxl.load_workbook(file_path, read_only=True)
                rows = []
                for sheet in wb.sheetnames:
                    ws = wb[sheet]
                    rows.append(f"--- Лист: {sheet} ---")
                    for row in ws.iter_rows(values_only=True):
                        row_text = ' | '.join(str(c) if c is not None else '' for c in row)
                        if row_text.strip():
                            rows.append(row_text)
                extracted_text = '\n'.join(rows)
                wb.close()
            except Exception as e:
                logger.error(f"Ошибка чтения xlsx: {e}")

        elif ext in ['txt', 'csv']:
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    extracted_text = f.read()
            except UnicodeDecodeError:
                with open(file_path, 'r', encoding='cp1251') as f:
                    extracted_text = f.read()

        try:
            os.remove(file_path)
        except:
            pass

        if not extracted_text or len(extracted_text.strip()) < 10:
            await update.message.reply_text(
                "⚠️ Не удалось извлечь текст из файла.\n"
                "Попробуйте сфотографировать содержимое и отправить как фото."
            )
            return

        if len(extracted_text) > 12000:
            extracted_text = extracted_text[:12000] + "\n\n[...текст обрезан...]"

        await update.message.reply_text(f"✅ Текст извлечён ({len(extracted_text)} символов)")

        if caption:
            prompt = f"Вот содержимое документа «{filename}»:\n\n{extracted_text}\n\n{caption}"
        else:
            context.user_data['last_document'] = extracted_text
            context.user_data['last_document_name'] = filename
            await update.message.reply_text(
                "📄 Файл прочитан! Что сделать с этим материалом?\n\n"
                "Напишите, например:\n"
                "• «Создай тест по этому материалу»\n"
                "• «Сделай презентацию на основе этого»\n"
                "• «Составь задания ОГЭ по каждому пункту»\n"
                "• «Создай план урока»"
            )
            return

        caption_lower = caption.lower()
        needs_pptx = any(kw in caption_lower for kw in ['презентац', 'powerpoint', 'pptx', 'слайд'])
        needs_word = any(kw in caption_lower for kw in ['документ', 'word', 'ворд', 'конспект', 'доклад'])
        needs_excel = any(kw in caption_lower for kw in ['таблиц', 'excel', 'эксель'])

        if needs_pptx:
            await update.message.reply_text("⏳ Создаю презентацию на основе документа...")
            prompt += ("\n\nОБЯЗАТЕЛЬНО начни ответ с маркера [PPTX_DOC].\n"
                "Каждый слайд в формате [SLIDE]...[/SLIDE].\n"
                "ВАЖНО: на каждом слайде 5-7 РАЗВЁРНУТЫХ пунктов!\n"
                "Создай ровно 10 подробных слайдов.")
        elif needs_word:
            await update.message.reply_text("⏳ Создаю документ на основе файла...")
            prompt += "\n\nМаркер [WORD_DOC]. Подробная структура."
        elif needs_excel:
            await update.message.reply_text("⏳ Создаю таблицу на основе файла...")
            prompt += "\n\nМаркер [EXCEL_DOC]. Столбцы через |"
        else:
            await update.message.reply_text("⏳ Обрабатываю документ...")

        use_query(user_id)
        answer = ask_claude(user_id, prompt)
        await send_limited_reply(update, context, user_id, answer)

    except Exception as e:
        logger.error(f"Ошибка обработки документа: {e}")
        await update.message.reply_text("⚠️ Ошибка при обработке файла. Попробуйте сфотографировать содержимое.")


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка голосовых сообщений."""
    user_id = update.effective_user.id

    if not check_query_limit(user_id):
        await update.message.reply_text(LIMIT_MESSAGE, parse_mode='Markdown')
        return

    await update.message.chat.send_action('typing')
    await update.message.reply_text("🎙 Распознаю голосовое сообщение...")

    try:
        voice = update.message.voice or update.message.audio
        file = await voice.get_file()
        
        ogg_path = f"/tmp/voice_{user_id}.ogg"
        wav_path = f"/tmp/voice_{user_id}.wav"
        
        await file.download_to_drive(ogg_path)
        
        subprocess.run(['ffmpeg', '-y', '-i', ogg_path, '-ar', '16000', '-ac', '1', wav_path],
                       capture_output=True, timeout=30)
        
        recognizer = sr.Recognizer()
        with sr.AudioFile(wav_path) as source:
            audio = recognizer.record(source)
        
        text = recognizer.recognize_google(audio, language="ru-RU")
        
        try:
            os.remove(ogg_path)
            os.remove(wav_path)
        except:
            pass
        
        if not text:
            await update.message.reply_text("⚠️ Не удалось распознать речь. Попробуйте ещё раз.")
            return
        
        await update.message.reply_text(f"✅ Распознано: «{text}»")
        
        text_lower = text.lower()
        needs_pptx = any(kw in text_lower for kw in ['презентац', 'powerpoint', 'pptx', 'слайд'])
        needs_word = any(kw in text_lower for kw in ['документ', 'word', 'ворд', 'конспект', 'доклад']) and not needs_pptx
        needs_excel = any(kw in text_lower for kw in ['таблиц', 'excel', 'эксель']) and not needs_pptx

        if needs_pptx:
            await update.message.reply_text("⏳ Создаю презентацию...")
            prompt = (
                text + "\n\nОБЯЗАТЕЛЬНО начни ответ с маркера [PPTX_DOC].\n"
                "Каждый слайд в формате [SLIDE]...[/SLIDE].\n"
                "ВАЖНО: на каждом слайде 5-7 РАЗВЁРНУТЫХ пунктов!\n"
                "Добавь 2-3 диаграммы [CHART]...[/CHART].\n"
                "Создай ровно 10 подробных слайдов."
            )
        elif needs_word:
            await update.message.reply_text("⏳ Создаю документ...")
            prompt = text + "\n\nМаркер [WORD_DOC]. Подробная структура."
        elif needs_excel:
            await update.message.reply_text("⏳ Создаю таблицу...")
            prompt = text + "\n\nМаркер [EXCEL_DOC]. Столбцы через |"
        else:
            prompt = text

        use_query(user_id)
        answer = ask_claude(user_id, prompt)
        await send_limited_reply(update, context, user_id, answer)
        
    except sr.UnknownValueError:
        await update.message.reply_text("⚠️ Не удалось распознать речь. Говорите чётче или попробуйте ещё раз.")
    except sr.RequestError as e:
        await update.message.reply_text(f"⚠️ Ошибка сервиса распознавания: {e}")
    except Exception as e:
        logger.error(f"Ошибка обработки голоса: {e}")
        await update.message.reply_text("⚠️ Ошибка обработки голосового сообщения. Попробуйте текстом.")


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка фотографий."""
    user_id = update.effective_user.id

    if not check_query_limit(user_id):
        await update.message.reply_text(LIMIT_MESSAGE, parse_mode='Markdown')
        return

    caption = update.message.caption or ""
    
    await update.message.chat.send_action('typing')
    
    photo = update.message.photo[-1]
    file = await photo.get_file()
    photo_bytes = await file.download_as_bytearray()
    
    b64_data = base64.b64encode(bytes(photo_bytes)).decode('utf-8')
    image_data = {'base64': b64_data, 'mime': 'image/jpeg'}
    
    if not caption:
        await update.message.reply_text(
            "📸 Фото получено! Что сделать с этим материалом?\n\n"
            "Напишите подпись к фото, например:\n"
            "• «Создай план урока по этой теме»\n"
            "• «Сделай презентацию по этому материалу»\n"
            "• «Составь тест по этой теме»\n"
            "• «Объясни что изображено»\n\n"
            "Или отправьте фото ещё раз с подписью."
        )
        context.user_data['last_photo'] = image_data
        return
    
    caption_lower = caption.lower()
    needs_pptx = any(kw in caption_lower for kw in ['презентац', 'powerpoint', 'pptx', 'слайд'])
    needs_word = any(kw in caption_lower for kw in ['документ', 'word', 'ворд', 'конспект', 'доклад'])
    needs_excel = any(kw in caption_lower for kw in ['таблиц', 'excel', 'эксель'])
    
    if needs_pptx:
        await update.message.reply_text("⏳ Анализирую фото и создаю презентацию...")
        prompt = (
            f"Посмотри на это изображение. {caption}\n\n"
            "ОБЯЗАТЕЛЬНО начни ответ с маркера [PPTX_DOC].\n"
            "Каждый слайд в формате [SLIDE]...[/SLIDE].\n"
            "ВАЖНО: на каждом слайде 5-7 РАЗВЁРНУТЫХ пунктов!\n"
            "Добавь 2-3 диаграммы [CHART]...[/CHART].\n"
            "Создай ровно 10 подробных слайдов."
        )
    elif needs_word:
        await update.message.reply_text("⏳ Анализирую фото и создаю документ Word...")
        prompt = f"Посмотри на это изображение. {caption}\n\nМаркер [WORD_DOC]. Подробная структура."
    elif needs_excel:
        await update.message.reply_text("⏳ Анализирую фото и создаю таблицу...")
        prompt = f"Посмотри на это изображение. {caption}\n\nМаркер [EXCEL_DOC]. Столбцы через |"
    else:
        await update.message.reply_text("⏳ Анализирую изображение...")
        prompt = f"Посмотри на это изображение. {caption}\n\nПодробно проанализируй и ответь на запрос учителя."
    
    use_query(user_id)
    answer = ask_claude(user_id, prompt, image_data=image_data)
    await send_limited_reply(update, context, user_id, answer)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text

    # Кнопки постоянной клавиатуры (НЕ тратят лимит)
    if text == "📋 Меню":
        keyboard = [
            [InlineKeyboardButton("📝 План урока", callback_data="plan"),
             InlineKeyboardButton("📋 Тест/Контрольная", callback_data="test")],
            [InlineKeyboardButton("📊 Таблица (Excel)", callback_data="table"),
             InlineKeyboardButton("📖 Конспект (Word)", callback_data="summary")],
            [InlineKeyboardButton("📽 Презентация (PPT)", callback_data="pptx"),
             InlineKeyboardButton("💡 Идеи для урока", callback_data="ideas")],
            [InlineKeyboardButton("🎯 ОГЭ/ЕГЭ", callback_data="ege"),
             InlineKeyboardButton("📚 Материалы", callback_data="sources")],
        ]
        await update.message.reply_text("🏫 **Выберите действие:**", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        return
    if text == "📽 Презентация":
        await update.message.reply_text("📽 Напишите тему и предмет:\n«Презентация: Клетка, биология, 5 класс»\nИли: /pptx тема")
        return
    if text == "📄 Word":
        await update.message.reply_text("📄 Напишите тему:\n«Создай документ: План урока по математике, 8 класс»\nИли: /word тема")
        return
    if text == "📊 Excel":
        await update.message.reply_text("📊 Напишите тему:\n«Таблица: сравнение планет Солнечной системы»\nИли: /excel тема")
        return
    if text == "🗑 Очистить":
        user_conversations[user_id] = []
        await update.message.reply_text("🗑 История очищена!")
        return
    if text == "❓ Помощь":
        await help_command(update, context)
        return

    # Проверяем лимит запросов
    if not check_query_limit(user_id):
        await update.message.reply_text(LIMIT_MESSAGE, parse_mode='Markdown')
        return

    await update.message.chat.send_action('typing')

    # Проверка: ранее отправлен документ без подписи
    saved_doc = context.user_data.get('last_document')
    if saved_doc:
        doc_name = context.user_data.get('last_document_name', 'документ')
        context.user_data['last_document'] = None
        context.user_data['last_document_name'] = None

        caption_lower = text.lower()
        needs_pptx = any(kw in caption_lower for kw in ['презентац', 'powerpoint', 'pptx', 'слайд'])
        needs_word = any(kw in caption_lower for kw in ['документ', 'word', 'ворд', 'конспект', 'доклад', 'план'])

        prompt = f"Вот содержимое документа «{doc_name}»:\n\n{saved_doc}\n\n{text}"

        if needs_pptx:
            await update.message.reply_text("⏳ Создаю презентацию на основе документа...")
            prompt += ("\n\nОБЯЗАТЕЛЬНО начни ответ с маркера [PPTX_DOC].\n"
                "Каждый слайд в формате [SLIDE]...[/SLIDE].\n"
                "ВАЖНО: на каждом слайде 5-7 РАЗВЁРНУТЫХ пунктов!\n"
                "Создай ровно 10 подробных слайдов.")
        elif needs_word:
            await update.message.reply_text("⏳ Создаю документ...")
            prompt += "\n\nМаркер [WORD_DOC]. Подробная структура."
        else:
            await update.message.reply_text("⏳ Обрабатываю...")

        use_query(user_id)
        answer = ask_claude(user_id, prompt)
        await send_limited_reply(update, context, user_id, answer)
        return

    # Проверка: ранее отправлено фото без подписи
    saved_photo = context.user_data.get('last_photo')
    if saved_photo:
        context.user_data['last_photo'] = None
        caption_lower = text.lower()
        needs_pptx = any(kw in caption_lower for kw in ['презентац', 'powerpoint', 'pptx', 'слайд'])
        needs_word = any(kw in caption_lower for kw in ['документ', 'word', 'ворд', 'конспект', 'доклад', 'план урока', 'план'])

        if needs_pptx:
            await update.message.reply_text("⏳ Анализирую фото и создаю презентацию...")
            prompt = (
                f"Посмотри на это изображение. {text}\n\n"
                "ОБЯЗАТЕЛЬНО начни ответ с маркера [PPTX_DOC].\n"
                "Каждый слайд в формате [SLIDE]...[/SLIDE].\n"
                "ВАЖНО: на каждом слайде 5-7 РАЗВЁРНУТЫХ пунктов!\n"
                "Добавь 2-3 диаграммы [CHART]...[/CHART].\n"
                "Создай ровно 10 подробных слайдов."
            )
        elif needs_word:
            await update.message.reply_text("⏳ Анализирую фото и создаю документ...")
            prompt = f"Посмотри на это изображение. {text}\n\nМаркер [WORD_DOC]. Подробная структура."
        else:
            await update.message.reply_text("⏳ Анализирую изображение...")
            prompt = f"Посмотри на это изображение. {text}"

        use_query(user_id)
        answer = ask_claude(user_id, prompt, image_data=saved_photo)
        await send_limited_reply(update, context, user_id, answer)
        return

    # Обычный текстовый запрос
    text_lower = text.lower()

    needs_pptx = any(kw in text_lower for kw in ['презентац', 'powerpoint', 'pptx', 'слайд', 'ppt'])
    needs_word = any(kw in text_lower for kw in ['создай документ', 'word', 'в ворде', 'конспект']) and not needs_pptx
    needs_excel = any(kw in text_lower for kw in ['создай таблицу', 'сделай таблицу', 'excel', 'эксель', 'таблицу']) and not needs_pptx

    if needs_pptx:
        await update.message.reply_text("⏳ Создаю презентацию с диаграммами и картинками...")
        prompt = (
            text + "\n\nОБЯЗАТЕЛЬНО начни ответ с маркера [PPTX_DOC].\n"
            "Каждый слайд в формате [SLIDE]...[/SLIDE].\n"
            "ВАЖНО: на каждом слайде 5-7 РАЗВЁРНУТЫХ пунктов!\n"
            "Каждый пункт — полное предложение, минимум 10-15 слов.\n"
            "Добавь 2-3 диаграммы [CHART]...[/CHART].\n"
            "Добавь 2-3 картинки [IMAGE]...[/IMAGE] с запросами на английском.\n"
            "Создай ровно 10 подробных слайдов."
        )
    elif needs_word:
        prompt = text + "\n\nМаркер [WORD_DOC]. Заголовки и структура."
    elif needs_excel:
        prompt = text + "\n\nМаркер [EXCEL_DOC]. Столбцы через |"
    else:
        prompt = text

    use_query(user_id)
    answer = ask_claude(user_id, prompt)
    await send_limited_reply(update, context, user_id, answer)


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Ошибка: {context.error}")
    if update and update.message:
        await update.message.reply_text("⚠️ Ошибка. Попробуйте /clear и повторите.")


def main():
    if not TELEGRAM_TOKEN or not ANTHROPIC_API_KEY:
        print("❌ Не указаны токены в .env!")
        return
    print(f"🏫 «{BOT_NAME}» v{BOT_VERSION} (демо) запускается...")
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("clear", clear_history))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("word", word_command))
    app.add_handler(CommandHandler("excel", excel_command))
    app.add_handler(CommandHandler("pptx", pptx_command))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_error_handler(error_handler)
    print(f"✅ «{BOT_NAME}» v{BOT_VERSION} запущен! Лимиты: {LIMIT_QUERIES} запросов, {LIMIT_GENERATIONS} генераций")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
