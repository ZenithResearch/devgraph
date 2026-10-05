/* A bounded, nonmodal chooser for overlapping graph items. No graph state or I/O. */
(function(root, factory) {
  'use strict';
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.DevgraphChooser = api;
})(globalThis, function() {
  'use strict';
  const LIMIT = 20;
  let nextId = 0;
  const number = (value, fallback = 0) => Number.isFinite(value) ? value : fallback;
  const clamp = (value, low, high) => Math.max(low, Math.min(high, value));

  // Coordinates are viewport CSS pixels in; container-relative CSS pixels out.
  // The visible intersection matters when the graph is partly below the fold.
  function placement({bounds, viewport = bounds, point = {}, size = {}, margin = 8, gap = 10}) {
    const left = Math.max(number(bounds.left), number(viewport.left));
    const top = Math.max(number(bounds.top), number(viewport.top));
    const right = Math.min(number(bounds.left) + Math.max(0, number(bounds.width)), number(viewport.left) + Math.max(0, number(viewport.width)));
    const bottom = Math.min(number(bounds.top) + Math.max(0, number(bounds.height)), number(viewport.top) + Math.max(0, number(viewport.height)));
    const visibleWidth = Math.max(0, right - left), visibleHeight = Math.max(0, bottom - top);
    const inset = Math.min(Math.max(0, number(margin)), visibleWidth / 4, visibleHeight / 4);
    const maxWidth = Math.max(0, visibleWidth - 2 * inset);
    const maxHeight = Math.max(0, visibleHeight - 2 * inset);
    const width = Math.min(Math.max(0, number(size.width, 360)), maxWidth);
    const height = Math.min(Math.max(0, number(size.height)), maxHeight);
    const x = number(point.x, left), y = number(point.y, top), offset = Math.max(0, number(gap));
    const desiredX = x + offset + width <= right - inset ? x + offset : x - offset - width;
    const desiredY = y + offset + height <= bottom - inset ? y + offset : y - offset - height;
    return {
      left: clamp(desiredX, left + inset, left + inset + maxWidth - width) - number(bounds.left),
      top: clamp(desiredY, top + inset, top + inset + maxHeight - height) - number(bounds.top),
      width, height, maxWidth, maxHeight,
    };
  }

  function navigationIndex(key, current, count) {
    if (!Number.isInteger(count) || count < 1) return null;
    if (key === 'Home') return 0;
    if (key === 'End') return count - 1;
    if (key === 'ArrowDown') return current < 0 ? 0 : (current + 1) % count;
    if (key === 'ArrowUp') return current < 0 ? count - 1 : (current - 1 + count) % count;
    return null;
  }

  function create({panel, container, onSelect, onClose = () => {}, renderIcon}) {
    const doc = panel.ownerDocument, win = doc.defaultView;
    const labelId = `devgraph-chooser-title-${++nextId}`, hintId = `${labelId}-hint`;
    let active = false, opener = null, anchor = null, buttons = [], destroyed = false;
    if (panel.parentNode !== container) container.append(panel);
    container.classList.add('dg-chooser-container');
    panel.classList.add('dg-chooser');
    panel.setAttribute('role', 'dialog');
    panel.setAttribute('aria-modal', 'false');
    panel.setAttribute('aria-labelledby', labelId);
    panel.setAttribute('aria-describedby', hintId);
    panel.hidden = true;

    const make = (tag, className, text) => {
      const element = doc.createElement(tag);
      element.className = className;
      if (text !== undefined) element.textContent = text;
      return element;
    };
    function close({restoreFocus = true, reason = 'close'} = {}) {
      if (!active) return;
      const target = opener;
      active = false;
      panel.hidden = true;
      opener = null;
      onClose({reason, restoreFocus});
      if (restoreFocus && target?.focus && target.isConnected !== false && !target.disabled && !target.closest?.('[inert], [hidden]')) {
        target.focus({preventScroll: true});
      }
    }
    function select(item, modal) {
      if (!active) return;
      close({restoreFocus: false, reason: 'selection'});
      onSelect(item, {modal});
    }
    function bounds() {
      const rect = container.getBoundingClientRect();
      return {
        left: rect.left + (container.clientLeft || 0), top: rect.top + (container.clientTop || 0),
        width: container.clientWidth, height: container.clientHeight,
      };
    }
    function reposition() {
      if (!active) return;
      const box = bounds(), visual = win.visualViewport;
      const viewport = visual ? {left: visual.offsetLeft, top: visual.offsetTop, width: visual.width, height: visual.height}
        : {left: 0, top: 0, width: win.innerWidth, height: win.innerHeight};
      const point = {x: box.left + anchor.x - container.scrollLeft, y: box.top + anchor.y - container.scrollTop};
      const limit = placement({bounds: box, viewport, point, size: {width: 360}});
      if (limit.maxWidth < 2 || limit.maxHeight < 2) { close({restoreFocus: false, reason: 'unavailable'}); return; }
      panel.style.width = `${limit.width}px`;
      panel.style.maxWidth = `${limit.maxWidth}px`;
      panel.style.maxHeight = `${Math.min(440, limit.maxHeight)}px`;
      // Even a tiny viewport must accommodate the border and padding inside its cap.
      panel.style.padding = `${Math.min(8, Math.max(0, (Math.min(limit.maxWidth, limit.maxHeight) - 2) / 2))}px`;
      const rect = panel.getBoundingClientRect();
      const result = placement({bounds: box, viewport, point, size: {width: rect.width, height: rect.height}});
      panel.style.left = `${result.left + container.scrollLeft}px`;
      panel.style.top = `${result.top + container.scrollTop}px`;
    }
    function focusButton(button) {
      button.focus({preventScroll: true});
      const item = button.getBoundingClientRect(), outer = panel.getBoundingClientRect();
      const top = outer.top + (panel.clientTop || 0), bottom = top + panel.clientHeight;
      if (item.top < top) panel.scrollTop -= top - item.top;
      else if (item.bottom > bottom) panel.scrollTop += item.bottom - bottom;
    }
    function open(items, {clientX, clientY, returnFocus} = {}) {
      if (destroyed) return false;
      // A background refresh must not replace choices or steal focus mid-choice.
      if (active) { reposition(); return false; }
      if (!items.length) return false;
      const snapshot = items.map(item => Object.freeze({...item}));
      const counts = new Map();
      for (const item of snapshot) counts.set(item.title, (counts.get(item.title) || 0) + 1);
      const title = make('h4', 'dg-chooser__title', `${snapshot.length} item${snapshot.length === 1 ? '' : 's'} here`);
      title.id = labelId;
      const hint = make('p', 'dg-chooser__hint', 'Choose one to open its details.'); hint.id = hintId;
      const header = make('div', 'dg-chooser__header');
      const dismiss = make('button', 'dg-chooser__close', 'Close'); dismiss.type = 'button';
      dismiss.setAttribute('aria-label', 'Close item chooser');
      dismiss.addEventListener('click', () => close());
      header.append(title, dismiss);
      const list = make('ul', 'dg-chooser__list'); buttons = [];
      for (const item of snapshot.slice(0, LIMIT)) {
        const row = make('li', 'dg-chooser__row'), button = make('button', 'dg-chooser__item');
        button.type = 'button'; button.dataset.chooserKey = item.key;
        const icon = renderIcon?.(item);
        if (icon) { const slot = make('span', 'dg-chooser__icon'); slot.setAttribute('aria-hidden', 'true'); slot.append(icon); button.append(slot); }
        const copy = make('span', 'dg-chooser__copy');
        copy.append(make('span', 'dg-chooser__name', item.title || 'Untitled item'));
        const metadata = [item.kind, item.status?.replaceAll('_', ' ')].filter(Boolean);
        copy.append(make('span', 'dg-chooser__meta', metadata.join(' · ')));
        if (counts.get(item.title) > 1) copy.append(make('span', 'dg-chooser__id', item.id || item.key));
        button.append(copy);
        button.addEventListener('click', event => { event.stopPropagation(); select(item, false); });
        button.addEventListener('contextmenu', event => { event.preventDefault(); event.stopPropagation(); select(item, true); });
        button.addEventListener('keydown', event => {
          if (event.key === 'ContextMenu' || (event.shiftKey && event.key === 'F10')) {
            event.preventDefault(); event.stopPropagation(); select(item, true);
          }
        });
        buttons.push(button); row.append(button); list.append(row);
      }
      panel.replaceChildren(header, hint, list);
      if (snapshot.length > LIMIT) panel.append(make('p', 'dg-chooser__limit', `Showing ${LIMIT} of ${snapshot.length}. Zoom in or browse visible items to find the others.`));
      opener = returnFocus || doc.activeElement;
      const box = bounds(); anchor = {x: number(clientX, box.left) - box.left + container.scrollLeft, y: number(clientY, box.top) - box.top + container.scrollTop};
      active = true; panel.hidden = false; panel.style.visibility = 'hidden'; panel.scrollTop = 0;
      reposition(); panel.style.visibility = '';
      if (active) focusButton(buttons[0]);
      return active;
    }
    function keyboard(event) {
      if (!active) return;
      if (event.key === 'Escape') {
        event.preventDefault(); event.stopPropagation(); close({reason: 'escape'}); return;
      }
      // This is nonmodal. Tab follows the document; focus outside dismisses it.
      if (event.key === 'Tab' || event.altKey || event.metaKey || event.ctrlKey) return;
      const next = navigationIndex(event.key, buttons.indexOf(doc.activeElement), buttons.length);
      if (next !== null) { event.preventDefault(); event.stopPropagation(); focusButton(buttons[next]); }
    }
    const outside = event => { if (active && !panel.contains(event.target)) close({restoreFocus: false, reason: 'outside'}); };
    const focusOutside = event => { if (active && !panel.contains(event.target)) close({restoreFocus: false, reason: 'focusout'}); };
    panel.addEventListener('keydown', keyboard);
    doc.addEventListener('pointerdown', outside, true);
    doc.addEventListener('focusin', focusOutside);
    win.addEventListener('resize', reposition);
    win.addEventListener('scroll', reposition, true);
    win.visualViewport?.addEventListener('resize', reposition);
    win.visualViewport?.addEventListener('scroll', reposition);
    const observer = win.ResizeObserver ? new win.ResizeObserver(reposition) : null;
    observer?.observe(container); observer?.observe(panel);
    function destroy() {
      close({restoreFocus: false, reason: 'destroy'}); destroyed = true;
      panel.removeEventListener('keydown', keyboard);
      doc.removeEventListener('pointerdown', outside, true);
      doc.removeEventListener('focusin', focusOutside);
      win.removeEventListener('resize', reposition);
      win.removeEventListener('scroll', reposition, true);
      win.visualViewport?.removeEventListener('resize', reposition);
      win.visualViewport?.removeEventListener('scroll', reposition);
      observer?.disconnect();
    }
    return {open, close, destroy, get isOpen() { return active; }};
  }
  return {create, placement, navigationIndex, limit: LIMIT};
});
