import fs from "node:fs/promises";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

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
const sheet = workbook.worksheets.getItemAt(0);
const used = sheet.getUsedRange();
const values = used.values;
const headers = values[0].map((value) => String(value ?? "").trim());
const idCol = headers.indexOf("编码 ID");
const countCol = headers.indexOf("剪辑");
if (idCol < 0 || countCol < 0) throw new Error("缺少编码 ID或剪辑列");

let lastDataIndex = values.length - 1;
while (lastDataIndex > 0 && String(values[lastDataIndex][idCol] ?? "").trim() === "") lastDataIndex -= 1;
const dataRows = values.slice(1, lastDataIndex + 1);
const outputValues = dataRows.map((row) => [counts.get(String(row[idCol])) || 0]);
sheet.getRangeByIndexes(1, countCol, outputValues.length, 1).values = outputValues;
sheet.getRangeByIndexes(1, countCol, outputValues.length, 1).setNumberFormat("0");
if (lastDataIndex + 1 < values.length) {
  sheet.getRangeByIndexes(lastDataIndex + 1, countCol, values.length - lastDataIndex - 1, 1).clear({ applyTo: "contents" });
}
workbook.recalculate();

const check = await workbook.inspect({
  kind: "table",
  range: `${sheet.name}!A1:C${lastDataIndex + 1}`,
  include: "values,formulas",
  tableMaxRows: lastDataIndex + 1,
  tableMaxCols: 3,
  maxChars: 12000,
});
console.log(check.ndjson);
const errors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!",
  options: { useRegex: true, maxResults: 100 },
  summary: "final formula error scan",
});
console.log(errors.ndjson);
const preview = await workbook.render({ sheetName: sheet.name, range: `A1:C${lastDataIndex + 1}`, scale: 2, format: "png" });
await fs.writeFile(previewPath, new Uint8Array(await preview.arrayBuffer()));
const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(workbookPath);
console.log(JSON.stringify({ workbookPath, rowsUpdated: outputValues.length, totalVideos: outputValues.reduce((sum, row) => sum + row[0], 0) }));
