from __future__ import annotations

from datetime import datetime
from html import escape
from io import BytesIO
import re
from zipfile import ZIP_DEFLATED, ZipFile
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import Response

from cloud.routes_auth import require_teacher_class
from cloud.routes_board import build_board_data


router = APIRouter()

CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _xml_text(value: object) -> str:
    text = str(value)
    return escape(text, quote=False)


def _column_name(index: int) -> str:
    name = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def _cell_xml(reference: str, value: Any, style: int = 0) -> str:
    style_attribute = f' s="{style}"' if style else ""
    if value is None:
        return f'<c r="{reference}"{style_attribute}/>'
    if isinstance(value, bool):
        return (
            f'<c r="{reference}" t="b"{style_attribute}>'
            f"<v>{1 if value else 0}</v></c>"
        )
    if isinstance(value, (int, float)):
        return f'<c r="{reference}"{style_attribute}><v>{value}</v></c>'
    return (
        f'<c r="{reference}" t="inlineStr"{style_attribute}>'
        f'<is><t xml:space="preserve">{_xml_text(value)}</t></is></c>'
    )


def _sheet_xml(rows: list[list[Any]], widths: list[int]) -> bytes:
    column_xml = "".join(
        f'<col min="{index}" max="{index}" width="{width}" customWidth="1"/>'
        for index, width in enumerate(widths, start=1)
    )
    row_xml: list[str] = []
    for row_index, values in enumerate(rows, start=1):
        cells = "".join(
            _cell_xml(
                f"{_column_name(column_index)}{row_index}",
                value,
                style=1 if row_index == 1 else 0,
            )
            for column_index, value in enumerate(values, start=1)
        )
        row_xml.append(f'<row r="{row_index}">{cells}</row>')
    worksheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<sheetViews><sheetView workbookViewId="0">'
        '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
        "</sheetView></sheetViews>"
        f"<cols>{column_xml}</cols>"
        f"<sheetData>{''.join(row_xml)}</sheetData>"
        "</worksheet>"
    )
    return worksheet.encode("utf-8")


def build_xlsx(board: dict) -> bytes:
    overview_rows = [
        ["项目", "内容"],
        ["班级", board.get("class_name") or ""],
        ["班级码", board["class_code"]],
        ["生成时间", board["generated_at"]],
        ["名单人数", board["totals"]["roster"]],
        ["已交", board["totals"]["submitted"]],
        ["未交", board["totals"]["missing"]],
        ["总作答数", board["totals"]["attempts"]],
    ]
    student_rows = [
        [
            "学号",
            "姓名",
            "是否已交",
            "作答数",
            "正确数",
            "正确率",
            "首次时间",
            "最近时间",
            "建议",
        ]
    ]
    for student in board["students"]:
        student_rows.append(
            [
                student["student_no"],
                student["real_name"],
                "是" if student["submitted"] else "否",
                student["attempt_count"],
                student["correct_count"],
                student["correct_rate"],
                student["first_at"],
                student["last_at"],
                student.get("advice", ""),
            ]
        )
    missing_rows = [["学号", "姓名"]]
    missing_rows.extend(
        [[student["student_no"], student["real_name"]] for student in board["missing"]]
    )
    review_rows = [
        [
            "排名",
            "题号",
            "错人数",
            "做题人数",
            "错误率",
            "平均用时",
            "概念错",
            "计算错",
            "看错",
            "为什么讲",
        ]
    ]
    for item in board["review_list"]:
        error_types = item["error_types"]
        review_rows.append(
            [
                item["rank"],
                item["qid"],
                item["wrong_devices"],
                item["devices"],
                item["wrong_rate"],
                item["avg_use_sec"],
                error_types.get("concept", 0),
                error_types.get("calc", 0),
                error_types.get("careless", 0),
                item.get("reason", ""),
            ]
        )

    sheets = [
        ("班级概况", overview_rows, [20, 28]),
        ("逐人明细", student_rows, [14, 14, 11, 11, 11, 12, 22, 22, 34]),
        ("未交名单", missing_rows, [16, 18]),
        (
            "讲评清单",
            review_rows,
            [8, 14, 10, 12, 12, 12, 10, 10, 10, 38],
        ),
    ]

    workbook_sheets = "".join(
        f'<sheet name="{_xml_text(name)}" sheetId="{index}" '
        f'r:id="rId{index}"/>'
        for index, (name, _, _) in enumerate(sheets, start=1)
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f"<sheets>{workbook_sheets}</sheets></workbook>"
    )
    workbook_relationships = "".join(
        '<Relationship '
        f'Id="rId{index}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        f'Target="worksheets/sheet{index}.xml"/>'
        for index in range(1, len(sheets) + 1)
    )
    workbook_relationships += (
        '<Relationship Id="rId5" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
        'Target="styles.xml"/>'
    )
    workbook_relationships = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + workbook_relationships
        + "</Relationships>"
    )
    styles = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="2">'
        '<font><sz val="11"/><name val="Arial"/></font>'
        '<font><b/><color rgb="FFFFFFFF"/><sz val="11"/><name val="Arial"/></font>'
        "</fonts>"
        '<fills count="3">'
        '<fill><patternFill patternType="none"/></fill>'
        '<fill><patternFill patternType="gray125"/></fill>'
        '<fill><patternFill patternType="solid"><fgColor rgb="FF2563EB"/>'
        '<bgColor indexed="64"/></patternFill></fill>'
        "</fills>"
        '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/>'
        "</border></borders>"
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/>'
        "</cellStyleXfs>"
        '<cellXfs count="2">'
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
        '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" '
        'applyFont="1" applyFill="1"/>'
        "</cellXfs>"
        '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/>'
        "</cellStyles>"
        "</styleSheet>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        + "".join(
            f'<Override PartName="/xl/worksheets/sheet{index}.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            for index in range(1, len(sheets) + 1)
        )
        + '<Override PartName="/xl/styles.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        "</Types>"
    )
    root_relationships = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/>'
        "</Relationships>"
    )

    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", root_relationships)
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_relationships)
        archive.writestr("xl/styles.xml", styles)
        for index, (_, rows, widths) in enumerate(sheets, start=1):
            archive.writestr(f"xl/worksheets/sheet{index}.xml", _sheet_xml(rows, widths))
    return output.getvalue()


def _filename(board: dict) -> str:
    generated_date = datetime.fromisoformat(board["generated_at"]).strftime("%Y%m%d")
    safe_class_code = re.sub(r"[^A-Za-z0-9_-]", "_", board["class_code"])
    return f"{safe_class_code}_board_{generated_date}.xlsx"


@router.get("/api/export.xlsx")
def export_board_xlsx(request: Request, class_code: str = "") -> Response:
    scoped_class_code = require_teacher_class(request, class_code)
    board = build_board_data(request.app.state.database, scoped_class_code)
    filename = _filename(board)
    return Response(
        content=build_xlsx(board),
        media_type=CONTENT_TYPE,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )
