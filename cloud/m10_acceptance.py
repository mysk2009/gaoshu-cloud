from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ["CLOUD_DB_URL"] = "sqlite:///:memory:"
os.environ["CLOUD_SECRET_KEY"] = "test-only-secret"

from cloud.config import Settings
from cloud.main import create_app


CLASS_CODE = "M10F2"
CLASS_SECRET = "class-secret"
ADMIN_SECRET = "teach-secret"


def emit(label: str, payload: Any) -> None:
    print(f"[{label}] {json.dumps(payload, ensure_ascii=False)}")


def student_fragment(board: dict, student_no: str) -> dict:
    student = next(
        item for item in board["students"] if item["student_no"] == student_no
    )
    return {
        "student_no": student["student_no"],
        "real_name": student["real_name"],
        "submitted": student["submitted"],
        "attempt_count": student["attempt_count"],
        "correct_count": student["correct_count"],
        "correct_rate": student["correct_rate"],
    }


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    with tempfile.TemporaryDirectory(prefix="cloud-m10-f2-") as temp_dir:
        db_path = Path(temp_dir) / "legacy.db"
        with closing(sqlite3.connect(db_path)) as connection:
            connection.execute(
                """
                CREATE TABLE attempts_cloud (
                  device_uuid TEXT NOT NULL,
                  attempt_id  TEXT NOT NULL,
                  class_code  TEXT NOT NULL,
                  qid         TEXT NOT NULL,
                  correct     SMALLINT,
                  use_sec     INTEGER,
                  error_type  TEXT,
                  ts          TEXT,
                  received_at TEXT,
                  PRIMARY KEY (device_uuid, attempt_id)
                )
                """
            )
            before_columns = [
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(attempts_cloud)"
                ).fetchall()
            ]
            connection.commit()
        emit("schema-before", {"columns": before_columns})
        assert "student_no" not in before_columns

        settings = Settings(
            db_url=f"sqlite:///{db_path.as_posix()}",
            admin_token="test-admin",
            teacher_password="",
            secret_key="test-secret",
        )
        app = create_app(settings)
        with TestClient(app) as client:
            with closing(sqlite3.connect(db_path)) as connection:
                after_columns = [
                    row[1]
                    for row in connection.execute(
                        "PRAGMA table_info(attempts_cloud)"
                    ).fetchall()
                ]
            emit("migration-after", {"columns": after_columns})
            assert "student_no" in after_columns

            created = client.post(
                "/api/admin/classes",
                headers={"X-Admin-Token": "test-admin"},
                json={
                    "class_code": CLASS_CODE,
                    "class_name": "M10 验收班",
                    "teacher_name": "测试老师",
                    "class_secret": CLASS_SECRET,
                    "class_admin_secret": ADMIN_SECRET,
                },
            )
            assert created.status_code == 200, created.text
            entered = client.post(
                "/api/teacher/enter",
                json={
                    "class_code": CLASS_CODE,
                    "admin_secret": ADMIN_SECRET,
                },
            )
            assert entered.status_code == 200, entered.text
            teacher_headers = {
                "Authorization": f"Bearer {entered.json()['token']}"
            }
            imported = client.post(
                "/api/admin/roster",
                headers=teacher_headers,
                json={
                    "class_code": CLASS_CODE,
                    "rows": [
                        {"student_no": "S1", "real_name": "学生一"},
                        {"student_no": "S2", "real_name": "学生二"},
                    ],
                },
            )
            assert imported.status_code == 200, imported.text
            emit(
                "step-1",
                {
                    "class_code": CLASS_CODE,
                    "roster": imported.json(),
                },
            )

            def receive(
                device_uuid: str,
                attempts: list[dict[str, Any]],
            ) -> dict:
                response = client.post(
                    "/api/sync/receive",
                    headers={"X-Class-Secret": CLASS_SECRET},
                    json={
                        "class_code": CLASS_CODE,
                        "device_uuid": device_uuid,
                        "student_no": "S1",
                        "real_name": "学生一",
                        "attempts": attempts,
                    },
                )
                assert response.status_code == 200, response.text
                return response.json()

            def board() -> dict:
                response = client.get(
                    "/api/board",
                    headers=teacher_headers,
                )
                assert response.status_code == 200, response.text
                return response.json()

            first_sync = receive(
                "D1",
                [
                    {
                        "attempt_id": "s1-1",
                        "qid": "Q1",
                        "correct": 1,
                        "use_sec": 10,
                    },
                    {
                        "attempt_id": "s1-2",
                        "qid": "Q2",
                        "correct": 0,
                        "error_type": "calc",
                        "use_sec": 20,
                    },
                    {
                        "attempt_id": "s1-3",
                        "qid": "Q3",
                        "correct": 0,
                        "error_type": "concept",
                        "use_sec": 30,
                    },
                ],
            )
            board_before_unbind = board()
            exported = client.get(
                "/api/export.xlsx",
                headers=teacher_headers,
            )
            assert exported.status_code == 200, exported.text
            assert exported.content.startswith(b"PK"), exported.content[:20]
            emit(
                "step-2",
                {
                    "sync": first_sync,
                    "student": student_fragment(board_before_unbind, "S1"),
                    "totals": board_before_unbind["totals"],
                    "export_bytes": len(exported.content),
                    "review_qids": [
                        item["qid"] for item in board_before_unbind["review_list"]
                    ],
                },
            )
            assert student_fragment(board_before_unbind, "S1")["attempt_count"] == 3
            assert student_fragment(board_before_unbind, "S1")["correct_rate"] == 0.3333
            assert board_before_unbind["totals"]["attempts"] == 3

            with app.state.database.transaction() as connection:
                app.state.database.execute(
                    connection,
                    """
                    INSERT INTO attempts_cloud
                      (device_uuid, attempt_id, class_code, student_no, qid,
                       correct, use_sec, error_type, ts, received_at)
                    VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "D1",
                        "legacy-null",
                        CLASS_CODE,
                        "Q-LEGACY",
                        1,
                        5,
                        None,
                        None,
                        "2026-11-17T00:00:00",
                    ),
                )
            legacy_board = board()
            emit(
                "legacy-null-fallback",
                {
                    "student": student_fragment(legacy_board, "S1"),
                    "totals": legacy_board["totals"],
                },
            )
            assert student_fragment(legacy_board, "S1")["attempt_count"] == 4
            with app.state.database.transaction() as connection:
                app.state.database.execute(
                    connection,
                    "DELETE FROM attempts_cloud WHERE attempt_id = ?",
                    ("legacy-null",),
                )

            unbound = client.post(
                "/api/admin/roster/unbind",
                headers=teacher_headers,
                json={"class_code": CLASS_CODE, "student_no": "S1"},
            )
            assert unbound.status_code == 200, unbound.text
            emit("step-3", unbound.json())

            board_after_unbind = board()
            emit(
                "step-4",
                {
                    "student": student_fragment(board_after_unbind, "S1"),
                    "totals": board_after_unbind["totals"],
                    "unbound_devices": board_after_unbind["unbound_devices"],
                },
            )
            assert student_fragment(board_after_unbind, "S1")["attempt_count"] == 3
            assert student_fragment(board_after_unbind, "S1")["correct_rate"] == 0.3333
            assert board_after_unbind["totals"]["attempts"] == 3

            second_sync = receive(
                "D2",
                [
                    {
                        "attempt_id": "s1-4",
                        "qid": "Q4",
                        "correct": 1,
                        "use_sec": 40,
                    }
                ],
            )
            board_new_device = board()
            emit(
                "step-5",
                {
                    "sync": second_sync,
                    "student": student_fragment(board_new_device, "S1"),
                    "totals": board_new_device["totals"],
                },
            )
            assert student_fragment(board_new_device, "S1")["attempt_count"] == 4
            assert board_new_device["totals"]["attempts"] == 4

            duplicate_and_dirty = receive(
                "D2",
                [
                    {
                        "attempt_id": "s1-4",
                        "qid": "Q4",
                        "correct": 1,
                    },
                    {
                        "attempt_id": "dirty",
                        "qid": "",
                        "correct": 1,
                    },
                ],
            )
            with closing(sqlite3.connect(db_path)) as connection:
                stored = [
                    (row[0], row[1], row[2])
                    for row in connection.execute(
                        """
                        SELECT device_uuid, student_no, COUNT(*)
                        FROM attempts_cloud
                        GROUP BY device_uuid, student_no
                        ORDER BY device_uuid
                        """
                    ).fetchall()
                ]
            emit(
                "step-6",
                {
                    "duplicate_and_dirty": duplicate_and_dirty,
                    "stored_rows": stored,
                },
            )
            assert duplicate_and_dirty == {
                "accepted": 0,
                "duplicated": 1,
                "skipped": 1,
                "total_received": 4,
                "server_time": duplicate_and_dirty["server_time"],
            }

    print("[PASS] M10 F2 acceptance passed")


if __name__ == "__main__":
    main()
