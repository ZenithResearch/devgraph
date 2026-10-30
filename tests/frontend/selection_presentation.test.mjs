import test from 'node:test';
import assert from 'node:assert/strict';
import {runPresentation} from '../../src/devgraph/frontend/static/selection/page/components.mjs';

test('run presentation distinguishes delayed and rejected refreshes without hiding edited-input staleness', () => {
  const previousRun = {schema: 'devgraph.selection-run.v1'};

  assert.deepEqual(runPresentation(null, {refreshing: true}), {
    label: 'Not run', warning: false, exportable: false, explanation: '',
  });
  assert.deepEqual(runPresentation(previousRun, {refreshing: true}), {
    label: 'Refreshing snapshot', warning: true, exportable: false,
    explanation: 'Refreshing the network. This result uses the last captured snapshot. ',
  });
  assert.deepEqual(runPresentation(previousRun, {refreshError: 'Failed to fetch'}), {
    label: 'Previous snapshot', warning: true, exportable: true,
    explanation: 'The network refresh failed. This result uses the previous snapshot. ',
  });
  assert.deepEqual(runPresentation(previousRun, {stale: true, refreshError: 'Failed to fetch'}), {
    label: 'Previous snapshot · inputs changed', warning: true, exportable: true,
    explanation: 'The network refresh failed. This result uses the previous snapshot. These results use previous inputs. Run again to update. ',
  });
  assert.deepEqual(runPresentation(previousRun), {
    label: 'Current run', warning: false, exportable: true, explanation: '',
  });
});
