from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile


def _xml_cell(value: str, style_id: int = 0) -> str:
    text = "" if value is None else str(value)
    clean_text = "".join(
        ch for ch in text if ch in "\t\n\r" or (ord(ch) >= 32 and ord(ch) != 127)
    )
    safe = escape(clean_text)
    return f'<c t="inlineStr" s="{style_id}"><is><t>{safe}</t></is></c>'


def export_attendance_to_xlsx(
    export_path: Path,
    columns: list[str],
    rows: list[list[str]],
    sheet_name: str = "Attendance",
) -> Path:
    """Create a simple Excel .xlsx file without requiring third-party packages."""
    export_path.parent.mkdir(parents=True, exist_ok=True)

    worksheet_rows: list[str] = []
    header_cells = "".join(_xml_cell(column, style_id=1) for column in columns)
    worksheet_rows.append(f'<row r="1">{header_cells}</row>')

    for index, row_values in enumerate(rows, start=2):
        cells = "".join(_xml_cell(value) for value in row_values)
        worksheet_rows.append(f'<row r="{index}">{cells}</row>')

    worksheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<sheetViews><sheetView workbookViewId="0"/></sheetViews>'
        '<sheetFormatPr defaultRowHeight="15"/>'
        '<sheetData>'
        + "".join(worksheet_rows)
        + '</sheetData>'
        '</worksheet>'
    )

    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets>'
        f'<sheet name="{escape(sheet_name)}" sheetId="1" r:id="rId1"/>'
        '</sheets>'
        '</workbook>'
    )

    workbook_rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        'Target="worksheets/sheet1.xml"/>'
        '<Relationship Id="rId2" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
        'Target="styles.xml"/>'
        '</Relationships>'
    )

    package_rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/>'
        '</Relationships>'
    )

    content_types_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '<Override PartName="/xl/styles.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        '</Types>'
    )

    styles_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="2">'
        '<font><sz val="11"/><name val="Calibri"/></font>'
        '<font><b/><sz val="11"/><name val="Calibri"/></font>'
        '</fonts>'
        '<fills count="2">'
        '<fill><patternFill patternType="none"/></fill>'
        '<fill><patternFill patternType="gray125"/></fill>'
        '</fills>'
        '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="2">'
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
        '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
        '</cellXfs>'
        '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
        '</styleSheet>'
    )

    with ZipFile(export_path, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types_xml)
        archive.writestr("_rels/.rels", package_rels_xml)
        archive.writestr("xl/workbook.xml", workbook_xml)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels_xml)
        archive.writestr("xl/worksheets/sheet1.xml", worksheet_xml)
        archive.writestr("xl/styles.xml", styles_xml)

    return export_path


def export_attendance_to_pdf(
    export_path: Path,
    title: str,
    lines: list[str],
) -> Path:
    """Create a clean PDF report with proper text positioning and pagination."""
    export_path.parent.mkdir(parents=True, exist_ok=True)

    def pdf_escape(value: str) -> str:
        clean = "".join(ch if ord(ch) < 256 else "?" for ch in str(value))
        return clean.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    lines_per_page = 38
    chunks = [lines[i : i + lines_per_page] for i in range(0, max(1, len(lines)), lines_per_page)]
    page_count = len(chunks)

    # Object IDs:
    # 1: Catalog
    # 2: Pages
    # Font: Shared object at id 3
    # For each page i (0-indexed):
    #   Page obj: 4 + 2*i
    #   Contents obj: 4 + 2*i + 1
    font_obj_id = 3
    page_obj_ids = [4 + 2 * i for i in range(page_count)]
    content_obj_ids = [4 + 2 * i + 1 for i in range(page_count)]

    kids_ref = " ".join(f"{pid} 0 R" for pid in page_obj_ids)
    objects: list[tuple[int, bytes]] = [
        (1, b"<< /Type /Catalog /Pages 2 0 R >>"),
        (2, f"<< /Type /Pages /Kids [{kids_ref}] /Count {page_count} >>".encode("latin-1")),
        (font_obj_id, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"),
    ]

    y_step = 18
    for page_idx, chunk in enumerate(chunks):
        page_num = page_idx + 1
        page_title = title if page_count == 1 else f"{title} (Page {page_num} of {page_count})"
        stream_lines = ["BT", "/F1 11 Tf", "50 790 Td", f"({pdf_escape(page_title)}) Tj"]
        for line in chunk:
            stream_lines.append(f"0 -{y_step} Td")
            stream_lines.append(f"({pdf_escape(line)}) Tj")
        stream_lines.append("ET")
        stream_bytes = "\n".join(stream_lines).encode("latin-1", errors="replace")

        page_pid = page_obj_ids[page_idx]
        content_pid = content_obj_ids[page_idx]

        page_obj_body = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            f"/Contents {content_pid} 0 R /Resources << /Font << /F1 {font_obj_id} 0 R >> >> >>"
        ).encode("latin-1")
        content_obj_body = (
            f"<< /Length {len(stream_bytes)} >> stream\n".encode("latin-1")
            + stream_bytes
            + b"\nendstream"
        )

        objects.append((page_pid, page_obj_body))
        objects.append((content_pid, content_obj_body))

    # Sort objects by object ID
    objects.sort(key=lambda item: item[0])
    max_obj_id = max(item[0] for item in objects)

    pdf = bytearray(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}
    for obj_id, body in objects:
        offsets[obj_id] = len(pdf)
        pdf.extend(f"{obj_id} 0 obj\n".encode("latin-1"))
        pdf.extend(body)
        pdf.extend(b"\nendobj\n")

    xref_pos = len(pdf)
    pdf.extend(f"xref\n0 {max_obj_id + 1}\n".encode("latin-1"))
    pdf.extend(b"0000000000 65535 f \n")
    for obj_id in range(1, max_obj_id + 1):
        offset = offsets.get(obj_id, 0)
        pdf.extend(f"{offset:010d} 00000 n \n".encode("latin-1"))

    pdf.extend(
        (
            f"trailer << /Size {max_obj_id + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_pos}\n%%EOF\n"
        ).encode("latin-1")
    )

    export_path.write_bytes(pdf)
    return export_path

