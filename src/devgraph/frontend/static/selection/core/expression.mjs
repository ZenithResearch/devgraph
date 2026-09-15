import {finite, requireThat} from './contracts.mjs';

/** Bounded arithmetic grammar; no JS evaluation, property access or hidden inputs. */
export function evaluateExpression(source, variables) {
  requireThat(typeof source === 'string' && source.length > 0 && source.length <= 2048, 'expression_size', 'Expression must contain 1–2048 characters.');
  const tokens = []; let offset = 0;
  while (offset < source.length) {
    const match = /^\s*(?:(\d+(?:\.\d*)?(?:[eE][+-]?\d+)?|\.\d+(?:[eE][+-]?\d+)?)|([A-Za-z_][A-Za-z0-9_]*)|([+*/(),-]))/.exec(source.slice(offset));
    if (!match && /^\s*$/.test(source.slice(offset))) break;
    requireThat(match, 'expression_syntax', 'Unsupported expression syntax at character ' + offset + '.');
    tokens.push(match[1] ?? match[2] ?? match[3]); offset += match[0].length;
    requireThat(tokens.length <= 512, 'expression_size', 'Expression has too many tokens.');
  }
  let at = 0;
  const functions = {min: (...xs) => Math.min(...xs), max: (...xs) => Math.max(...xs), abs: Math.abs, sqrt: Math.sqrt};
  function expression(depth = 0) {
    let value = term(depth + 1);
    while (tokens[at] === '+' || tokens[at] === '-') { const op = tokens[at++], rhs = term(depth + 1); value = op === '+' ? value + rhs : value - rhs; }
    return finite(value, 'Expression result');
  }
  function term(depth) {
    let value = primary(depth + 1);
    while (tokens[at] === '*' || tokens[at] === '/') {
      const op = tokens[at++], rhs = primary(depth + 1);
      requireThat(op !== '/' || rhs !== 0, 'expression_zero', 'Division by zero.'); value = op === '*' ? value * rhs : value / rhs;
    }
    return finite(value, 'Expression result');
  }
  function primary(depth) {
    requireThat(depth <= 48, 'expression_depth', 'Expression nesting is too deep.');
    const token = tokens[at++];
    if (token === '+' || token === '-') return (token === '-' ? -1 : 1) * primary(depth + 1);
    if (token === '(') { const v = expression(depth + 1); requireThat(tokens[at++] === ')', 'expression_syntax', 'Missing closing parenthesis.'); return v; }
    if (token && /^(\d|\.)/.test(token)) return finite(Number(token), 'Expression number');
    requireThat(token && /^[A-Za-z_]/.test(token), 'expression_syntax', 'Expected a number or declared predictor.');
    if (tokens[at] === '(') {
      requireThat(Object.hasOwn(functions, token), 'expression_function', 'Allowed functions: min, max, abs, sqrt.'); at++;
      const args = [expression(depth + 1)];
      while (tokens[at] === ',') { at++; args.push(expression(depth + 1)); }
      requireThat(tokens[at++] === ')' && args.length <= 16 && (!['abs', 'sqrt'].includes(token) || args.length === 1), 'expression_arguments', 'Invalid function arguments.');
      return finite(functions[token](...args), 'Function result');
    }
    requireThat(Object.hasOwn(variables, token), 'expression_variable', 'Unknown predictor: ' + token);
    return finite(variables[token], token);
  }
  const value = expression();
  requireThat(at === tokens.length, 'expression_syntax', 'Unexpected token: ' + tokens[at]);
  return value;
}
