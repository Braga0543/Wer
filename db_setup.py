#!/usr/bin/env python3
# db_setup.py — создаёт pharmacy.db и заполняет тестовыми данными.
# Скрипт идемпотентный: таблицы создаются через IF NOT EXISTS,
# тестовые данные добавляются только в пустые таблицы.
import os
import random
import sqlite3
from datetime import datetime, timedelta, timezone

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pharmacy.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    salt TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS medicines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    active_ingredient TEXT NOT NULL,
    form TEXT,
    dosage TEXT,
    manufacturer TEXT,
    avg_price REAL
);
CREATE TABLE IF NOT EXISTS pharmacies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    address TEXT NOT NULL,
    lat REAL NOT NULL,
    lng REAL NOT NULL,
    phone TEXT,
    working_hours TEXT
);
CREATE TABLE IF NOT EXISTS availability (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pharmacy_id INTEGER NOT NULL REFERENCES pharmacies(id),
    medicine_id INTEGER NOT NULL REFERENCES medicines(id),
    price REAL,
    in_stock INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    UNIQUE (pharmacy_id, medicine_id)
);
CREATE TABLE IF NOT EXISTS shortage_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    medicine_id INTEGER NOT NULL REFERENCES medicines(id),
    district TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS user_medicines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    medicine_id INTEGER NOT NULL REFERENCES medicines(id),
    doses_per_day INTEGER NOT NULL,
    pills_per_pack INTEGER NOT NULL,
    started_at TEXT NOT NULL
);
"""

# Реальные аптеки Атырау (название, адрес, координаты, телефон, часы работы).
# Данные получены через Google Places (сентябрь 2026): названия, адреса,
# координаты, телефоны и часы работы — фактические. Наличие и цены конкретных
# препаратов (таблица availability ниже) публично не публикуются ни одной
# аптекой, поэтому они смоделированы — как и в реальных агрегаторах вроде
# i-teka, такие данные получают только по прямому соглашению с сетью аптек.
PHARMACIES = [
    ("Apteka Diana", "ул. Сатпаева, 32, Атырау 060011", 47.104791, 51.903262, "+7 7122 50 77 59", "круглосуточно"),
    ("Apteka Zdorov'ye", "пр. Каныша Сатпаева, 32, Атырау 060011", 47.104799, 51.903483, "+7 778 872 0300", "круглосуточно"),
    ("Аптека «Социальная» 24/7", "мкр. Привокзальный, 3а, стр. 4, Атырау", 47.129119, 51.943662, "+7 7122 36 07 06", "круглосуточно"),
    ("Zelenaya Apteka", "4W4H+P8R, Атырау", 47.106866, 51.928252, "+7 7122 75 55 15", "круглосуточно"),
    ("Pharmacy 24 hours", "4W9V+F42, Атырау", 47.118646, 51.942764, "+7 775 245 2635", "круглосуточно"),
    ("Aybolit", "мкр. Береке, 28, Атырау", 47.140907, 51.955343, "+7 778 602 0667", "09:30–00:00"),
    ("Apteka-24", "ул. Шокана Уалиханова, 5, Атырау", 47.108724, 51.924720, "+7 7122 32 89 68", "круглосуточно"),
    ("Bekas Plyus", "3V4C+CCQ, Атырау", 47.056092, 51.871116, "+7 701 277 3773", "10:00–01:00"),
    ("Zerde", "мкр. Привокзальный, Атырау", 47.118357, 51.865689, "", "08:00–23:00 (сб-вс до 22:00)"),
    ("Аптека на пр. Азаттык", "пр. Азаттык, Атырау", 47.076716, 51.887918, "", "09:00–22:00"),
    ("Apteka Na Gur'yevskoy", "3VWG+F68, Атырау", 47.096170, 51.875575, "", "уточняйте по телефону"),
    ("АО «Медицина» №10", "пр. Каныша Сатпаева, 22, Атырау", 47.104836, 51.908358, "+7 7122 21 41 24", "уточняйте по телефону"),
    ("Na Gur'yevskoy", "пр. Каныша Сатпаева, 22, Атырау", 47.104766, 51.908088, "+7 7122 21 03 82", "09:00–21:00 (сб-вс до 20:00)"),
    ("Аптека «АлоЕ+»", "пр. Азаттык, 72б, Атырау", 47.091217, 51.917938, "", "09:00–22:00"),
]

# (название, действующее вещество, форма, дозировка, производитель, средняя цена, ₸)
MEDICINES = [
    ("Метформин", "метформин", "таблетки", "500 мг", "Тева", 1800),
    ("Глибенкламид", "глибенкламид", "таблетки", "5 мг", "Органика", 900),
    ("Инсулин Хумулин Р", "инсулин человеческий", "раствор для инъекций", "100 МЕ/мл", "Эли Лилли", 9500),
    ("Эналаприл", "эналаприл", "таблетки", "10 мг", "Гедеон Рихтер", 1200),
    ("Лозартан", "лозартан", "таблетки", "50 мг", "Крка", 2600),
    ("Амлодипин", "амлодипин", "таблетки", "5 мг", "Кусум", 1400),
    ("Бисопролол", "бисопролол", "таблетки", "5 мг", "Тева", 2100),
    ("Аторвастатин", "аторвастатин", "таблетки", "20 мг", "Гедеон Рихтер", 3800),
    ("Сальбутамол", "сальбутамол", "аэрозоль", "100 мкг/доза", "ГлаксоСмитКляйн", 2400),
    ("Будесонид", "будесонид", "ингалятор", "200 мкг/доза", "АстраЗенека", 7200),
    ("Левотироксин", "левотироксин натрия", "таблетки", "50 мкг", "Мерк", 2300),
    ("Омепразол", "омепразол", "капсулы", "20 мг", "Крка", 1500),
    ("Аспирин Кардио", "ацетилсалициловая кислота", "таблетки", "100 мг", "Байер", 2700),
    ("Клопидогрел", "клопидогрел", "таблетки", "75 мг", "Тева", 4200),
    ("Валсартан", "валсартан", "таблетки", "80 мг", "Новартис", 3900),
    ("Карведилол", "карведилол", "таблетки", "12,5 мг", "Актавис", 2000),
    ("Спиронолактон", "спиронолактон", "таблетки", "25 мг", "Гедеон Рихтер", 2500),
    ("Фуросемид", "фуросемид", "таблетки", "40 мг", "Санофи", 700),
    ("Аллопуринол", "аллопуринол", "таблетки", "100 мг", "Гедеон Рихтер", 1300),
    ("Преднизолон", "преднизолон", "таблетки", "5 мг", "Гедеон Рихтер", 1100),
    # Бренды-аналоги (одно действующее вещество), чтобы работал раздел «Аналоги»
    ("Глюкофаж", "метформин", "таблетки", "500 мг", "Мерк", 2900),
    ("Конкор", "бисопролол", "таблетки", "5 мг", "Мерк", 3300),
    ("Кардиомагнил", "ацетилсалициловая кислота", "таблетки", "75 мг", "Такеда", 2500),
    ("Лозап", "лозартан", "таблетки", "50 мг", "Зентива", 2800),
]

DISTRICTS = ["Центр", "Жилгородок", "Привокзальный", "Авангард", "Ак Жайык"]


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def main():
    random.seed(42)  # воспроизводимые тестовые данные
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    cur = conn.cursor()

    if cur.execute("SELECT COUNT(*) FROM pharmacies").fetchone()[0] == 0:
        cur.executemany(
            "INSERT INTO pharmacies (name, address, lat, lng, phone, working_hours) "
            "VALUES (?,?,?,?,?,?)", PHARMACIES)

    if cur.execute("SELECT COUNT(*) FROM medicines").fetchone()[0] == 0:
        cur.executemany(
            "INSERT INTO medicines (name, active_ingredient, form, dosage, "
            "manufacturer, avg_price) VALUES (?,?,?,?,?,?)", MEDICINES)

    # Наличие: 70% «в наличии», 30% «нет»; цена ±15% от средней
    if cur.execute("SELECT COUNT(*) FROM availability").fetchone()[0] == 0:
        ts = now_iso()
        pharm_ids = [r[0] for r in cur.execute("SELECT id FROM pharmacies")]
        meds = cur.execute("SELECT id, avg_price FROM medicines").fetchall()
        rows = []
        for pid in pharm_ids:
            for mid, avg in meds:
                in_stock = 1 if random.random() < 0.7 else 0
                price = round(avg * random.uniform(0.9, 1.15) / 10) * 10
                rows.append((pid, mid, price, in_stock, ts))
        cur.executemany(
            "INSERT INTO availability (pharmacy_id, medicine_id, price, in_stock, "
            "updated_at) VALUES (?,?,?,?,?)", rows)

    # Несколько демо-отчётов о дефиците, чтобы статистика не была пустой
    if cur.execute("SELECT COUNT(*) FROM shortage_reports").fetchone()[0] == 0:
        n_meds = len(MEDICINES)
        for _ in range(25):
            mid = random.choice([1, 3, 3, 3, 8, 9, 11, 13, 15]) if n_meds >= 15 else 1
            days_ago = random.randint(0, 25)
            ts = (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%S")
            cur.execute(
                "INSERT INTO shortage_reports (medicine_id, district, created_at) VALUES (?,?,?)",
                (mid, random.choice(DISTRICTS), ts))

    conn.commit()
    conn.close()
    print("Готово: pharmacy.db создана/обновлена.")


if __name__ == "__main__":
    main()

# ─────────────────────────────────────────────────────────────
# ПОЛНЫЙ СБРОС БД (раскомментируйте, запустите один раз, закомментируйте обратно)
# ─────────────────────────────────────────────────────────────
# import os
# if os.path.exists(DB_PATH):
#     os.remove(DB_PATH)
#     print("pharmacy.db удалена. Запустите скрипт заново для пересоздания.")
