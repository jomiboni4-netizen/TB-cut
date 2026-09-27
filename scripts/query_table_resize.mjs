import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";
const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(process.argv[2]));
const sheet = workbook.worksheets.getItemAt(0);
const table = sheet.tables.getItemAt(0);
console.log(String(table.name));
