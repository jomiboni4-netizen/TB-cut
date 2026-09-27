// Read-only verification of the exact private distribution, including transitives.
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';

const sha = bytes => createHash('sha256').update(bytes).digest('hex');
export async function checkDependencies() {
  const lock = JSON.parse(await fs.readFile(new URL('../workbook-dependencies.lock.json', import.meta.url), 'utf8'));
  if (process.version !== `v${lock.node}`) throw new Error('workbook_node_version_mismatch');
  if (process.platform !== lock.platform || process.arch !== lock.arch) throw new Error('workbook_platform_mismatch');
  const entry = fileURLToPath(import.meta.resolve('@oai/artifact-tool'));
  let directory = path.dirname(entry);
  while (true) {
    try {
      const info = JSON.parse(await fs.readFile(path.join(directory, 'package.json'), 'utf8'));
      if (info.name === '@oai/artifact-tool') break;
    } catch (error) {
      if (error.code !== 'ENOENT') throw error;
    }
    const parent = path.dirname(directory);
    if (parent === directory) throw new Error('artifact_tool_package_missing');
    directory = parent;
  }
  const info = JSON.parse(await fs.readFile(path.join(directory, 'package.json'), 'utf8'));
  if (info.version !== lock.artifactTool) throw new Error('artifact_tool_version_mismatch');
  const names = [];
  async function walk(relative) {
    for (const item of await fs.readdir(path.join(directory, relative), {withFileTypes: true})) {
      const name = relative ? `${relative}/${item.name}` : item.name;
      if (item.isSymbolicLink()) throw new Error('artifact_tool_symlink_not_locked');
      if (item.isDirectory()) await walk(name);
      else if (item.isFile()) names.push(name);
      else throw new Error('artifact_tool_special_file');
    }
  }
  await walk('');
  const digest = createHash('sha256');
  for (const name of names.sort()) {
    digest.update(`${name}\0${sha(await fs.readFile(path.join(directory, name)))}\n`);
  }
  if (names.length !== lock.fileCount || digest.digest('hex') !== lock.treeSha256) {
    throw new Error('artifact_tool_distribution_digest_mismatch');
  }
  const module = await import('@oai/artifact-tool');
  if (typeof module.FileBlob?.load !== 'function' || typeof module.SpreadsheetFile?.importXlsx !== 'function') {
    throw new Error('artifact_tool_workbook_api_missing');
  }
  return {node: lock.node, artifactTool: lock.artifactTool, distribution: 'verified', workbookApi: 'importable'};
}
if (process.argv[1] && await fs.realpath(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try { console.log(JSON.stringify(await checkDependencies())); }
  catch (error) { console.error(`workbook_dependency_check_failed: ${error.message}`); process.exitCode = 1; }
}
