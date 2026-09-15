import {defaultProfile} from '../core/index.mjs';
export function exampleDecision() {
  const profile = defaultProfile(); profile.id = 'example'; profile.baseline = 'Illustrative product baseline'; profile.horizon = 'Example planning cycle';
  const nodes = [{id: 'activation', title: 'Improve first-run activation'}, {id: 'insights', title: 'Add team insights'}, {id: 'platform', title: 'Shared event foundation'}].map(n => ({...n, key: 'Project:' + n.id, kind: 'Project', version: '1', status: 'draft'}));
  const estimates = [[42, 8, 20, 13, .8], [30, 5, 15, 8, .8], [0, 8, 0, 13, .9]];
  const observations = Object.fromEntries(nodes.map((n, i) => [n.key, {work_version: n.version, context: {baseline: profile.baseline, horizon: profile.horizon}, values: {
    impact__kpi: {point: estimates[i][0], adverse: estimates[i][2], confidence: estimates[i][4], unit: 'KPI units', source: 'Illustrative local forecast'},
    effort__estimate: {point: estimates[i][1], adverse: estimates[i][3], confidence: estimates[i][4], unit: 'points', source: 'Illustrative team estimate'},
  }}]));
  return {schema: 'devgraph.selection-decision.v1', profile, snapshot: {schema: 'devgraph.selection-snapshot.v1', nodes,
    dependencies: nodes.slice(0, 2).map(n => ({dependent: n.key, prerequisite: nodes[2].key})), blocks: [], hierarchy: [],
    coverage: {status: 'scanned', consistency: 'fixture', read_started_at: '2026-09-14T12:00:00Z', read_finished_at: '2026-09-14T12:00:00Z', unresolved: []}}, seeds: nodes.slice(0, 2).map(n => n.key), observations};
}
