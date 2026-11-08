"""Fixed, offline parsers. Run only in the bounded BOM Runner container."""

import csv
import io
import json
from pathlib import PurePosixPath
import re
import sys
import zipfile
import xml.etree.ElementTree as ET

VERSION = "bom-parser-v1"
MAX_INPUT = 8 * 1024 * 1024
MAX_OUTPUT = 2 * 1024 * 1024
MAX_ROWS = 4096
MAX_COLUMNS = 1024
MAX_CELL = 16384
MAX_XML = 16 * 1024 * 1024
NS = {"table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
      "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
      "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0"}


class Rejected(Exception):
    pass


def fail(code):
    raise Rejected(code)


def cells_ok(cells):
    while cells and not cells[-1]:
        cells.pop()
    if len(cells) > MAX_COLUMNS or any(len(x) > MAX_CELL or "\x00" in x for x in cells):
        fail("PARSER_CELL_LIMIT")
    return cells


def csv_sheets(data):
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        # Legacy vendor exports are accepted explicitly, never with replacement.
        text = data.decode("cp1252")
    if "\x00" in text:
        fail("PARSER_MALFORMED")
    csv.field_size_limit(MAX_CELL)
    try:
        dialect = csv.Sniffer().sniff(text[:32768], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    rows = []
    for number, cells in enumerate(csv.reader(io.StringIO(text, newline=""), dialect, strict=True), 1):
        cells = cells_ok(cells)
        if cells:
            rows.append({"row_number": number, "cells": cells})
        if len(rows) > MAX_ROWS or number > 1048576:
            fail("PARSER_ROW_LIMIT")
    return [{"name": "CSV", "rows": rows}]


def repeated(element, key):
    value = element.attrib.get("{" + NS["table"] + "}" + key, "1")
    if not re.fullmatch(r"[0-9]{1,9}", value) or not 1 <= int(value) <= 1048576:
        fail("PARSER_REPEAT_LIMIT")
    return int(value)


def odf_text(element):
    """Preserve ODF whitespace rather than concatenating source identifiers."""
    parts = []
    length = 0

    def append(value):
        nonlocal length
        value = value or ""
        length += len(value)
        if length > MAX_CELL:
            fail("PARSER_CELL_LIMIT")
        parts.append(value)

    def visit(node, depth=0):
        if depth > 64:
            fail("PARSER_XML_LIMIT")
        append(node.text)
        for child in node:
            if child.tag == "{" + NS["text"] + "}s":
                count = child.attrib.get("{" + NS["text"] + "}c", "1")
                if not re.fullmatch(r"[0-9]{1,5}", count) or not 1 <= int(count) <= MAX_CELL:
                    fail("PARSER_CELL_LIMIT")
                append(" " * int(count))
            elif child.tag == "{" + NS["text"] + "}tab":
                append("\t")
            elif child.tag == "{" + NS["text"] + "}line-break":
                append("\n")
            else:
                visit(child, depth + 1)
            append(child.tail)
    visit(element)
    return "".join(parts)


def ods_sheets(data):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        infos = archive.infolist()
        if not 1 <= len(infos) <= 128 or len({x.filename for x in infos}) != len(infos):
            fail("PARSER_ARCHIVE_REJECTED")
        total = 0
        for info in infos:
            name = PurePosixPath(info.filename)
            mode = (info.external_attr >> 16) & 0o170000
            if (name.is_absolute() or ".." in name.parts or "\\" in info.filename
                    or mode == 0o120000 or info.flag_bits & 1
                    or info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}):
                fail("PARSER_ARCHIVE_REJECTED")
            total += info.file_size
            if (info.file_size > MAX_XML or total > 32 * 1024 * 1024
                    or (info.file_size > 1024 * 1024 and info.file_size > max(info.compress_size, 1) * 300)):
                fail("PARSER_ARCHIVE_LIMIT")
        try:
            mimetype = archive.read("mimetype")
            if mimetype != b"application/vnd.oasis.opendocument.spreadsheet":
                fail("PARSER_FORMAT_MISMATCH")
            xml = archive.read("content.xml")
        except KeyError:
            fail("PARSER_MALFORMED")
        # ODF XML is accepted as UTF-8 only. Reject alternate encodings before
        # parsing so UTF-16/32 cannot hide a DTD from the explicit policy check.
        if b"\x00" in xml:
            fail("PARSER_XML_ENCODING_REJECTED")
        try:
            xml_text = xml.decode("utf-8-sig")
        except UnicodeDecodeError:
            fail("PARSER_XML_ENCODING_REJECTED")
        declaration = re.match(r"\s*<\?xml\s+([^?]*)\?>", xml_text)
        if declaration:
            encoding = re.search(r"encoding\s*=\s*['\"]([^'\"]+)['\"]", declaration[1], re.I)
            if encoding and encoding[1].lower().replace("-", "") != "utf8":
                fail("PARSER_XML_ENCODING_REJECTED")
        if re.search(r"<!\s*(DOCTYPE|ENTITY)\b", xml_text, re.I):
            fail("PARSER_XML_ENTITY_REJECTED")
        # ODF scripts, linked external sheets, and formula evaluation are not jobs.
        root = ET.fromstring(xml_text)
        count = 0
        stack = [(root, 0)]
        while stack:
            element, depth = stack.pop()
            count += 1
            if count > 100000 or depth > 64:
                fail("PARSER_XML_LIMIT")
            local = element.tag.rsplit("}", 1)[-1]
            if local in {"scripts", "script", "table-source", "dde-source"}:
                if len(element) or element.text or local != "scripts":
                    fail("PARSER_ACTIVE_CONTENT_REJECTED")
            for key, value in element.attrib.items():
                if key.endswith("}href") and value and not value.startswith("#"):
                    fail("PARSER_EXTERNAL_REFERENCE_REJECTED")
                if key.endswith("}formula"):
                    fail("PARSER_FORMULA_REQUIRES_REVIEW")
            stack.extend((child, depth + 1) for child in element)
        sheets = []
        names = set()
        total_rows = 0
        for table in root.findall(".//table:table", NS):
            name = table.attrib.get("{" + NS["table"] + "}name", "")
            if not name or len(name) > 250 or name in names or len(sheets) >= 32:
                fail("PARSER_SHEET_REJECTED")
            names.add(name)
            rows = []
            number = 1
            for row in table.findall(".//table:table-row", NS):
                row_repeat = repeated(row, "number-rows-repeated")
                cells = []
                column = 0
                for cell in row:
                    if cell.tag not in {"{" + NS["table"] + "}table-cell", "{" + NS["table"] + "}covered-table-cell"}:
                        continue
                    cell_repeat = repeated(cell, "number-columns-repeated")
                    paragraphs = cell.findall("text:p", NS)
                    value = "\n".join(odf_text(p) for p in paragraphs)
                    if not paragraphs:
                        value = cell.attrib.get("{" + NS["office"] + "}string-value", "")
                        if not value:
                            value = cell.attrib.get("{" + NS["office"] + "}value", "")
                    if len(value) > MAX_CELL or "\x00" in value:
                        fail("PARSER_CELL_LIMIT")
                    if value:
                        if column + cell_repeat > MAX_COLUMNS:
                            fail("PARSER_CELL_LIMIT")
                        cells.extend([""] * (column - len(cells)))
                        cells.extend([value] * cell_repeat)
                    column += cell_repeat
                    if column > 1048576:
                        fail("PARSER_REPEAT_LIMIT")
                if cells:
                    if total_rows + row_repeat > MAX_ROWS:
                        fail("PARSER_ROW_LIMIT")
                    for offset in range(row_repeat):
                        rows.append({"row_number": number + offset, "cells": list(cells)})
                    total_rows += row_repeat
                number += row_repeat
                if number > 1048577:
                    fail("PARSER_ROW_LIMIT")
            sheets.append({"name": name, "rows": rows})
        if not sheets:
            fail("PARSER_EMPTY")
        return sheets


def pdf_sheets(data):
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(data), strict=True)
    if reader.is_encrypted or len(reader.pages) > 256:
        fail("PARSER_PDF_REJECTED")
    root = reader.trailer["/Root"]
    if any(key in root for key in ("/OpenAction", "/AA")) or any(key in root.get("/Names", {}) for key in ("/JavaScript", "/EmbeddedFiles")):
        fail("PARSER_ACTIVE_CONTENT_REJECTED")
    sheets = []
    count = 0
    for number, page in enumerate(reader.pages, 1):
        if "/AA" in page:
            fail("PARSER_ACTIVE_CONTENT_REJECTED")
        text = page.extract_text(extraction_mode="layout")
        if not text.strip():
            fail("PARSER_PDF_OCR_REQUIRED")
        rows = []
        for line_number, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            # Text lines are evidence, never silently claimed as verified BOM rows.
            cells = cells_ok([line.rstrip()])
            rows.append({"row_number": line_number, "cells": cells})
            count += 1
            if count > MAX_ROWS:
                fail("PARSER_ROW_LIMIT")
        sheets.append({"name": "page:" + str(number), "rows": rows})
    return sheets


def main():
    if len(sys.argv) != 3 or sys.argv[1] not in {"csv", "ods", "pdf"} or sys.argv[2] != "/input/blob":
        fail("PARSER_ARGUMENT_REJECTED")
    with open(sys.argv[2], "rb") as stream:
        data = stream.read(MAX_INPUT + 1)
    if not data or len(data) > MAX_INPUT:
        fail("PARSER_INPUT_LIMIT")
    kind = sys.argv[1]
    if kind == "ods" and not data.startswith(b"PK") or kind == "pdf" and not data.startswith(b"%PDF-"):
        fail("PARSER_FORMAT_MISMATCH")
    sheets = {"csv": csv_sheets, "ods": ods_sheets, "pdf": pdf_sheets}[kind](data)
    if not any(sheet["rows"] for sheet in sheets):
        fail("PARSER_EMPTY")
    result = {"parser_version": VERSION, "format": kind, "sheets": sheets}
    encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_OUTPUT:
        fail("PARSER_OUTPUT_LIMIT")
    sys.stdout.buffer.write(encoded)


if __name__ == "__main__":
    try:
        main()
    except Rejected as error:
        sys.stdout.write(json.dumps({"error": str(error)}))
        raise SystemExit(2)
    except Exception:
        sys.stdout.write('{"error":"PARSER_MALFORMED"}')
        raise SystemExit(2)
