from __future__ import annotations

from datetime import datetime
import math
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from cloud.normalize import normalize_class_code
from cloud.routes_auth import require_teacher_class


router = APIRouter()

STATIC_DIR = Path(__file__).resolve().parent / "static"

ERROR_LABELS = {
    "concept": "概念",
    "calc": "计算",
    "careless": "看错",
}

RESOLVED_ATTEMPTS_CTE = """
WITH resolved_attempts AS (
  SELECT
    a.device_uuid,
    a.attempt_id,
    a.class_code,
    a.qid,
    a.correct,
    a.use_sec,
    a.error_type,
    a.ts,
    a.received_at,
    COALESCE(NULLIF(a.student_no, ''), d.student_no) AS student_no
  FROM attempts_cloud AS a
  LEFT JOIN devices AS d
    ON d.device_uuid = a.device_uuid
   AND d.class_code = a.class_code
)
"""


def _require_class(
    database: Any, connection: Any, raw_class_code: object
) -> tuple[str, str | None]:
    class_code = normalize_class_code(raw_class_code)
    if not class_code:
        raise HTTPException(status_code=400, detail="班级码不能为空")
    row = database.fetchone(
        connection,
        """
        SELECT class_name
        FROM classes_cloud
        WHERE class_code = ?
        """,
        (class_code,),
    )
    if row is None:
        raise HTTPException(status_code=404, detail="班级不存在，请核对老师给的班级码")
    return class_code, database.row_value(row, "class_name")


def _round_rate(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 4)


def _percent(rate: float) -> int:
    return math.floor(rate * 100 + 0.5)


def _dominant_error(
    errors: dict[str, int],
) -> tuple[str, int] | None:
    if not errors:
        return None
    return sorted(errors.items(), key=lambda item: (-item[1], item[0]))[0]


def _missing_callout(missing: list[dict[str, Any]]) -> str:
    return "、".join(
        str(student["real_name"] or student["student_no"]) for student in missing
    )


def _build_headline(roster_count: int, submitted: int, missing: list[dict[str, Any]]) -> str:
    headline = f"名单 {roster_count} 人，{submitted} 人已交"
    if not missing:
        return headline

    names = [
        str(student["real_name"] or student["student_no"]) for student in missing
    ]
    if len(names) <= 3:
        callout = "、".join(names)
    else:
        callout = f"{'、'.join(names[:3])}等 {len(names)} 人"
    return f"{headline}，{callout}还没交"


def _build_next_action(weak_top: list[dict[str, Any]], missing_count: int) -> str:
    if weak_top:
        action = f"建议这节课先讲下面 {min(3, len(weak_top))} 道题"
        if missing_count:
            action += f"，再让没交的 {missing_count} 人补上练习"
        return action
    if missing_count:
        return f"这节课没有明显的共性错题，先让没交的 {missing_count} 人补上练习"
    return "这个班目前没有明显共性错题，可以按进度往下讲"


def _build_question_reason(question: dict[str, Any]) -> str:
    wrong_devices = int(question["wrong_devices"])
    devices = int(question["devices"])
    if devices > 0 and wrong_devices == devices:
        prefix = f"{wrong_devices} 人全做错"
    else:
        prefix = f"{wrong_devices} 人做错"

    dominant = _dominant_error(question.get("error_types", {}))
    if dominant is not None:
        error_type, _count = dominant
        label = ERROR_LABELS.get(error_type, "其他")
        # 错人数在单独一列里已经有了，这里只说错在哪，避免重复数字导致折行
        if devices > 0 and wrong_devices == devices:
            return f"全都错在{label}"
        return f"多错在{label}"
    rate = _round_rate(wrong_devices, devices)
    percentage = _percent(rate) if rate is not None else 0
    if devices > 0 and wrong_devices == devices:
        return "全都做错（没标错因）"
    return f"部分做错（错误率 {percentage}%）"


def _build_student_advice(student: dict[str, Any]) -> str:
    """一句话建议。正确率已有单独一列，这里不重复数字，只讲该干什么。"""
    if not student["submitted"]:
        return "还没交"
    rate = student["correct_rate"]
    if rate is not None and rate < 0.6:
        return "建议补基础"
    dominant = _dominant_error(student.get("_error_counts", {}))
    if dominant is not None:
        label = ERROR_LABELS.get(dominant[0], "其他")
        return f"{label}要留意"
    return "保持住"


def build_board_data(database: Any, raw_class_code: object) -> dict:
    with database.connection() as connection:
        class_code, class_name = _require_class(
            database, connection, raw_class_code
        )
        roster_rows = database.fetchall(
            connection,
            """
            SELECT student_no, real_name
            FROM roster_cloud
            WHERE class_code = ?
            ORDER BY student_no
            """,
            (class_code,),
        )
        student_rows = database.fetchall(
            connection,
            RESOLVED_ATTEMPTS_CTE
            + """
            SELECT
              r.student_no,
              r.real_name,
              (
                SELECT d.device_uuid
                FROM devices AS d
                WHERE d.class_code = r.class_code
                  AND d.student_no = r.student_no
                  AND d.student_no IS NOT NULL
                ORDER BY d.verified_at DESC
                LIMIT 1
              ) AS device_uuid,
              COUNT(a.attempt_id) AS attempt_count,
              COALESCE(SUM(CASE WHEN a.correct = 1 THEN 1 ELSE 0 END), 0)
                AS correct_count,
              MIN(COALESCE(a.ts, a.received_at)) AS first_at,
              MAX(COALESCE(a.ts, a.received_at)) AS last_at
            FROM roster_cloud AS r
            LEFT JOIN resolved_attempts AS a
              ON a.class_code = r.class_code
             AND a.student_no = r.student_no
            WHERE r.class_code = ?
            GROUP BY r.student_no, r.real_name
            ORDER BY r.student_no
            """,
            (class_code,),
        )
        error_rows = database.fetchall(
            connection,
            RESOLVED_ATTEMPTS_CTE
            + """
            SELECT
              r.student_no,
              a.error_type,
              COUNT(DISTINCT a.device_uuid) AS error_count
            FROM resolved_attempts AS a
            JOIN roster_cloud AS r
              ON r.class_code = a.class_code
             AND r.student_no = a.student_no
            WHERE a.class_code = ?
              AND a.error_type IS NOT NULL
              AND a.error_type <> ''
              AND a.error_type <> 'none'
            GROUP BY r.student_no, a.error_type
            """,
            (class_code,),
        )
        question_rows = database.fetchall(
            connection,
            RESOLVED_ATTEMPTS_CTE
            + """
            SELECT
              a.qid,
              COUNT(DISTINCT a.device_uuid) AS devices,
              COUNT(DISTINCT CASE
                WHEN a.correct = 0 THEN a.device_uuid
              END) AS wrong_devices,
              AVG(CASE
                WHEN a.use_sec IS NOT NULL THEN a.use_sec
              END) AS avg_use_sec
            FROM resolved_attempts AS a
            JOIN roster_cloud AS r
              ON r.class_code = a.class_code
             AND r.student_no = a.student_no
            WHERE a.class_code = ?
            GROUP BY a.qid
            """,
            (class_code,),
        )
        question_error_rows = database.fetchall(
            connection,
            RESOLVED_ATTEMPTS_CTE
            + """
            SELECT
              a.qid,
              a.error_type,
              COUNT(DISTINCT a.device_uuid) AS error_count
            FROM resolved_attempts AS a
            JOIN roster_cloud AS r
              ON r.class_code = a.class_code
             AND r.student_no = a.student_no
            WHERE a.class_code = ?
              AND a.correct = 0
              AND a.error_type IS NOT NULL
              AND a.error_type <> ''
              AND a.error_type <> 'none'
            GROUP BY a.qid, a.error_type
            """,
            (class_code,),
        )
        unbound_rows = database.fetchall(
            connection,
            """
            SELECT device_uuid, nickname, attempts, last_at
            FROM devices
            WHERE class_code = ?
              AND student_no IS NULL
              AND COALESCE(attempts, 0) > 0
            ORDER BY last_at DESC, device_uuid
            """,
            (class_code,),
        )
        bound_totals = database.fetchone(
            connection,
            RESOLVED_ATTEMPTS_CTE
            + """
            SELECT
              COUNT(a.attempt_id) AS attempts,
              COUNT(DISTINCT a.device_uuid) AS devices
            FROM resolved_attempts AS a
            JOIN roster_cloud AS r
              ON r.class_code = a.class_code
             AND r.student_no = a.student_no
            WHERE a.class_code = ?
            """,
            (class_code,),
        )

    error_counts: dict[str, dict[str, int]] = {}
    for row in error_rows:
        student_no = str(database.row_value(row, "student_no"))
        error_type = str(database.row_value(row, "error_type"))
        error_counts.setdefault(student_no, {})[error_type] = int(
            database.row_value(row, "error_count")
        )

    student_aggregates: dict[str, dict[str, Any]] = {}
    for row in student_rows:
        student_no = str(database.row_value(row, "student_no"))
        attempt_count = int(database.row_value(row, "attempt_count") or 0)
        correct_count = int(database.row_value(row, "correct_count") or 0)
        aggregate = student_aggregates.setdefault(
            student_no,
            {
                "student_no": student_no,
                "real_name": database.row_value(row, "real_name"),
                "submitted": False,
                "attempt_count": 0,
                "correct_count": 0,
                "first_at": None,
                "last_at": None,
                "device_uuid": None,
            },
        )
        aggregate["attempt_count"] += attempt_count
        aggregate["correct_count"] += correct_count
        if attempt_count > 0:
            aggregate["submitted"] = True
        if database.row_value(row, "device_uuid"):
            aggregate["device_uuid"] = database.row_value(row, "device_uuid")
        first_at = database.row_value(row, "first_at")
        last_at = database.row_value(row, "last_at")
        if first_at and (
            aggregate["first_at"] is None or first_at < aggregate["first_at"]
        ):
            aggregate["first_at"] = first_at
        if last_at and (
            aggregate["last_at"] is None or last_at > aggregate["last_at"]
        ):
            aggregate["last_at"] = last_at

    students: list[dict[str, Any]] = []
    for roster_row in roster_rows:
        student_no = str(database.row_value(roster_row, "student_no"))
        aggregate = student_aggregates.get(student_no)
        if aggregate is None:
            aggregate = {
                "student_no": student_no,
                "real_name": database.row_value(roster_row, "real_name"),
                "submitted": False,
                "attempt_count": 0,
                "correct_count": 0,
                "first_at": None,
                "last_at": None,
                "device_uuid": None,
            }
        aggregate["correct_rate"] = _round_rate(
            aggregate["correct_count"], aggregate["attempt_count"]
        )
        student_errors = error_counts.get(student_no, {})
        dominant_student_error = _dominant_error(student_errors)
        aggregate["error_top"] = (
            dominant_student_error[0] if dominant_student_error else None
        )
        aggregate["_error_counts"] = student_errors
        students.append(aggregate)

    question_errors: dict[str, dict[str, int]] = {}
    for row in question_error_rows:
        qid = str(database.row_value(row, "qid"))
        error_type = str(database.row_value(row, "error_type"))
        question_errors.setdefault(qid, {})[error_type] = int(
            database.row_value(row, "error_count")
        )

    questions: list[dict[str, Any]] = []
    for row in question_rows:
        devices = int(database.row_value(row, "devices") or 0)
        wrong_devices = int(database.row_value(row, "wrong_devices") or 0)
        qid = str(database.row_value(row, "qid"))
        average = database.row_value(row, "avg_use_sec")
        questions.append(
            {
                "qid": qid,
                "wrong_devices": wrong_devices,
                "devices": devices,
                "wrong_rate": _round_rate(wrong_devices, devices),
                "avg_use_sec": (
                    math.floor(float(average) + 0.5)
                    if average is not None
                    else None
                ),
                "error_types": dict(sorted(question_errors.get(qid, {}).items())),
            }
        )
    questions.sort(
        key=lambda item: (
            -item["wrong_devices"],
            -(item["wrong_rate"] or 0),
            item["qid"],
        )
    )

    weak_questions = [
        item for item in questions if item["wrong_devices"] > 0
    ]
    weak_top = [
        {
            "qid": item["qid"],
            "wrong_devices": item["wrong_devices"],
            "devices": item["devices"],
            "wrong_rate": item["wrong_rate"],
            "avg_use_sec": item["avg_use_sec"],
            "reason": _build_question_reason(item),
        }
        for item in weak_questions[:5]
    ]
    review_list = [
        {
            "rank": rank,
            "qid": item["qid"],
            "wrong_devices": item["wrong_devices"],
            "devices": item["devices"],
            "wrong_rate": item["wrong_rate"],
            "error_types": item["error_types"],
            "avg_use_sec": item["avg_use_sec"],
            "reason": _build_question_reason(item),
        }
        for rank, item in enumerate(questions[:8], start=1)
    ]
    missing = [
        {
            "student_no": student["student_no"],
            "real_name": student["real_name"],
        }
        for student in students
        if not student["submitted"]
    ]
    for student in students:
        student["advice"] = _build_student_advice(student)
        student.pop("_error_counts", None)

    roster_count = len(roster_rows)
    submitted_count = sum(1 for student in students if student["submitted"])
    missing_count = len(missing)

    return {
        "class_code": class_code,
        "class_name": class_name,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "totals": {
            "roster": roster_count,
            "submitted": submitted_count,
            "missing": missing_count,
            "attempts": int(database.row_value(bound_totals, "attempts") or 0),
            "devices": int(database.row_value(bound_totals, "devices") or 0),
        },
        "missing": missing,
        "unbound_devices": [
            {
                "device_uuid": database.row_value(row, "device_uuid"),
                "nickname": database.row_value(row, "nickname"),
                "attempts": int(database.row_value(row, "attempts") or 0),
                "last_at": database.row_value(row, "last_at"),
            }
            for row in unbound_rows
        ],
        "students": students,
        "weak_top": weak_top,
        "review_list": review_list,
        "headline": _build_headline(roster_count, submitted_count, missing),
        "next_action": _build_next_action(weak_top, missing_count),
        "missing_callout": _missing_callout(missing),
    }


@router.get("/board")
def board_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "board.html", media_type="text/html")


@router.get("/api/board")
def teacher_board(request: Request, class_code: str = "") -> dict:
    scoped_class_code = require_teacher_class(request, class_code)
    return build_board_data(request.app.state.database, scoped_class_code)
