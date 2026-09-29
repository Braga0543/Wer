# main.py — FastAPI-приложение «Аптека рядом».
import math
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import db_setup
from auth import (create_session, delete_session, get_current_user, get_db,
                   hash_password, new_salt, now_iso, token_from_request,
                   verify_password)

app = FastAPI(title="Аптека рядом")


# На обычном сервере вы один раз запускаете `python db_setup.py` вручную.
# На Vercel (serverless) файловая система пересоздаётся при каждом
# "холодном старте" функции, поэтому база в /tmp может быть пустой —
# создаём и заполняем её здесь автоматически при старте приложения.
# db_setup.setup() идемпотентен (IF NOT EXISTS / проверка на пустоту),
# так что повторный вызов на "тёплом" старте ничего не портит.
@app.on_event("startup")
def ensure_database_ready():
    db_setup.setup()


# Глобальный обработчик непредвиденных ошибок: без него FastAPI/Starlette
# по умолчанию отвечает на 500-е ошибки простым текстом "Internal Server
# Error" (не JSON), а фронтенд не может его разобрать и показывает общее
# "Произошла ошибка". С этим обработчиком в ответе всегда будет JSON
# с описанием ошибки — легче понять, что пошло не так.
@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    from fastapi.responses import JSONResponse
    print(f"[ОШИБКА] {request.method} {request.url.path}: {exc!r}")
    return JSONResponse(status_code=500, content={"detail": f"Ошибка сервера: {exc}"})

# CORS: разрешаем всё, включая заголовок Authorization
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# ───────────────────────── Pydantic-модели входных данных ─────────────────────────

class AuthBody(BaseModel):
    email: str
    password: str


class ShortageBody(BaseModel):
    medicine_id: int
    district: str


class MyMedicineBody(BaseModel):
    medicine_id: int
    doses_per_day: int
    pills_per_pack: int


# ───────────────────────── Вспомогательные функции ─────────────────────────

def normalize(text: str) -> str:
    """Приводит строку к нижнему регистру и заменяет ё→е для нечувствительного поиска."""
    return text.lower().replace("ё", "е")


def haversine_km(lat1, lng1, lat2, lng2) -> float:
    """Расстояние между двумя точками на сфере (формула гаверсинуса), км."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


# ───────────────────────── Авторизация ─────────────────────────

@app.post("/register")
def register(body: AuthBody):
    email = body.email.strip().lower()
    if not EMAIL_RE.match(email):
        raise HTTPException(400, "Некорректный email")
    if len(body.password) < 6:
        raise HTTPException(400, "Пароль должен быть не короче 6 символов")

    conn = get_db()
    try:
        exists = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if exists:
            raise HTTPException(400, "Этот email уже зарегистрирован")
        salt = new_salt()
        pw_hash = hash_password(body.password, salt)
        cur = conn.execute(
            "INSERT INTO users (email, password_hash, salt, created_at) VALUES (?,?,?,?)",
            (email, pw_hash, salt, now_iso()))
        conn.commit()
        user_id = cur.lastrowid
    finally:
        conn.close()

    token = create_session(user_id)
    return {"token": token, "email": email}


@app.post("/login")
def login(body: AuthBody):
    email = body.email.strip().lower()
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT id, password_hash, salt FROM users WHERE email = ?", (email,)).fetchone()
    finally:
        conn.close()
    if row is None or not verify_password(body.password, row["salt"], row["password_hash"]):
        raise HTTPException(401, "Неверный email или пароль")
    token = create_session(row["id"])
    return {"token": token, "email": email}


@app.post("/logout")
def logout(request: Request):
    delete_session(token_from_request(request))
    return {"ok": True}


@app.get("/me")
def me(user_id: int = Depends(get_current_user)):
    conn = get_db()
    try:
        row = conn.execute("SELECT email FROM users WHERE id = ?", (user_id,)).fetchone()
    finally:
        conn.close()
    if row is None:
        raise HTTPException(401, "Пользователь не найден")
    return {"email": row["email"]}


# ───────────────────────── Лекарства (публичные) ─────────────────────────

@app.get("/medicines")
def search_medicines(q: str = ""):
    q_norm = normalize(q.strip())
    conn = get_db()
    try:
        if not q_norm:
            rows = conn.execute(
                "SELECT id, name, active_ingredient, form, dosage, manufacturer, avg_price "
                "FROM medicines ORDER BY name LIMIT 50").fetchall()
        else:
            like = f"%{q_norm}%"
            rows = conn.execute(
                "SELECT id, name, active_ingredient, form, dosage, manufacturer, avg_price "
                "FROM medicines "
                "WHERE REPLACE(LOWER(name), 'ё', 'е') LIKE ? "
                "   OR REPLACE(LOWER(active_ingredient), 'ё', 'е') LIKE ? "
                "ORDER BY name LIMIT 50", (like, like)).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


@app.get("/medicine/{medicine_id}")
def medicine_detail(medicine_id: int):
    conn = get_db()
    try:
        med = conn.execute(
            "SELECT id, name, active_ingredient, form, dosage, manufacturer, avg_price "
            "FROM medicines WHERE id = ?", (medicine_id,)).fetchone()
        if med is None:
            raise HTTPException(404, "Лекарство не найдено")
        analogs = conn.execute(
            "SELECT id, name, form, dosage, manufacturer, avg_price FROM medicines "
            "WHERE active_ingredient = ? AND id != ? ORDER BY avg_price",
            (med["active_ingredient"], medicine_id)).fetchall()
    finally:
        conn.close()
    result = dict(med)
    result["analogs"] = [dict(a) for a in analogs]
    return result


@app.get("/pharmacies")
def pharmacies_for_medicine(medicine_id: int, lat: Optional[float] = None, lng: Optional[float] = None):
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT p.name, p.address, p.phone, p.lat, p.lng, "
            "       a.price, a.in_stock "
            "FROM pharmacies p "
            "JOIN availability a ON a.pharmacy_id = p.id "
            "WHERE a.medicine_id = ?", (medicine_id,)).fetchall()
    finally:
        conn.close()

    result = []
    for r in rows:
        item = {
            "name": r["name"], "address": r["address"], "phone": r["phone"],
            "price": r["price"], "in_stock": bool(r["in_stock"]),
            "lat": r["lat"], "lng": r["lng"],
        }
        if lat is not None and lng is not None:
            item["distance_km"] = round(haversine_km(lat, lng, r["lat"], r["lng"]), 2)
        else:
            item["distance_km"] = None
        result.append(item)

    # Сортировка: сначала по наличию, затем по расстоянию (неизвестное расстояние — в конец)
    result.sort(key=lambda x: (not x["in_stock"],
                                x["distance_km"] if x["distance_km"] is not None else 9e9))
    return result


@app.post("/shortage")
def report_shortage(body: ShortageBody):
    conn = get_db()
    try:
        med = conn.execute("SELECT id FROM medicines WHERE id = ?", (body.medicine_id,)).fetchone()
        if med is None:
            raise HTTPException(404, "Лекарство не найдено")
        conn.execute(
            "INSERT INTO shortage_reports (medicine_id, district, created_at) VALUES (?,?,?)",
            (body.medicine_id, body.district.strip(), now_iso()))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


@app.get("/shortage-stats")
def shortage_stats():
    since = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%S")
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT m.id, m.name, COUNT(*) AS reports "
            "FROM shortage_reports s JOIN medicines m ON m.id = s.medicine_id "
            "WHERE s.created_at >= ? "
            "GROUP BY m.id ORDER BY reports DESC LIMIT 5", (since,)).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


# ───────────────────────── Мои препараты (только авторизованные) ─────────────────────────

@app.post("/my-medicines")
def add_my_medicine(body: MyMedicineBody, user_id: int = Depends(get_current_user)):
    if body.doses_per_day <= 0 or body.pills_per_pack <= 0:
        raise HTTPException(400, "Значения должны быть положительными")
    conn = get_db()
    try:
        med = conn.execute("SELECT id FROM medicines WHERE id = ?", (body.medicine_id,)).fetchone()
        if med is None:
            raise HTTPException(404, "Лекарство не найдено")
        conn.execute(
            "INSERT INTO user_medicines (user_id, medicine_id, doses_per_day, "
            "pills_per_pack, started_at) VALUES (?,?,?,?,?)",
            (user_id, body.medicine_id, body.doses_per_day, body.pills_per_pack, now_iso()))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


@app.get("/my-medicines")
def list_my_medicines(user_id: int = Depends(get_current_user)):
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT um.id, um.doses_per_day, um.pills_per_pack, um.started_at, "
            "       m.id AS medicine_id, m.name, m.dosage, m.form "
            "FROM user_medicines um JOIN medicines m ON m.id = um.medicine_id "
            "WHERE um.user_id = ? ORDER BY um.started_at DESC", (user_id,)).fetchall()
    finally:
        conn.close()

    now = datetime.now(timezone.utc)
    result = []
    for r in rows:
        started = datetime.strptime(r["started_at"], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
        days_passed = (now - started).days
        total_days_supply = r["pills_per_pack"] / r["doses_per_day"]
        days_left = max(0, round(total_days_supply - days_passed))
        result.append({
            "id": r["id"], "medicine_id": r["medicine_id"], "name": r["name"],
            "dosage": r["dosage"], "form": r["form"],
            "doses_per_day": r["doses_per_day"], "pills_per_pack": r["pills_per_pack"],
            "days_left": days_left, "need_refill": days_left < 7,
        })
    return result


@app.delete("/my-medicines/{item_id}")
def delete_my_medicine(item_id: int, user_id: int = Depends(get_current_user)):
    conn = get_db()
    try:
        cur = conn.execute(
            "DELETE FROM user_medicines WHERE id = ? AND user_id = ?", (item_id, user_id))
        conn.commit()
    finally:
        conn.close()
    if cur.rowcount == 0:
        raise HTTPException(404, "Запись не найдена")
    return {"ok": True}


# ───────────────────────── Отдача фронтенда ─────────────────────────

@app.get("/")
def index():
    return FileResponse("index.html")

# Отдаём index.html и как статику на случай прямого запроса /index.html
app.mount("/static", StaticFiles(directory=".", html=False), name="static")
