// Exercise the actual producer CLI, including the originally rejected-write bug.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {spawnSync} from 'node:child_process';
import {inventory} from './lib/immutable-release.mjs';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const core = await fs.stat(path.join(root, 'ontology/core')).then(() => true, () => false);
const configName = core ? 'ontology/core/publication.json' : 'ontology/extension/publication.json';
const command = core ? 'scripts/build-release.mjs' : 'scripts/build_ontology_extension.mjs';
for (const mode of ['new-target', 'unlisted-file']) test('actual builder preserves rejected release: ' + mode, async t => {
  const copy = await fs.mkdtemp(path.join(os.tmpdir(), 'producer-builder-test-'));
  t.after(() => fs.rm(copy, {recursive: true, force: true}));
  for (const name of ['ontology', 'imports', 'scripts']) {
    if (await fs.stat(path.join(root, name)).then(() => true, () => false))
      await fs.cp(path.join(root, name), path.join(copy, name), {recursive: true});
  }
  const configPath = path.join(copy, configName), config = JSON.parse(await fs.readFile(configPath));
  const release = path.join(copy, core ? 'ontology/releases' : 'ontology/extension/releases', config.release);
  if (mode === 'new-target') {
    config.files.push({...config.files[0], target: 'new-rejected-target.json'});
    await fs.writeFile(configPath, JSON.stringify(config));
  } else await fs.writeFile(path.join(release, 'unlisted.json'), 'unlisted');
  const before = await inventory(release);
  for (const args of [[], ['--check']]) {
    const result = spawnSync(process.execPath, [command, ...args], {cwd: copy, encoding: 'utf8'});
    assert.notEqual(result.status, 0, result.stdout + result.stderr);
    assert.deepEqual(await inventory(release), before);
  }
});
