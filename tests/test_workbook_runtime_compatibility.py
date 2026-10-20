"""Real artifact-tool contract tests; all workbook writes use temporary copies."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "tests")]
from index_titles import build_model, parse_workbook, workbook_guards
from workbook_runtime import preferred_node
from test_index_titles import synthetic_xlsx


class WorkbookRuntimeCompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.node = preferred_node()
        cls.checker = ROOT / "scripts/check_workbook_dependencies.mjs"
        cls.update_counts = ROOT / "scripts/update_title_counts.mjs"

    def run_node(self, *args, check=True):
        result = subprocess.run([self.node, *map(str, args)], cwd=ROOT,
                                text=True, capture_output=True)
        if check and result.returncode:
            self.fail(f"Node script failed ({result.returncode}): {result.stderr[-500:]}")
        return result

    def test_current_runtime_identity_and_capabilities_are_reused(self):
        result = self.run_node(self.checker)
        first = json.loads(result.stdout)
        self.assertEqual(first["status"], "verified")
        self.assertEqual(first["package"], "@oai/artifact-tool")
        self.assertTrue(first["artifactTool"])
        again = json.loads(self.run_node(self.checker).stdout)
        self.assertEqual(again["status"], "verified")
        self.assertEqual(again["verification"], "cached-environment-record")

    def test_real_import_preserves_row_numbers_and_text_ids(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "titles.xlsx"
            synthetic_xlsx(source, [
                ["编码 ID", "标题", "剪辑"],
                ["00012", "Synthetic one", None],
                ["00013", None, None],
                [14, "Synthetic two", None],
                [],
                [],
            ], numeric_format="00000")
            parsed = parse_workbook(source, ROOT, self.node)
            model = build_model(source, parsed, workbook_guards(source, parsed["sheet"]),
                                skip_blank_titles=True, preserve_blank_row_indices=True)
            self.assertEqual([(row["product_index"], row["encoding_id"]) for row in model["products"]],
                             [(1, "00012"), (3, "00014")])
            self.assertEqual([(row["product_index"], row["worksheet_row"], row["encoding_id"])
                              for row in model["placeholders"]], [(2, 3, "00013")])
            self.assertEqual(len(parsed["values"]), 4)

    def test_count_backfill_round_trip_keeps_other_cells_and_original(self):
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            source = temp / "source.xlsx"
            work = temp / "working-copy.xlsx"
            preview = temp / "preview.png"
            manifest = temp / "manifest.json"
            synthetic_xlsx(source, [
                ["编码 ID", "标题", "剪辑"],
                ["00012", "Synthetic one", None],
                ["00013", None, None],
                [14, "Synthetic two", None],
            ], numeric_format="00000")
            original_digest = hashlib.sha256(source.read_bytes()).hexdigest()
            work.write_bytes(source.read_bytes())
            manifest.write_text(json.dumps({"variants": [
                {"encoding_id": "00012"},
                {"encoding_id": "00014"},
                {"encoding_id": "00014"},
            ]}), encoding="utf-8")
            self.run_node(self.update_counts, work, manifest, preview)
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), original_digest)
            self.assertTrue(preview.is_file())
            parsed = parse_workbook(work, ROOT, self.node)
            rows = parsed["values"]
            self.assertEqual([row[0] for row in rows[1:4]], ["00012", "00013", 14])
            self.assertEqual([row[1] for row in rows[1:4]], ["Synthetic one", None, "Synthetic two"])
            self.assertEqual([row[2] for row in rows[1:4]], [1, 0, 2])
            model = build_model(work, parsed, workbook_guards(work, parsed["sheet"]),
                                skip_blank_titles=True, preserve_blank_row_indices=True)
            self.assertEqual([product["encoding_id"] for product in model["products"]], ["00012", "00014"])

    def test_formula_input_fails_without_changing_workbook(self):
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            workbook = temp / "formula.xlsx"
            preview = temp / "preview.png"
            manifest = temp / "manifest.json"
            synthetic_xlsx(workbook, [["编码 ID", "标题", "剪辑"], ["00012", "Synthetic", None]], formula=True)
            manifest.write_text(json.dumps({"variants": [{"encoding_id": "00012"}]}), encoding="utf-8")
            original_digest = hashlib.sha256(workbook.read_bytes()).hexdigest()
            result = self.run_node(self.update_counts, workbook, manifest, preview, check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("merged_cells_or_formulas_not_supported", result.stderr)
            self.assertEqual(hashlib.sha256(workbook.read_bytes()).hexdigest(), original_digest)
            self.assertFalse(preview.exists())

    def test_semver_is_not_a_gate_but_required_api_is(self):
        with tempfile.TemporaryDirectory() as temp:
            fake = Path(temp) / "node_modules/@oai/artifact-tool"
            (fake / "dist").mkdir(parents=True)
            (fake / "package.json").write_text(json.dumps({"name": "@oai/artifact-tool", "version": "2.8.94"}))
            entry = fake / "dist/artifact_tool.mjs"
            entry.write_text("export const FileBlob={load(){}}; export const SpreadsheetFile={importXlsx(){},exportXlsx(){}};\n")
            script = (
                "import {checkDependencies,validateApiSurface} from './scripts/check_workbook_dependencies.mjs';"
                f"const result=await checkDependencies({{moduleEntryOverride:{json.dumps(str(entry))}}});"
                "console.log(JSON.stringify({result,missing:validateApiSurface({FileBlob:{},SpreadsheetFile:{}})}));"
            )
            result = json.loads(self.run_node("--input-type=module", "-e", script).stdout)
            self.assertEqual(result["result"]["status"], "verified")
            self.assertEqual(result["result"]["artifactTool"], "2.8.94")
            self.assertEqual(set(result["missing"]), {
                "FileBlob.load", "SpreadsheetFile.importXlsx", "SpreadsheetFile.exportXlsx"})
            package = fake / "package.json"
            package.write_text(json.dumps({"name": "@oai/artifact-tool", "version": "99.0.0"}))
            result = json.loads(self.run_node("--input-type=module", "-e", script).stdout)
            self.assertEqual(result["result"]["status"], "verified")
            self.assertEqual(result["result"]["artifactTool"], "99.0.0")
            entry.write_text("export const FileBlob={}; export const SpreadsheetFile={};\n")
            result = json.loads(self.run_node("--input-type=module", "-e", script).stdout)
            self.assertEqual(result["result"]["status"], "incompatible")
            self.assertEqual(result["result"]["reason"], "artifact_tool_required_api_missing")


if __name__ == "__main__":
    unittest.main()
