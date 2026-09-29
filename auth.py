# auth.py — хэширование паролей, сессии и FastAPI-зависимость авторизации.
import hashlib
import hmac
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException, Request

# На обычном сервере/локально храним pharmacy.db рядом с кодом.
# На Vercel (serverless) диск доступен на запись только в /tmp,
# и он не сохраняется между "холодными стартами" функции —
# поэтому там база создаётся заново при каждом старте (см. main.py).
if os.environ.get("VERCEL"):
    DB_PATH = "/tmp/pharmacy.db"
else:
    DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pharmacy.db")
ITERATIONS = 100_000
SESSION_DAYS = 30


def get_db() -> sqlite3.Connection:
    """Новое соединение с БД (строки доступны по имени колонки)."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def now_iso() -> str:
    """Текущее время UTC в формате, который корректно сравнивается как строка."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def new_salt() -> str:
    return secrets.token_hex(16)


def hash_password(password: str, salt: str) -> str:
    """PBKDF2-HMAC-SHA256, 100 000 итераций. Возвращает hex-строку."""
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                             bytes.fromhex(salt), ITERATIONS)
    return dk.hex()


def verify_password(password: str, salt: str, password_hash: str) -> bool:
    """Сравнение за постоянное время (защита от timing-атак)."""
    return hmac.compare_digest(hash_password(password, salt), password_hash)


def create_session(user_id: int) -> str:
    """Создаёт токен, сохраняет в sessions на 30 дней, возвращает токен."""
    token = secrets.token_urlsafe(32)
    created = datetime.now(timezone.utc)
    expires = created + timedelta(days=SESSION_DAYS)
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO sessions (user_id, token, created_at, expires_at) VALUES (?,?,?,?)",
            (user_id, token, created.strftime("%Y-%m-%dT%H:%M:%S"),
             expires.strftime("%Y-%m-%dT%H:%M:%S")))
        conn.commit()
    finally:
        conn.close()
    return token


def token_from_request(request: Request) -> Optional[str]:
    """Достаёт токен из заголовка 'Authorization: Bearer <token>'."""
    header = request.headers.get("Authorization", "")
    parts = header.split(" ", 1)
    if len(parts) == 2 and parts[0].lower() == "bearer" and parts[1].strip():
        return parts[1].strip()
    return None


def get_current_user(request: Request) -> int:
    """FastAPI-зависимость: возвращает user_id или отвечает 401."""
    token = token_from_request(request)
    if not token:
        raise HTTPException(status_code=401, detail="Требуется вход")
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT user_id, expires_at FROM sessions WHERE token = ?", (token,)
        ).fetchone()
        if row is None or row["expires_at"] <= now_iso():
            if row is not None:  # чистим просроченную сессию
                conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
                conn.commit()
            raise HTTPException(status_code=401, detail="Сессия недействительна или истекла")
        return row["user_id"]
    finally:
        conn.close()


def delete_session(token: Optional[str]) -> None:
    """Удаляет сессию (logout)."""
    if not token:
        return
    conn = get_db()
    try:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
        conn.commit()
    finally:
        conn.close()
