"""
🏫 ПроУрок v1.2 — Telegram-бот помощник для учителей
- Лимиты хранятся в JSON (переживают перезагрузку)
- Кнопка «Баланс»
- Поддержка тарифов (демо/старт/про/премиум)
- Ссылка на бот поддержки
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
from pathlib import Path
import PyPDF2
from docx import Document as DocxDocument

import requests
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)
from anthropic import Anthropic

from docx import Document
from docx.shared import Pt, Inches, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

from pptx import Presentation
from pptx.util import Inches as PptxInches, Pt as PptxPt
from pptx.dml.color import RGBColor as PptxRGB
from pptx.enum.text import PP_ALIGN
from pptx.enum.shapes import MSO_SHAPE

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
client = Anthropic(api_key=ANTHROPIC_API_KEY)

BOT_NAME = "ПроУрок"
BOT_VERSION = "1.2"
SUPPORT_BOT_URL = "https://t.me/pro_lessen_help_bot"

# ─── Тарифы ──────────────────────────────────────────────────
TARIFFS = {
    "demo": {"name": "🆓 Демо", "queries": 20, "generations": 5, "price": "Бесплатно"},
    "start": {"name": "🟢 Старт", "queries": 200, "generations": 30, "price": "490₽/мес"},
    "pro": {"name": "🔵 Про", "queries": 500, "generations": 100, "price": "890₽/мес"},
    "premium": {"name": "🟡 Премиум", "queries": 999999, "generations": 999999, "price": "1 490₽/мес"},
}

# ─── JSON-хранилище лимитов ──────────────────────────────────
LIMITS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "user_limits.json")

def load_all_limits():
    if os.path.exists(LIMITS_FILE):
        try:
            with open(LIMITS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_all_limits(data):
    try:
        with open(LIMITS_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Ошибка сохранения лимитов: {e}")

def get_user_data(user_id):
    all_data = load_all_limits()
    uid = str(user_id)
    if uid not in all_data:
        all_data[uid] = {
            "tariff": "demo",
            "queries_used": 0,
            "generations_used": 0,
            "registered": datetime.now().strftime("%d.%m.%Y"),
            "name": "",
            "username": ""
        }
        save_all_limits(all_data)
    return all_data[uid]

def update_user_data(user_id, data):
    all_data = load_all_limits()
    all_data[str(user_id)] = data
    save_all_limits(all_data)

def save_user_info(user_id, first_name, username):
    data = get_user_data(user_id)
    data["name"] = first_name or ""
    data["username"] = username or ""
    update_user_data(user_id, data)

def get_tariff(user_id):
    data = get_user_data(user_id)
    return TARIFFS.get(data["tariff"], TARIFFS["demo"])

def check_query_limit(user_id):
    data = get_user_data(user_id)
    tariff = TARIFFS.get(data["tariff"], TARIFFS["demo"])
    return data["queries_used"] < tariff["queries"]

def check_generation_limit(user_id):
    data = get_user_data(user_id)
    tariff = TARIFFS.get(data["tariff"], TARIFFS["demo"])
    return data["generations_used"] < tariff["generations"]

def use_query(user_id):
    data = get_user_data(user_id)
    data["queries_used"] += 1
    update_user_data(user_id, data)

def use_generation(user_id):
    data = get_user_data(user_id)
    data["generations_used"] += 1
    update_user_data(user_id, data)

def get_limit_status(user_id):
    data = get_user_data(user_id)
    tariff = TARIFFS.get(data["tariff"], TARIFFS["demo"])
    q_left = max(0, tariff["queries"] - data["queries_used"])
    g_left = max(0, tariff["generations"] - data["generations_used"])
    return q_left, g_left

def get_balance_text(user_id):
    data = get_user_data(user_id)
    tariff = TARIFFS.get(data["tariff"], TARIFFS["demo"])
    q_left, g_left = get_limit_status(user_id)
    is_premium = data["tariff"] == "premium"

    text = f"💰 **Ваш баланс:**\n\n"
    text += f"📋 Тариф: {tariff['name']} ({tariff['price']})\n\n"
    if is_premium:
        text += f"💬 Запросов: ♾ безлимит\n"
        text += f"📄 Оформлений: ♾ безлимит\n"
    else:
        text += f"💬 Запросов: {data['queries_used']} / {tariff['queries']} (осталось {q_left})\n"
        text += f"📄 Оформлений: {data['generations_used']} / {tariff['generations']} (осталось {g_left})\n"

    if data["tariff"] == "demo" and q_left < 5:
        text += f"\n⚠️ Запросы заканчиваются!\n"
        text += f"Подключите тариф: {SUPPORT_BOT_URL}"
    elif data["tariff"] != "premium" and q_left < 20:
        text += f"\n💡 Запросы заканчиваются. Повысьте тариф: {SUPPORT_BOT_URL}"

    return text

LIMIT_MESSAGE = (
    "🔒 **Лимит исчерпан!**\n\n"
    f"✨ Подключите тариф или повысьте текущий:\n👉 {SUPPORT_BOT_URL}\n\n"
    "🟢 Старт — 490₽/мес (200 запросов)\n"
    "🔵 Про — 890₽/мес (500 запросов)\n"
    "🟡 Премиум — 1 490₽/мес (безлимит)"
)

GENERATION_LIMIT_MESSAGE = (
    "🔒 **Лимит оформления документов исчерпан!**\n\n"
    "Текстовые запросы ещё доступны.\n\n"
    f"✨ Повысьте тариф:\n👉 {SUPPORT_BOT_URL}"
)

# ─── История диалогов ─────────────────────────────────────────
user_conversations = {}
MAX_HISTORY = 20

SYSTEM_PROMPT = f"""Ты — профессиональный помощник учителя. Твоё имя — {BOT_NAME}.
Ты помогаешь учителям ЛЮБЫХ предметов: математика, русский язык, литература, история, обществознание, физика, химия, биология, география, английский язык, информатика, ИЗО, музыка, физкультура, технология, ОБЖ и другие.

ВОЗМОЖНОСТИ: планы уроков (1-11 класс), тесты, конспекты, рабочие листы, ФГОС, ОГЭ/ЕГЭ, анализ фото, идеи для уроков.

ПРАВИЛА:
- Русский язык, развёрнуто, с примерами
- Адаптируй под класс и предмет
- НЕ используй маркеры [PPTX_DOC], [WORD_DOC], [EXCEL_DOC]
- Просто давай качественный текстовый ответ

Источники: resh.edu.ru, yaklass.ru, infourok.ru, fipi.ru
"""

PPTX_PROMPT = """
Переработай материал в презентацию. Начни с [PPTX_DOC].
Формат слайдов: [SLIDE]ЗАГОЛОВОК:..ПОДЗАГОЛОВОК:..СОДЕРЖИМОЕ:-пункт..ЗАМЕТКИ:..[/SLIDE]
Диаграммы: [CHART]ТИП:bar/pie/line НАЗВАНИЕ:.. МЕТКИ:.. ЗНАЧЕНИЯ:.. ЕДИНИЦЫ:..[/CHART]
Картинки: [IMAGE]ЗАПРОС:english query ОПИСАНИЕ:..[/IMAGE]
10 слайдов, 5-7 развёрнутых пунктов на каждом, минимум 10-15 слов.
"""

WORD_PROMPT = "Переработай материал в документ. Начни с [WORD_DOC]. Заголовки (#,##,###), списки, структура."
EXCEL_PROMPT = "Переработай материал в таблицу. Начни с [EXCEL_DOC]. Столбцы через |."


# ─── Базовые функции ─────────────────────────────────────────

def get_history(uid):
    if uid not in user_conversations: user_conversations[uid] = []
    return user_conversations[uid]

def add_to_history(uid, role, content):
    h = get_history(uid); h.append({"role": role, "content": content})
    if len(h) > MAX_HISTORY: user_conversations[uid] = h[-MAX_HISTORY:]

def ask_claude(uid, msg, image_data=None):
    if image_data:
        content = [{"type": "image", "source": {"type": "base64", "media_type": image_data['mime'], "data": image_data['base64']}}, {"type": "text", "text": msg}]
        add_to_history(uid, "user", msg); messages = [{"role": "user", "content": content}]
    else:
        add_to_history(uid, "user", msg); messages = get_history(uid)
    try:
        r = client.messages.create(model="claude-3-haiku-20240307", max_tokens=4096, system=SYSTEM_PROMPT, messages=messages)
        answer = r.content[0].text; add_to_history(uid, "assistant", answer); return answer
    except Exception as e:
        logger.error(f"Claude: {e}"); return f"⚠️ Ошибка ИИ: {e}"

def clean_for_chat(text):
    c = text
    for m in ['[PPTX_DOC]', '[WORD_DOC]', '[EXCEL_DOC]']: c = c.replace(m, '')
    c = re.sub(r'\[/?SLIDE\]', '', c); c = re.sub(r'\[CHART\].*?\[/CHART\]', '', c, flags=re.DOTALL)
    c = re.sub(r'\[IMAGE\].*?\[/IMAGE\]', '', c, flags=re.DOTALL); return re.sub(r'\n{3,}', '\n\n', c).strip()

async def send_text(update, text):
    if len(text) > 4000:
        for i in range(0, len(text), 4000): await update.message.reply_text(text[i:i+4000])
    else: await update.message.reply_text(text)


# ─── Картинки / Диаграммы / Темы / Парсинг / PPTX / Word / Excel ─
# (идентичны v1.1, вставлены компактно)

def download_image(query):
    try:
        url = "https://commons.wikimedia.org/w/api.php"
        r = requests.get(url, params={"action":"query","list":"search","srsearch":query,"srnamespace":"6","srlimit":"3","format":"json"}, timeout=10)
        d = r.json()
        if "query" in d and d["query"]["search"]:
            t = d["query"]["search"][0]["title"]
            r2 = requests.get(url, params={"action":"query","titles":t,"prop":"imageinfo","iiprop":"url|mime","format":"json"}, timeout=10)
            d2 = r2.json()
            if "query" in d2 and "pages" in d2["query"]:
                for pid, pd in d2["query"]["pages"].items():
                    if "imageinfo" in pd and "image" in pd["imageinfo"][0].get("mime",""):
                        ir = requests.get(pd["imageinfo"][0]["url"], timeout=15)
                        if ir.status_code == 200: return io.BytesIO(ir.content)
    except: pass
    try:
        r = requests.get(f"https://source.unsplash.com/800x500/?{query}", timeout=15, allow_redirects=True)
        if r.status_code == 200 and len(r.content) > 1000: return io.BytesIO(r.content)
    except: pass
    return None

def create_chart_image(ct, title, labels, values, units="", tc=None):
    if not tc: tc = ['#4A90D9','#50C878','#FF6B6B','#FFD93D','#6C5CE7','#A8E6CF','#FF8A80','#82B1FF']
    plt.rcParams['font.size']=14; fig,ax=plt.subplots(figsize=(8,5))
    if ct=='bar':
        bars=ax.bar(labels,values,color=tc[:len(labels)],edgecolor='white')
        for b,v in zip(bars,values): ax.text(b.get_x()+b.get_width()/2.,b.get_height()+max(values)*0.02,f'{v:g}',ha='center',va='bottom',fontsize=12,fontweight='bold')
        ax.set_ylabel(units); ax.spines['top'].set_visible(False); ax.spines['right'].set_visible(False)
        if any(len(str(l))>8 for l in labels): plt.xticks(rotation=30,ha='right')
    elif ct=='pie': ax.pie(values,labels=labels,autopct='%1.1f%%',colors=tc[:len(labels)],startangle=90,wedgeprops={'edgecolor':'white','linewidth':2})
    elif ct=='line':
        ax.plot(labels,values,color=tc[0],linewidth=3,marker='o',markersize=8,markerfacecolor=tc[1])
        ax.fill_between(range(len(labels)),values,alpha=0.1,color=tc[0]); ax.set_ylabel(units); ax.grid(axis='y',alpha=0.3)
        if any(len(str(l))>8 for l in labels): plt.xticks(rotation=30,ha='right')
    ax.set_title(title,fontsize=16,fontweight='bold',pad=15,color='#1E293B'); plt.tight_layout()
    buf=io.BytesIO(); fig.savefig(buf,format='png',dpi=150,bbox_inches='tight',facecolor='white'); plt.close(fig); buf.seek(0); return buf

THEMES = {
    "blue":{"bg_dark":PptxRGB(26,54,93),"bg_light":PptxRGB(240,244,250),"accent":PptxRGB(74,144,217),"text_light":PptxRGB(255,255,255),"text_dark":PptxRGB(30,41,59),"subtitle":PptxRGB(180,200,230),"chart_colors":['#4A90D9','#50C878','#FF6B6B','#FFD93D','#6C5CE7']},
    "green":{"bg_dark":PptxRGB(34,87,60),"bg_light":PptxRGB(242,248,244),"accent":PptxRGB(80,200,120),"text_light":PptxRGB(255,255,255),"text_dark":PptxRGB(30,45,35),"subtitle":PptxRGB(180,220,195),"chart_colors":['#22573C','#50C878','#A8E6CF','#4A90D9','#FFD93D']},
    "warm":{"bg_dark":PptxRGB(93,52,40),"bg_light":PptxRGB(250,245,240),"accent":PptxRGB(217,144,74),"text_light":PptxRGB(255,255,255),"text_dark":PptxRGB(60,40,35),"subtitle":PptxRGB(230,200,180),"chart_colors":['#D9904A','#C86450','#FFD93D','#A8E6CF','#6C5CE7']},
}
def choose_theme(t):
    tl=t.lower()
    if any(w in tl for w in ['матем','физик','информ','алгебр']): return THEMES["blue"]
    elif any(w in tl for w in ['биолог','природ','географ']): return THEMES["green"]
    elif any(w in tl for w in ['истор','литератур','музык']): return THEMES["warm"]
    return THEMES["blue"]

def parse_chart(text):
    charts=[]
    for m in re.findall(r'\[CHART\](.*?)\[/CHART\]',text,re.DOTALL):
        c={'type':'bar','title':'','labels':[],'values':[],'units':''}
        for l in m.strip().split('\n'):
            l=l.strip()
            if l.startswith('ТИП:'): c['type']=l[4:].strip().lower()
            elif l.startswith('НАЗВАНИЕ:'): c['title']=l[9:].strip()
            elif l.startswith('МЕТКИ:'): c['labels']=[x.strip() for x in l[6:].split(',')]
            elif l.startswith('ЗНАЧЕНИЯ:'):
                try: c['values']=[float(x.strip().replace(' ','')) for x in l[9:].split(',')]
                except: pass
            elif l.startswith('ЕДИНИЦЫ:'): c['units']=l[8:].strip()
        if c['labels'] and c['values']: charts.append(c)
    return charts

def parse_image(text):
    imgs=[]
    for m in re.findall(r'\[IMAGE\](.*?)\[/IMAGE\]',text,re.DOTALL):
        img={'query':'','description':''}
        for l in m.strip().split('\n'):
            l=l.strip()
            if l.startswith('ЗАПРОС:'): img['query']=l[7:].strip()
            elif l.startswith('ОПИСАНИЕ:'): img['description']=l[9:].strip()
        if img['query']: imgs.append(img)
    return imgs

def parse_slides(content):
    slides=[]
    for part in content.split('[SLIDE]'):
        if '[/SLIDE]' not in part: continue
        st=part.split('[/SLIDE]')[0].strip()
        sl={'title':'','subtitle':'','content':[],'notes':'','charts':parse_chart(st),'images':parse_image(st)}
        clean=re.sub(r'\[CHART\].*?\[/CHART\]','',st,flags=re.DOTALL)
        clean=re.sub(r'\[IMAGE\].*?\[/IMAGE\]','',clean,flags=re.DOTALL)
        sec=None
        for l in clean.split('\n'):
            s=l.strip()
            if s.startswith('ЗАГОЛОВОК:'): sl['title']=s[10:].strip()
            elif s.startswith('ПОДЗАГОЛОВОК:'): sl['subtitle']=s[13:].strip()
            elif s.startswith('СОДЕРЖИМОЕ:'): sec='content'
            elif s.startswith('ЗАМЕТКИ:'): sl['notes']=s[8:].strip(); sec='notes'
            elif sec=='content' and s: sl['content'].append(s[2:] if s[:2] in ['- ','• '] else s)
            elif sec=='notes' and s: sl['notes']+=' '+s
        if sl['title']: slides.append(sl)
    return slides

def create_slides_from_text(title,content):
    slides=[{'title':title,'subtitle':'','content':[],'notes':'','charts':[],'images':[]}]
    clean=re.sub(r'\[CHART\].*?\[/CHART\]','',content,flags=re.DOTALL)
    clean=re.sub(r'\[IMAGE\].*?\[/IMAGE\]','',clean,flags=re.DOTALL)
    cur=None
    for l in clean.split('\n'):
        s=l.strip()
        if not s: continue
        if s.startswith('# ') or s.startswith('## '): cur={'title':s.lstrip('#').strip(),'subtitle':'','content':[],'notes':'','charts':[],'images':[]}; slides.append(cur)
        elif cur:
            if s[:2] in ['- ','• ']: cur['content'].append(s[2:])
            elif len(s)>5: cur['content'].append(s)
    return slides if len(slides)>1 else [{'title':title,'subtitle':'','content':[content[:200]],'notes':'','charts':[],'images':[]}]

def add_slide_number(sl,num,theme,sw,sh):
    bg=sl.background;fill=bg.fill;fill.solid();fill.fore_color.rgb=theme['bg_light']
    bar=sl.shapes.add_shape(MSO_SHAPE.RECTANGLE,0,0,PptxInches(0.3),sh);bar.fill.solid();bar.fill.fore_color.rgb=theme['bg_dark'];bar.line.fill.background()
    acc=sl.shapes.add_shape(MSO_SHAPE.RECTANGLE,PptxInches(0.3),0,PptxInches(0.05),sh);acc.fill.solid();acc.fill.fore_color.rgb=theme['accent'];acc.line.fill.background()
    ns=sl.shapes.add_shape(MSO_SHAPE.OVAL,PptxInches(0.65),PptxInches(0.35),PptxInches(0.55),PptxInches(0.55));ns.fill.solid();ns.fill.fore_color.rgb=theme['bg_dark'];ns.line.fill.background()
    ntf=ns.text_frame;ntf.paragraphs[0].text=str(num);ntf.paragraphs[0].font.size=PptxPt(18);ntf.paragraphs[0].font.bold=True;ntf.paragraphs[0].font.color.rgb=theme['text_light'];ntf.paragraphs[0].alignment=PP_ALIGN.CENTER

def create_pptx_presentation(title,content):
    sd=parse_slides(content)
    if not sd: sd=create_slides_from_text(title,content)
    theme=choose_theme(title);prs=Presentation();prs.slide_width=PptxInches(13.333);prs.slide_height=PptxInches(7.5);sw=prs.slide_width;sh=prs.slide_height
    sl=prs.slides.add_slide(prs.slide_layouts[6]);bg=sl.background;fill=bg.fill;fill.solid();fill.fore_color.rgb=theme['bg_dark']
    s=sl.shapes.add_shape(MSO_SHAPE.RECTANGLE,0,0,sw,PptxInches(0.12));s.fill.solid();s.fill.fore_color.rgb=theme['accent'];s.line.fill.background()
    d=sl.shapes.add_shape(MSO_SHAPE.RECTANGLE,PptxInches(0.8),PptxInches(1.5),PptxInches(0.12),PptxInches(3));d.fill.solid();d.fill.fore_color.rgb=theme['accent'];d.line.fill.background()
    ft=sd[0]['title'] if sd else title
    tb=sl.shapes.add_textbox(PptxInches(1.3),PptxInches(1.8),PptxInches(10),PptxInches(2));tf=tb.text_frame;tf.word_wrap=True
    p=tf.paragraphs[0];p.text=ft;p.font.size=PptxPt(44);p.font.bold=True;p.font.color.rgb=theme['text_light'];p.font.name="Georgia"
    if sd and sd[0].get('subtitle'):
        p2=tf.add_paragraph();p2.text=sd[0]['subtitle'];p2.font.size=PptxPt(22);p2.font.color.rgb=theme['subtitle'];p2.font.name="Calibri"
    tb2=sl.shapes.add_textbox(PptxInches(1.3),PptxInches(5.5),PptxInches(8),PptxInches(0.8))
    p3=tb2.text_frame.paragraphs[0];p3.text=f"Подготовлено в «{BOT_NAME}» • {datetime.now().strftime('%d.%m.%Y')}";p3.font.size=PptxPt(14);p3.font.color.rgb=theme['subtitle']
    s2=sl.shapes.add_shape(MSO_SHAPE.RECTANGLE,0,sh-PptxInches(0.12),sw,PptxInches(0.12));s2.fill.solid();s2.fill.fore_color.rgb=theme['accent'];s2.line.fill.background()
    cs=sd[1:] if len(sd)>1 else sd
    for i,sdata in enumerate(cs):
        sl=prs.slides.add_slide(prs.slide_layouts[6]);add_slide_number(sl,i+1,theme,sw,sh)
        hv=bool(sdata.get('charts') or sdata.get('images'));cw=PptxInches(6) if hv else PptxInches(11)
        tb=sl.shapes.add_textbox(PptxInches(1.5),PptxInches(0.3),PptxInches(11),PptxInches(0.8));ttf=tb.text_frame;ttf.word_wrap=True
        tp=ttf.paragraphs[0];tp.text=sdata['title'];tp.font.size=PptxPt(30);tp.font.bold=True;tp.font.color.rgb=theme['text_dark'];tp.font.name="Georgia"
        ln=sl.shapes.add_shape(MSO_SHAPE.RECTANGLE,PptxInches(1.5),PptxInches(1.15),PptxInches(2.5),PptxInches(0.04));ln.fill.solid();ln.fill.fore_color.rgb=theme['accent'];ln.line.fill.background()
        if sdata['content']:
            cb=sl.shapes.add_textbox(PptxInches(1.5),PptxInches(1.5),cw,PptxInches(5));ctf=cb.text_frame;ctf.word_wrap=True
            for j,item in enumerate(sdata['content']):
                pp=ctf.paragraphs[0] if j==0 else ctf.add_paragraph();pp.text=f"  •  {item}";pp.font.size=PptxPt(16);pp.font.color.rgb=theme['text_dark'];pp.font.name="Calibri";pp.space_after=PptxPt(10)
        if sdata.get('charts'):
            cd=sdata['charts'][0];ci=create_chart_image(cd['type'],cd['title'],cd['labels'],cd['values'],cd.get('units',''),theme.get('chart_colors'))
            if ci: sl.shapes.add_picture(ci,PptxInches(7.5),PptxInches(1.3),PptxInches(5.3),PptxInches(3.5))
        elif sdata.get('images'):
            img=download_image(sdata['images'][0]['query'])
            if img:
                try: sl.shapes.add_picture(img,PptxInches(7.8),PptxInches(1.3),PptxInches(4.8),PptxInches(3.5))
                except: pass
        if sdata.get('notes'): sl.notes_slide.notes_text_frame.text=sdata['notes']
        ft2=sl.shapes.add_textbox(PptxInches(0.7),PptxInches(6.95),PptxInches(12),PptxInches(0.35))
        fp=ft2.text_frame.paragraphs[0];fp.text=f"{BOT_NAME} • {sdata['title'][:50]}";fp.font.size=PptxPt(9);fp.font.color.rgb=PptxRGB(170,170,170)
    sl=prs.slides.add_slide(prs.slide_layouts[6]);bg=sl.background;fill=bg.fill;fill.solid();fill.fore_color.rgb=theme['bg_dark']
    s=sl.shapes.add_shape(MSO_SHAPE.RECTANGLE,0,0,sw,PptxInches(0.12));s.fill.solid();s.fill.fore_color.rgb=theme['accent'];s.line.fill.background()
    tb=sl.shapes.add_textbox(PptxInches(1),PptxInches(2.2),PptxInches(11.333),PptxInches(2.5));tf=tb.text_frame;tf.word_wrap=True
    p=tf.paragraphs[0];p.text="Спасибо за внимание!";p.font.size=PptxPt(44);p.font.bold=True;p.font.color.rgb=theme['text_light'];p.font.name="Georgia";p.alignment=PP_ALIGN.CENTER
    p2=tf.add_paragraph();p2.text=f"Создано в «{BOT_NAME}»";p2.font.size=PptxPt(18);p2.font.color.rgb=theme['subtitle'];p2.alignment=PP_ALIGN.CENTER
    s2=sl.shapes.add_shape(MSO_SHAPE.RECTANGLE,0,sh-PptxInches(0.12),sw,PptxInches(0.12));s2.fill.solid();s2.fill.fore_color.rgb=theme['accent'];s2.line.fill.background()
    buf=io.BytesIO();prs.save(buf);buf.seek(0);return buf

def create_word_document(title,content):
    doc=Document();doc.styles['Normal'].font.name='Times New Roman';doc.styles['Normal'].font.size=Pt(12)
    h=doc.add_heading(title,level=0);h.alignment=WD_ALIGN_PARAGRAPH.CENTER
    for r in h.runs: r.font.color.rgb=RGBColor(26,54,93)
    dp=doc.add_paragraph();dp.alignment=WD_ALIGN_PARAGRAPH.RIGHT;dr=dp.add_run(f"Дата: {datetime.now().strftime('%d.%m.%Y')}");dr.font.size=Pt(10);dr.font.color.rgb=RGBColor(128,128,128)
    doc.add_paragraph()
    for line in content.split('\n'):
        s=line.strip()
        if not s: doc.add_paragraph()
        elif s.startswith('## '): doc.add_heading(s[3:],level=2)
        elif s.startswith('### '): doc.add_heading(s[4:],level=3)
        elif s.startswith('# '): doc.add_heading(s[2:],level=1)
        elif s[:2] in ['- ','• ']: doc.add_paragraph(s[2:],style='List Bullet')
        else:
            p=doc.add_paragraph();parts=s.split('**')
            for idx,part in enumerate(parts):
                if part: run=p.add_run(part);run.bold=(idx%2==1)
    buf=io.BytesIO();doc.save(buf);buf.seek(0);return buf

def create_excel_document(title,content):
    wb=openpyxl.Workbook();ws=wb.active;ws.title=title[:31]
    hf=Font(name='Arial',size=12,bold=True,color='FFFFFF');hfl=PatternFill(start_color='1A365D',end_color='1A365D',fill_type='solid')
    ha=Alignment(horizontal='center',vertical='center',wrap_text=True);cf=Font(name='Arial',size=11);ca=Alignment(vertical='center',wrap_text=True)
    bd=Border(left=Side(style='thin'),right=Side(style='thin'),top=Side(style='thin'),bottom=Side(style='thin'))
    tl=[l.strip() for l in content.split('\n') if l.strip() and '|' in l]
    if tl:
        rn=0
        for line in tl:
            if all(c in '-| ' for c in line): continue
            rn+=1;cells=[c.strip() for c in line.split('|') if c.strip()]
            for ci,v in enumerate(cells,1):
                cell=ws.cell(row=rn,column=ci,value=v);cell.border=bd
                if rn==1: cell.font,cell.fill,cell.alignment=hf,hfl,ha
                else: cell.font,cell.alignment=cf,ca
    for col in ws.columns:
        ml=max((len(str(c.value or '')) for c in col),default=10);ws.column_dimensions[col[0].column_letter].width=min(ml+4,50)
    buf=io.BytesIO();wb.save(buf);buf.seek(0);return buf

def detect_document_request(text):
    if '[PPTX_DOC]' in text:
        parts=text.split('[PPTX_DOC]',1);c=parts[1].strip() if len(parts)>1 else text;t="Презентация"
        for l in c.split('\n'):
            s=l.strip()
            if s.startswith('ЗАГОЛОВОК:'): t=s[10:].strip();break
            elif s.startswith('#'): t=s.lstrip('#').strip();break
            elif s and not s.startswith('['): t=s[:60];break
        return('pptx',t,c)
    elif '[WORD_DOC]' in text:
        parts=text.split('[WORD_DOC]',1);c=parts[1].strip() if len(parts)>1 else text;t="Материал"
        for l in c.split('\n'):
            if l.strip().startswith('#'): t=l.strip().lstrip('#').strip();break
            elif l.strip(): t=l.strip()[:60];break
        return('word',t,c)
    elif '[EXCEL_DOC]' in text:
        parts=text.split('[EXCEL_DOC]',1);c=parts[1].strip() if len(parts)>1 else text;t="Таблица"
        for l in c.split('\n'):
            if l.strip() and '|' not in l: t=l.strip()[:31];break
        return('excel',t,c)
    return(None,'','')


# ─── Клавиатура ───────────────────────────────────────────────

PERSISTENT_KEYBOARD = ReplyKeyboardMarkup(
    [[KeyboardButton("📋 Меню"), KeyboardButton("💰 Баланс")],
     [KeyboardButton("📽 Оформить презентацию"), KeyboardButton("📄 Оформить Word")],
     [KeyboardButton("📊 Оформить Excel"), KeyboardButton("❓ Помощь")]],
    resize_keyboard=True, is_persistent=True
)


# ─── Генерация документа ─────────────────────────────────────

async def generate_document(update, context, uid, fmt, topic=None):
    if not check_generation_limit(uid):
        await update.message.reply_text(GENERATION_LIMIT_MESSAGE, parse_mode='Markdown'); return
    h = get_history(uid); has = any(m['role']=='assistant' for m in h)
    prompts = {'pptx': PPTX_PROMPT, 'word': WORD_PROMPT, 'excel': EXCEL_PROMPT}
    labels = {'pptx': '⏳ Создаю презентацию...', 'word': '⏳ Создаю Word...', 'excel': '⏳ Создаю Excel...'}
    await update.message.reply_text(labels.get(fmt, '⏳ Создаю...'))
    base = f"На основе нашего диалога создай {fmt}." if has and not topic else f"Создай по теме: {topic or 'материал'}"
    answer = ask_claude(uid, base + prompts.get(fmt, ''))
    dt, dtitle, dcontent = detect_document_request(answer)
    if not dt:
        await update.message.reply_text("⚠️ Не удалось. Обсудите тему подробнее и попробуйте снова."); return
    use_generation(uid); ql, gl = get_limit_status(uid)
    if dt=='pptx':
        buf=create_pptx_presentation(dtitle,dcontent);await update.message.reply_document(document=buf,filename=f"{dtitle[:30].replace(' ','_')}.pptx",caption=f"📽 {dtitle}")
    elif dt=='word':
        buf=create_word_document(dtitle,dcontent);await update.message.reply_document(document=buf,filename=f"{dtitle[:30].replace(' ','_')}.docx",caption=f"📄 {dtitle}")
    elif dt=='excel':
        buf=create_excel_document(dtitle,dcontent);await update.message.reply_document(document=buf,filename=f"{dtitle[:30].replace(' ','_')}.xlsx",caption=f"📊 {dtitle}")
    await update.message.reply_text(f"📊 Осталось: {ql} запросов, {gl} оформлений")


# ─── Обработчики ──────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    save_user_info(uid, update.effective_user.first_name, update.effective_user.username)
    ql, gl = get_limit_status(uid)
    tariff = get_tariff(uid)
    kb = [
        [InlineKeyboardButton("📝 План урока", callback_data="plan"), InlineKeyboardButton("📋 Тест", callback_data="test")],
        [InlineKeyboardButton("💡 Идеи", callback_data="ideas"), InlineKeyboardButton("🎯 ОГЭ/ЕГЭ", callback_data="ege")],
        [InlineKeyboardButton("📚 Материалы", callback_data="sources")],
    ]
    w = (
        f"🏫 **Добро пожаловать в «{BOT_NAME}»!**\n\n"
        "Я — ИИ-помощник для учителей **любого предмета**.\n\n"
        "**Напишите что нужно:**\n"
        "📝 «План урока по биологии, 6 класс»\n"
        "📋 «Тест: дроби, 5 класс, 15 вопросов»\n"
        "📸 Отправьте фото  •  🎙 Голосовое\n\n"
        "**Когда готово** — кнопки оформления внизу\n\n"
        f"📋 Тариф: {tariff['name']}\n"
        f"💬 Запросов: {ql}  •  📄 Оформлений: {gl}"
    )
    await update.message.reply_text(w, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
    await update.message.reply_text("⬇️ Кнопки:", reply_markup=PERSISTENT_KEYBOARD)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id; ql, gl = get_limit_status(uid)
    await update.message.reply_text(
        f"🏫 **«{BOT_NAME}» v{BOT_VERSION}**\n\n"
        "1️⃣ Напишите запрос\n2️⃣ Обсуждайте\n3️⃣ Нажмите кнопку оформления\n\n"
        f"/start /help /clear /status /balance\n\n"
        f"📊 {ql} запросов, {gl} оформлений\n"
        f"Поддержка: {SUPPORT_BOT_URL}", parse_mode='Markdown')

async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(get_balance_text(update.effective_user.id), parse_mode='Markdown')

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await balance_command(update, context)

async def clear_history_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_conversations[update.effective_user.id] = []; await update.message.reply_text("🗑 Очищено!")

async def pptx_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await generate_document(update, context, update.effective_user.id, 'pptx', ' '.join(context.args) if context.args else None)

async def word_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await generate_document(update, context, update.effective_user.id, 'word', ' '.join(context.args) if context.args else None)

async def excel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await generate_document(update, context, update.effective_user.id, 'excel', ' '.join(context.args) if context.args else None)

BUTTON_PROMPTS = {
    "plan": "📝 Тема, предмет, класс:\n«План урока: Фотосинтез, биология, 6 класс»",
    "test": "📋 Тема, предмет, кол-во:\n«Тест: Дроби, 5 класс, 20 вопросов»",
    "ege": "🎯 Предмет:\n«Разбор задания ЕГЭ, русский, задание 8»",
    "ideas": "💡 Предмет:\n«Идеи: Электричество, физика, 8 класс»",
    "sources": "📚 Тему:\n«Материалы: Реформы Петра I»",
}

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if q.data in BUTTON_PROMPTS: await q.message.reply_text(BUTTON_PROMPTS[q.data])

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not check_query_limit(uid): await update.message.reply_text(LIMIT_MESSAGE, parse_mode='Markdown'); return
    doc = update.message.document; caption = update.message.caption or ""; fn = doc.file_name or "file"
    ext = fn.rsplit('.',1)[-1].lower() if '.' in fn else ""
    if ext not in ['docx','doc','pdf','xlsx','xls','txt','csv']:
        await update.message.reply_text(f"⚠️ .{ext} не поддерживается."); return
    await update.message.chat.send_action('typing'); await update.message.reply_text(f"📄 Читаю «{fn}»...")
    try:
        file = await doc.get_file(); fp = f"/tmp/doc_{uid}.{ext}"; await file.download_to_drive(fp); et = ""
        if ext=='docx':
            try:
                d=DocxDocument(fp);parts=[p.text for p in d.paragraphs if p.text.strip()]
                for t in d.tables:
                    for r in t.rows: rt=' | '.join(c.text.strip() for c in r.cells);parts.append(rt) if rt.strip() else None
                et='\n'.join(parts)
            except Exception as e: logger.error(f"docx:{e}")
        elif ext=='pdf':
            try:
                with open(fp,'rb') as f: reader=PyPDF2.PdfReader(f);et='\n'.join(pg.extract_text() or '' for pg in reader.pages)
            except Exception as e: logger.error(f"pdf:{e}")
        elif ext in ['xlsx','xls']:
            try:
                wb=openpyxl.load_workbook(fp,read_only=True);rows=[]
                for sn in wb.sheetnames:
                    ws=wb[sn];rows.append(f"--- {sn} ---")
                    for r in ws.iter_rows(values_only=True): rt=' | '.join(str(c) if c else '' for c in r);rows.append(rt) if rt.strip() else None
                et='\n'.join(rows);wb.close()
            except Exception as e: logger.error(f"xlsx:{e}")
        elif ext in ['txt','csv']:
            try:
                with open(fp,'r',encoding='utf-8') as f: et=f.read()
            except:
                with open(fp,'r',encoding='cp1251') as f: et=f.read()
        try: os.remove(fp)
        except: pass
        if not et or len(et.strip())<10: await update.message.reply_text("⚠️ Не удалось извлечь текст."); return
        if len(et)>12000: et=et[:12000]+"\n\n[...обрезано...]"
        await update.message.reply_text(f"✅ {len(et)} символов")
        if caption:
            use_query(uid);answer=ask_claude(uid,f"Документ «{fn}»:\n\n{et}\n\n{caption}")
            await send_text(update,clean_for_chat(answer));ql,gl=get_limit_status(uid)
            await update.message.reply_text(f"📊 Осталось: {ql} запросов, {gl} оформлений")
        else:
            context.user_data['last_document']=et;context.user_data['last_document_name']=fn
            await update.message.reply_text("📄 Прочитано! Что сделать?\n• «Создай тест»\n• «План урока»")
    except Exception as e: logger.error(f"Doc:{e}");await update.message.reply_text("⚠️ Ошибка.")

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid=update.effective_user.id
    if not check_query_limit(uid): await update.message.reply_text(LIMIT_MESSAGE,parse_mode='Markdown');return
    await update.message.chat.send_action('typing');await update.message.reply_text("🎙 Распознаю...")
    try:
        v=update.message.voice or update.message.audio;f=await v.get_file()
        op=f"/tmp/voice_{uid}.ogg";wp=f"/tmp/voice_{uid}.wav";await f.download_to_drive(op)
        subprocess.run(['ffmpeg','-y','-i',op,'-ar','16000','-ac','1',wp],capture_output=True,timeout=30)
        rec=sr.Recognizer()
        with sr.AudioFile(wp) as src: audio=rec.record(src)
        text=rec.recognize_google(audio,language="ru-RU")
        try: os.remove(op);os.remove(wp)
        except: pass
        if not text: await update.message.reply_text("⚠️ Не распознано.");return
        await update.message.reply_text(f"✅ «{text}»")
        use_query(uid);answer=ask_claude(uid,text);await send_text(update,clean_for_chat(answer))
        ql,gl=get_limit_status(uid);await update.message.reply_text(f"📊 Осталось: {ql} запросов, {gl} оформлений")
    except sr.UnknownValueError: await update.message.reply_text("⚠️ Не распознано.")
    except Exception as e: logger.error(f"Voice:{e}");await update.message.reply_text("⚠️ Ошибка.")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid=update.effective_user.id
    if not check_query_limit(uid): await update.message.reply_text(LIMIT_MESSAGE,parse_mode='Markdown');return
    caption=update.message.caption or "";await update.message.chat.send_action('typing')
    photo=update.message.photo[-1];f=await photo.get_file();pb=await f.download_as_bytearray()
    idata={'base64':base64.b64encode(bytes(pb)).decode('utf-8'),'mime':'image/jpeg'}
    if not caption:
        await update.message.reply_text("📸 Фото получено! Что сделать?\n• «Создай тест»\n• «Объясни»")
        context.user_data['last_photo']=idata;return
    await update.message.reply_text("⏳ Анализирую...")
    use_query(uid);answer=ask_claude(uid,f"Изображение. {caption}\n\nПроанализируй и ответь.",image_data=idata)
    await send_text(update,clean_for_chat(answer));ql,gl=get_limit_status(uid)
    await update.message.reply_text(f"📊 Осталось: {ql} запросов, {gl} оформлений")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid=update.effective_user.id;text=update.message.text
    # Кнопки
    if text=="📋 Меню":
        kb=[[InlineKeyboardButton("📝 План",callback_data="plan"),InlineKeyboardButton("📋 Тест",callback_data="test")],
            [InlineKeyboardButton("💡 Идеи",callback_data="ideas"),InlineKeyboardButton("🎯 ОГЭ/ЕГЭ",callback_data="ege")],
            [InlineKeyboardButton("📚 Материалы",callback_data="sources")]]
        await update.message.reply_text("🏫 **Выберите:**",reply_markup=InlineKeyboardMarkup(kb),parse_mode='Markdown');return
    if text=="💰 Баланс": await update.message.reply_text(get_balance_text(uid),parse_mode='Markdown');return
    if text=="📽 Оформить презентацию": await generate_document(update,context,uid,'pptx');return
    if text=="📄 Оформить Word": await generate_document(update,context,uid,'word');return
    if text=="📊 Оформить Excel": await generate_document(update,context,uid,'excel');return
    if text=="❓ Помощь": await help_command(update,context);return
    if not check_query_limit(uid): await update.message.reply_text(LIMIT_MESSAGE,parse_mode='Markdown');return
    await update.message.chat.send_action('typing')
    # Сохранённый документ
    sd=context.user_data.get('last_document')
    if sd:
        dn=context.user_data.get('last_document_name','документ');context.user_data['last_document']=None
        use_query(uid);answer=ask_claude(uid,f"Документ «{dn}»:\n\n{sd}\n\n{text}")
        await send_text(update,clean_for_chat(answer));ql,gl=get_limit_status(uid)
        await update.message.reply_text(f"📊 Осталось: {ql} запросов, {gl} оформлений");return
    # Сохранённое фото
    sp=context.user_data.get('last_photo')
    if sp:
        context.user_data['last_photo']=None;await update.message.reply_text("⏳ Анализирую...")
        use_query(uid);answer=ask_claude(uid,f"Изображение. {text}",image_data=sp)
        await send_text(update,clean_for_chat(answer));ql,gl=get_limit_status(uid)
        await update.message.reply_text(f"📊 Осталось: {ql} запросов, {gl} оформлений");return
    # Обычный
    use_query(uid);answer=ask_claude(uid,text);await send_text(update,clean_for_chat(answer))
    ql,gl=get_limit_status(uid);await update.message.reply_text(f"📊 Осталось: {ql} запросов, {gl} оформлений")

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Err:{context.error}")
    if update and update.message: await update.message.reply_text("⚠️ Ошибка. /clear")

def main():
    if not TELEGRAM_TOKEN or not ANTHROPIC_API_KEY: print("❌ Нет токенов!");return
    print(f"🏫 «{BOT_NAME}» v{BOT_VERSION} запускается...")
    app=Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start",start));app.add_handler(CommandHandler("help",help_command))
    app.add_handler(CommandHandler("clear",clear_history_cmd));app.add_handler(CommandHandler("status",status_command))
    app.add_handler(CommandHandler("balance",balance_command))
    app.add_handler(CommandHandler("pptx",pptx_command));app.add_handler(CommandHandler("word",word_command));app.add_handler(CommandHandler("excel",excel_command))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.VOICE|filters.AUDIO,handle_voice))
    app.add_handler(MessageHandler(filters.Document.ALL,handle_document))
    app.add_handler(MessageHandler(filters.PHOTO,handle_photo))
    app.add_handler(MessageHandler(filters.TEXT&~filters.COMMAND,handle_message))
    app.add_error_handler(error_handler)
    print(f"✅ «{BOT_NAME}» v{BOT_VERSION} запущен!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__=="__main__": main()
