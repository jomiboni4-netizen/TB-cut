import fs from "node:fs/promises";
import path from "node:path";
import { randomUUID } from "node:crypto";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";
import { checkDependencies } from "./check_workbook_dependencies.mjs";

const dependencyStatus = await checkDependencies();
if (dependencyStatus.status !== "verified") {
  throw new Error(`workbook_runtime_${dependencyStatus.status}: ${dependencyStatus.reason ?? "unknown"}`);
}

const workbookPath = process.argv[2];
const manifestPath = process.argv[3];
const previewPath = process.argv[4];
const manifest = JSON.parse(await fs.readFile(manifestPath, "utf8"));
const counts = new Map();
for (const row of manifest.variants) {
  counts.set(String(row.encoding_id), (counts.get(String(row.encoding_id)) || 0) + 1);
}

const input = await FileBlob.load(workbookPath);
const workbook = await SpreadsheetFile.importXlsx(input);
const sheetInventory = await workbook.inspect({kind: "sheet", include: "id,name", maxChars: 100000});
const sheets = sheetInventory.ndjson.split("\n").filter(Boolean).map(JSON.parse).filter(row => row.kind === "sheet");
if (sheetInventory.truncated || sheets.length !== 1) throw new Error("workbook_requires_one_visible_sheet");
const sheet = workbook.worksheets.getItemAt(0);
const used = sheet.getUsedRange();
const values = used.values;
const formulas = used.formulas;
const styles = values.map((row, rowIndex) => row.map((_, columnIndex) =>
  used.format.getStyleIndexAt(rowIndex, columnIndex)));
const headers = values[0].map((value) => String(value ?? ""));
const idHeaderIndices = ["编码 ID", "ID"].flatMap((name) => {
  const index = headers.indexOf(name);
  return index < 0 ? [] : [index];
});
const idCol = idHeaderIndices.length === 1 ? idHeaderIndices[0] : -1;
const titleCol = headers.indexOf("标题");
const countCol = headers.indexOf("剪辑");
if (idCol < 0 || titleCol < 0 || countCol < 0
    || headers.lastIndexOf(headers[idCol]) !== idCol
    || headers.lastIndexOf("标题") !== titleCol || headers.lastIndexOf("剪辑") !== countCol) {
  throw new Error("缺少或重复的编码 ID/标题/剪辑列");
}
const structure = JSON.parse(execFileSync("python3", [
  fileURLToPath(new URL("./check_workbook_structure.py", import.meta.url)),
  workbookPath, sheets[0].name, String(idCol),
], {encoding: "utf8"}));
if (formulas.flat().some(formula => typeof formula === "string" && formula.startsWith("="))) {
  throw new Error("workbook_formulas_not_supported");
}

let lastDataIndex = values.length - 1;
while (lastDataIndex > 0 && String(values[lastDataIndex][idCol] ?? "").trim() === "") lastDataIndex -= 1;
const dataRows = values.slice(1, lastDataIndex + 1);
function columnName(index) {
  let result = "";
  while (index > 0) {
    index -= 1;
    result = String.fromCharCode(65 + index % 26) + result;
    index = Math.floor(index / 26);
  }
  return result;
}
const normalizedIds = dataRows.map((row, index) => {
  const cell = `${columnName(idCol + 1)}${index + 2}`;
  const value = row[idCol];
  if (typeof value === "string" && value !== value.trim()) throw new Error(`encoding_id_surrounding_whitespace:${index + 2}`);
  return structure.numeric_encoding_ids[cell] ?? String(value ?? "");
});
for (let i = 0; i < dataRows.length; i++) {
  const title = dataRows[i][titleCol];
  const hasTitle = typeof title === "string" && title.trim() !== "";
  if (title != null && typeof title !== "string") throw new Error(`title_must_be_text:${i + 2}`);
  if (hasTitle && normalizedIds[i] === "") throw new Error(`required_encoding_id_missing:${i + 2}`);
}
const nonemptyIds = normalizedIds.filter(id => id.trim() !== "");
if (new Set(nonemptyIds).size !== nonemptyIds.length) throw new Error("duplicate_encoding_id");
for (const encodingId of counts.keys()) {
  if (!nonemptyIds.includes(encodingId)) throw new Error("manifest_encoding_id_not_in_workbook");
}
const outputValues = normalizedIds.map((id) => [counts.get(id) || 0]);
sheet.getRangeByIndexes(1, countCol, outputValues.length, 1).values = outputValues;
sheet.getRangeByIndexes(1, countCol, outputValues.length, 1).setNumberFormat("0");
if (lastDataIndex + 1 < values.length) {
  sheet.getRangeByIndexes(lastDataIndex + 1, countCol, values.length - lastDataIndex - 1, 1).clear({ applyTo: "contents" });
}
workbook.recalculate();

function comparableCell(value) {
  if (value instanceof Date) return {date: value.toISOString()};
  if (value === undefined) return null;
  return value;
}
function validateRoundTrip(reopened) {
  const postInventory = reopened.inspect({kind: "sheet", include: "id,name", maxChars: 100000});
  return Promise.resolve(postInventory).then(result => {
    const postSheets = result.ndjson.split("\n").filter(Boolean).map(JSON.parse).filter(row => row.kind === "sheet");
    if (result.truncated || postSheets.length !== 1 || postSheets[0].name !== sheets[0].name) {
      throw new Error("workbook_roundtrip_sheet_mismatch");
    }
    const postSheet = reopened.worksheets.getItem(postSheets[0].name);
    const postRange = postSheet.getUsedRange();
    const postValues = postRange.values;
    const postFormulas = postRange.formulas;
    const postStyles = postValues.map((row, rowIndex) => row.map((_, columnIndex) =>
      postRange.format.getStyleIndexAt(rowIndex, columnIndex)));
    if (postValues.length !== values.length || postValues.some((row, i) => row.length !== values[i].length)) {
      throw new Error("workbook_roundtrip_shape_mismatch");
    }
    for (let r = 0; r < values.length; r++) {
      for (let c = 0; c < values[r].length; c++) {
        if (c === countCol && r > 0) continue;
        if (JSON.stringify(comparableCell(values[r][c])) !== JSON.stringify(comparableCell(postValues[r][c]))
            || formulas[r][c] !== postFormulas[r][c] || styles[r][c] !== postStyles[r][c]) {
          throw new Error(`workbook_roundtrip_non_target_changed:${r + 1}:${c + 1}`);
        }
      }
    }
    for (let i = 0; i < outputValues.length; i++) {
      if (postValues[i + 1][countCol] !== outputValues[i][0]) {
        throw new Error(`workbook_roundtrip_count_mismatch:${i + 2}`);
      }
    }
  });
}

const preview = await workbook.render({ sheetName: sheet.name, range: `A1:C${lastDataIndex + 1}`, scale: 2, format: "png" });
await fs.writeFile(previewPath, new Uint8Array(await preview.arrayBuffer()));
const output = await SpreadsheetFile.exportXlsx(workbook);
const directory = path.dirname(workbookPath);
const temporaryPath = path.join(directory, `.${path.basename(workbookPath)}.${randomUUID()}.tmp.xlsx`);
try {
  await output.save(temporaryPath);
  const reopened = await SpreadsheetFile.importXlsx(await FileBlob.load(temporaryPath));
  await validateRoundTrip(reopened);
  const originalMode = (await fs.stat(workbookPath)).mode;
  await fs.chmod(temporaryPath, originalMode);
  await fs.rename(temporaryPath, workbookPath);
} finally {
  await fs.rm(temporaryPath, {force: true});
}
console.log(JSON.stringify({ rowsUpdated: outputValues.length, totalVideos: outputValues.reduce((sum, row) => sum + row[0], 0), roundTrip: "verified" }));
