import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {inventory, publishTree} from './immutable-release.mjs';

const expected = () => new Map([['ontology.jsonld', Buffer.from('ontology\n')], ['manifest.json', Buffer.from('manifest\n')]]);
async function fixture(t) {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'immutable-release-test-'));
  t.after(() => fs.rm(root, {recursive: true, force: true}));
  return {root, directory: path.join(root, 'releases', 'v1')};
}
for (const mode of ['changed', 'added', 'missing', 'unexpected', 'symlink']) test('rejects ' + mode + ' without mutation', async t => {
  const {root, directory} = await fixture(t), files = expected();
  await publishTree(directory, files, {boundary: root});
  if (mode === 'changed') files.set('ontology.jsonld', Buffer.from('different'));
  if (mode === 'added') files.set('extra.json', Buffer.from('new target'));
  if (mode === 'missing') await fs.unlink(path.join(directory, 'ontology.jsonld'));
  if (mode === 'unexpected') await fs.writeFile(path.join(directory, 'extra.json'), 'unlisted');
  if (mode === 'symlink') await fs.symlink('manifest.json', path.join(directory, 'link.json'));
  const before = await fs.readdir(directory), manifest = await fs.readFile(path.join(directory, 'manifest.json'));
  const snapshot = mode === 'symlink' ? null : await inventory(directory);
  await assert.rejects(publishTree(directory, files, {boundary: root}));
  await assert.rejects(publishTree(directory, files, {boundary: root, check: true}));
  assert.deepEqual(await fs.readdir(directory), before);
  assert.deepEqual(await fs.readFile(path.join(directory, 'manifest.json')), manifest);
  if (snapshot) assert.deepEqual(await inventory(directory), snapshot);
});
test('repeat and check builds are read-only and deterministic', async t => {
  const {root, directory} = await fixture(t), files = expected();
  await publishTree(directory, files, {boundary: root});
  const before = await fs.stat(path.join(directory, 'manifest.json'));
  await publishTree(directory, files, {boundary: root});
  await publishTree(directory, files, {boundary: root, check: true});
  assert.equal((await fs.stat(path.join(directory, 'manifest.json'))).mtimeMs, before.mtimeMs);
});
test('concurrent writers cannot mix releases', async t => {
  const {root, directory} = await fixture(t);
  const a = expected(), b = expected(); b.set('ontology.jsonld', Buffer.from('other'));
  const results = await Promise.allSettled([publishTree(directory, a, {boundary: root}), publishTree(directory, b, {boundary: root})]);
  assert.equal(results.filter(r => r.status === 'fulfilled').length, 1);
  const final = await inventory(directory);
  assert(final.get('ontology.jsonld').equals(a.get('ontology.jsonld')) || final.get('ontology.jsonld').equals(b.get('ontology.jsonld')));
  assert.deepEqual([...final.keys()].sort(), [...a.keys()].sort());
});
test('stale lock and interrupted staging are preserved for inspection', async t => {
  const {root, directory} = await fixture(t);
  await fs.mkdir(path.dirname(directory));
  await fs.writeFile(directory + '.lock', 'stale');
  const orphan = await fs.mkdtemp(path.join(path.dirname(directory), '.v1.stage-'));
  await fs.writeFile(path.join(orphan, 'partial'), 'interrupted');
  await assert.rejects(publishTree(directory, expected(), {boundary: root}), {code: 'EEXIST'});
  assert.equal(await fs.readFile(directory + '.lock', 'utf8'), 'stale');
  assert.equal(await fs.readFile(path.join(orphan, 'partial'), 'utf8'), 'interrupted');
  await assert.rejects(fs.stat(directory), {code: 'ENOENT'});
});
test('check never creates a missing release; unsafe paths fail before writing', async t => {
  const {root, directory} = await fixture(t);
  await assert.rejects(publishTree(directory, expected(), {boundary: root, check: true}));
  for (const name of ['../escape', '/absolute', 'a/./b', 'a//b']) {
    await assert.rejects(publishTree(directory, new Map([[name, Buffer.from('x')]]), {boundary: root}));
  }
  await assert.rejects(fs.stat(path.dirname(directory)), {code: 'ENOENT'});
});
