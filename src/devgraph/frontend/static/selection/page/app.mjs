import {bindings, initializeDecision, describeNetwork, defaultProfile, fromMonitorSnapshot, validateDecision, validateProfile, evaluateScore, evaluateDecision, canonicalJSON} from '../core/index.mjs';
import {FlowView} from './flow-view.mjs';
import {exampleDecision} from './example.mjs';
import {byId, setText, el, format, candidateRow, renderEditor, readEditor, renderResult} from './components.mjs';

const emptySnapshot = () => ({schema: 'devgraph.selection-snapshot.v1', nodes: [], dependencies: [], blocks: [], hierarchy: [], coverage: {status: 'unknown', consistency: 'assembled', read_started_at: '', read_finished_at: '', unresolved: []}});
const blank = () => ({schema: 'devgraph.selection-decision.v1', profile: defaultProfile(), snapshot: emptySnapshot(), seeds: [], observations: {}});
const state = {decision: blank(), source: 'empty', revision: 0, run: null, runRevision: -1, evaluation: null, scopeError: null, worker: null, controller: null, request: 0, editorKey: null, loading: false, scopeFilter: '$all'};
const storage = {get(key) { try { return sessionStorage.getItem(key); } catch { return null; } }, set(key, value) { try { sessionStorage.setItem(key, value); return true; } catch { return false; } }, remove(key) { try { sessionStorage.removeItem(key); } catch {} }};
const cookieReader = document.querySelector('meta[name=devgraph-selection-reader]')?.content === 'local-session';
byId('connection-settings').hidden = cookieReader;
if (cookieReader) for (const link of document.querySelectorAll('a[href="/monitor"]')) link.href = 'http://127.0.0.1:8080/monitor';
byId('credential').value = cookieReader ? '' : storage.get('devgraph-monitor-token') || '';
function message(text) { setText('message', text); }
function fail(error) { message(error.message || String(error)); }
function cancelRun() { state.worker?.terminate(); state.worker = null; byId('cancel-run').hidden = true; }
function changed() { state.revision++; cancelRun(); render(); }
function basicProfile() { const p = state.decision.profile; return p.model.mode === 'linear_v1' && p.goals.length === 1 && p.goals[0].predictors.length === 1 && p.resources.length === 1 && p.resources[0].id === 'effort' && p.resources[0].unit === 'points' && p.resources[0].predictors.length === 1 && p.resources[0].scale === 1 && p.resources[0].predictors[0].reference === 1; }
function syncProfile() {
  const p = state.decision.profile; byId('horizon').value = p.horizon; byId('baseline').value = p.baseline; byId('confidence').value = p.confidence_mode;
  byId('simple-model').hidden = !basicProfile();
  if (basicProfile()) { byId('goal-label').value = p.goals[0].label; byId('benefit-scale').value = p.goals[0].scale; byId('kpi-reference').value = p.goals[0].predictors[0].reference; byId('direction').value = p.goals[0].predictors[0].direction; byId('conversion').value = p.resources[0].conversion; }
  byId('profile-json').value = JSON.stringify(p, null, 2);
  setText('profile-version', 'Profile v' + p.version);
  setText('benefit-formula', p.goals[0].scale + ' × normalized KPI improvement');
  setText('confidence-help', p.confidence_mode === 'none' ? 'Confidence is retained but not applied. It is separate from importance and success probability.' : (p.model.mode === 'linear_v1' ? 'Benefit moves toward its adverse lower estimate; cost moves toward its adverse upper estimate. Missing confidence or bounds must be supplied.' : 'Confidence adjusts predictor inputs before the custom function. Nonmonotone expressions can reverse the effect; treat this as a scenario, not a conservative guarantee.'));
}
function updateProfile(mutator) {
  const next = JSON.parse(canonicalJSON(state.decision.profile)); mutator(next); next.version = String(Number(next.version) >= 1 && Number.isSafeInteger(Number(next.version)) ? Number(next.version) + 1 : Date.now());
  validateProfile(next); closeEditor(); state.decision.profile = next; syncProfile(); changed();
}
function needsReview(key) {
  const d = state.decision, node = d.snapshot.nodes.find(n => n.key === key), o = d.observations[key];
  if (!node || node.version === null) return true;
  return !!o && (o.work_version !== node.version || o.context?.baseline !== d.profile.baseline || o.context?.horizon !== d.profile.horizon || bindings(d.profile).some(b => o.values?.[b.key]?.unit && o.values[b.key].unit !== b.unit));
}
function syncScopeFilter() {
  const nodes = state.decision.snapshot.nodes.filter(n => n.kind === byId('kind').value), seeds = new Set(state.decision.seeds);
  state.scopeFilter = nodes.length === seeds.size && nodes.every(n => seeds.has(n.key)) ? '$all' : seeds.size === 1 && nodes.some(n => seeds.has(n.key)) ? [...seeds][0] : '$custom';
}
function renderProjectPicker() {
  const picker = byId('project-scope'), kind = byId('kind').value, nodes = state.decision.snapshot.nodes.filter(n => n.kind === kind).sort((a, b) => a.title.localeCompare(b.title));
  const options = [['$all', 'All ' + kind.toLowerCase() + 's' + (nodes.length ? ' (' + nodes.length + ')' : '')]];
  if (state.scopeFilter === '$custom') options.push(['$custom', 'Custom scope (' + state.decision.seeds.length + ')']);
  if (!state.scopeFilter.startsWith('$') && !nodes.some(n => n.key === state.scopeFilter)) options.push([state.scopeFilter, 'Unavailable · ' + state.scopeFilter]);
  for (const n of nodes) options.push([n.key, n.title]);
  const signature = JSON.stringify(options);
  if (picker.dataset.options !== signature) { picker.replaceChildren(...options.map(([value, label]) => { const option = el('option', label); option.value = value; return option; })); picker.dataset.options = signature; }
  picker.value = state.scopeFilter; picker.disabled = state.loading || !nodes.length;
  setText('project-picker-label', kind + 's'); picker.setAttribute('aria-label', kind + ' scope');
  byId('refresh-network').disabled = state.loading;
}
function setSeed(key, enabled) {
  if (state.loading) return;
  state.decision.seeds = enabled ? [...new Set([...state.decision.seeds, key])] : state.decision.seeds.filter(k => k !== key); syncScopeFilter(); changed();
}
const flow = new FlowView(byId('flow-canvas'), {
  inspect: openEditor, edit: updateNodeValue, seed: setSeed,
  summary: text => setText('flow-summary', text), zoom: value => setText('flow-zoom', value + '%'),
  networkError: error => { state.networkError = error; }
});
function render() {
  const focusedScope = document.activeElement?.dataset.scopeKey, focusedEdit = document.activeElement?.dataset.editKey;
  const d = state.decision, sourceNames = {empty: 'No network loaded', sample: 'Example scenario · illustrative values', live: 'Monitor network · local planning estimates', imported: 'Imported scenario · review source and assumptions', restored: 'Saved scenario · review inputs before relying on Work state', restored_live: 'Saved Monitor scenario · reload to reconcile Work versions'};
  setText('source-label', sourceNames[state.source]); setText('source-tag', state.source === 'sample' ? 'Sample data' : 'Local planning');
  setText('snapshot-time', d.snapshot.coverage.read_finished_at ? 'Captured ' + d.snapshot.coverage.read_finished_at.replace('T', ' ').replace('Z', ' UTC') : 'Load Work objects and dependencies first.');
  state.evaluation = null; state.scopeError = null; state.networkError = null;
  try { state.evaluation = evaluateDecision(d); } catch (error) { state.scopeError = error; }
  const dependencies = [...d.snapshot.dependencies, ...(d.profile.blocks_policy === 'require' ? d.snapshot.blocks : [])];
  const known = new Set(d.snapshot.nodes.map(n => n.key)), scopeKeys = new Set(d.seeds.filter(k => known.has(k)));
  const adjacent = new Map(); for (const e of dependencies) { if (!adjacent.has(e.dependent)) adjacent.set(e.dependent, []); adjacent.get(e.dependent).push(e.prerequisite); }
  const queue = [...scopeKeys]; for (let i = 0; i < queue.length; i++) for (const key of adjacent.get(queue[i]) || []) if (known.has(key) && !scopeKeys.has(key)) { scopeKeys.add(key); queue.push(key); }
  const evaluation = state.evaluation, added = [...scopeKeys].filter(k => !d.seeds.includes(k));
  const candidateNodes = d.snapshot.nodes.filter(n => (state.scopeFilter.startsWith('$') ? n.kind === byId('kind').value : n.key === state.scopeFilter) || scopeKeys.has(n.key));
  renderProjectPicker();
  const visible = new Set(candidateNodes.map(n => n.key));
  const scores = new Map(), problems = new Map();
  for (const node of candidateNodes) { try { scores.set(node.key, evaluateScore(node, d.observations[node.key], d.profile)); } catch (e) { problems.set(node.key, e); } }
  const filter = byId('search').value.toLowerCase(), rows = byId('candidate-rows'); rows.replaceChildren();
  const currentRun = state.run && state.runRevision === state.revision ? state.run : null;
  for (const node of candidateNodes) {
    if (!(node.title + ' ' + node.key).toLowerCase().includes(filter)) continue;
    rows.append(candidateRow(node, scores.get(node.key), problems.get(node.key), {seed: d.seeds.includes(node.key), required: added.includes(node.key), hasObservations: !!d.observations[node.key], result: currentRun?.rows.find(r => r.key === node.key), onSeed: setSeed, onEdit: openEditor}));
  }
  flow.update({nodes: candidateNodes, dependencies: dependencies.filter(e => visible.has(e.dependent) && visible.has(e.prerequisite)), profile: d.profile, observations: d.observations, scores, problems, seeds: d.seeds, added, scopeKeys, needsReview, loading: state.loading, search: filter, run: currentRun});
  byId('empty').hidden = candidateNodes.length > 0; byId('candidate-rows').closest('.table-wrap').hidden = candidateNodes.length === 0;
  const coverageOK = d.snapshot.coverage.status === 'scanned' && !state.scopeError;
  setText('coverage-state', 'Dependencies · ' + (coverageOK ? 'scanned for this scope' : state.scopeError && d.seeds.length ? 'needs review' : d.snapshot.coverage.status === 'scanned' ? 'choose scope' : 'unknown'));
  const incomplete = evaluation?.problems.length ?? 0;
  setText('estimate-state', evaluation ? 'Estimates · ' + (evaluation.scope.nodes.length - incomplete) + '/' + evaluation.scope.nodes.length + ' ready' : 'Estimates · scope unresolved');
  setText('scope-count', d.seeds.length + ' seeds + ' + added.length + ' added prerequisites');
  const blocked = !!state.scopeError || !!state.networkError || !evaluation || incomplete > 0 || state.loading;
  byId('run').disabled = blocked || !!state.worker; byId('run').textContent = state.worker ? 'Solving…' : 'Run selection ↗';
  byId('export').disabled = !d.snapshot.nodes.length; byId('save-draft').disabled = !d.snapshot.nodes.length;
  setText('flow-state', !d.snapshot.nodes.length && !state.loading ? 'No network loaded' : state.loading ? 'Loading network…' : currentRun ? 'Minimum cut' : state.run ? 'Edited · rerun' : blocked ? 'Network · needs inputs' : 'Capacities ready');
  const issue = state.networkError || state.scopeError;
  setText('run-help', state.worker ? 'Solving the frozen network…' : state.loading ? 'Loading Work objects and dependencies…' : !d.snapshot.nodes.length ? 'Load a network to reveal its required variables.' : blocked ? issue?.message || 'Fill or review variables on ' + incomplete + ' work item(s).' : 'Ready to solve ' + evaluation.scope.nodes.length + ' work items. Hard prerequisites stay together.');
  const errors = byId('errors'); errors.replaceChildren(); errors.hidden = !d.snapshot.nodes.length || !issue;
  if (!errors.hidden) errors.append(el('p', issue.message));
  renderResult(state.run, state.runRevision !== state.revision);
  const focused = [...rows.querySelectorAll('[data-scope-key], [data-edit-key]')].find(n => focusedScope ? n.dataset.scopeKey === focusedScope && !n.disabled : focusedEdit && n.dataset.editKey === focusedEdit);
  if (focused) focused.focus({preventScroll: true});
}
function replaceDecision(decision, source) {
  validateDecision(decision); closeEditor(); state.decision = JSON.parse(canonicalJSON(decision)); state.source = source; state.run = null; state.runRevision = -1; state.controller?.abort(); state.request++; state.loading = false;
  const seedKind = state.decision.snapshot.nodes.find(n => state.decision.seeds.includes(n.key))?.kind; if (seedKind) byId('kind').value = seedKind;
  syncScopeFilter(); syncProfile(); changed();
}
function closeEditor() { byId('editor').hidden = true; state.editorKey = null; flow.select(null); }
function editorMode() {
  const review = needsReview(state.editorKey); byId('review-inputs').hidden = !review;
  setText('editor-mode', review ? 'Work version, units, or forecast context changed. Review these values before using them again.' : 'Each edit updates this node immediately. Blank means unknown.');
}
function updateNodeValue(key, predictor, field, value) {
  if (state.loading || needsReview(key)) return;
  const d = state.decision, node = d.snapshot.nodes.find(n => n.key === key), b = bindings(d.profile).find(b => b.key === predictor);
  if (!node || !b || !['point', 'adverse', 'confidence', 'source'].includes(field)) return;
  if (!d.observations[key]) d.observations[key] = {work_version: node.version, context: {baseline: d.profile.baseline, horizon: d.profile.horizon}, values: {}};
  const o = d.observations[key], previous = o.values[predictor];
  o.values[predictor] = {...{point: null, adverse: null, confidence: null, unit: b.unit, source: ''}, ...previous, [field]: value, observed_at: new Date().toISOString()};
  if (field !== 'source' && !o.values[predictor].source) o.values[predictor].source = 'Manual planning estimate';
  changed(); state.editorRevision = state.revision;
  if (state.editorKey === key) {
    if (!byId('editor').contains(document.activeElement)) renderEditor(node, o, d.profile);
    previewEditor();
  }
}
function openEditor(key) {
  if (state.loading) { message('Wait for the network read before reviewing estimates.'); return; }
  const node = state.decision.snapshot.nodes.find(n => n.key === key); if (!node) return;
  state.editorKey = key; state.editorRevision = state.revision;
  renderEditor(node, state.decision.observations[key], state.decision.profile); editorMode(); previewEditor();
  byId('editor').hidden = false; flow.select(key); flow.focusNode(key);
  byId('editor').querySelector('input')?.focus({preventScroll: true});
  if (innerWidth < 900) byId('editor').scrollIntoView({block: 'start', behavior: 'smooth'});
}
function previewEditor() {
  const d = state.decision, node = d.snapshot.nodes.find(n => n.key === state.editorKey); if (!node) return;
  try { const score = evaluateScore(node, readEditor(node, d.profile, d.observations[node.key]), d.profile); setText('editor-score', d.profile.model.mode === 'custom_net_v1' ? 'Direct custom u(i) = ' + format(score.utility) : format(score.benefit) + ' benefit − ' + format(score.burden) + ' burden = ' + format(score.utility) + ' utility'); }
  catch (e) { setText('editor-score', e.details?.map(p => p.key + ': ' + p.message).join(' · ') || e.message); }
  const dependencies = [...d.snapshot.dependencies, ...(d.profile.blocks_policy === 'require' ? d.snapshot.blocks : [])].filter(e => e.dependent === node.key);
  const names = new Map(d.snapshot.nodes.map(n => [n.key, n.title]));
  setText('editor-dependencies', dependencies.length ? 'Requires: ' + dependencies.map(e => names.get(e.prerequisite) || e.prerequisite).join(', ') : 'No declared prerequisites.');
  try { const draft = describeNetwork({nodes: [node], dependencies: [], profile: d.profile, observations: d.observations}); setText('node-schema', JSON.stringify(draft.nodes[0], null, 2)); } catch (e) { setText('node-schema', e.message); }
}
byId('estimate-form').oninput = event => {
  const [predictor, field] = event.target.name.split(':');
  if (state.editorKey && !needsReview(state.editorKey)) updateNodeValue(state.editorKey, predictor, field, field === 'source' ? event.target.value.trim() : event.target.value === '' ? null : Number(event.target.value));
  previewEditor();
};
byId('estimate-form').onsubmit = event => {
  event.preventDefault(); if (!needsReview(state.editorKey)) return;
  if (state.editorRevision !== state.revision || state.loading) { setText('editor-error', 'Inputs changed while this review was open. Reopen the node to review its current version.'); return; }
  const node = state.decision.snapshot.nodes.find(n => n.key === state.editorKey), observation = readEditor(node, state.decision.profile, state.decision.observations[node.key]);
  try { evaluateScore(node, observation, state.decision.profile); }
  catch (e) { const invalid = e.code === 'incomplete_score' ? e.details.filter(d => !['missing_estimate', 'missing_confidence', 'missing_source'].includes(d.code)) : [e]; if (invalid.length) { setText('editor-error', invalid.map(d => d.message).join(' ')); return; } }
  state.decision.observations[node.key] = observation; changed(); state.editorRevision = state.revision; editorMode(); previewEditor(); message('Reviewed current inputs for ' + node.title + '.');
};
for (const id of ['close-editor', 'cancel-editor']) byId(id).onclick = closeEditor;
addEventListener('keydown', event => { if (event.key === 'Escape' && !byId('editor').hidden) { const key = state.editorKey; closeEditor(); flow.cards.get(key)?.inspect.focus({preventScroll: true}); } });
byId('settings-toggle').onclick = () => { const open = byId('workspace-settings').hidden; byId('workspace-settings').hidden = !open; byId('settings-toggle').setAttribute('aria-expanded', String(open)); };
byId('zoom-in').onclick = () => flow.setZoom(flow.zoom * 1.2);
byId('zoom-out').onclick = () => flow.setZoom(flow.zoom / 1.2);
byId('fit-network').onclick = () => flow.fit();
for (const [id, setter] of [
  ['goal-label', (p, v) => { p.goals[0].label = v; }], ['benefit-scale', (p, v) => { p.goals[0].scale = Number(v); }], ['kpi-reference', (p, v) => { p.goals[0].predictors[0].reference = Number(v); }], ['direction', (p, v) => { p.goals[0].predictors[0].direction = Number(v); }], ['conversion', (p, v) => { p.resources[0].conversion = Number(v); }], ['confidence', (p, v) => { p.confidence_mode = v; }], ['baseline', (p, v) => { p.baseline = v; }], ['horizon', (p, v) => { p.horizon = v; }],
]) byId(id).onchange = event => { try { if (event.target.value.trim() === '') throw Error('A value is required.'); updateProfile(p => setter(p, event.target.value)); message('Model updated. Run selection to refresh the result.'); } catch (e) { fail(e); syncProfile(); } };
byId('apply-profile').onclick = () => { try { const next = JSON.parse(byId('profile-json').value); validateProfile(next); closeEditor(); state.decision.profile = next; syncProfile(); changed(); message('Profile applied. Review any new predictors before running.'); } catch (e) { fail(e); } };
byId('uniform').onclick = () => { try { const next = JSON.parse(byId('profile-json').value); for (const g of [...next.goals, ...next.resources]) g.predictors.forEach(p => { p.weight = 1 / g.predictors.length; }); next.goals.forEach(g => { g.weight = 1 / next.goals.length; }); validateProfile(next); closeEditor(); state.decision.profile = next; syncProfile(); changed(); message('Equal weights applied within each group and across goals.'); } catch (e) { fail(e); } };
byId('project-scope').onchange = event => {
  closeEditor(); state.scopeFilter = event.target.value;
  if (state.scopeFilter === '$custom') return;
  state.decision.seeds = state.scopeFilter === '$all' ? state.decision.snapshot.nodes.filter(n => n.kind === byId('kind').value).map(n => n.key) : [state.scopeFilter];
  byId('search').value = ''; changed(); flow.fit(); message('Project scope updated. Existing node estimates are preserved.');
};
byId('refresh-network').onclick = () => byId('connect-form').requestSubmit();
byId('kind').onchange = () => { closeEditor(); state.scopeFilter = '$all'; state.decision.seeds = state.decision.snapshot.nodes.filter(n => n.kind === byId('kind').value).map(n => n.key); changed(); message('Candidate scope updated; prerequisites will be added.'); };
byId('search').oninput = render;
byId('all').onclick = () => { const query = byId('search').value.toLowerCase(); state.decision.seeds = [...new Set([...state.decision.seeds, ...state.decision.snapshot.nodes.filter(n => n.kind === byId('kind').value && (n.title + ' ' + n.key).toLowerCase().includes(query)).map(n => n.key)])]; syncScopeFilter(); changed(); };
byId('none').onclick = () => { state.decision.seeds = []; syncScopeFilter(); changed(); };
for (const id of ['example', 'empty-example']) byId(id).onclick = () => { replaceDecision(exampleDecision(), 'sample'); message('Example loaded. These are illustrative values, separate from live Work.'); };
byId('connect-form').onsubmit = async event => {
  event.preventDefault(); const token = byId('credential').value.trim(); if (!token && !cookieReader) { byId('connection-settings').open = true; message('Connect once with your scoped Monitor reader; projects load automatically after that.'); return; }
  closeEditor(); state.controller?.abort(); const controller = new AbortController(); state.controller = controller; const request = ++state.request; state.loading = true; cancelRun(); render(); message('Loading Work objects, dependencies, and variable slots…');
  const timer = setTimeout(() => controller.abort(), cookieReader ? 30000 : 15000);
  try {
    const response = await fetch('/monitor/snapshot', {headers: cookieReader ? {} : {Authorization: 'Bearer ' + token}, signal: controller.signal, cache: 'no-store', credentials: cookieReader ? 'same-origin' : 'omit', redirect: 'error'});
    if (!response.ok) throw Error(response.status === 401 || response.status === 403 ? cookieReader ? 'The local reader session expired. Reopen the current preview connection.' : 'Read access denied. Use a valid scoped Monitor credential.' : 'Monitor snapshot unavailable (HTTP ' + response.status + ').');
    const raw = await response.json(); if (request !== state.request) return;
    const snapshot = fromMonitorSnapshot(raw), first = !['live', 'restored_live'].includes(state.source);
    const d = first ? initializeDecision(snapshot, state.decision.profile, snapshot.nodes.filter(n => n.kind === byId('kind').value).map(n => n.key)) : state.decision; d.snapshot = snapshot;
    d.observations = {...initializeDecision(snapshot, d.profile, d.seeds).observations, ...d.observations};
    if (first) state.scopeFilter = '$all';
    if (state.scopeFilter === '$all') d.seeds = snapshot.nodes.filter(n => n.kind === byId('kind').value).map(n => n.key);
    // Existing seeds and estimates survive refresh: removed work blocks the scope,
    // and changed Work versions require an explicit estimate review.
    state.decision = d; state.source = 'live'; if (first) state.run = null;
    if (!cookieReader) storage.set('devgraph-monitor-token', token); byId('connection-settings').open = false; syncProfile(); changed();
    message('Network loaded. Work versions and dependencies are assembled during a read window; external gates and outcome attribution remain scenario assumptions.');
  } catch (e) { if (request === state.request) message(e.name === 'AbortError' ? 'Snapshot read timed out or was cancelled. Previous inputs remain unchanged.' : e.message); }
  finally { clearTimeout(timer); if (request === state.request) { state.loading = false; state.controller = null; render(); } }
};
byId('credential').oninput = () => {
  state.controller?.abort(); state.request++; state.loading = false; storage.remove('devgraph-selection-draft-v1'); storage.remove('devgraph-monitor-token');
  if (['live', 'restored_live'].includes(state.source)) replaceDecision(blank(), 'empty');
};
byId('run').onclick = () => {
  if (byId('run').disabled) return; cancelRun(); const revision = state.revision;
  const worker = new Worker(new URL('./worker.mjs', import.meta.url), {type: 'module'}); state.worker = worker; byId('cancel-run').hidden = false; render();
  worker.onmessage = ({data}) => { if (state.worker !== worker || revision !== state.revision) return; cancelRun(); if (data.ok) { state.run = data.result; state.runRevision = revision; message('Selection complete. ' + state.run.solution.selected.length + ' work items selected.'); } else fail(data.error); render(); };
  worker.onerror = () => { if (state.worker !== worker) return; cancelRun(); message('Selection worker failed. Review inputs and retry.'); render(); };
  worker.postMessage(state.decision);
};
byId('cancel-run').onclick = () => { cancelRun(); render(); message('Selection cancelled.'); };
byId('import').onclick = () => byId('import-file').click();
byId('import-file').onchange = async event => {
  try { const file = event.target.files[0]; if (!file) return; if (file.size > 8_000_000) throw Error('Decision files must be smaller than 8 MB.'); const value = JSON.parse(await file.text()); const decision = value.schema === 'devgraph.selection-run.v1' ? value.input : value; replaceDecision(decision, 'imported'); message('Imported input scenario. Run locally to verify its selection.'); } catch (e) { fail(e); } finally { event.target.value = ''; }
};
byId('export').onclick = () => {
  const value = state.run && state.runRevision === state.revision ? state.run : state.decision;
  const blob = new Blob([JSON.stringify(value, null, 2) + '\n'], {type: 'application/json'}), url = URL.createObjectURL(blob), link = el('a'); link.href = url; link.download = 'devgraph-selection-' + (value.schema === 'devgraph.selection-run.v1' ? 'run' : 'inputs') + '.json'; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); message('Exported the ' + (value.schema === 'devgraph.selection-run.v1' ? 'current run and its inputs' : 'current inputs; previous results were out of date') + '.');
};
byId('save-draft').onclick = () => { message(storage.set('devgraph-selection-draft-v1', canonicalJSON({schema: 'devgraph.selection-draft.v1', source: ['live', 'restored_live'].includes(state.source) ? 'live' : 'scenario', decision: state.decision})) ? 'Draft saved for this browser tab. Export JSON to keep it independently.' : 'Browser storage is unavailable. Export JSON to save the draft.'); };
byId('restore-draft').onclick = () => { try { const raw = storage.get('devgraph-selection-draft-v1'); if (!raw) throw Error('No draft saved in this tab.'); const saved = JSON.parse(raw); replaceDecision(saved.decision || saved, saved.source === 'live' ? 'restored_live' : 'restored'); message(saved.source === 'live' ? 'Saved Monitor inputs restored. Load snapshot to reconcile Work versions while preserving your estimates.' : 'Saved scenario restored. Run to verify the result.'); } catch (e) { fail(e); } };
addEventListener('pagehide', () => { cancelRun(); state.controller?.abort(); });
syncProfile(); render();
async function connectAutomatically() {
  if (cookieReader) {
    const ticket = new URLSearchParams(location.hash.slice(1)).get('selection_session');
    if (ticket) {
      history.replaceState(null, '', location.pathname + location.search);
      try { const response = await fetch('/monitor/selection/session', {method: 'POST', headers: {Authorization: 'Bearer ' + ticket}, credentials: 'same-origin', cache: 'no-store', redirect: 'error'}); if (!response.ok && response.status !== 401) throw Error('The local reader connection could not be opened.'); } catch (e) { fail(e); return; }
    }
    byId('connect-form').requestSubmit();
  } else if (byId('credential').value) byId('connect-form').requestSubmit();
  else byId('connection-settings').open = true;
}
connectAutomatically();
