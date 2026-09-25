import fs from "node:fs/promises";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const inputPath = process.argv[2];
const previewPath = process.argv[3];
const input = await FileBlob.load(inputPath);
const workbook = await SpreadsheetFile.importXlsx(input);
const summary = await workbook.inspect({
  kind: "workbook,sheet,table,region",
  maxChars: 12000,
  tableMaxRows: 40,
  tableMaxCols: 12,
  tableMaxCellChars: 100,
});
console.log(summary.ndjson);
const first = workbook.worksheets.getItemAt(0);
const preview = await workbook.render({ sheetName: first.name, autoCrop: "all", scale: 1.5, format: "png" });
await fs.writeFile(previewPath, new Uint8Array(await preview.arrayBuffer()));
