from __future__ import annotations

import json
import secrets
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from cloud.normalize import normalize_class_code
from cloud.routes_auth import require_teacher_class


router = APIRouter(prefix="/api")

QBANK_ACTIONS = {"add", "update", "remove"}


async def _json_object(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="请求体必须是 JSON 对象") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="请求体必须是 JSON 对象")
    return payload


def _require_class_secret(request: Request, raw_class_code: object) -> str:
    class_code = normalize_class_code(raw_class_code)
    database = request.app.state.database
    with database.connection() as connection:
        class_row = database.fetchone(
            connection,
            "SELECT class_secret FROM classes_cloud WHERE class_code = ?",
            (class_code,),
        )
    if class_row is None:
        raise HTTPException(
            status_code=404,
            detail="班级不存在，请核对老师给的班级码",
        )

    provided_secret = request.headers.get("X-Class-Secret", "")
    expected_secret = database.row_value(class_row, "class_secret")
    if not isinstance(expected_secret, str) or not secrets.compare_digest(
        provided_secret, expected_secret
    ):
        raise HTTPException(
            status_code=401,
            detail="班级口令不正确，请找老师确认",
        )
    return class_code


def _teacher_identity(
    database: Any,
    connection: Any,
    class_code: str,
) -> str:
    row = database.fetchone(
        connection,
        "SELECT teacher_name FROM classes_cloud WHERE class_code = ?",
        (class_code,),
    )
    teacher_name = (
        database.row_value(row, "teacher_name") if row is not None else None
    )
    if isinstance(teacher_name, str) and teacher_name.strip():
        return teacher_name.strip()
    return class_code


def _decode_payload(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except Exception:
        return value


@router.post("/qbank/publish")
async def publish_qbank_changes(request: Request) -> dict:
    payload = await _json_object(request)
    raw_class_code = payload.get("class_code")
    if not normalize_class_code(raw_class_code):
        raise HTTPException(status_code=400, detail="班级码不能为空")
    class_code = require_teacher_class(request, raw_class_code)

    changes = payload.get("changes")
    if not isinstance(changes, list) or not changes:
        raise HTTPException(status_code=400, detail="changes 必须是非空数组")
    if len(changes) > 500:
        raise HTTPException(status_code=400, detail="一次最多 500 条")

    normalized_changes: list[tuple[str, str, str | None]] = []
    for change in changes:
        if not isinstance(change, dict):
            raise HTTPException(status_code=400, detail="每条改动必须是对象")
        qid = change.get("qid")
        if not isinstance(qid, str) or not qid.strip():
            raise HTTPException(status_code=400, detail="qid 不能为空")
        action = change.get("action")
        if not isinstance(action, str) or action not in QBANK_ACTIONS:
            raise HTTPException(
                status_code=400,
                detail="action 只能是 add、update 或 remove",
            )
        change_payload = change.get("payload")
        if action != "remove" and not isinstance(change_payload, dict):
            raise HTTPException(
                status_code=400,
                detail="非 remove 改动必须带 payload 对象",
            )
        serialized_payload = (
            json.dumps(change_payload, ensure_ascii=False)
            if action != "remove"
            else None
        )
        normalized_changes.append((qid.strip(), action, serialized_payload))

    now = datetime.now().isoformat(timespec="seconds")
    database = request.app.state.database
    with database.transaction() as connection:
        pushed_by = _teacher_identity(database, connection, class_code)
        for qid, action, serialized_payload in normalized_changes:
            database.execute(
                connection,
                """
                INSERT INTO class_qbank_change
                  (class_code, qid, action, payload, updated_at, pushed_by)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    class_code,
                    qid,
                    action,
                    serialized_payload,
                    now,
                    pushed_by,
                ),
            )
        seq_row = database.fetchone(
            connection,
            """
            SELECT COALESCE(MAX(seq), 0) AS seq_max
            FROM class_qbank_change
            WHERE class_code = ?
            """,
            (class_code,),
        )
        seq_max = int(database.row_value(seq_row, "seq_max") or 0)

    return {
        "ok": True,
        "count": len(normalized_changes),
        "seq_max": seq_max,
    }


@router.get("/qbank/seq")
async def qbank_seq(request: Request, class_code: str = "") -> dict:
    class_code = _require_class_secret(request, class_code)
    database = request.app.state.database
    with database.connection() as connection:
        summary = database.fetchone(
            connection,
            """
            SELECT COALESCE(MAX(seq), 0) AS seq, COUNT(*) AS count
            FROM class_qbank_change
            WHERE class_code = ?
            """,
            (class_code,),
        )
        last_row = database.fetchone(
            connection,
            """
            SELECT updated_at
            FROM class_qbank_change
            WHERE class_code = ?
            ORDER BY seq DESC
            LIMIT 1
            """,
            (class_code,),
        )

    return {
        "ok": True,
        "seq": int(database.row_value(summary, "seq") or 0),
        "count": int(database.row_value(summary, "count") or 0),
        "updated_at": (
            database.row_value(last_row, "updated_at")
            if last_row is not None
            else None
        ),
    }


@router.get("/qbank/changes")
async def qbank_changes(
    request: Request,
    class_code: str = "",
    since: int = 0,
    limit: int = 500,
) -> dict:
    class_code = _require_class_secret(request, class_code)
    effective_limit = max(0, min(limit, 500))
    database = request.app.state.database
    with database.connection() as connection:
        max_row = database.fetchone(
            connection,
            """
            SELECT COALESCE(MAX(seq), 0) AS seq
            FROM class_qbank_change
            WHERE class_code = ?
            """,
            (class_code,),
        )
        rows = database.fetchall(
            connection,
            """
            SELECT seq, qid, action, payload, updated_at
            FROM class_qbank_change
            WHERE class_code = ? AND seq > ?
            ORDER BY seq ASC
            LIMIT ?
            """,
            (class_code, since, effective_limit + 1),
        )

    has_more = len(rows) > effective_limit
    items = [
        {
            "seq": int(database.row_value(row, "seq")),
            "qid": database.row_value(row, "qid"),
            "action": database.row_value(row, "action"),
            "payload": _decode_payload(database.row_value(row, "payload")),
            "updated_at": database.row_value(row, "updated_at"),
        }
        for row in rows[:effective_limit]
    ]
    return {
        "ok": True,
        "seq": int(database.row_value(max_row, "seq") or 0),
        "changes": items,
        "has_more": has_more,
    }


@router.get("/teacher/reports")
async def teacher_reports(request: Request, class_code: str = "") -> dict:
    class_code = require_teacher_class(request, class_code)
    database = request.app.state.database
    with database.connection() as connection:
        rows = database.fetchall(
            connection,
            """
            SELECT id, qid, student_no, real_name, kind, note,
                   created_at, handled
            FROM reports_cloud
            WHERE class_code = ?
            ORDER BY created_at DESC, id DESC
            LIMIT 500
            """,
            (class_code,),
        )
        pending_row = database.fetchone(
            connection,
            """
            SELECT COUNT(*) AS pending
            FROM reports_cloud
            WHERE class_code = ? AND handled = 0
            """,
            (class_code,),
        )

    items = [
        {
            "id": int(database.row_value(row, "id")),
            "qid": database.row_value(row, "qid"),
            "student_no": database.row_value(row, "student_no"),
            "real_name": database.row_value(row, "real_name"),
            "kind": database.row_value(row, "kind"),
            "note": database.row_value(row, "note"),
            "created_at": database.row_value(row, "created_at"),
            "handled": int(database.row_value(row, "handled") or 0),
        }
        for row in rows
    ]
    return {
        "ok": True,
        "items": items,
        "pending": int(database.row_value(pending_row, "pending") or 0),
    }


@router.post("/teacher/reports/handled")
async def mark_teacher_reports_handled(request: Request) -> dict:
    payload = await _json_object(request)
    raw_class_code = payload.get("class_code")
    if not normalize_class_code(raw_class_code):
        raise HTTPException(status_code=400, detail="班级码不能为空")
    class_code = require_teacher_class(request, raw_class_code)

    raw_ids = payload.get("ids")
    if not isinstance(raw_ids, list):
        raise HTTPException(status_code=400, detail="ids 必须是数组")
    ids: list[int] = []
    seen: set[int] = set()
    for report_id in raw_ids:
        if isinstance(report_id, bool) or not isinstance(report_id, int):
            raise HTTPException(status_code=400, detail="ids 只能包含整数")
        if report_id not in seen:
            seen.add(report_id)
            ids.append(report_id)

    database = request.app.state.database
    with database.transaction() as connection:
        handled_by = _teacher_identity(database, connection, class_code)
        updated = 0
        for report_id in ids:
            cursor = database.execute(
                connection,
                """
                UPDATE reports_cloud
                SET handled = 1, handled_by = ?
                WHERE class_code = ? AND id = ? AND handled = 0
                """,
                (handled_by, class_code, report_id),
            )
            updated += max(int(getattr(cursor, "rowcount", 0) or 0), 0)

    return {"ok": True, "updated": updated}
