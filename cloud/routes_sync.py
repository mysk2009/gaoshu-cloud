from __future__ import annotations

import secrets
from datetime import datetime, timedelta
import threading
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from cloud.normalize import (
    mask_name,
    normalize_class_code,
    normalize_name,
    normalize_student_no,
)


router = APIRouter(prefix="/api")

BINDING_FAILURE_LIMIT = 5
BINDING_FAILURE_WINDOW_SECONDS = 10 * 60
REBIND_LIMIT = 3
REBIND_WINDOW_HOURS = 24
MAX_USE_SECONDS = 86400

# 免费版 Cloud Run 按单实例渲染；多实例部署需把此计数迁移到 Redis。
_binding_failures: dict[str, list[float]] = {}
_binding_failures_lock = threading.Lock()


@router.get("/health")
def health(request: Request):
    database = request.app.state.database
    try:
        with database.connection() as connection:
            database.execute(connection, "SELECT 1").fetchone()
    except Exception:
        return JSONResponse(
            status_code=503,
            content={"ok": False, "db": "down", "backend": database.backend},
        )
    return {"ok": True, "db": "up", "backend": database.backend}


def _optional_text(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _binding_limited(device_uuid: str) -> bool:
    cutoff = time.monotonic() - BINDING_FAILURE_WINDOW_SECONDS
    with _binding_failures_lock:
        timestamps = [
            timestamp
            for timestamp in _binding_failures.get(device_uuid, [])
            if timestamp > cutoff
        ]
        if timestamps:
            _binding_failures[device_uuid] = timestamps
        else:
            _binding_failures.pop(device_uuid, None)
        return len(timestamps) > BINDING_FAILURE_LIMIT


def _record_binding_failure(device_uuid: str) -> None:
    cutoff = time.monotonic() - BINDING_FAILURE_WINDOW_SECONDS
    with _binding_failures_lock:
        timestamps = [
            timestamp
            for timestamp in _binding_failures.get(device_uuid, [])
            if timestamp > cutoff
        ]
        timestamps.append(time.monotonic())
        _binding_failures[device_uuid] = timestamps


def _clear_binding_failures(device_uuid: str) -> None:
    with _binding_failures_lock:
        _binding_failures.pop(device_uuid, None)


def _normalize_correct(value: Any) -> int | None:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int) and value in (0, 1):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in ("1", "true"):
            return 1
        if normalized in ("0", "false"):
            return 0
    return None


def _normalize_use_sec(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        seconds = value
    elif isinstance(value, str):
        normalized = value.strip()
        if not normalized or any(
            character not in "0123456789" for character in normalized
        ):
            return None
        seconds = int(normalized)
    else:
        return None
    if seconds < 0:
        return None
    return min(seconds, MAX_USE_SECONDS)


@router.post("/sync/receive")
async def receive_attempts(request: Request) -> dict:
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="请求体必须是 JSON 对象") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="请求体必须是 JSON 对象")

    class_code = normalize_class_code(payload.get("class_code"))
    raw_device_uuid = payload.get("device_uuid")
    attempts = payload.get("attempts")

    database = request.app.state.database
    with database.transaction() as connection:
        class_row = database.fetchone(
            connection,
            "SELECT class_secret FROM classes_cloud WHERE class_code = ?",
            (class_code,),
        )
        if class_row is None:
            raise HTTPException(status_code=404, detail="班级不存在，请核对老师给的班级码")

        provided_secret = request.headers.get("X-Class-Secret", "")
        expected_secret = database.row_value(class_row, "class_secret")
        if not secrets.compare_digest(provided_secret, expected_secret):
            raise HTTPException(
                status_code=401,
                detail="班级口令不正确，请找老师确认",
            )

        if not isinstance(raw_device_uuid, str) or not raw_device_uuid.strip():
            raise HTTPException(
                status_code=400,
                detail="设备标识不能为空，请刷新页面后重试",
            )
        device_uuid = raw_device_uuid.strip()
        if not isinstance(attempts, list):
            raise HTTPException(status_code=400, detail="attempts 必须是数组")
        if len(attempts) > 5000:
            raise HTTPException(status_code=400, detail="单次提交过多，请分批")

        device_statement = """
            SELECT class_code, student_no
            FROM devices
            WHERE device_uuid = ?
        """
        if database.backend == "postgres":
            # Keep this device's binding stable until the whole sync commits.
            device_statement += " FOR UPDATE"
        device_row = database.fetchone(
            connection,
            device_statement,
            (device_uuid,),
        )
        if device_row is not None:
            existing_class_code = database.row_value(device_row, "class_code")
            if existing_class_code != class_code:
                # The primary key remains (device_uuid, attempt_id) to avoid a data
                # migration; rejecting cross-class reuse before insert keeps a
                # device's attempt IDs from swallowing another class's records.
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"这台设备已在班级 {existing_class_code} 提交过，"
                        "换班请找老师解绑"
                    ),
                )

        student_no = normalize_student_no(payload.get("student_no"))
        real_name = normalize_name(payload.get("real_name"))
        if not student_no or not real_name:
            raise HTTPException(status_code=400, detail="请先绑定学号与姓名")

        roster_statement = """
            SELECT real_name
            FROM roster_cloud
            WHERE class_code = ? AND student_no = ?
        """
        if database.backend == "postgres":
            # Serialize takeover checks for one student across cloud processes.
            roster_statement += " FOR UPDATE"
        roster_row = database.fetchone(
            connection,
            roster_statement,
            (class_code, student_no),
        )
        if roster_row is None:
            _record_binding_failure(device_uuid)
            if _binding_limited(device_uuid):
                raise HTTPException(
                    status_code=429, detail="尝试次数过多，请 10 分钟后再试"
                )
            raise HTTPException(
                status_code=404, detail="学号不在本班名单，请联系老师"
            )

        registered_name = database.row_value(roster_row, "real_name")
        if real_name != registered_name:
            _record_binding_failure(device_uuid)
            if _binding_limited(device_uuid):
                raise HTTPException(
                    status_code=429, detail="尝试次数过多，请 10 分钟后再试"
                )
            raise HTTPException(
                status_code=422,
                detail=f"姓名与该学号登记的不一致（登记：{mask_name(registered_name)}）",
            )

        _clear_binding_failures(device_uuid)
        now = datetime.now().isoformat(timespec="seconds")
        old_student_rows = database.fetchall(
            connection,
            """
            SELECT device_uuid
            FROM devices
            WHERE class_code = ? AND student_no = ? AND device_uuid <> ?
            """,
            (class_code, student_no, device_uuid),
        )
        existing_student_no = (
            database.row_value(device_row, "student_no")
            if device_row is not None
            else None
        )
        switched_student = bool(
            existing_student_no and existing_student_no != student_no
        )
        takeover_operations = len(old_student_rows)
        if takeover_operations:
            cutoff = (
                datetime.now() - timedelta(hours=REBIND_WINDOW_HOURS)
            ).isoformat(timespec="seconds")
            count_row = database.fetchone(
                connection,
                """
                SELECT COUNT(*) AS total
                FROM device_rebind_log
                WHERE class_code = ? AND student_no = ? AND ts >= ?
                  AND reason = ?
                """,
                (class_code, student_no, cutoff, "takeover"),
            )
            rebind_count = int(database.row_value(count_row, "total") or 0)
            if rebind_count + takeover_operations > REBIND_LIMIT:
                raise HTTPException(
                    status_code=429,
                    detail="该学号近期频繁更换设备，请让老师解除绑定",
                )

        for old_row in old_student_rows:
            old_device_uuid = database.row_value(old_row, "device_uuid")
            database.execute(
                connection,
                """
                UPDATE devices
                SET student_no = NULL
                WHERE class_code = ? AND student_no = ?
                  AND device_uuid = ?
                """,
                (class_code, student_no, old_device_uuid),
            )
            database.execute(
                connection,
                """
                INSERT INTO device_rebind_log
                  (class_code, student_no, old_device_uuid,
                   new_device_uuid, reason, ts)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    class_code,
                    student_no,
                    old_device_uuid,
                    device_uuid,
                    "takeover",
                    now,
                ),
            )

        if switched_student:
            database.execute(
                connection,
                """
                UPDATE devices
                SET student_no = NULL
                WHERE device_uuid = ? AND class_code = ?
                  AND student_no = ?
                """,
                (device_uuid, class_code, existing_student_no),
            )
            database.execute(
                connection,
                """
                INSERT INTO device_rebind_log
                  (class_code, student_no, old_device_uuid,
                   new_device_uuid, reason, ts)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    class_code,
                    existing_student_no,
                    device_uuid,
                    device_uuid,
                    "device_switched_student",
                    now,
                ),
            )

        accepted = 0
        skipped = 0
        statement = """
            INSERT INTO attempts_cloud
              (device_uuid, attempt_id, class_code, student_no, qid, correct,
               use_sec, error_type, ts, received_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(device_uuid, attempt_id) DO UPDATE SET
              error_type = CASE
                WHEN COALESCE(attempts_cloud.error_type, '') IN ('', 'none')
                 AND COALESCE(excluded.error_type, '') NOT IN ('', 'none')
                THEN excluded.error_type
                ELSE attempts_cloud.error_type
              END
        """

        for attempt in attempts:
            if not isinstance(attempt, dict):
                skipped += 1
                continue
            qid = attempt.get("qid")
            attempt_id = attempt.get("attempt_id")
            if not isinstance(qid, str) or not qid.strip():
                skipped += 1
                continue
            if not isinstance(attempt_id, str) or not attempt_id.strip():
                skipped += 1
                continue
            correct = _normalize_correct(attempt.get("correct"))
            if correct is None:
                skipped += 1
                continue
            cursor = database.execute(
                connection,
                statement,
                (
                    device_uuid,
                    attempt_id,
                    class_code,
                    student_no,
                    qid,
                    correct,
                    _normalize_use_sec(attempt.get("use_sec")),
                    _optional_text(attempt.get("error_type")),
                    _optional_text(attempt.get("ts")),
                    now,
                ),
            )
            accepted += max(cursor.rowcount, 0)

        if device_row is None:
            database.execute(
                connection,
                """
                INSERT INTO devices
                  (device_uuid, class_code, nickname, student_no, real_name,
                   verified_at, attempts, first_at, last_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    device_uuid,
                    class_code,
                    _optional_text(payload.get("nickname")),
                    student_no,
                    real_name,
                    now,
                    accepted,
                    now,
                    now,
                ),
            )
        else:
            database.execute(
                connection,
                """
                UPDATE devices
                SET class_code = ?, last_at = ?, nickname = ?,
                    student_no = ?, real_name = ?, verified_at = ?,
                    attempts = COALESCE(attempts, 0) + ?
                WHERE device_uuid = ?
                """,
                (
                    class_code,
                    now,
                    _optional_text(payload.get("nickname")),
                    student_no,
                    real_name,
                    now,
                    accepted,
                    device_uuid,
                ),
            )

        total_row = database.fetchone(
            connection,
            "SELECT COUNT(*) AS total FROM attempts_cloud WHERE class_code = ?",
            (class_code,),
        )
        total_received = database.row_value(total_row, "total")

    return {
        "accepted": accepted,
        "duplicated": len(attempts) - accepted - skipped,
        "skipped": skipped,
        "total_received": total_received,
        "server_time": now,
    }
