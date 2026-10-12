#!/usr/bin/env python3
"""Generate titles from the configured workbook; never use old titles as input."""
from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import zipfile

from runtime_meta import validate_cache, write_metadata
from runtime_paths import resolve_runtime_paths
from runtime_publication import BuildSnapshot, generator_lock, publish_pair
from workbook_runtime import preferred_node

NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


class TitlesError(ValueError):
    pass


def blank(value):
    return value is None or isinstance(value, str) and not value.strip()


def workbook_guards(source: Path, sheet_name: str) -> dict:
    """Read OOXML safety evidence, not business rows; Node owns value parsing.

    Raw numeric tokens/styles prevent JS rounding or display zero loss in IDs.
    Reject structural ambiguity instead of silently ignoring formulas/merges.
    """
    with zipfile.ZipFile(source) as archive:
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        sheets = workbook.findall("s:sheets/s:sheet", NS)
        if len(sheets) != 1 or sheets[0].get("name") != sheet_name or sheets[0].get("state", "visible") != "visible":
            raise TitlesError("titles_requires_one_visible_sheet")
        relationship = sheets[0].get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
        links = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        target = next((x.get("Target") for x in links if x.get("Id") == relationship and x.get("TargetMode") != "External"), None)
        if not target:
            raise TitlesError("sheet_relationship_missing")
        from posixpath import normpath
        location = normpath(target.lstrip("/") if target.startswith("/") else "xl/" + target)
        sheet = ET.fromstring(archive.read(location))
        if sheet.find("s:mergeCells/s:mergeCell", NS) is not None or sheet.find(".//s:f", NS) is not None:
            raise TitlesError("merged_cells_or_formulas_not_supported")
        if any(x.get("hidden") in {"1", "true"} for x in sheet.findall("s:sheetData/s:row", NS) + sheet.findall("s:cols/s:col", NS)):
            raise TitlesError("hidden_rows_or_columns_not_supported")
        formats = {0: "General", 1: "0", 49: "@"}
        styles = [0]
        if "xl/styles.xml" in archive.namelist():
            style = ET.fromstring(archive.read("xl/styles.xml"))
            formats.update({int(x.get("numFmtId")): x.get("formatCode") for x in style.findall("s:numFmts/s:numFmt", NS)})
            styles = [int(x.get("numFmtId", "0")) for x in style.findall("s:cellXfs/s:xf", NS)]
        numeric = {}
        for cell in sheet.findall("s:sheetData/s:row/s:c", NS):
            if cell.get("t") == "e":
                raise TitlesError("workbook_cell_error")
            value = cell.find("s:v", NS)
            if cell.get("t", "n") == "n" and value is not None:
                index = int(cell.get("s", "0"))
                if index >= len(styles):
                    raise TitlesError("invalid_cell_style")
                numeric[cell.get("r")] = (value.text, formats.get(styles[index]))
        return numeric


def column_name(index):
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def encoding_string(value, numeric_evidence, address):
    if isinstance(value, str) and address not in numeric_evidence:
        if value != value.strip():
            raise TitlesError(f"encoding_id_surrounding_whitespace: {address}")
        return value
    if isinstance(value, bool) or not isinstance(value, (str, int, float)) or address not in numeric_evidence:
        raise TitlesError(f"encoding_id_type_invalid: {address}")
    raw, number_format = numeric_evidence[address]
    try:
        number = Decimal(raw)
        parsed_number = Decimal(str(value))
    except (InvalidOperation, TypeError):
        raise TitlesError(f"encoding_id_number_invalid: {address}") from None
    # Excel numeric storage cannot establish lossless long identifiers.
    if not number.is_finite() or number < 0 or number != number.to_integral_value() or number >= 10**15 or number != parsed_number:
        raise TitlesError(f"encoding_id_precision_or_value_invalid: {address}")
    text = str(int(number))
    if number_format in {"General", "@"}:
        return text
    if isinstance(number_format, str) and re.fullmatch("0+", number_format):
        return text.zfill(len(number_format))
    raise TitlesError(f"encoding_id_number_format_ambiguous: {address}")


def build_model(source: Path, parsed: dict, numeric_evidence: dict, *, skip_blank_titles: bool = False,
                preserve_blank_row_indices: bool = False) -> dict:
    if parsed.get("schema_version") != 1 or not isinstance(parsed.get("sheet"), str) or not parsed["sheet"]:
        raise TitlesError("parser_schema_invalid")
    rows = parsed.get("values")
    if not isinstance(rows, list) or not rows or any(not isinstance(row, list) for row in rows):
        raise TitlesError("titles_rows_invalid")
    headers = rows[0]
    if any(headers.count(name) != 1 for name in ("编码 ID", "标题")):
        raise TitlesError("required_header_missing_or_duplicate")
    id_col, title_col = headers.index("编码 ID"), headers.index("标题")
    products, placeholders, seen = [], [], set()
    for physical_row, row in enumerate(rows[1:], 2):
        value = row[id_col] if id_col < len(row) else None
        title = row[title_col] if title_col < len(row) else None
        if preserve_blank_row_indices and blank(title) and (skip_blank_titles or all(blank(v) for v in row)):
            encoding = None if blank(value) else encoding_string(value, numeric_evidence, f"{column_name(id_col+1)}{physical_row}")
            if encoding is not None:
                if encoding in seen:
                    raise TitlesError(f"duplicate_encoding_id: row {physical_row}")
                seen.add(encoding)
            placeholders.append({"product_index": physical_row - 1, "worksheet_row": physical_row,
                                 "encoding_id": encoding, "reason": "blank_title", "cut": False})
            continue
        if all(blank(value) for value in row):
            continue
        if blank(title) and skip_blank_titles:
            continue
        if blank(value) or blank(title):
            raise TitlesError(f"required_product_field_empty: row {physical_row}")
        if not isinstance(title, str):
            raise TitlesError(f"title_must_be_text: row {physical_row}")
        encoding = encoding_string(value, numeric_evidence, f"{column_name(id_col+1)}{physical_row}")
        if encoding in seen:
            raise TitlesError(f"duplicate_encoding_id: row {physical_row}")
        seen.add(encoding)
        product_index = physical_row - 1 if preserve_blank_row_indices else len(products) + 1
        products.append({"product_index": product_index, "encoding_id": encoding, "title": title})
    if not products:
        raise TitlesError("titles_has_no_products")
    model = {"schema_version": 2, "generator": "index_titles.py:v2", "source": str(source), "sheet": parsed["sheet"], "products": products}
    if preserve_blank_row_indices:
        model["placeholders"] = placeholders
    return model


def parse_workbook(source: Path, repo: Path, node: str) -> dict:
    result = subprocess.run([node, str(repo / "scripts/inspect_title_workbook.mjs"), str(source), "--json"],
                            check=True, text=True, capture_output=True)
    return json.loads(result.stdout)


def generate(paths, node="node") -> Path:
    with generator_lock(paths):
        return _generate_locked(paths, node)


def _generate_locked(paths, node):
    snapshot = BuildSnapshot(paths)
    raw_source = snapshot.state.get("title_path")
    if not isinstance(raw_source, str) or not raw_source:
        raise TitlesError("title_path_missing")
    source = Path(raw_source).expanduser().resolve()
    output = paths.upstream_cache_dir / 'titles.json'
    if snapshot.identity.get('schema_version') == 2 and output.exists():
        validate_cache(paths, output, expected=snapshot.identity)
        snapshot.check()
        return output
    parsed = parse_workbook(source, paths.repo_root, node)
    overrides = snapshot.state.get("batch_overrides") or {}
    skip_blank_titles = overrides.get("skip_blank_titles", False)
    if not isinstance(skip_blank_titles, bool):
        raise TitlesError("skip_blank_titles_override_invalid")
    preserve_blank_row_indices = overrides.get("preserve_blank_row_indices", False)
    if not isinstance(preserve_blank_row_indices, bool):
        raise TitlesError("preserve_blank_row_indices_override_invalid")
    model = build_model(source, parsed, workbook_guards(source, parsed.get("sheet")),
                        skip_blank_titles=skip_blank_titles,
                        preserve_blank_row_indices=preserve_blank_row_indices)
    if preserve_blank_row_indices:
        model["generator"] = "index_titles.py:v3"
    content = (json.dumps(model, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
    output = paths.upstream_cache_dir / "titles.json"
    with tempfile.TemporaryDirectory(prefix=".titles-", dir=paths.upstream_cache_dir) as temporary:
        staged = Path(temporary) / "titles.json"
        staged.write_bytes(content)
        staged_meta = write_metadata(paths, staged, "index_titles.py", identity=snapshot.identity)
        validate_cache(paths, staged, expected=snapshot.identity)
        publish_pair(staged, staged_meta, output, before_commit=snapshot.check)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--workspace")
    parser.add_argument("--node", default=preferred_node(), help="Node executable with the locked artifact-tool distribution")
    args = parser.parse_args()
    generate(resolve_runtime_paths(args.root, args.workspace), args.node)
    print("titles index generated and identity validated")


if __name__ == "__main__":
    main()
