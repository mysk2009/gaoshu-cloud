from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path
from typing import Sequence


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cloud.db import Database
from cloud.routes_admin import (
    PurgeInputError,
    PurgeSafetyError,
    purge_classes,
)


def _split_values(raw: str | None) -> list[str]:
    if raw is None:
        return []
    values: list[str] = []
    for item in raw.split(","):
        value = item.strip().upper()
        if not value:
            raise PurgeInputError("selector entries must not be empty")
        if value not in values:
            values.append(value)
    return values


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Preview or delete cloud classes by test-oriented LIKE patterns "
            "or exact class codes. The default mode never writes."
        )
    )
    parser.add_argument(
        "--dsn",
        help="SQLite/PostgreSQL URL; falls back to CLOUD_DB_URL",
    )
    selector = parser.add_mutually_exclusive_group(required=True)
    selector.add_argument(
        "--match",
        help="comma-separated SQL LIKE patterns, for example DEMO%%,AUD%%",
    )
    selector.add_argument(
        "--codes",
        help="comma-separated exact class codes, for example DEMO26,PG26A",
    )
    parser.add_argument(
        "--allow-any",
        action="store_true",
        help="allow exact matches outside DEMO/AUD/PG/DSH/TEST/LV",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="perform the deletion; without it this command is dry-run",
    )
    return parser


def _report(result: dict, selector: str) -> None:
    deleted = result["deleted"]
    print(
        "[cloud-purge] matched classes="
        f"{','.join(result['matched_classes'])}"
    )
    print(
        f"[cloud-purge] mode={result['mode']} pattern={selector} "
        f"attempts_cloud={deleted['attempts_cloud']} "
        f"devices={deleted['devices']} "
        f"roster_cloud={deleted['roster_cloud']} "
        f"classes_cloud={deleted['classes_cloud']}"
    )
    print(
        f"[cloud-purge] total rows={sum(deleted.values())} "
        f"classes={deleted['classes_cloud']}"
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        patterns = _split_values(args.match)
        codes = _split_values(args.codes)
    except PurgeInputError as exc:
        print(f"[cloud-purge] ERROR: {exc}", file=sys.stderr)
        return 2

    selector = ",".join(patterns or codes)
    dsn = (args.dsn or os.getenv("CLOUD_DB_URL", "")).strip()
    if not dsn:
        print(
            "[cloud-purge] ERROR: provide --dsn or set CLOUD_DB_URL",
            file=sys.stderr,
        )
        return 2

    try:
        database = Database(dsn)
    except ValueError as exc:
        print(f"[cloud-purge] ERROR: {exc}", file=sys.stderr)
        return 1

    try:
        result = purge_classes(
            database,
            patterns=patterns,
            codes=codes,
            execute=args.yes,
            allow_any=args.allow_any,
        )
    except PurgeSafetyError as exc:
        print(
            "[cloud-purge] ERROR: refusing non-test classes="
            f"{','.join(exc.class_codes)}; use --allow-any to proceed",
            file=sys.stderr,
        )
        return 2
    except PurgeInputError as exc:
        print(f"[cloud-purge] ERROR: {exc}", file=sys.stderr)
        return 2
    except ModuleNotFoundError as exc:
        if exc.name == "psycopg":
            print(
                "[cloud-purge] ERROR: PostgreSQL support requires psycopg; "
                "install it with: pip install 'psycopg[binary]'",
                file=sys.stderr,
            )
        else:
            print(
                f"[cloud-purge] ERROR: missing dependency {exc.name}",
                file=sys.stderr,
            )
        return 1
    except (sqlite3.Error, OSError) as exc:
        print(
            f"[cloud-purge] ERROR: database unavailable: {exc}",
            file=sys.stderr,
        )
        return 1
    except Exception as exc:
        print(
            "[cloud-purge] ERROR: database operation failed: "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 1

    _report(result, selector)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
