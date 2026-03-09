#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Авто-сборщик ОГЭ по географии (демоверсия 2026) для ПроУрока.
Первая версия: скачивает PDF демоверсии, вытаскивает текст заданий
и сохраняет их в CSV /root/pro-lesson-data/fipi/geography/oge/tasks_geography_oge.csv

ВНИМАНИЕ:
- URL демоверсии сейчас примерный, при необходимости замени на актуальный.
- Парсинг очень упрощённый, цель — показать полный цикл "скачал → распарсил → CSV".
"""

import os
import re
import csv
import tempfile
import logging
from pathlib import Path

import requests
import PyPDF2

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("fipi_oge_geo_collect")

# ----- НАСТРОЙКИ -------------------------------------------------------------

# Путь к боевому CSV на сервере
DATA_DIR = Path("/root/pro-lesson-data/fipi/geography/oge")
CSV_PATH = DATA_DIR / "tasks_geography_oge.csv"

# Прямая ссылка на демоверсию ОГЭ-2026 по географии (замени при необходимости)
DEMO_URL = "https://eobraz.ru/wp-content/uploads/2025/08/GG-9-OGE-2026_DEMO-1.pdf"

EXAM = "OGE"
YEAR = "2026"
SUBJECT = "geography"
SOURCE = "demo"


# ----- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ----------------------------------------------

def download_demo_pdf(url: str) -> str:
    """
    Скачивает PDF демоверсии во временный файл и возвращает путь к нему.
    """
    logger.info(f"Скачиваю демоверсию ОГЭ география 2026: {url}")
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()

    fd, tmp_path = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)

    with open(tmp_path, "wb") as f:
        f.write(resp.content)

    logger.info(f"PDF демоверсии сохранён во временный файл: {tmp_path}")
    return tmp_path


def extract_text_from_pdf(pdf_path: str) -> str:
    """
    Извлекает весь текст из PDF демоверсии.
    """
    logger.info(f"Читаю PDF: {pdf_path}")
    text_parts = []
    with open(pdf_path, "rb") as f:
        reader = PyPDF2.PdfReader(f)
        for i, page in enumerate(reader.pages):
            try:
                t = page.extract_text() or ""
            except Exception as e:
                logger.warning(f"Ошибка чтения страницы {i}: {e}")
                t = ""
            text_parts.append(t)

    full_text = "\n".join(text_parts)
    logger.info(f"Извлечено символов из PDF: {len(full_text)}")
    return full_text


def parse_tasks_from_text(full_text: str):
    """
    Очень простой парсер заданий.
    Ищем блоки, начинающиеся с 'Задание 1', 'Задание 2', ...
    Возвращаем список словарей: {'kim_number': int, 'text': '...'}.
    """
    logger.info("Парсю задания из текста демоверсии")

    # Нормализуем переводы строк
    text = full_text.replace("\r\n", "\n").replace("\r", "\n")

    # Примерный шаблон для начала задания: "Задание 1", "Задание 2", ...
    pattern = r"(Задание\s+(\d+)[^\n]*\n)"

    matches = list(re.finditer(pattern, text))
    if not matches:
        logger.warning("Не найдено ни одного блока 'Задание N' в тексте")
        return []

    tasks = []

    for idx, match in enumerate(matches):
        # Начало блока задания
        start = match.start()
        kim_number_str = match.group(2).strip()

        try:
            kim_number = int(kim_number_str)
        except ValueError:
            logger.warning(f"Не удалось распознать номер КИМ: {kim_number_str}")
            continue

        # Конец блока — начало следующего задания или конец текста
        if idx + 1 < len(matches):
            end = matches[idx + 1].start()
        else:
            end = len(text)

        block = text[start:end].strip()

        # Немного чистим блок: убираем лишние пустые строки в начале
        block = re.sub(r"\n{3,}", "\n\n", block).strip()

        # Ограничимся пока «разумным» размером, чтобы не тащить весь PDF в одно задание
        if len(block) > 3000:
            block = block[:3000] + "\n\n[...обрезано для CSV...]"

        tasks.append({
            "kim_number": kim_number,
            "text": block,
        })

    logger.info(f"Найдено заданий по шаблону 'Задание N': {len(tasks)}")
    return tasks


def write_tasks_to_csv(tasks, csv_path: Path):
    """
    Записывает задания в CSV.
    Формат: exam;year;source;subject;kim_number;text
    """
    logger.info(f"Сохраняю задания в CSV: {csv_path}")

    csv_path.parent.mkdir(parents=True, exist_ok=True)

    # Если файл уже есть, можно:
    # - или перезаписать (первая версия),
    # - или потом доработать объединение.
    # Сейчас перезаписываем.
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(["exam", "year", "source", "subject", "kim_number", "text"])

        for t in tasks:
            writer.writerow([
                EXAM,
                YEAR,
                SOURCE,
                SUBJECT,
                t.get("kim_number"),
                t.get("text", "").replace("\t", " ").strip(),
            ])

    logger.info(f"В CSV записано заданий: {len(tasks)}")


# ----- ГЛАВНАЯ ФУНКЦИЯ ------------------------------------------------------

def main():
    logger.info("=== Старт авто-сборщика ОГЭ география 2026 (демоверсия) ===")

    try:
        pdf_path = download_demo_pdf(DEMO_URL)
    except Exception as e:
        logger.error(f"Не удалось скачать демоверсию: {e}")
        return

    try:
        text = extract_text_from_pdf(pdf_path)
    except Exception as e:
        logger.error(f"Не удалось прочитать PDF: {e}")
        return
    finally:
        # Удаляем временный файл
        try:
            os.remove(pdf_path)
        except Exception:
            pass

    tasks = parse_tasks_from_text(text)
    if not tasks:
        logger.warning("Парсер не нашёл заданий — CSV не будет обновлён")
        return

    write_tasks_to_csv(tasks, CSV_PATH)
    logger.info("=== Сбор заданий завершён успешно ===")


if __name__ == "__main__":
    main()

