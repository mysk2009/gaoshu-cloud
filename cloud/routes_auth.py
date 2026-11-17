from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from fastapi import HTTPException, Request

from cloud.normalize import normalize_class_code


TOKEN_TTL_HOURS = 12


@dataclass(frozen=True)
class TeacherSession:
    class_code: str
    expires_at: str


def _sign(secret_key: str, payload: str) -> str:
    return hmac.new(
        secret_key.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def create_teacher_token(request: Request, class_code: str) -> tuple[str, str]:
    expires_at = (
        datetime.now() + timedelta(hours=TOKEN_TTL_HOURS)
    ).isoformat(timespec="seconds")
    payload = f"{expires_at}|{class_code}"
    signature = _sign(request.app.state.settings.secret_key, payload)
    return f"{payload}.{signature}", expires_at


def verify_teacher_token(request: Request) -> TeacherSession:
    authorization = request.headers.get("Authorization", "")
    scheme, separator, token = authorization.partition(" ")
    if separator != " " or scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="请重新登录")

    payload, separator, supplied_signature = token.rpartition(".")
    if separator != "." or not payload or not supplied_signature:
        raise HTTPException(status_code=401, detail="请重新登录")

    expires_at, separator, class_code = payload.partition("|")
    if separator != "|" or not expires_at or not class_code or "|" in class_code:
        raise HTTPException(status_code=401, detail="请重新登录")

    try:
        expiry = datetime.fromisoformat(expires_at)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="请重新登录") from exc
    if expiry <= datetime.now():
        raise HTTPException(status_code=401, detail="请重新登录")

    settings = request.app.state.settings
    expected_signature = _sign(settings.secret_key, payload)
    if not secrets.compare_digest(supplied_signature, expected_signature):
        raise HTTPException(status_code=401, detail="请重新登录")

    normalized_class_code = normalize_class_code(class_code)
    if not normalized_class_code:
        raise HTTPException(status_code=401, detail="请重新登录")
    return TeacherSession(
        class_code=normalized_class_code,
        expires_at=expires_at,
    )


def require_teacher_class(request: Request, raw_class_code: object = None) -> str:
    session = verify_teacher_token(request)
    requested_class_code = normalize_class_code(raw_class_code)
    if requested_class_code and requested_class_code != session.class_code:
        raise HTTPException(status_code=403, detail="这不是你名下的班级")
    return session.class_code
