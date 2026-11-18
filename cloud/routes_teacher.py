from __future__ import annotations

import re
import secrets
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from cloud.normalize import normalize_class_code
from cloud.routes_auth import create_teacher_token


router = APIRouter()

STATIC_DIR = Path(__file__).resolve().parent / "static"
CLASS_CODE_PATTERN = re.compile(r"^[A-Z0-9]{4,12}$")
ADMIN_FAILURE_LIMIT = 10
ADMIN_FAILURE_WINDOW_SECONDS = 10 * 60

# 当前部署按单实例运行；若扩展到多实例，应把此计数迁移到共享缓存。
_admin_failures: dict[str, list[float]] = {}
_admin_failures_lock = threading.Lock()


async def _json_object(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="请求体必须是 JSON 对象") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="请求体必须是 JSON 对象")
    return payload


def _normalized_secret(value: Any) -> str:
    if not isinstance(value, str):
        raise HTTPException(status_code=400, detail="口令至少 4 位")
    secret = value.strip()
    if len(secret) < 4:
        raise HTTPException(status_code=400, detail="口令至少 4 位")
    if len(secret) > 32:
        raise HTTPException(status_code=400, detail="口令最多 32 位")
    return secret


def _request_secret(payload: dict[str, Any], fallback: str = "admin_secret") -> Any:
    if "secret" in payload:
        return payload.get("secret")
    return payload.get(fallback)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _record_admin_failure(client_ip: str) -> bool:
    cutoff = time.monotonic() - ADMIN_FAILURE_WINDOW_SECONDS
    with _admin_failures_lock:
        timestamps = [
            timestamp
            for timestamp in _admin_failures.get(client_ip, [])
            if timestamp > cutoff
        ]
        timestamps.append(time.monotonic())
        _admin_failures[client_ip] = timestamps
        return len(timestamps) >= ADMIN_FAILURE_LIMIT


def _clear_admin_failures(client_ip: str) -> None:
    """登录成功后清零，避免"刚才手滑几次"把之后整段窗口都锁死。"""
    with _admin_failures_lock:
        _admin_failures.pop(client_ip, None)


@router.get("/teacher")
def teacher_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "teacher.html", media_type="text/html")


@router.post("/api/teacher/create")
async def create_teacher_class(request: Request) -> dict:
    payload = await _json_object(request)
    class_code = normalize_class_code(payload.get("class_code"))
    secret = _normalized_secret(_request_secret(payload))
    class_name = payload.get("class_name")
    teacher_name = payload.get("teacher_name")

    if not class_code or not CLASS_CODE_PATTERN.fullmatch(class_code):
        raise HTTPException(
            status_code=400,
            detail="班级码必须是 4–12 位大写字母或数字",
        )
    if not isinstance(class_name, str) or not class_name.strip():
        raise HTTPException(status_code=400, detail="班级名不能为空")
    if not isinstance(teacher_name, str) or not teacher_name.strip():
        raise HTTPException(status_code=400, detail="你的名字不能为空")

    created_at = datetime.now().isoformat(timespec="seconds")
    database = request.app.state.database
    with database.transaction() as connection:
        cursor = database.execute(
            connection,
            """
            INSERT INTO classes_cloud
              (class_code, class_name, teacher_name, class_admin_secret,
               class_secret, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(class_code) DO NOTHING
            """,
            (
                class_code,
                class_name.strip(),
                teacher_name.strip(),
                secret,
                secret,
                created_at,
            ),
        )
        if cursor.rowcount == 0:
            raise HTTPException(
                status_code=409,
                detail="这个班级码已经有人用了：要么换一个班级码，要么用原来的口令进这个班",
            )

    return {
        "ok": True,
        "class_code": class_code,
        "class_name": class_name.strip(),
    }


@router.post("/api/teacher/enter")
async def enter_teacher_class(request: Request) -> dict:
    payload = await _json_object(request)
    class_code = normalize_class_code(payload.get("class_code"))
    secret = _normalized_secret(_request_secret(payload))

    if not class_code:
        raise HTTPException(status_code=400, detail="班级码不能为空")

    database = request.app.state.database
    with database.connection() as connection:
        row = database.fetchone(
            connection,
            """
            SELECT class_name, teacher_name, class_secret
            FROM classes_cloud
            WHERE class_code = ?
            """,
            (class_code,),
        )

    if row is None:
        raise HTTPException(status_code=404, detail="这个班级码还没人建过")

    stored_secret = database.row_value(row, "class_secret")
    authenticated = isinstance(stored_secret, str) and secrets.compare_digest(
        secret, stored_secret
    )

    if not authenticated:
        limited = _record_admin_failure(_client_ip(request))
        if limited:
            raise HTTPException(
                status_code=429,
                detail="尝试次数过多，请 10 分钟后再试",
            )
        raise HTTPException(status_code=401, detail="口令不对")

    _clear_admin_failures(_client_ip(request))

    token, expires_at = create_teacher_token(request, class_code)
    return {
        "ok": True,
        "token": token,
        "expires_at": expires_at,
        "class_code": class_code,
        "class_name": database.row_value(row, "class_name"),
        "teacher_name": database.row_value(row, "teacher_name"),
    }


@router.post("/api/teacher/reset_secret")
async def reset_teacher_secret(request: Request) -> dict:
    payload = await _json_object(request)
    class_code = normalize_class_code(payload.get("class_code"))
    old_secret = payload.get("old_secret")
    if old_secret is None:
        old_secret = payload.get("secret")
    new_secret = _normalized_secret(payload.get("new_secret"))

    if not class_code:
        raise HTTPException(status_code=400, detail="班级码不能为空")

    database = request.app.state.database
    with database.transaction() as connection:
        row = database.fetchone(
            connection,
            "SELECT class_secret FROM classes_cloud WHERE class_code = ?",
            (class_code,),
        )
        if row is None:
            raise HTTPException(status_code=404, detail="这个班级码还没人建过")

        stored_secret = database.row_value(row, "class_secret")
        normalized_old = old_secret.strip() if isinstance(old_secret, str) else ""
        if not isinstance(stored_secret, str) or not secrets.compare_digest(
            normalized_old, stored_secret
        ):
            limited = _record_admin_failure(_client_ip(request))
            if limited:
                raise HTTPException(
                    status_code=429,
                    detail="尝试次数过多，请 10 分钟后再试",
                )
            raise HTTPException(status_code=401, detail="原口令不对")

        database.execute(
            connection,
            """
            UPDATE classes_cloud
            SET class_secret = ?, class_admin_secret = ?
            WHERE class_code = ?
            """,
            (new_secret, new_secret, class_code),
        )

    _clear_admin_failures(_client_ip(request))
    return {"ok": True, "class_code": class_code}


@router.post("/api/teacher/delete_class")
async def delete_teacher_class(request: Request) -> dict:
    payload = await _json_object(request)
    class_code = normalize_class_code(payload.get("class_code"))
    secret = _request_secret(payload, fallback="secret")

    if not class_code:
        raise HTTPException(status_code=400, detail="班级码不能为空")

    database = request.app.state.database
    with database.transaction() as connection:
        row = database.fetchone(
            connection,
            "SELECT class_secret FROM classes_cloud WHERE class_code = ?",
            (class_code,),
        )
        if row is None:
            raise HTTPException(status_code=404, detail="这个班级码还没人建过")

        stored_secret = database.row_value(row, "class_secret")
        normalized_secret = secret.strip() if isinstance(secret, str) else ""
        if not isinstance(stored_secret, str) or not secrets.compare_digest(
            normalized_secret, stored_secret
        ):
            limited = _record_admin_failure(_client_ip(request))
            if limited:
                raise HTTPException(
                    status_code=429,
                    detail="尝试次数过多，请 10 分钟后再试",
                )
            raise HTTPException(
                status_code=401,
                detail="口令不对，删班需要班级口令",
            )

        deleted: dict[str, int] = {}
        for name, table in (
            ("attempts", "attempts_cloud"),
            ("devices", "devices"),
            ("roster", "roster_cloud"),
            ("class_rows", "classes_cloud"),
        ):
            cursor = database.execute(
                connection,
                f"DELETE FROM {table} WHERE class_code = ?",
                (class_code,),
            )
            deleted[name] = max(int(getattr(cursor, "rowcount", 0) or 0), 0)

    _clear_admin_failures(_client_ip(request))
    return {"ok": True, "deleted": deleted}
