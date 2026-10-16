import test from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';

test('ESM import does not read browser globals, perform I/O, or initialize WASM', () => {
  const source = new URL('../dist/index.js',import.meta.url).href;
  const result = spawnSync(process.execPath,['--input-type=module','-e',`
    for (const key of ['window','document','castaliaWallet']) Object.defineProperty(globalThis,key,{get(){throw Error('browser side effect')}});
    globalThis.fetch=()=>{throw Error('import attempted fetch')};
    const sdk=await import(${JSON.stringify(source)});
    if(typeof sdk.initialize!=='function')throw Error('missing API');
    if(sdk.exportJson({version:9223372036854775807n})!=='{"version":"9223372036854775807"}')throw Error('bad bigint export');
  `],{encoding:'utf8'});
  assert.equal(result.status,0,result.stderr);
});

test('missing Wallet is a stable capability error', async () => {
  const sdk = await import('../dist/index.js');
  await assert.rejects(sdk.connectCastalia(),{code:'capability_unavailable',dispatched:false});
});
