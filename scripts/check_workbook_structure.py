#!/usr/bin/env python3
"""Fail-closed OOXML structural checks shared by workbook mutations."""
import argparse
import json
from pathlib import Path
import sys

from index_titles import column_name, encoding_string, workbook_guards


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("workbook")
    parser.add_argument("sheet")
    parser.add_argument("id_column", type=int, help="Zero-based worksheet column index")
    args = parser.parse_args()
    evidence = workbook_guards(Path(args.workbook), args.sheet)
    column = column_name(args.id_column + 1)
    normalized = {}
    for address, (raw, _number_format) in evidence.items():
        if address.startswith(column) and address[len(column):].isdigit():
            normalized[address] = encoding_string(raw, evidence, address)
    print(json.dumps({"numeric_encoding_ids": normalized}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(getattr(error, "args", [type(error).__name__])[0], file=sys.stderr)
        raise SystemExit(1)
