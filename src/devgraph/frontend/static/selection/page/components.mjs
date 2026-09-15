import {bindings} from '../core/index.mjs';
export const byId = id => document.getElementById(id);
export const format = value => Number.isFinite(value) ? new Intl.NumberFormat('en', {maximumFractionDigits: 3}).format(value) : '—';
export function el(tag, text, className) { const n = document.createElement(tag); if (text !== undefined) n.textContent = String(text); if (className) n.className = className; return n; }
export function setText(id, text) { byId(id).textContent = text; }
export function candidateRow(node, score, problem, options) {
  const row = el('tr'), checkCell = el('td'), checkbox = el('input'); checkbox.type = 'checkbox'; checkbox.dataset.scopeKey = node.key; checkbox.checked = options.seed || options.required; checkbox.disabled = options.required; checkbox.setAttribute('aria-label', options.required ? 'Required prerequisite in scope: ' + node.title : 'Include ' + node.title + ' in candidate scope'); checkbox.onchange = () => options.onSeed(node.key, checkbox.checked); checkCell.append(checkbox);
  const workCell = el('td'), title = el('button', node.title, 'work-button'); title.dataset.editKey = node.key; title.onclick = () => options.onEdit(node.key); workCell.append(title);
  workCell.append(el('span', node.kind + ' · v' + (node.version ?? '?') + (options.required ? ' · prerequisite' : ''), 'work-meta'));
  if (options.result) workCell.append(el('span', options.result.selected ? 'Selected' + (options.result.required_by.length ? ' · required by selected work' : '') : 'Not selected in this run', 'work-meta ' + (options.result.selected ? 'positive' : '')));
  const cells = [score?.benefit, score?.burden, score?.utility].map((v, i) => el('td', format(v), 'number' + (i === 2 && Number.isFinite(v) ? v >= 0 ? ' positive' : ' negative' : '')));
  const statusCell = el('td'), status = el('button', problem ? ['estimate_version', 'estimate_context'].includes(problem.code) && options.hasObservations ? 'Review estimates' : 'Needs inputs' : 'Edit estimates', 'input-status' + (problem ? ' missing' : '')); status.onclick = () => options.onEdit(node.key); status.setAttribute('aria-label', status.textContent + ' for ' + node.title); statusCell.append(status);
  row.append(checkCell, workCell, ...cells, statusCell); return row;
}
export function renderEditor(node, observations, profile) {
  setText('editor-title', node.title); setText('editor-meta', node.key + ' · Work v' + node.version + ' · ' + node.status + ' · local planning estimates'); setText('editor-error', '');
  const fields = byId('editor-fields'); fields.replaceChildren();
  for (const b of bindings(profile)) {
    const o = observations?.values?.[b.key], fieldset = el('fieldset', undefined, 'predictor'); fieldset.dataset.predictor = b.key;
    fieldset.append(el('legend', (b.label || b.id) + ' · ' + b.unit));
    fieldset.append(el('p', (b.channel === 'benefit' ? 'Forecast delta relative to baseline. ' : 'Remaining cost only. ') + 'Reference: ' + b.reference + ' · Importance: ' + format(b.weight * 100) + '%', 'hint'));
    if (o?.unit && o.unit !== b.unit) fieldset.append(el('p', 'Unit changed from ' + o.unit + '. Review and convert this estimate before saving.', 'warning'));
    const grid = el('div', undefined, 'predictor-grid');
    for (const [key, label] of [['point', b.channel === 'benefit' ? 'Point improvement' : 'Point estimate'], ['adverse', b.channel === 'benefit' ? 'Adverse improvement' : 'Adverse estimate'], ['confidence', 'Evidence confidence (0–1)']]) {
      const l = el('label', label), input = el('input'); input.type = 'number'; input.step = 'any'; input.name = b.key + ':' + key; input.id = 'predictor-' + b.key + '-' + key; input.value = o?.[key] ?? ''; input.placeholder = 'Unknown';
      if (key === 'confidence') { input.min = '0'; input.max = '1'; }
      if (b.channel === 'cost' && key !== 'confidence') input.min = '0';
      if (b.channel === 'cost' && b.id === 'estimate' && key !== 'confidence') input.setAttribute('list', 'fibonacci-estimates');
      l.append(input); grid.append(l);
    }
    const sourceLabel = el('label', 'Source / assumption', 'source'), source = el('input'); source.name = b.key + ':source'; source.value = o?.source ?? 'Manual planning estimate'; source.maxLength = 2000; sourceLabel.append(source);
    fieldset.append(grid, sourceLabel); fields.append(fieldset);
  }
  const list = el('datalist'); list.id = 'fibonacci-estimates'; for (const v of [0,1,2,3,5,8,13,21,34,55,89]) { const option = el('option'); option.value = v; list.append(option); } fields.append(list);
}
export function readEditor(node, profile, previous) {
  const values = {...(previous?.values || {})}, form = byId('estimate-form');
  for (const b of bindings(profile)) {
    const o = {...(values[b.key] || {})};
    for (const key of ['point', 'adverse', 'confidence']) { const raw = form.elements.namedItem(b.key + ':' + key).value.trim(); o[key] = raw === '' ? null : Number(raw); }
    o.unit = b.unit; o.source = form.elements.namedItem(b.key + ':source').value.trim(); o.observed_at = new Date().toISOString(); values[b.key] = o;
  }
  return {work_version: node.version, context: {baseline: profile.baseline, horizon: profile.horizon}, values};
}
export function renderResult(run, stale) {
  byId('results').hidden = !run; setText('result-state', run ? stale ? 'Out of date' : 'Current run' : 'Not run'); byId('result-state').className = 'tag' + (stale ? ' warning' : '');
  if (!run) return;
  const custom = run.input.profile.model.mode === 'custom_net_v1', totals = byId('totals'); totals.replaceChildren();
  const effort = run.totals.costs.map(c => format(c.amount) + ' ' + c.unit).join(' · ');
  for (const [label, value, note] of [['NET UTILITY · u(A)', format(run.totals.utility), 'Quantized objective ' + format(run.solution.quantized_utility)], [custom ? 'BASE BENEFIT CONTEXT' : 'GOAL CONTRIBUTION', format(run.totals.benefit), custom ? 'Direct net function defines u(i)' : 'Across weighted goals'], [custom ? 'BASE COST CONTEXT' : 'RESOURCE BURDEN', format(run.totals.burden), effort || 'No cost channels'], ['SELECTED WORK', run.solution.selected.length, run.rows.filter(r => r.selected && r.required_by.length).length + ' required by selected work']]) {
    const card = el('div', undefined, 'total'); card.append(el('span', label, 'total-label'), el('span', value, 'total-value'), el('span', note, 'total-note')); totals.append(card);
  }
  setText('result-explanation', (stale ? 'These results use previous inputs. Run again to update. ' : '') + (custom ? 'The custom function defines net utility directly; displayed base cost context is not subtracted again. ' : 'Net utility is weighted benefit minus resource burden. ') + 'Each prerequisite is charged once. Search changes highlighting and the table view; it does not change this result.');
  const names = new Map(run.input.snapshot.nodes.map(n => [n.key, n.title])); const list = byId('selected-list'); list.replaceChildren();
  const chosen = run.rows.filter(r => r.selected);
  if (!chosen.length) list.append(el('p', 'The empty set is optimal for the quantized objective. No bundle improves that objective above zero.', 'hint'));
  for (const row of chosen) {
    const item = el('div', undefined, 'selected-item'), copy = el('div'); item.append(el('span', '✓', 'positive')); copy.append(el('strong', names.get(row.key)));
    if (row.required_by.length) copy.append(el('p', 'Required by ' + row.required_by.map(k => names.get(k) || k).join(', ') + '.'));
    copy.append(el('p', custom ? 'Direct net expression · u(i) = ' + format(row.utility) : format(row.benefit) + ' benefit − ' + format(row.burden) + ' burden = ' + format(row.utility) + ' utility.'));
    item.append(copy, el('span', format(row.utility), 'mono')); list.append(item);
  }
  const network = byId('network'); network.replaceChildren();
  if (!run.scope.dependencies.length) network.append(el('p', 'No prerequisite edges in this scope.'));
  for (const d of run.scope.dependencies) network.append(el('div', (names.get(d.dependent) || d.dependent) + ' → requires → ' + (names.get(d.prerequisite) || d.prerequisite)));
  const s = run.solution;
  setText('certificate', 'Fingerprint (SHA-256)\n' + run.fingerprint + '\n\nProfile: ' + run.input.profile.id + ' v' + run.profile_version + '\nConfidence: ' + run.input.profile.confidence_mode + '\nRead consistency: ' + run.input.snapshot.coverage.consistency + '\nRead window: ' + run.input.snapshot.coverage.read_started_at + ' → ' + run.input.snapshot.coverage.read_finished_at + '\n\nInteger capacity scale: ' + s.scale + '\nC = ' + s.C + ' · M = ' + s.M + ' · min-cut = ' + s.cut_capacity + '\nu(A), quantized = (C − cut) / scale = ' + format(s.quantized_utility) + '\nSolver: ' + s.solver + '\nTie policy: ' + s.tie_policy + '\nRounding: ' + s.rounding + '\nMaximum total rounding error: ' + format(run.scope.keys.length / (2 * s.scale)) + ' utility\n\nCut edges\n' + (s.cut.map(e => e.from + ' → ' + e.to + ' : ' + e.capacity).join('\n') || '(none)'));
}
