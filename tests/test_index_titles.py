"""Synthetic workbook tests only; no real project inputs or external services."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from xml.sax.saxutils import escape
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import index_titles as titles
import runtime_publication as publication
from locate_link_intros import chinese_number, link_pattern
from runtime_meta import CacheIdentityError, invalidation_path, metadata_path, validate_cache
from runtime_paths import resolve_runtime_paths


def synthetic_xlsx(path, rows, sheet="当前商品", numeric_format="General", extra_sheet=False, formula=False, merged=False):
    """Small OOXML fixture with literal strings, numbers, and optional hazards."""
    namespace = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    cells = []
    for row_index, row in enumerate(rows, 1):
        items = []
        for col, value in enumerate(row, 1):
            if value is None:
                continue
            address = f"{titles.column_name(col)}{row_index}"
            if isinstance(value, (int, float)):
                items.append(f'<c r="{address}" s="1"><v>{value}</v></c>')
            else:
                items.append(f'<c r="{address}" t="inlineStr"><is><t xml:space="preserve">{escape(value)}</t></is></c>')
        if formula and row_index == 2:
            items.append('<c r="C2"><f>1+1</f><v>2</v></c>')
        cells.append(f'<row r="{row_index}">{"".join(items)}</row>')
    second = '<sheet name="Other" sheetId="2" r:id="rId2"/>' if extra_sheet else ''
    relation2 = '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/>' if extra_sheet else ''
    sheet_xml = f'<worksheet xmlns="{namespace}"><dimension ref="A1:C{len(rows)}"/><sheetData>{"".join(cells)}</sheetData>' + ('<mergeCells count="1"><mergeCell ref="A2:A3"/></mergeCells>' if merged else '') + '</worksheet>'
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr('[Content_Types].xml', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>')
        archive.writestr('_rels/.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        archive.writestr('xl/workbook.xml', f'<workbook xmlns="{namespace}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="{sheet}" sheetId="1" r:id="rId1"/>{second}</sheets></workbook>')
        archive.writestr('xl/_rels/workbook.xml.rels', f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>{relation2}<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
        archive.writestr('xl/worksheets/sheet1.xml', sheet_xml)
        if extra_sheet:
            archive.writestr('xl/worksheets/sheet2.xml', sheet_xml)
        archive.writestr('xl/styles.xml', f'<styleSheet xmlns="{namespace}"><numFmts count="1"><numFmt numFmtId="164" formatCode="{numeric_format}"/></numFmts><fonts count="1"><font><sz val="11"/><name val="Arial"/></font></fonts><fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/></cellXfs></styleSheet>')


class IndexTitlesTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repo = self.root / "repo"
        self.workspace = self.root / "workspace"
        self.repo.mkdir()
        self.workspace.mkdir()
        (self.repo / 'config').mkdir()
        (self.repo / 'PROJECT_RULES.md').write_text('Synthetic rules')
        (self.repo / 'config/content_rules.json').write_text('{}')
        self.source = self.root / 'title.xlsx'
        self.rows = [['编码 ID', '标题', '剪辑'], ['0012', 'Synthetic title', None]]
        synthetic_xlsx(self.source, self.rows)
        (self.root / 'input.srt').write_text('synthetic subtitle')
        (self.root / 'input.mp4').write_bytes(b'synthetic')
        self.state = {'title_path': str(self.source), 'subtitle_paths': [str(self.root / 'input.srt')], 'video_paths': [str(self.root / 'input.mp4')], 'batch': {'batch_id': 'test'}, 'runtime': {'pipeline_version': '2.1'}}
        (self.workspace / 'PROJECT_STATE.json').write_text(json.dumps(self.state))
        self.paths = resolve_runtime_paths(self.repo, self.workspace)
        self.output = self.paths.cache_dir / 'titles.json'

    def parsed(self, rows=None):
        return {'schema_version': 1, 'sheet': '当前商品', 'values': copy.deepcopy(self.rows if rows is None else rows)}

    def model(self, rows):
        return titles.build_model(self.source, self.parsed(rows), {})

    def generate(self, rows=None):
        with patch.object(titles, 'parse_workbook', return_value=self.parsed(rows)):
            return titles.generate(self.paths)

    def test_valid_rows_are_numbered_continuously(self):
        model = self.model([self.rows[0], ['a', 'One'], ['b', 'Two']])
        self.assertEqual([p['product_index'] for p in model['products']], [1, 2])

    def test_empty_rows_do_not_consume_numbers(self):
        model = self.model([self.rows[0], [], [None, '  '], ['a', 'One'], [], ['b', 'Two'], []])
        self.assertEqual([p['product_index'] for p in model['products']], [1, 2])

    def test_missing_id_fails(self):
        with self.assertRaisesRegex(titles.TitlesError, 'required_product_field_empty'):
            self.model([self.rows[0], [None, 'Title']])

    def test_missing_title_fails(self):
        for row in (['id', None], [None, None, 0]):
            with self.subTest(row=row), self.assertRaisesRegex(titles.TitlesError, 'required_product_field_empty'):
                self.model([self.rows[0], row])

    def test_duplicate_id_fails(self):
        with self.assertRaisesRegex(titles.TitlesError, 'duplicate_encoding_id'):
            self.model([self.rows[0], ['001', 'One'], ['001', 'Two']])

    def test_text_ids_and_leading_zeros_are_preserved(self):
        values = ['00012', 'AB-01', '12345678901234567890']
        self.assertEqual([p['encoding_id'] for p in self.model([self.rows[0], *[[v, 'Title'] for v in values]])['products']], values)

    def test_numeric_id_formats_and_precision(self):
        self.assertEqual(titles.encoding_string(12, {'A2': ('12', '00000')}, 'A2'), '00012')
        self.assertEqual(titles.encoding_string(12, {'A2': ('12', 'General')}, 'A2'), '12')
        for value, raw, fmt in ((1.2, '1.2', 'General'), (1234567890123456, '1234567890123456', 'General'), (12, '12', '0.00'), (12, '13', 'General')):
            with self.subTest(value=value, fmt=fmt), self.assertRaises(titles.TitlesError):
                titles.encoding_string(value, {'A2': (raw, fmt)}, 'A2')

    def test_actual_sheet_name(self):
        self.assertEqual(self.model(self.rows)['sheet'], '当前商品')

    def test_repeated_generation_is_deterministic_and_metadata_valid(self):
        self.generate()
        first = self.output.read_bytes()
        self.generate()
        self.assertEqual(first, self.output.read_bytes())
        meta = validate_cache(self.paths, self.output)
        self.assertEqual(meta['producer'], 'index_titles.py')

    def test_validation_failure_preserves_both_existing_files(self):
        self.generate()
        before = (self.output.read_bytes(), metadata_path(self.output).read_bytes())
        with self.assertRaises(titles.TitlesError):
            self.generate([self.rows[0], ['id', None]])
        self.assertEqual(before, (self.output.read_bytes(), metadata_path(self.output).read_bytes()))

    def test_metadata_failure_preserves_existing_pair(self):
        self.generate()
        before = (self.output.read_bytes(), metadata_path(self.output).read_bytes())
        with patch.object(titles, 'write_metadata', side_effect=OSError('injected')), self.assertRaises(OSError):
            self.generate()
        self.assertEqual(before, (self.output.read_bytes(), metadata_path(self.output).read_bytes()))
        self.assertEqual(sorted(p.name for p in self.paths.cache_dir.iterdir()), ['titles.json', 'titles.json.meta.json'])

    def test_second_publish_failure_rolls_back_existing_or_new_pair(self):
        for existing in (False, True):
            with self.subTest(existing=existing):
                if existing:
                    self.generate()
                before = (self.output.read_bytes(), metadata_path(self.output).read_bytes()) if existing else None
                replace = os.replace
                def fault(source, target):
                    if Path(target) == metadata_path(self.output):
                        raise OSError('injected publication failure')
                    return replace(source, target)
                with patch.object(publication.os, 'replace', side_effect=fault), self.assertRaises(OSError):
                    self.generate([self.rows[0], ['changed', 'Changed']])
                if existing:
                    self.assertEqual(before, (self.output.read_bytes(), metadata_path(self.output).read_bytes()))
                else:
                    self.assertFalse(self.output.exists())
                    self.assertFalse(metadata_path(self.output).exists())

    def test_publication_and_rollback_failures_preserve_original_error_and_block_reads(self):
        for existing in (False, True):
            for identical in (False, True):
                with self.subTest(existing=existing, identical=identical):
                    self.generate()
                    if not existing:
                        self.output.unlink()
                        metadata_path(self.output).unlink()
                    replace, unlink = os.replace, Path.unlink
                    def fail_replace(source, target):
                        if Path(target) == metadata_path(self.output):
                            raise OSError('original publication error')
                        if str(source).endswith('.previous'):
                            raise OSError('rollback error')
                        return replace(source, target)
                    def fail_unlink(path, *args, **kwargs):
                        if path == self.output:
                            raise OSError('rollback unlink error')
                        return unlink(path, *args, **kwargs)
                    with patch.object(publication.os, 'replace', side_effect=fail_replace), patch.object(Path, 'unlink', fail_unlink):
                        with self.assertRaisesRegex(OSError, '^original publication error$'):
                            self.generate(None if identical else [self.rows[0], ['different', 'Changed']])
                    self.assertTrue(invalidation_path(self.output).exists())
                    with self.assertRaisesRegex(CacheIdentityError, 'publication_incomplete'):
                        validate_cache(self.paths, self.output)
                    self.generate()
                    validate_cache(self.paths, self.output)

    def test_30_rows_ignore_physical_row_and_match_link_numbers(self):
        rows = [self.rows[0]]
        for i in range(1, 31):
            rows.extend([[], [str(i).zfill(4), f'Title {i}'], [None, None]])
        products = self.model(rows)['products']
        self.assertEqual([p['product_index'] for p in products], list(range(1, 31)))
        for p in products:
            i = p['product_index']
            for speech in (f'{i}号链接', f'{chinese_number(i)}号链接'):
                self.assertEqual([j for j in range(1,31) if link_pattern(j).search(speech)], [i])

    def test_headers_and_empty_workbook_rejected(self):
        for rows in ([], [['标题']], [['编码 ID', '编码 ID', '标题']], [self.rows[0]]):
            with self.subTest(rows=rows), self.assertRaises(titles.TitlesError):
                self.model(rows)

    def test_formulas_merges_and_multiple_sheets_rejected(self):
        for option in ('formula', 'merged', 'extra_sheet'):
            with self.subTest(option=option):
                synthetic_xlsx(self.source, self.rows, **{option: True})
                with self.assertRaises(titles.TitlesError):
                    titles.workbook_guards(self.source, '当前商品')

    def test_changed_input_aborts_without_publication(self):
        def changed(*args):
            self.source.write_bytes(b'changed while parsing')
            return self.parsed()
        with patch.object(titles, 'parse_workbook', side_effect=changed), self.assertRaises((titles.TitlesError, zipfile.BadZipFile)):
            titles.generate(self.paths)
        self.assertFalse(self.output.exists())

    def test_existing_cache_is_not_a_generation_input(self):
        self.paths.cache_dir.mkdir()
        self.output.write_bytes(b'not JSON; must not be parsed')
        metadata_path(self.output).write_bytes(b'untrusted old metadata')
        self.generate()
        self.assertEqual(json.loads(self.output.read_bytes())['products'][0]['encoding_id'], '0012')
        validate_cache(self.paths, self.output)

    def test_state_changed_during_parse_aborts_and_preserves_pair(self):
        self.generate()
        before = (self.output.read_bytes(), metadata_path(self.output).read_bytes())
        def changed(*args):
            state = copy.deepcopy(self.state)
            state['batch']['batch_id'] = 'other-batch'
            self.paths.state_path.write_text(json.dumps(state))
            return self.parsed()
        with patch.object(titles, 'parse_workbook', side_effect=changed), self.assertRaisesRegex(CacheIdentityError, 'inputs_changed'):
            titles.generate(self.paths)
        self.assertEqual(before, (self.output.read_bytes(), metadata_path(self.output).read_bytes()))

    def test_node_machine_parser_and_real_generator_on_synthetic_workbook(self):
        # Exercise the actual existing artifact-tool dependency, not a mock.
        node = os.environ.get('TB_CUT_TEST_NODE', 'node')
        rows = [self.rows[0], ['00012', 'Text ID'], [], [34, 'Numeric ID']]
        synthetic_xlsx(self.source, rows, numeric_format='00000')
        parsed = titles.parse_workbook(self.source, ROOT, node)
        self.assertEqual(parsed['sheet'], '当前商品')
        self.paths = resolve_runtime_paths(ROOT, self.workspace)
        output = titles.generate(self.paths, node)
        self.assertEqual([p['encoding_id'] for p in json.loads(output.read_bytes())['products']], ['00012', '00034'])
        validate_cache(self.paths, output)
        first = output.read_bytes()
        titles.generate(self.paths, node)
        self.assertEqual(first, output.read_bytes())


if __name__ == '__main__':
    unittest.main()
