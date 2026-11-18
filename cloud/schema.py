from __future__ import annotations

from cloud.db import Database


DDL_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS classes_cloud (
      class_code   TEXT PRIMARY KEY,
      class_name   TEXT,
      teacher_name TEXT,
      class_admin_secret TEXT,
      class_secret TEXT NOT NULL,
      created_at   TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS devices (
      device_uuid TEXT PRIMARY KEY,
      class_code  TEXT NOT NULL,
      nickname    TEXT,
      student_no  TEXT,
      real_name   TEXT,
      verified_at TEXT,
      attempts    INTEGER DEFAULT 0,
      first_at    TEXT,
      last_at     TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS roster_cloud (
      class_code TEXT NOT NULL,
      student_no TEXT NOT NULL,
      real_name  TEXT NOT NULL,
      created_at TEXT,
      manual_submitted TEXT,
      PRIMARY KEY (class_code, student_no)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_devices_cloud_binding
    ON devices(class_code, student_no)
    """,
    """
    CREATE TABLE IF NOT EXISTS attempts_cloud (
      device_uuid TEXT NOT NULL,
      attempt_id  TEXT NOT NULL,
      class_code  TEXT NOT NULL,
      student_no  TEXT,
      qid         TEXT NOT NULL,
      correct     SMALLINT,
      use_sec     INTEGER,
      error_type  TEXT,
      ts          TEXT,
      received_at TEXT,
      PRIMARY KEY (device_uuid, attempt_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_attempts_cloud_class
    ON attempts_cloud(class_code)
    """,
)

BACKFILL_ATTEMPTS_STUDENT_NO_SQL = """
UPDATE attempts_cloud
SET student_no = (
  SELECT d.student_no
  FROM devices AS d
  WHERE d.device_uuid = attempts_cloud.device_uuid
    AND d.class_code = attempts_cloud.class_code
    AND d.student_no IS NOT NULL
)
WHERE (student_no IS NULL OR student_no = '')
  AND EXISTS (
    SELECT 1
    FROM devices AS d
    WHERE d.device_uuid = attempts_cloud.device_uuid
      AND d.class_code = attempts_cloud.class_code
      AND d.student_no IS NOT NULL
  )
"""

UNIFY_CLASS_SECRET_SQL = """
UPDATE classes_cloud
SET class_secret = class_admin_secret
WHERE class_admin_secret IS NOT NULL
  AND TRIM(class_admin_secret) <> ''
  AND (class_secret IS NULL OR class_secret <> class_admin_secret)
"""


def ensure_schema(database: Database) -> None:
    with database.transaction() as connection:
        for statement in DDL_STATEMENTS:
            connection.execute(statement)

        if database.backend == "sqlite":
            columns = [
                database.row_value(row, "name")
                for row in connection.execute(
                    "PRAGMA table_info(classes_cloud)"
                ).fetchall()
            ]
        else:
            columns = [
                database.row_value(row, "column_name")
                for row in database.fetchall(
                    connection,
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = 'classes_cloud'
                    """,
                )
            ]
        if "class_admin_secret" not in columns:
            connection.execute(
                "ALTER TABLE classes_cloud ADD COLUMN class_admin_secret TEXT"
            )

        if database.backend == "sqlite":
            roster_columns = [
                database.row_value(row, "name")
                for row in connection.execute(
                    "PRAGMA table_info(roster_cloud)"
                ).fetchall()
            ]
            if "manual_submitted" not in roster_columns:
                connection.execute(
                    "ALTER TABLE roster_cloud ADD COLUMN manual_submitted TEXT"
                )
        else:
            connection.execute(
                "ALTER TABLE roster_cloud "
                "ADD COLUMN IF NOT EXISTS manual_submitted TEXT"
            )

        unify_cursor = database.execute(connection, UNIFY_CLASS_SECRET_SQL)
        raw_unified_rowcount = getattr(unify_cursor, "rowcount", None)
        unified = (
            raw_unified_rowcount
            if isinstance(raw_unified_rowcount, int) and raw_unified_rowcount >= 0
            else 0
        )
        suffix = (
            ""
            if isinstance(raw_unified_rowcount, int) and raw_unified_rowcount >= 0
            else " (rowcount unavailable)"
        )
        print(
            f"[cloud] unified class secrets migrated={unified}{suffix}",
            flush=True,
        )

        if database.backend == "sqlite":
            attempt_columns = [
                database.row_value(row, "name")
                for row in connection.execute(
                    "PRAGMA table_info(attempts_cloud)"
                ).fetchall()
            ]
            if "student_no" not in attempt_columns:
                connection.execute(
                    "ALTER TABLE attempts_cloud ADD COLUMN student_no TEXT"
                )
        else:
            connection.execute(
                "ALTER TABLE attempts_cloud "
                "ADD COLUMN IF NOT EXISTS student_no TEXT"
            )

        backfill_cursor = database.execute(
            connection, BACKFILL_ATTEMPTS_STUDENT_NO_SQL
        )
        raw_rowcount = getattr(backfill_cursor, "rowcount", None)
        if isinstance(raw_rowcount, int) and raw_rowcount >= 0:
            backfilled = raw_rowcount
            suffix = ""
        else:
            backfilled = 0
            suffix = " (rowcount unavailable)"
        print(
            f"[cloud] backfilled attempts.student_no rows={backfilled}{suffix}",
            flush=True,
        )
