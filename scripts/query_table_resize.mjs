import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";
import { checkDependencies } from "./check_workbook_dependencies.mjs";
const dependencyStatus = await checkDependencies();
if (dependencyStatus.status !== "verified") {
  throw new Error(`workbook_runtime_${dependencyStatus.status}: ${dependencyStatus.reason ?? "unknown"}`);
}
const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(process.argv[2]));
const sheet = workbook.worksheets.getItemAt(0);
const table = sheet.tables.getItemAt(0);
console.log(String(table.name));
