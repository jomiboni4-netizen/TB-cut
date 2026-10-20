// Validate a workbook runtime against reviewed compatibility evidence.
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';
import {createHash} from 'node:crypto';

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, '..');
const sha = bytes => createHash('sha256').update(bytes).digest('hex');
const canonical = value => Array.isArray(value) ? value.map(canonical)
  : value && typeof value === 'object'
    ? Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])]))
    : value;
const stable = value => JSON.stringify(canonical(value));
const lexical = (left, right) => left < right ? -1 : left > right ? 1 : 0;

export function validateApiSurface(api) {
  const missing = [];
  if (typeof api?.FileBlob?.load !== 'function') missing.push('FileBlob.load');
  if (typeof api?.SpreadsheetFile?.importXlsx !== 'function') missing.push('SpreadsheetFile.importXlsx');
  if (typeof api?.SpreadsheetFile?.exportXlsx !== 'function') missing.push('SpreadsheetFile.exportXlsx');
  return missing;
}

async function packageInfo(entryOverride) {
  const entry = await fs.realpath(entryOverride ?? fileURLToPath(import.meta.resolve('@oai/artifact-tool')));
  let directory = path.dirname(entry);
  while (true) {
    try {
      const info = JSON.parse(await fs.readFile(path.join(directory, 'package.json'), 'utf8'));
      if (info.name === '@oai/artifact-tool') return {entry, directory, info};
    } catch (error) {
      if (error.code !== 'ENOENT') throw error;
    }
    const parent = path.dirname(directory);
    if (parent === directory) throw new Error('artifact_tool_package_missing');
    directory = parent;
  }
}

async function treeEvidence(directory) {
  const files = [];
  async function walk(relative) {
    for (const item of await fs.readdir(path.join(directory, relative), {withFileTypes: true})) {
      const name = relative ? `${relative}/${item.name}` : item.name;
      const full = path.join(directory, name);
      if (item.isSymbolicLink()) throw new Error('artifact_tool_symlink_not_allowed');
      if (item.isDirectory()) await walk(name);
      else if (item.isFile()) {
        const stat = await fs.stat(full);
        files.push({name, size: stat.size, mtimeMs: stat.mtimeMs, mode: stat.mode});
      } else throw new Error('artifact_tool_special_file');
    }
  }
  await walk('');
  files.sort((a, b) => lexical(a.name, b.name));
  const digest = createHash('sha256');
  for (const file of files) {
    digest.update(`${file.name}\0${sha(await fs.readFile(path.join(directory, file.name)))}\n`);
  }
  return {files, fileCount: files.length, treeSha256: digest.digest('hex')};
}

async function quickTreeStamp(directory) {
  const rows = [];
  async function walk(relative) {
    for (const item of await fs.readdir(path.join(directory, relative), {withFileTypes: true})) {
      const name = relative ? `${relative}/${item.name}` : item.name;
      const full = path.join(directory, name);
      if (item.isSymbolicLink()) throw new Error('artifact_tool_symlink_not_allowed');
      if (item.isDirectory()) await walk(name);
      else if (item.isFile()) {
        const stat = await fs.stat(full);
        rows.push([name, stat.size, stat.mtimeMs, stat.mode]);
      } else throw new Error('artifact_tool_special_file');
    }
  }
  await walk('');
  rows.sort((a, b) => lexical(a[0], b[0]));
  return sha(Buffer.from(JSON.stringify(rows)));
}

async function readCache(cachePath) {
  try { return JSON.parse(await fs.readFile(cachePath, 'utf8')); }
  catch (error) { if (error.code === 'ENOENT') return null; throw error; }
}

async function writeCache(cachePath, value) {
  await fs.mkdir(path.dirname(cachePath), {recursive: true});
  const temporary = `${cachePath}.${process.pid}.tmp`;
  await fs.writeFile(temporary, `${JSON.stringify(value, null, 2)}\n`, {flag: 'wx'});
  await fs.rename(temporary, cachePath);
}

export async function checkDependencies({mode = 'compatible', moduleEntryOverride} = {}) {
  const pkg = await packageInfo(moduleEntryOverride);

  if (mode === 'strict-history') {
    const lock = JSON.parse(await fs.readFile(path.join(root, 'workbook-dependencies.lock.json'), 'utf8'));
    if (pkg.info.version !== lock.artifactTool) {
      return {status: 'incompatible', reason: 'historical_artifact_tool_version_mismatch', actual: pkg.info.version, expected: lock.artifactTool};
    }
    const tree = await treeEvidence(pkg.directory);
    if (tree.fileCount !== lock.fileCount || tree.treeSha256 !== lock.treeSha256) {
      return {status: 'incompatible', reason: 'historical_artifact_tool_distribution_mismatch'};
    }
    const module = await import(pathToFileURL(pkg.entry));
    const missing = validateApiSurface(module);
    if (missing.length) return {status: 'incompatible', reason: 'artifact_tool_required_api_missing', missing};
    return {status: 'verified', mode, node: process.version, artifactTool: pkg.info.version,
      distribution: lock.distribution, workbookApi: 'required-import-export'};
  }
  if (mode !== 'compatible') return {status: 'incompatible', reason: 'unknown_workbook_dependency_mode'};

  const policy = JSON.parse(await fs.readFile(path.join(root, 'config/workbook-compatibility.json'), 'utf8'));
  if (pkg.info.name !== policy.trusted_package) {
    return {status: 'incompatible', reason: 'artifact_tool_package_identity_mismatch',
      expected: policy.trusted_package, actual: pkg.info.name};
  }
  if (!moduleEntryOverride) {
    const bundledPackageRoot = path.resolve(path.dirname(path.dirname(process.execPath)),
      'node_modules', '@oai', 'artifact-tool');
    if (pkg.directory !== bundledPackageRoot) {
      return {status: 'incompatible', reason: 'artifact_tool_not_from_codex_runtime',
        packageRoot: pkg.directory};
    }
  }
  const stamp = await quickTreeStamp(pkg.directory);
  const cachePath = path.join(root, '.cache/workbook_runtime_compatibility.json');
  const cache = await readCache(cachePath);
  const identity = {node: process.version, platform: process.platform, arch: process.arch,
    artifactTool: pkg.info.version, packageRoot: pkg.directory, packageEntry: pkg.entry,
    packageName: pkg.info.name, quickTreeStamp: stamp};
  if (cache && stable(cache.identity) === stable(identity) && cache.status === 'verified') {
    const module = await import(pathToFileURL(pkg.entry));
    const missing = validateApiSurface(module);
    if (missing.length) return {status: 'incompatible', reason: 'artifact_tool_required_api_missing', missing};
    return {status: 'verified', mode, node: process.version, artifactTool: pkg.info.version,
      package: pkg.info.name, verification: 'cached-environment-record', workbookApi: 'required-import-export'};
  }
  const tree = await treeEvidence(pkg.directory);
  const module = await import(pathToFileURL(pkg.entry));
  const missing = validateApiSurface(module);
  if (missing.length) return {status: 'incompatible', reason: 'artifact_tool_required_api_missing', missing};
  await writeCache(cachePath, {status: 'verified', identity, distributionSha256: tree.treeSha256,
    fileCount: tree.fileCount, verifiedAt: new Date().toISOString(), trustPolicy: policy.policy});
  return {status: 'verified', mode, node: process.version, artifactTool: pkg.info.version,
    package: pkg.info.name, verification: 'package-identity-and-runtime-capabilities', workbookApi: 'required-import-export'};
}

if (process.argv[1] && await fs.realpath(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const mode = process.argv.includes('--mode') ? process.argv[process.argv.indexOf('--mode') + 1] : 'compatible';
  try {
    const result = await checkDependencies({mode});
    console.log(JSON.stringify(result));
    if (result.status === 'unverified') process.exitCode = 2;
    else if (result.status !== 'verified') process.exitCode = 3;
  } catch (error) {
    console.log(JSON.stringify({status: 'incompatible', reason: error.message || 'workbook_dependency_check_failed'}));
    process.exitCode = 3;
  }
}
