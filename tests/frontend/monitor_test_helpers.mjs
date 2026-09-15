import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

export const frontend = readFileSync(new URL('../../src/devgraph/frontend/app.py', import.meta.url), 'utf8');

// Run the shipped functions, rather than a second implementation of their behavior.
export function functionsSource(names) {
  return names.map(name => {
    const declaration = new RegExp(`^    (?:async )?function ${name}\\(`, 'm').exec(frontend);
    assert.ok(declaration, `${name} must exist in the shipped frontend`);
    const end = frontend.indexOf('\n    }', declaration.index);
    assert.ok(end >= declaration.index, `${name} must have a complete declaration`);
    return frontend.slice(declaration.index, end + 6);
  }).join('\n');
}

export function contextWithFunctions(names, globals) {
  const context = vm.createContext(globals);
  vm.runInContext(functionsSource(names), context);
  return context;
}

export function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

export async function flushPromises() {
  await new Promise(resolve => setImmediate(resolve));
}

export function fakeClock() {
  let nextId = 0;
  const timers = new Map();
  const clock = {
    timers,
    setTimeout(callback, milliseconds) {
      const id = ++nextId; timers.set(id, { callback, milliseconds }); return id;
    },
    clearTimeout(id) { timers.delete(id); },
    expireAll() {
      const pending = [...timers];
      for (const [id, { callback }] of pending) { timers.delete(id); callback(); }
    },
  };
  return clock;
}

export function pendingUntilAbort(signal) {
  return new Promise((_resolve, reject) => {
    const aborted = () => reject(signal.reason || Object.assign(new Error('Aborted'), { name: 'AbortError' }));
    if (signal.aborted) aborted();
    else signal.addEventListener('abort', aborted, { once: true });
  });
}

export function textDocument() {
  const elements = new Map();
  return {
    elements,
    getElementById(id) {
      if (!elements.has(id)) {
        const attributes = {};
        elements.set(id, {
          id, textContent: '', innerHTML: '', className: '', hidden: false,
          setAttribute(name, value) { attributes[name] = String(value); },
          getAttribute(name) { return attributes[name]; },
        });
      }
      return elements.get(id);
    },
  };
}

// A small tree model for exercising keyed reconciliation. Browser QA still checks
// real focus/layout; this records identity, text writes, and child replacements.
export function treeDocument() {
  const document = { activeElement: null };
  class Element {
    constructor(name, value = null) {
      this.nodeName = name === '#text' ? name : name.toUpperCase();
      this.tagName = this.nodeName;
      this.nodeType = name === '#text' ? 3 : 1;
      this.nodeValue = value;
      this.childNodes = [];
      this.dataset = {};
      this.attrs = new Map();
      this.parentNode = null;
      this.listeners = new Map();
      this.scrollTop = 0;
    }
    get children() { return this.childNodes.filter(node => node.nodeType === 1); }
    get attributes() { return [...this.attrs].map(([name, value]) => ({ name, value })); }
    get textContent() { return this.nodeType === 3 ? this.nodeValue : this.childNodes.map(node => node.textContent).join(''); }
    set textContent(value) { this.replaceChildren(new Element('#text', String(value))); }
    set innerHTML(_value) { assert.fail('reader content must be rendered as text, never HTML'); }
    setAttribute(name, value) {
      this.attrs.set(name, String(value));
      if (name === 'class') this.className = String(value);
      if (name.startsWith('data-')) this.dataset[name.slice(5).replace(/-([a-z])/g, (_, letter) => letter.toUpperCase())] = String(value);
    }
    getAttribute(name) { return this.attrs.get(name) ?? null; }
    hasAttribute(name) { return this.attrs.has(name); }
    removeAttribute(name) { this.attrs.delete(name); }
    insertBefore(node, next) {
      node.remove();
      const index = next ? this.childNodes.indexOf(next) : this.childNodes.length;
      this.childNodes.splice(index, 0, node); node.parentNode = this;
      return node;
    }
    append(...nodes) { nodes.forEach(node => this.insertBefore(node, null)); }
    replaceChildren(...nodes) { [...this.childNodes].forEach(node => node.remove()); this.append(...nodes); }
    remove() {
      if (!this.parentNode) return;
      const siblings = this.parentNode.childNodes; siblings.splice(siblings.indexOf(this), 1); this.parentNode = null;
    }
    contains(node) { return node === this || this.childNodes.some(child => child.contains(node)); }
    focus() { document.activeElement = this; }
    addEventListener(name, callback) { this.listeners.set(name, callback); }
    querySelector(selector) {
      return this.children.flatMap(node => [node, ...node.descendants()]).find(node => selector[0] === '.' ? node.className?.split(' ').includes(selector.slice(1)) : node.nodeName === selector.toUpperCase()) || null;
    }
    descendants() { return this.children.flatMap(node => [node, ...node.descendants()]); }
  }
  const ids = new Map();
  document.createElement = name => new Element(name);
  document.getElementById = id => {
    if (!ids.has(id)) ids.set(id, new Element('div'));
    return ids.get(id);
  };
  return document;
}
