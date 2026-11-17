from __future__ import annotations

import json
import sqlite3
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
BASE_URL = "http://127.0.0.1:8099"
ADMIN_HEADERS = {
    "Content-Type": "application/json",
    "X-Admin-Token": "test-admin",
}
CLASS_HEADERS = {
    "Content-Type": "application/json",
    "X-Class-Secret": "abc123",
}
CLASS_CODE = "F34F05"


def request(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, str]:
    body = None
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request_headers = headers or {}
    req = urllib.request.Request(
        BASE_URL + path,
        data=body,
        headers=request_headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


def emit(
    label: str,
    method: str,
    path: str,
    payload_summary: str,
    status: int,
    body: str,
) -> None:
    print(f"[{label}] {method} {path} {payload_summary}")
    print(f"status={status}")
    print(f"body={body}")


def expect(
    label: str,
    method: str,
    path: str,
    payload: dict[str, Any] | None,
    headers: dict[str, str] | None,
    status_code: int,
    payload_summary: str = "",
) -> tuple[int, str]:
    status, body = request(method, path, payload, headers)
    emit(label, method, path, payload_summary, status, body)
    assert status == status_code, (label, status, body)
    return status, body


def sync_payload(
    device_uuid: str | None,
    student_no: str | None,
    real_name: str | None,
    attempts: Any,
) -> dict[str, Any]:
    return {
        "class_code": CLASS_CODE,
        "device_uuid": device_uuid,
        "student_no": student_no,
        "real_name": real_name,
        "attempts": attempts,
    }


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")

    expect("A1", "GET", "/api/health", None, None, 200)

    create_payload = {
        "class_code": CLASS_CODE,
        "class_name": "高数A班",
        "teacher_name": "老师",
        "class_secret": "abc123",
    }
    expect(
        "A2",
        "POST",
        "/api/admin/classes",
        create_payload,
        ADMIN_HEADERS,
        200,
        "class_code=F34F05",
    )

    roster_rows = [
        {"student_no": "S001", "real_name": "张测一"},
        {"student_no": "S002", "real_name": "李测二"},
        {"student_no": "S003", "real_name": "王测三"},
    ]
    _, body = expect(
        "B5",
        "POST",
        "/api/admin/roster",
        {"class_code": CLASS_CODE, "rows": roster_rows},
        ADMIN_HEADERS,
        200,
        "rows=3",
    )
    assert json.loads(body)["imported"] == 3

    attempts = [
        {
            "attempt_id": "m3-a1",
            "qid": "Q1",
            "correct": 1,
            "use_sec": 20,
        },
        {
            "attempt_id": "m3-a2",
            "qid": "Q2",
            "correct": 0,
            "use_sec": 30,
        },
        {
            "attempt_id": "m3-a3",
            "qid": "Q3",
            "correct": 1,
            "use_sec": 40,
        },
    ]
    first_payload = sync_payload("dev-A", "S001", "张测一", attempts)
    _, body = expect(
        "A3a/B9",
        "POST",
        "/api/sync/receive",
        first_payload,
        CLASS_HEADERS,
        200,
        "device_uuid=dev-A student_no=S001 attempts=3",
    )
    first_result = json.loads(body)
    assert first_result["accepted"] == 3
    assert first_result["total_received"] == 3

    with sqlite3.connect(ROOT / "cloud/data/m3_test.db") as connection:
        binding = connection.execute(
            """
            SELECT student_no, class_code, real_name
            FROM devices
            WHERE device_uuid = ?
            """,
            ("dev-A",),
        ).fetchone()
    print(f"[B9-db] devices dev-A -> {binding}")
    assert binding == ("S001", CLASS_CODE, "张测一")

    _, body = expect(
        "A3b",
        "POST",
        "/api/sync/receive",
        first_payload,
        CLASS_HEADERS,
        200,
        "same_payload device_uuid=dev-A attempts=3",
    )
    duplicate_result = json.loads(body)
    assert duplicate_result["accepted"] == 0
    assert duplicate_result["duplicated"] == 3
    assert duplicate_result["total_received"] == 3

    wrong_class_payload = sync_payload("dev-validation", "S001", "张测一", [])
    wrong_class_payload["class_code"] = "NO-SUCH-CLASS"
    expect(
        "A4a",
        "POST",
        "/api/sync/receive",
        wrong_class_payload,
        {"Content-Type": "application/json", "X-Class-Secret": "wrong-secret"},
        404,
        "class_code=NO-SUCH-CLASS X-Class-Secret=wrong-secret",
    )
    expect(
        "A4b",
        "POST",
        "/api/sync/receive",
        sync_payload(None, None, None, []),
        {"Content-Type": "application/json", "X-Class-Secret": "wrong-secret"},
        401,
        "device_uuid missing X-Class-Secret=wrong-secret",
    )
    expect(
        "A4c",
        "POST",
        "/api/sync/receive",
        sync_payload(None, None, None, []),
        CLASS_HEADERS,
        400,
        "device_uuid missing",
    )
    expect(
        "A4d",
        "POST",
        "/api/sync/receive",
        sync_payload("dev-validation", None, None, "not-an-array"),
        CLASS_HEADERS,
        400,
        "attempts=string",
    )
    too_many = sync_payload(
        "dev-validation",
        None,
        None,
        [
            {"attempt_id": f"bulk-{index}", "qid": f"Q{index}"}
            for index in range(5001)
        ],
    )
    expect(
        "A4e",
        "POST",
        "/api/sync/receive",
        too_many,
        CLASS_HEADERS,
        400,
        "attempts_count=5001",
    )

    _, body = expect(
        "B6",
        "POST",
        "/api/admin/roster",
        {"class_code": CLASS_CODE, "rows": roster_rows},
        ADMIN_HEADERS,
        200,
        "same_rows=3",
    )
    second_import = json.loads(body)
    assert second_import["imported"] == 0
    assert second_import["updated"] == 3

    paste_text = "学号\t姓名\nS004\t赵测四\n# 注释"
    expect(
        "B7a",
        "POST",
        "/api/admin/roster/paste",
        {"class_code": CLASS_CODE, "text": paste_text},
        ADMIN_HEADERS,
        200,
        "text has header/data/comment",
    )
    _, body = expect(
        "B7b",
        "GET",
        f"/api/admin/roster?class_code={CLASS_CODE}",
        None,
        {"X-Admin-Token": "test-admin"},
        200,
    )
    roster_result = json.loads(body)
    assert roster_result["total"] == 4
    assert all("#" not in row["real_name"] for row in roster_result["rows"])

    _, body = expect(
        "B8",
        "POST",
        "/api/admin/roster",
        {
            "class_code": CLASS_CODE,
            "rows": [{"real_name": "无学号"}],
        },
        ADMIN_HEADERS,
        400,
        "rows=1 missing student_no",
    )
    assert "第 1 行" in body

    _, body = expect(
        "B10",
        "POST",
        "/api/sync/receive",
        sync_payload("dev-unknown", "S999", "不存在", []),
        CLASS_HEADERS,
        404,
        "device_uuid=dev-unknown student_no=S999",
    )
    assert "学号不在本班名单，请联系老师" in body

    _, body = expect(
        "B11",
        "POST",
        "/api/sync/receive",
        sync_payload("dev-mismatch", "S001", "张测X", []),
        CLASS_HEADERS,
        422,
        "device_uuid=dev-mismatch student_no=S001 real_name=张测X",
    )
    full_name_absent = "张测一" not in body
    print(f"[B11-assert] 张测一 not in response body -> {full_name_absent}")
    assert full_name_absent

    expect(
        "B12",
        "POST",
        "/api/sync/receive",
        sync_payload("dev-A", "S002", "李测二", []),
        CLASS_HEADERS,
        409,
        "device_uuid=dev-A student_no=S002",
    )
    expect(
        "B13",
        "POST",
        "/api/sync/receive",
        sync_payload("dev-B", "S001", "张测一", []),
        CLASS_HEADERS,
        409,
        "device_uuid=dev-B student_no=S001",
    )

    rate_statuses = []
    for index in range(1, 7):
        status, body = request(
            "POST",
            "/api/sync/receive",
            sync_payload("dev-C", "S999", "不存在", []),
            CLASS_HEADERS,
        )
        rate_statuses.append(status)
        emit(
            f"B14-{index}",
            "POST",
            "/api/sync/receive",
            "device_uuid=dev-C student_no=S999",
            status,
            body,
        )
    assert rate_statuses == [404, 404, 404, 404, 404, 429]

    _, body = expect(
        "B15a",
        "POST",
        "/api/admin/roster/unbind",
        {"class_code": CLASS_CODE, "student_no": "S001"},
        ADMIN_HEADERS,
        200,
        "student_no=S001",
    )
    assert json.loads(body)["unbound"] == 1
    expect(
        "B15b",
        "POST",
        "/api/sync/receive",
        sync_payload("dev-B", "S001", "张测一", []),
        CLASS_HEADERS,
        200,
        "device_uuid=dev-B student_no=S001",
    )
    _, body = expect(
        "B16",
        "POST",
        "/api/admin/roster/unbind",
        {"class_code": CLASS_CODE, "student_no": "S003"},
        ADMIN_HEADERS,
        200,
        "student_no=S003",
    )
    assert json.loads(body)["unbound"] == 0
    expect(
        "B17a",
        "POST",
        "/api/sync/receive",
        sync_payload("dev-B", "S001", "张测一", []),
        CLASS_HEADERS,
        200,
        "same device/student repeated",
    )
    post_success_statuses = []
    for index in range(1, 6):
        status, body = request(
            "POST",
            "/api/sync/receive",
            sync_payload("dev-B", f"S99{index}", "不存在", []),
            CLASS_HEADERS,
        )
        post_success_statuses.append(status)
        emit(
            f"B17-probe-{index}",
            "POST",
            "/api/sync/receive",
            f"device_uuid=dev-B student_no=S99{index}",
            status,
            body,
        )
    print(f"[B17-assert] repeated success not counted -> {post_success_statuses}")
    assert post_success_statuses == [404, 404, 404, 404, 404]

    with sqlite3.connect(ROOT / "cloud/data/m3_test.db") as connection:
        roster_count = connection.execute(
            "SELECT COUNT(*) FROM roster_cloud"
        ).fetchone()[0]
        bound_device_count = connection.execute(
            "SELECT COUNT(*) FROM devices WHERE student_no IS NOT NULL"
        ).fetchone()[0]
        bound_rows = connection.execute(
            """
            SELECT student_no, class_code, real_name
            FROM devices
            WHERE student_no IS NOT NULL
            ORDER BY student_no
            """
        ).fetchall()
    print(f"[C18a] roster_cloud count -> {roster_count}")
    print(f"[C18b] devices student_no not null count -> {bound_device_count}")
    print(f"[C18c] bound rows -> {bound_rows}")
    assert roster_count == 4
    # M8 起：空 attempts 不再建设备行（否则"学生没做题就点提交"会把学号锁死在这台机器上，
    # 换机即 409）。本脚本全程用空 attempts 提交，所以这里应为 0。
    assert bound_device_count == 0

    print("[PASS] M3 acceptance passed")


if __name__ == "__main__":
    main()
