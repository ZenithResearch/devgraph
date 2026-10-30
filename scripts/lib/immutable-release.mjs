// Shared publication primitives. Keep the vendored DevGraph copy byte-identical.
import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';

export function safeRelative(name) {
  assert(typeof name === 'string' && name.length > 0 && !path.isAbsolute(name)
    && !name.includes('\\') && !name.includes('\0')
    && name.split('/').every(part => part && part !== '.' && part !== '..'),
  'Unsafe release path: ' + name);
  return name;
}

async function stat(name) {
  try { return await fs.lstat(name); } catch (error) {
    if (error.code === 'ENOENT') return null;
    throw error;
  }
}

async function checkParents(directory, boundary) {
  const relative = path.relative(boundary, directory);
  assert(relative !== '..' && !relative.startsWith('..' + path.sep), 'Outside source root');
  let current = boundary;
  for (const part of relative.split(path.sep).filter(Boolean)) {
    current = path.join(current, part);
    const info = await stat(current);
    if (info) assert(info.isDirectory() && !info.isSymbolicLink(), 'Unsafe directory: ' + current);
  }
}

export async function inventory(directory) {
  const result = new Map();
  async function walk(current, prefix = '') {
    const info = await fs.lstat(current);
    assert(info.isDirectory() && !info.isSymbolicLink(), 'Unsafe release directory: ' + current);
    for (const name of (await fs.readdir(current)).sort()) {
      const member = path.join(current, name), key = prefix + name, item = await fs.lstat(member);
      assert(!item.isSymbolicLink(), 'Symlink in release: ' + key);
      if (item.isDirectory()) await walk(member, key + '/');
      else {
        assert(item.isFile(), 'Non-regular release member: ' + key);
        result.set(key, await fs.readFile(member));
      }
    }
  }
  await walk(directory);
  return result;
}

export async function verifyTree(directory, expected) {
  const actual = await inventory(directory);
  assert.deepEqual([...actual.keys()].sort(), [...expected.keys()].sort(),
    'Immutable release inventory collision; choose a new version');
  for (const [name, bytes] of expected) assert(actual.get(name).equals(bytes),
    'Immutable release collision: ' + name + '; choose a new version');
}

export async function publishTree(directory, expected, {check = false, boundary} = {}) {
  assert(expected.size > 0, 'Empty release');
  for (const [name, bytes] of expected) {
    safeRelative(name);
    assert(Buffer.isBuffer(bytes), 'Release members must be rendered bytes');
    for (const other of expected.keys()) assert(!other.startsWith(name + '/'), 'Conflicting release paths');
  }
  const parent = path.dirname(directory);
  await checkParents(parent, boundary ?? parent);
  if (check) { await verifyTree(directory, expected); return; }
  await fs.mkdir(parent, {recursive: true});
  // All supported writers use this lock. Never steal a stale lock automatically.
  const lockPath = directory + '.lock';
  const lock = await fs.open(lockPath, 'wx');
  let staging;
  try {
    await lock.writeFile(JSON.stringify({pid: process.pid}) + '\n');
    if (await stat(directory)) { await verifyTree(directory, expected); return; }
    staging = await fs.mkdtemp(path.join(parent, '.' + path.basename(directory) + '.stage-'));
    for (const [name, bytes] of expected) {
      const target = path.join(staging, name);
      await fs.mkdir(path.dirname(target), {recursive: true});
      await fs.writeFile(target, bytes, {flag: 'wx'});
    }
    await verifyTree(staging, expected);
    assert.equal(await stat(directory), null, 'Release destination appeared while locked');
    // Same-filesystem rename is atomic; no-replace is provided by the cooperating writer lock.
    await fs.rename(staging, directory);
    staging = undefined;
  } finally {
    if (staging) await fs.rm(staging, {recursive: true, force: true});
    await lock.close();
    await fs.unlink(lockPath);
  }
}

export async function generatedFile(target, bytes, check) {
  if (check) assert((await fs.readFile(target)).equals(bytes), 'Stale generated file: ' + target);
  else {
    const staging = await fs.mkdtemp(path.join(path.dirname(target), '.generated-'));
    try {
      await fs.writeFile(path.join(staging, 'file'), bytes, {flag: 'wx'});
      await fs.rename(path.join(staging, 'file'), target);
    } finally { await fs.rm(staging, {recursive: true, force: true}); }
  }
}
