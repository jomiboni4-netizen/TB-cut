import fs from "node:fs/promises";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

import { checkDependencies } from "./check_workbook_dependencies.mjs";

await checkDependencies();
const inputPath = process.argv[2];
const previewPath = process.argv[3];
const input = await FileBlob.load(inputPath);
const workbook = await SpreadsheetFile.importXlsx(input);
if (previewPath === "--json") {
  // Machine mode never renders, exports, or changes the input workbook.
  const inspection = await workbook.inspect({ kind: "sheet", include: "id,name", maxChars: 100000 });
  if (inspection.truncated) throw new Error("worksheet_inventory_truncated");
  const sheets = inspection.ndjson.split("\n").filter(Boolean).map(JSON.parse)
    .filter((row) => row.kind === "sheet");
  if (sheets.length !== 1) throw new Error("titles_requires_one_sheet");
  const sheet = workbook.worksheets.getItem(sheets[0].name);
  const address = sheets[0].range;
  if (!/^[A-Z]+[1-9][0-9]*(?::[A-Z]+[1-9][0-9]*)?$/.test(address ?? "")) {
    throw new Error("titles_invalid_used_range");
  }
  const range = sheet.getRange(`A1:${address.split(":").at(-1)}`);
  console.log(JSON.stringify({ schema_version: 1, sheet: sheet.name, values: range.values }));
} else {
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
}
