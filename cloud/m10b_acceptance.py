from __future__ import annotations

import io
import sqlite3
import sys
import tempfile
from contextlib import closing, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cloud.db import Database
from cloud.schema import ensure_schema


def _attempt_students(connection: sqlite3.Connection) -> dict[str, str | None]:
    return {
        row[0]: row[1]
        for row in connection.execute(
            """
            SELECT attempt_id, student_no
            FROM attempts_cloud
            ORDER BY attempt_id
            """
        ).fetchall()
    }


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    with tempfile.TemporaryDirectory(prefix="cloud-m10b-backfill-") as temp_dir:
        db_path = Path(temp_dir) / "legacy.db"
        with closing(sqlite3.connect(db_path)) as connection:
            connection.executescript(
                """
                CREATE TABLE devices (
                  device_uuid TEXT PRIMARY KEY,
                  class_code  TEXT NOT NULL,
                  nickname    TEXT,
                  student_no  TEXT,
                  real_name   TEXT,
                  verified_at TEXT,
                  attempts    INTEGER DEFAULT 0,
                  first_at    TEXT,
                  last_at     TEXT
                );
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
                );
                INSERT INTO devices
                  (device_uuid, class_code, student_no)
                VALUES
                  ('D1', 'M10B', 'S1'),
                  ('D2', 'M10B', NULL),
                  ('D3', 'OTHER', 'S3');
                INSERT INTO attempts_cloud
                  (device_uuid, attempt_id, class_code, qid, correct)
                VALUES
                  ('D1', 'A1', 'M10B', 'Q1', 1),
                  ('D2', 'A2', 'M10B', 'Q2', 0),
                  ('D3', 'A3', 'M10B', 'Q3', 1);
                """
            )
            connection.commit()

        database = Database(f"sqlite:///{db_path.as_posix()}")
        first_log = io.StringIO()
        with redirect_stdout(first_log):
            ensure_schema(database)
        print(first_log.getvalue().strip())

        with closing(sqlite3.connect(db_path)) as connection:
            after_first = _attempt_students(connection)
            connection.execute(
                "UPDATE attempts_cloud SET student_no = 'S-EXISTING' "
                "WHERE attempt_id = 'A1'"
            )
            connection.commit()
        print(f"[first-run] {after_first}")
        assert after_first == {
            "A1": "S1",
            "A2": None,
            "A3": None,
        }

        second_log = io.StringIO()
        with redirect_stdout(second_log):
            ensure_schema(database)
        print(second_log.getvalue().strip())

        with closing(sqlite3.connect(db_path)) as connection:
            after_second = _attempt_students(connection)
        print(f"[second-run] {after_second}")
        assert after_second == {
            "A1": "S-EXISTING",
            "A2": None,
            "A3": None,
        }

    print("[PASS] M10B student_no backfill is idempotent")


if __name__ == "__main__":
    main()
