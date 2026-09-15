import {bindings, finite, requireThat, SelectionError, validateProfile} from './contracts.mjs';
import {evaluateExpression} from './expression.mjs';

export function evaluateScore(node, observations, profile) {
  validateProfile(profile);
  requireThat(observations?.work_version === node.version, 'estimate_version', 'Review estimates against Work version ' + node.version + '.');
  requireThat(observations.context?.baseline === profile.baseline && observations.context?.horizon === profile.horizon, 'estimate_context', 'Review estimates for the current baseline and planning horizon.');
  const contributions = [], variables = Object.create(null), problems = [];
  for (const b of bindings(profile)) {
    const o = observations.values?.[b.key];
    try {
      requireThat(o && o.point !== null, 'missing_estimate', 'Enter a point estimate.');
      finite(o.point, b.label || b.key, b.channel === 'cost' ? 0 : -Infinity);
      requireThat(o.unit === b.unit, 'unit_mismatch', 'Expected unit: ' + b.unit);
      requireThat(typeof o.source === 'string' && o.source.trim().length > 0, 'missing_source', 'Describe the estimate source.');
      if (o.confidence !== null && o.confidence !== undefined) { finite(o.confidence, 'Confidence', 0); requireThat(o.confidence <= 1, 'confidence', 'Confidence must be between 0 and 1.'); }
      const point = finite(b.direction * o.point / b.reference, 'Normalized point estimate');
      let effective = point, adverse = null;
      if (o.adverse !== null && o.adverse !== undefined) {
        finite(o.adverse, 'Adverse estimate', b.channel === 'cost' ? 0 : -Infinity);
        adverse = finite(b.direction * o.adverse / b.reference, 'Normalized adverse estimate');
        requireThat(b.channel === 'benefit' ? adverse <= point : adverse >= point, 'adverse_direction', b.channel === 'benefit' ? 'Adverse benefit must be no better than the point estimate.' : 'Adverse cost must be at least the point estimate.');
      }
      if (profile.confidence_mode === 'conservative_interpolation_v1') {
        requireThat(o.confidence !== null && o.confidence !== undefined && adverse !== null, 'missing_confidence', 'Confidence and an adverse estimate are required.');
        effective = o.confidence * point + (1 - o.confidence) * adverse;
      }
      finite(effective, 'Effective estimate'); variables[b.key] = effective;
      contributions.push({key: b.key, channel: b.channel, group: b.group, unit: b.unit, point, adverse, effective, confidence: o.confidence ?? null, weight: b.weight, source: o.source});
    } catch (error) { problems.push({key: b.key, code: error.code || 'invalid_estimate', message: error.message}); }
  }
  if (problems.length) throw new SelectionError('incomplete_score', 'Review ' + problems.length + ' predictor input(s).', problems);
  const goal_values = profile.goals.map(g => ({id: g.id, benefit: finite(g.scale * g.predictors.reduce((sum, b) => sum + b.weight * variables[g.id + '__' + b.id], 0), 'Goal benefit'), weight: g.weight}));
  let benefit = goal_values.reduce((sum, g) => sum + g.weight * g.benefit, 0);
  const costs = profile.resources.map(r => ({id: r.id, unit: r.unit, conversion: r.conversion, amount: r.scale * r.predictors.reduce((sum, b) => sum + b.weight * variables[r.id + '__' + b.id], 0)}));
  variables.benefit = benefit;
  for (const c of costs) { variables['cost_' + c.id] = c.amount; variables['burden_' + c.id] = c.amount * c.conversion; }
  if (profile.model.mode === 'custom_v1') {
    benefit = evaluateExpression(profile.model.benefit, variables);
    for (const c of costs) if (Object.hasOwn(profile.model.costs, c.id)) c.amount = finite(evaluateExpression(profile.model.costs[c.id], variables), 'Custom cost', 0);
  }
  const burden = costs.reduce((sum, c) => sum + finite(c.amount, 'Cost', 0) * c.conversion, 0);
  const utility = profile.model.mode === 'custom_net_v1' ? evaluateExpression(profile.model.expression, variables) : benefit - burden;
  return {key: node.key, work_version: node.version, benefit: finite(benefit, 'Benefit'), costs, burden: finite(burden, 'Burden'), utility: finite(utility, 'Utility'), goal_values, contributions, confidence_mode: profile.confidence_mode, model: profile.model.mode};
}
