from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
TARGET_FILES = (
    "cloud/routes_board.py",
    "cloud/routes_admin.py",
    "cloud/routes_sync.py",
    "cloud/routes_teacher.py",
    "cloud/schema.py",
    "cloud/export_xlsx.py",
)

TRIPLE_STRING_RE = re.compile(
    r"(?s)(?P<prefix>[rRuUbBfF]{0,2})(?P<quote>'''|\"\"\")(?P<body>.*?)(?P=quote)"
)
SINGLE_STRING_RE = re.compile(r"(?P<quote>['\"])(?P<body>[^'\"\n]*)(?P=quote)")
SQL_RE = re.compile(r"\b(?:SELECT|INSERT|UPDATE|DELETE|CREATE|ALTER)\b", re.I)
FORBIDDEN_SQLITE_RE = re.compile(r"\b(?:INSERT\s+OR\s+IGNORE|AUTOINCREMENT)\b", re.I)
AGGREGATE_RE = re.compile(r"\b(?:COUNT|SUM|AVG|MIN|MAX)\s*\(", re.I)
QUALIFIED_REF_RE = re.compile(r"\b([A-Za-z_]\w*)\.([A-Za-z_]\w*)\b")
TABLE_ALIAS_RE = re.compile(
    r"\b(?:FROM|JOIN)\s+(?:[A-Za-z_]\w*\.)?[A-Za-z_]\w*"
    r"\s+(?:AS\s+)?([A-Za-z_]\w*)",
    re.I,
)
ALIAS_STOP_WORDS = {
    "left",
    "right",
    "full",
    "inner",
    "outer",
    "cross",
    "join",
    "where",
    "group",
    "order",
    "limit",
    "on",
}


@dataclass(frozen=True)
class SqlString:
    line: int
    text: str


@dataclass(frozen=True)
class Finding:
    file: str
    line: int
    message: str


def _line_at(sql: str, offset: int) -> int:
    return sql.count("\n", 0, offset) + 1


def _extract_sql_strings(source: str) -> list[SqlString]:
    strings: list[SqlString] = []
    masked = list(source)

    for match in TRIPLE_STRING_RE.finditer(source):
        body = match.group("body")
        prefix = match.group("prefix").lower()
        if "f" not in prefix and SQL_RE.search(body):
            strings.append(
                SqlString(
                    line=source.count("\n", 0, match.start("body")) + 1,
                    text=body,
                )
            )
        for index in range(match.start(), match.end()):
            if masked[index] != "\n":
                masked[index] = " "

    masked_source = "".join(masked)
    for match in SINGLE_STRING_RE.finditer(masked_source):
        body = match.group("body")
        if SQL_RE.search(body):
            strings.append(
                SqlString(
                    line=masked_source.count("\n", 0, match.start("body")) + 1,
                    text=body,
                )
            )
    return strings


def _mask_sql_literals(sql: str) -> str:
    chars = list(sql)
    index = 0
    while index < len(sql):
        if sql[index] != "'":
            index += 1
            continue
        chars[index] = " "
        index += 1
        while index < len(sql):
            if sql[index] == "'":
                chars[index] = " "
                index += 1
                if index < len(sql) and sql[index] == "'":
                    chars[index] = " "
                    index += 1
                    continue
                break
            if sql[index] != "\n":
                chars[index] = " "
            index += 1
    return "".join(chars)


def _depths(sql: str) -> list[int]:
    result: list[int] = []
    depth = 0
    for char in sql:
        result.append(depth)
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(depth - 1, 0)
    return result


def _find_at_depth(
    sql: str, pattern: re.Pattern[str], depth_map: list[int], start: int
) -> re.Match[str] | None:
    for match in pattern.finditer(sql, start):
        if depth_map[match.start()] == 0:
            return match
    return None


def _mask_aggregate_calls(sql: str) -> str:
    chars = list(sql)
    for match in AGGREGATE_RE.finditer(sql):
        open_index = match.end() - 1
        depth = 0
        end_index = len(sql)
        for index in range(open_index, len(sql)):
            if sql[index] == "(":
                depth += 1
            elif sql[index] == ")":
                depth -= 1
                if depth == 0:
                    end_index = index + 1
                    break
        for index in range(match.start(), end_index):
            if chars[index] != "\n":
                chars[index] = " "
    return "".join(chars)


def _find_grouping_issues(
    file: str, sql_string: SqlString
) -> list[Finding]:
    sql = _mask_sql_literals(sql_string.text)
    depth_map = _depths(sql)
    select = _find_at_depth(sql, re.compile(r"\bSELECT\b", re.I), depth_map, 0)
    group = _find_at_depth(
        sql, re.compile(r"\bGROUP\s+BY\b", re.I), depth_map, 0
    )
    if select is None or group is None:
        return []

    from_match = _find_at_depth(
        sql, re.compile(r"\bFROM\b", re.I), depth_map, select.end()
    )
    if from_match is None or from_match.start() > group.start():
        return []

    select_list = sql[select.end() : from_match.start()]
    group_tail = sql[group.end() :]
    group_end_match = re.search(r"\b(?:ORDER\s+BY|HAVING|LIMIT)\b", group_tail, re.I)
    if group_end_match is not None:
        group_tail = group_tail[: group_end_match.start()]
    grouped = {
        (match.group(1).lower(), match.group(2).lower())
        for match in QUALIFIED_REF_RE.finditer(group_tail)
    }

    outer_aliases = {
        match.group(1).lower()
        for match in TABLE_ALIAS_RE.finditer(sql[from_match.start() :])
        if match.group(1).lower() not in ALIAS_STOP_WORDS
    }
    masked_select_list = _mask_aggregate_calls(select_list)
    findings: list[Finding] = []
    seen: set[tuple[str, str]] = set()
    for match in QUALIFIED_REF_RE.finditer(masked_select_list):
        alias = match.group(1).lower()
        column = match.group(2).lower()
        if alias not in outer_aliases or (alias, column) in grouped:
            continue
        key = (alias, column)
        if key in seen:
            continue
        seen.add(key)
        reference_offset = select.end() + match.start()
        line = sql_string.line + _line_at(sql_string.text, reference_offset) - 1
        group_line = sql_string.line + _line_at(sql_string.text, group.start()) - 1
        findings.append(
            Finding(
                file=file,
                line=line,
                message=(
                    f"ungrouped SELECT reference {alias}.{column}; "
                    f"GROUP BY at line {group_line}"
                ),
            )
        )
    return findings


def _find_forbidden_sqlite(source: str, file: str) -> list[Finding]:
    findings: list[Finding] = []
    for match in FORBIDDEN_SQLITE_RE.finditer(source):
        findings.append(
            Finding(
                file=file,
                line=source.count("\n", 0, match.start()) + 1,
                message=f"SQLite-specific syntax: {match.group(0)}",
            )
        )
    return findings


def _self_test() -> None:
    buggy = """SELECT
  r.student_no,
  (
    SELECT d.device_uuid
    FROM devices AS d
    WHERE d.class_code = r.class_code
  ) AS device_uuid,
  COUNT(a.attempt_id) AS attempts
FROM roster_cloud AS r
LEFT JOIN attempts_cloud AS a ON a.student_no = r.student_no
GROUP BY r.student_no"""
    findings = _find_grouping_issues("self-test", SqlString(1, buggy))
    assert any("r.class_code" in item.message for item in findings), findings
    forbidden = _find_forbidden_sqlite(
        "INSERT OR IGNORE INTO t VALUES (1);\nCREATE TABLE x (id INTEGER AUTOINCREMENT)",
        "self-test",
    )
    assert len(forbidden) == 2, forbidden
    print("[sql-postgres-lint] self-test passed")


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        _self_test()
        return 0

    findings: list[Finding] = []
    for relative_file in TARGET_FILES:
        path = ROOT / relative_file
        source = path.read_text(encoding="utf-8")
        findings.extend(_find_forbidden_sqlite(source, relative_file))
        for sql_string in _extract_sql_strings(source):
            findings.extend(_find_grouping_issues(relative_file, sql_string))

    if not findings:
        print(
            f"[sql-postgres-lint] clean: scanned {len(TARGET_FILES)} files",
            flush=True,
        )
        return 0

    for finding in sorted(findings, key=lambda item: (item.file, item.line)):
        print(f"{finding.file}:{finding.line}: {finding.message}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
