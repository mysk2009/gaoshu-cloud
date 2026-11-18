from __future__ import annotations

import secrets
from datetime import datetime
import re
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from cloud.normalize import normalize_class_code, normalize_name, normalize_student_no
from cloud.routes_auth import require_teacher_class


router = APIRouter(prefix="/api/admin")

PURGE_TEST_PREFIXES = ("DEMO", "AUD", "PG", "DSH", "TEST", "LV")
PURGE_TABLES = (
    "attempts_cloud",
    "devices",
    "roster_cloud",
    "classes_cloud",
)
PURGE_COUNT_SQL = {
    "attempts_cloud": (
        "SELECT COUNT(*) AS total FROM attempts_cloud WHERE class_code = ?"
    ),
    "devices": "SELECT COUNT(*) AS total FROM devices WHERE class_code = ?",
    "roster_cloud": (
        "SELECT COUNT(*) AS total FROM roster_cloud WHERE class_code = ?"
    ),
    "classes_cloud": (
        "SELECT COUNT(*) AS total FROM classes_cloud WHERE class_code = ?"
    ),
}
PURGE_DELETE_SQL = {
    "attempts_cloud": "DELETE FROM attempts_cloud WHERE class_code = ?",
    "devices": "DELETE FROM devices WHERE class_code = ?",
    "roster_cloud": "DELETE FROM roster_cloud WHERE class_code = ?",
    "classes_cloud": "DELETE FROM classes_cloud WHERE class_code = ?",
}


class PurgeInputError(ValueError):
    """Invalid purge selectors supplied by a CLI or admin request."""


class PurgeSafetyError(ValueError):
    """A purge selected at least one class outside the test prefixes."""

    def __init__(self, class_codes: list[str]):
        self.class_codes = class_codes
        super().__init__(
            "refusing non-test classes: " + ",".join(class_codes)
        )


def _check_admin(request: Request) -> None:
    provided = request.headers.get("X-Admin-Token", "")
    expected = request.app.state.settings.admin_token
    if not expected or not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="管理员口令不正确")


async def _json_object(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="请求体必须是 JSON 对象") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="请求体必须是 JSON 对象")
    return payload


def _normalize_purge_values(values: object, label: str) -> list[str]:
    if values is None:
        return []
    if not isinstance(values, list):
        raise PurgeInputError(f"{label} must be a list")

    normalized: list[str] = []
    for raw_value in values:
        if not isinstance(raw_value, str):
            raise PurgeInputError(f"{label} entries must be strings")
        value = raw_value.strip().upper()
        if not value:
            raise PurgeInputError(f"{label} entries must not be empty")
        if value not in normalized:
            normalized.append(value)
    return normalized


def _match_purge_classes(
    database: Any,
    connection: Any,
    patterns: list[str],
    codes: list[str],
) -> list[str]:
    matched: set[str] = set()
    for pattern in patterns:
        rows = database.fetchall(
            connection,
            """
            SELECT class_code
            FROM classes_cloud
            WHERE class_code LIKE ?
            """,
            (pattern,),
        )
        matched.update(
            str(database.row_value(row, "class_code")) for row in rows
        )
    for class_code in codes:
        row = database.fetchone(
            connection,
            """
            SELECT class_code
            FROM classes_cloud
            WHERE class_code = ?
            """,
            (class_code,),
        )
        if row is not None:
            matched.add(str(database.row_value(row, "class_code")))
    return sorted(matched)


def purge_classes(
    database: Any,
    *,
    patterns: object = None,
    codes: object = None,
    execute: bool = False,
    allow_any: bool = False,
) -> dict[str, Any]:
    normalized_patterns = _normalize_purge_values(patterns, "patterns")
    normalized_codes = _normalize_purge_values(codes, "codes")
    if bool(normalized_patterns) == bool(normalized_codes):
        raise PurgeInputError("provide exactly one of patterns or codes")

    transaction = database.transaction() if execute else database.connection()
    deleted = {table: 0 for table in PURGE_TABLES}
    with transaction as connection:
        matched_classes = _match_purge_classes(
            database,
            connection,
            normalized_patterns,
            normalized_codes,
        )
        unsafe_classes = [
            class_code
            for class_code in matched_classes
            if not class_code.startswith(PURGE_TEST_PREFIXES)
        ]
        if unsafe_classes and not allow_any:
            raise PurgeSafetyError(unsafe_classes)

        for table in PURGE_TABLES:
            for class_code in matched_classes:
                if execute:
                    cursor = database.execute(
                        connection,
                        PURGE_DELETE_SQL[table],
                        (class_code,),
                    )
                    deleted[table] += max(cursor.rowcount, 0)
                else:
                    row = database.fetchone(
                        connection,
                        PURGE_COUNT_SQL[table],
                        (class_code,),
                    )
                    deleted[table] += int(database.row_value(row, "total") or 0)

    return {
        "mode": "DELETE" if execute else "dry-run",
        "matched_classes": matched_classes,
        "deleted": deleted,
    }


def _require_class(
    database: Any, connection: Any, raw_class_code: object
) -> str:
    class_code = normalize_class_code(raw_class_code)
    if not class_code:
        raise HTTPException(status_code=400, detail="班级码不能为空")
    class_row = database.fetchone(
        connection,
        "SELECT 1 FROM classes_cloud WHERE class_code = ?",
        (class_code,),
    )
    if class_row is None:
        raise HTTPException(status_code=404, detail="班级不存在，请核对老师给的班级码")
    return class_code


def _normalize_roster_rows(
    raw_rows: object, line_numbers: list[int] | None = None
) -> list[tuple[int, str, str]]:
    if not isinstance(raw_rows, list) or not raw_rows:
        raise HTTPException(status_code=400, detail="rows 必须是非空数组")

    rows: list[tuple[int, str, str]] = []
    for index, raw_row in enumerate(raw_rows):
        line_number = line_numbers[index] if line_numbers else index + 1
        if not isinstance(raw_row, dict):
            raise HTTPException(
                status_code=400, detail=f"第 {line_number} 行格式错误"
            )
        student_no = normalize_student_no(raw_row.get("student_no"))
        if not student_no:
            raise HTTPException(
                status_code=400, detail=f"第 {line_number} 行缺少 student_no"
            )
        real_name = normalize_name(raw_row.get("real_name"))
        if not real_name:
            raise HTTPException(
                status_code=400, detail=f"第 {line_number} 行缺少 real_name"
            )
        rows.append((line_number, student_no, real_name))
    return rows


def _import_roster_rows(
    database: Any,
    class_code: str,
    rows: list[tuple[int, str, str]],
) -> dict:
    imported = 0
    updated = 0
    created_at = datetime.now().isoformat(timespec="seconds")

    with database.transaction() as connection:
        for _, student_no, real_name in rows:
            existing = database.fetchone(
                connection,
                """
                SELECT 1
                FROM roster_cloud
                WHERE class_code = ? AND student_no = ?
                """,
                (class_code, student_no),
            )
            if existing is None:
                database.execute(
                    connection,
                    """
                    INSERT INTO roster_cloud
                      (class_code, student_no, real_name, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (class_code, student_no, real_name, created_at),
                )
                imported += 1
            else:
                database.execute(
                    connection,
                    """
                    UPDATE roster_cloud
                    SET real_name = ?
                    WHERE class_code = ? AND student_no = ?
                    """,
                    (real_name, class_code, student_no),
                )
                updated += 1

        total_row = database.fetchone(
            connection,
            """
            SELECT COUNT(*) AS total
            FROM roster_cloud
            WHERE class_code = ?
            """,
            (class_code,),
        )
        total = database.row_value(total_row, "total")

    return {
        "ok": True,
        "imported": imported,
        "updated": updated,
        "total": total,
    }


def _parse_paste_rows(text: str) -> list[tuple[int, str, str]]:
    rows: list[tuple[int, str, str]] = []
    first_content_seen = False

    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        if "\t" in line:
            parts = line.split("\t", 1)
        elif "," in line:
            parts = line.split(",", 1)
        else:
            parts = re.split(r"[ \u3000]{2,}", line, maxsplit=1)

        if not first_content_seen:
            first_content_seen = True
            if "学号" in line or "姓名" in line:
                continue

        rows.append(
            (
                line_number,
                parts[0].strip() if parts else "",
                parts[1].strip() if len(parts) > 1 else "",
            )
        )

    return rows


# POST /api/admin/classes 是唯一保留的运维总口令入口。
@router.post("/classes")
async def create_class(request: Request) -> dict:
    _check_admin(request)

    payload = await _json_object(request)

    class_secret = payload.get("class_secret")
    class_admin_secret = payload.get("class_admin_secret")
    class_code = normalize_class_code(payload.get("class_code"))
    if not class_code or not isinstance(class_secret, str) or not class_secret:
        raise HTTPException(status_code=400, detail="班级码和班级口令不能为空")
    if class_admin_secret is not None:
        if not isinstance(class_admin_secret, str):
            raise HTTPException(status_code=400, detail="管理口令至少 6 位")
        class_admin_secret = class_admin_secret.strip()
        if len(class_admin_secret) < 6:
            raise HTTPException(status_code=400, detail="管理口令至少 6 位")
    else:
        # 运维建班也必须有管理口令：enter 不再回落到运维总口令，
        # 没有管理口令的班谁都进不去，所以这里自动生成并返回给建班的人。
        class_admin_secret = "".join(
            secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(8)
        )

    database = request.app.state.database
    created_at = datetime.now().isoformat(timespec="seconds")
    with database.transaction() as connection:
        exists = database.fetchone(
            connection,
            "SELECT 1 FROM classes_cloud WHERE class_code = ?",
            (class_code,),
        )
        if exists is None:
            database.execute(
                connection,
                """
                INSERT INTO classes_cloud
                  (class_code, class_name, teacher_name, class_admin_secret,
                   class_secret, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    class_code,
                    payload.get("class_name"),
                    payload.get("teacher_name"),
                    class_admin_secret,
                    class_secret,
                    created_at,
                ),
            )
            created = True
        else:
            created = False

    return {
        "ok": True,
        "class_code": class_code,
        "created": created,
        "class_admin_secret": class_admin_secret if created else None,
    }


@router.post("/classes/purge")
async def purge_admin_classes(request: Request) -> dict:
    _check_admin(request)
    payload = await _json_object(request)

    dry_run = payload.get("dry_run", True)
    if not isinstance(dry_run, bool):
        raise HTTPException(status_code=400, detail="dry_run 必须是布尔值")
    if not dry_run and payload.get("confirm") != "DELETE":
        raise HTTPException(
            status_code=400,
            detail='真删必须提供 confirm="DELETE"',
        )
    allow_any = payload.get("allow_any", False)
    if not isinstance(allow_any, bool):
        raise HTTPException(status_code=400, detail="allow_any 必须是布尔值")

    try:
        return purge_classes(
            request.app.state.database,
            patterns=payload.get("patterns"),
            codes=payload.get("codes"),
            execute=not dry_run,
            allow_any=allow_any,
        )
    except PurgeSafetyError as exc:
        raise HTTPException(
            status_code=400,
            detail=(
                "匹配到非测试班："
                + ",".join(exc.class_codes)
                + "；如确认要删，请传 allow_any=true"
            ),
        ) from exc
    except PurgeInputError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/classes", include_in_schema=False)
def list_classes_removed() -> dict:
    raise HTTPException(status_code=404, detail="接口不存在")


@router.post("/classes/reset-secret")
async def reset_class_admin_secret(request: Request) -> dict:
    """给"还没设管理口令"的老班补一个（enter 提示里承诺的重置入口）。"""
    _check_admin(request)
    payload = await _json_object(request)
    class_code = normalize_class_code(payload.get("class_code"))
    admin_secret = payload.get("admin_secret")
    if admin_secret is not None:
        if not isinstance(admin_secret, str):
            raise HTTPException(status_code=400, detail="管理口令至少 6 位")
        admin_secret = admin_secret.strip()
        if len(admin_secret) < 6:
            raise HTTPException(status_code=400, detail="管理口令至少 6 位")
    else:
        admin_secret = "".join(
            secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(8)
        )

    database = request.app.state.database
    with database.transaction() as connection:
        exists = database.fetchone(
            connection,
            "SELECT 1 FROM classes_cloud WHERE class_code = ?",
            (class_code,),
        )
        if exists is None:
            raise HTTPException(status_code=404, detail="班级不存在")
        database.execute(
            connection,
            "UPDATE classes_cloud SET class_admin_secret = ? WHERE class_code = ?",
            (admin_secret, class_code),
        )
    return {"ok": True, "class_code": class_code, "class_admin_secret": admin_secret}


@router.post("/roster")
async def import_roster(request: Request) -> dict:
    payload = await _json_object(request)
    database = request.app.state.database
    teacher_class_code = require_teacher_class(
        request, payload.get("class_code")
    )

    with database.transaction() as connection:
        class_code = _require_class(database, connection, teacher_class_code)
    rows = _normalize_roster_rows(payload.get("rows"))
    return _import_roster_rows(database, class_code, rows)


@router.post("/roster/paste")
async def import_roster_paste(request: Request) -> dict:
    payload = await _json_object(request)
    text = payload.get("text")
    if not isinstance(text, str):
        raise HTTPException(status_code=400, detail="text 必须是字符串")

    database = request.app.state.database
    teacher_class_code = require_teacher_class(
        request, payload.get("class_code")
    )
    with database.transaction() as connection:
        class_code = _require_class(database, connection, teacher_class_code)
    raw_rows = _parse_paste_rows(text)
    rows = _normalize_roster_rows(
        [
            {"student_no": student_no, "real_name": real_name}
            for _, student_no, real_name in raw_rows
        ],
        [line_number for line_number, _, _ in raw_rows],
    )
    return _import_roster_rows(database, class_code, rows)


@router.get("/roster")
def get_roster(request: Request, class_code: str = "") -> dict:
    database = request.app.state.database
    teacher_class_code = require_teacher_class(request, class_code)

    with database.connection() as connection:
        normalized_class_code = _require_class(
            database, connection, teacher_class_code
        )
        rows = database.fetchall(
            connection,
            """
            SELECT
              roster_cloud.student_no,
              roster_cloud.real_name,
              (
                SELECT devices.device_uuid
                FROM devices
                WHERE devices.class_code = roster_cloud.class_code
                  AND devices.student_no = roster_cloud.student_no
                  AND devices.student_no IS NOT NULL
                ORDER BY devices.verified_at DESC
                LIMIT 1
              ) AS device_uuid,
              (
                SELECT devices.verified_at
                FROM devices
                WHERE devices.class_code = roster_cloud.class_code
                  AND devices.student_no = roster_cloud.student_no
                  AND devices.student_no IS NOT NULL
                ORDER BY devices.verified_at DESC
                LIMIT 1
              ) AS verified_at
            FROM roster_cloud
            WHERE roster_cloud.class_code = ?
            ORDER BY roster_cloud.student_no
            """,
            (normalized_class_code,),
        )

    return {
        "class_code": normalized_class_code,
        "total": len(rows),
        "rows": [
            {
                "student_no": database.row_value(row, "student_no"),
                "real_name": database.row_value(row, "real_name"),
                "device_uuid": database.row_value(row, "device_uuid"),
                "verified_at": database.row_value(row, "verified_at"),
            }
            for row in rows
        ],
    }


@router.post("/roster/unbind")
async def unbind_roster_student(request: Request) -> dict:
    payload = await _json_object(request)
    database = request.app.state.database
    teacher_class_code = require_teacher_class(
        request, payload.get("class_code")
    )

    with database.transaction() as connection:
        class_code = _require_class(database, connection, teacher_class_code)
        student_no = normalize_student_no(payload.get("student_no"))
        if not student_no:
            raise HTTPException(status_code=400, detail="学号不能为空")
        cursor = database.execute(
            connection,
            """
            UPDATE devices
            SET student_no = NULL,
                real_name = NULL,
                verified_at = NULL
            WHERE class_code = ? AND student_no = ?
            """,
            (class_code, student_no),
        )
        unbound = max(cursor.rowcount, 0)

    return {"ok": True, "unbound": unbound}
