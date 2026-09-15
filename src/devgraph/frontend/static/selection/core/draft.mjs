import {bindings, clone, requireThat, validateProfile} from './contracts.mjs';
import {validateSnapshot} from './adapter.mjs';
import {evaluateScore} from './scoring.mjs';
import {compileNetwork} from './network.mjs';

/** Create the input slots from Work and the model, without inventing predictions. */
export function initializeDecision(snapshot, profile, seeds) {
  validateSnapshot(snapshot); validateProfile(profile);
  requireThat(Array.isArray(seeds) && seeds.every(key => typeof key === 'string'), 'seeds', 'Candidate keys are required.');
  return clone({schema: 'devgraph.selection-decision.v1', snapshot, profile, seeds,
    observations: Object.fromEntries(snapshot.nodes.map(node => [node.key, {
      work_version: node.version, context: {baseline: profile.baseline, horizon: profile.horizon},
      values: Object.fromEntries(bindings(profile).map(b => [b.key, {point: null, adverse: null, confidence: null, unit: b.unit, source: ''}]))
    }]))});
}

/** Serializable, unsolved network schema. null is unresolved; it is never zero. */
export function describeNetwork({nodes, dependencies, profile, observations = {}}) {
  validateProfile(profile);
  // Validate identities and topology even when all utilities are still unknown.
  compileNetwork(nodes.map(n => ({key: n.key, utility: 0})), dependencies, profile.quantization);
  const definitions = bindings(profile), vertices = nodes.map(work => {
    const observation = observations[work.key]; let score = null, problem = null;
    try { score = evaluateScore(work, observation, profile); }
    catch (e) { problem = {code: e.code, message: e.message, details: e.details || []}; }
    const variables = definitions.map(b => ({...b, point: observation?.values?.[b.key]?.point ?? null,
      adverse: observation?.values?.[b.key]?.adverse ?? null, confidence: observation?.values?.[b.key]?.confidence ?? null,
      source: observation?.values?.[b.key]?.source ?? '', observed_unit: observation?.values?.[b.key]?.unit ?? null,
      required_fields: profile.confidence_mode === 'none' ? ['point', 'source'] : ['point', 'source', 'adverse', 'confidence']}));
    return {key: work.key, work, observation_work_version: observation?.work_version ?? null, observation_context: observation?.context ?? null, variables, score, problem};
  });
  const complete = vertices.every(v => v.score !== null);
  const compiled = complete ? compileNetwork(vertices.map(v => ({key: v.key, utility: v.score.utility})), dependencies, profile.quantization) : null;
  let edges = compiled?.edges;
  if (!edges) {
    edges = [];
    const add = (from, to, type, capacity, expression) => edges.push({id: JSON.stringify([from, to, type, edges.length]), from, to, type, capacity, expression});
    for (const v of vertices) {
      if (v.score) {
        for (const edge of compileNetwork([{key: v.key, utility: v.score.utility}], [], profile.quantization).edges) edges.push(edge);
      } else {
        add('$source', v.key, 'benefit', null, 'max(u(i), 0)');
        add(v.key, '$sink', 'cost', null, 'max(−u(i), 0)');
      }
    }
    for (const d of dependencies) if (d.dependent !== d.prerequisite) add(d.dependent, d.prerequisite, 'prerequisite', null, 'M > C');
  }
  return {schema: 'devgraph.selection-network.v1', status: complete ? 'ready' : 'needs_inputs', scale: profile.quantization,
    terminals: [{key: '$source', kind: 'source'}, {key: '$sink', kind: 'sink'}], nodes: vertices, edges,
    C: compiled?.C ?? null, M: compiled?.M ?? null};
}
