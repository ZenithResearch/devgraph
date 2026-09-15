import test from 'node:test';
import assert from 'node:assert/strict';
import {evaluateExpression} from '../../src/devgraph/frontend/static/selection/core/expression.mjs';

test('arithmetic functions retain their results and precedence', () => {
  for (const [source, expected] of [
    ['min(7, -4, 2)', -4],
    ['max(-7, -4, -2)', -2],
    ['abs(-9)', 9],
    ['sqrt(81)', 9],
    ['max(min(x, 4), abs(-2)) + sqrt(9) * 2', 10],
    ['min(3)', 3],
    ['max(3)', 3],
  ]) {
    assert.equal(evaluateExpression(source, {x: 8}), expected, source);
  }
});

test('function calls reject inherited, constructor and undeclared names before arguments', () => {
  for (const name of [
    'constructor', '__proto__', 'prototype', 'toString', 'valueOf',
    'hasOwnProperty', '__defineGetter__', 'eval', 'Function', 'random', 'MIN', 'custom',
  ]) {
    assert.throws(() => evaluateExpression(`${name}(unknown_predictor)`, {}), {
      name: 'SelectionError', code: 'expression_function',
    }, name);
  }
});

test('declared predictors do not introduce or override callable functions', () => {
  const variables = {min: 99, max: -99, abs: 88, sqrt: 77, custom: 66};
  assert.equal(evaluateExpression('min + max + abs + sqrt + custom', variables), 231);
  assert.equal(evaluateExpression('min(8, 3) + max(8, 3) + abs(-2) + sqrt(4)', variables), 15);
  assert.throws(() => evaluateExpression('custom(1)', variables), {
    code: 'expression_function',
  });
});

test('function arity and finite-result checks remain bounded', () => {
  const sixteen = Array.from({length: 16}, (_, i) => String(i)).join(',');
  assert.equal(evaluateExpression(`min(${sixteen})`, {}), 0);
  assert.equal(evaluateExpression(`max(${sixteen})`, {}), 15);
  for (const source of [`min(${sixteen},16)`, `max(${sixteen},16)`, 'abs(1, 2)', 'sqrt(1, 2)']) {
    assert.throws(() => evaluateExpression(source, {}), {code: 'expression_arguments'}, source);
  }
  for (const source of ['min()', 'max()', 'abs()', 'sqrt()']) {
    assert.throws(() => evaluateExpression(source, {}), {code: 'expression_syntax'}, source);
  }
  assert.throws(() => evaluateExpression('sqrt(-1)', {}), {code: 'invalid_number'});
});

test('expression input cannot invoke a property or chain a returned call', () => {
  for (const source of ['Math.max(1, 2)', 'min.call(1)', 'min[0](1)', 'sqrt(4)(1)']) {
    assert.throws(() => evaluateExpression(source, {}), {code: 'expression_syntax'}, source);
  }
});
