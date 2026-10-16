/* Owns the canvas panel's fullscreen lifecycle; never rebuilds graph state. */
(function (root) {
  'use strict';
  function create({element, button, onChange = () => {}, announce = () => {}}) {
    const doc = element.ownerDocument;
    let active = false, native = false, pending = false, placeholder = null;
    let previousFocus = null, background = [], scrollLock = '', scrollPosition = null;

    function restore() {
      if (!active) return;
      active = false; native = false;
      element.classList.remove('is-fullscreen');
      placeholder.parentNode.insertBefore(element, placeholder); placeholder.remove(); placeholder = null;
      for (const [item, inert] of background) item.inert = inert;
      background = []; doc.body.style.overflow = scrollLock;
      button.textContent = 'Full screen'; button.setAttribute('aria-expanded', 'false');
      onChange(false);
      if (scrollPosition) doc.defaultView.scrollTo({...scrollPosition, behavior: 'instant'});
      (previousFocus?.isConnected && !previousFocus.inert ? previousFocus : button).focus({preventScroll: true});
      announce('Canvas returned to the page.');
    }

    async function enter() {
      if (active || pending) return;
      active = true; previousFocus = doc.activeElement;
      scrollPosition = doc.defaultView ? {left: doc.defaultView.scrollX, top: doc.defaultView.scrollY} : null;
      // Portal the same element to the body so container queries and transformed
      // ancestors cannot constrain the browser-filling fallback.
      placeholder = doc.createComment('graph surface');
      element.parentNode.insertBefore(placeholder, element); doc.body.append(element);
      background = [...doc.body.children].filter(item => item !== element).map(item => [item, item.inert]);
      for (const [item] of background) item.inert = true;
      scrollLock = doc.body.style.overflow; doc.body.style.overflow = 'hidden';
      element.classList.add('is-fullscreen');
      button.textContent = 'Exit full screen'; button.setAttribute('aria-expanded', 'true');
      onChange(true); button.focus({preventScroll: true});
      announce('Canvas is full screen. Press Escape to return.');
      if (typeof element.requestFullscreen !== 'function' || doc.fullscreenEnabled === false) return;
      pending = true;
      try {
        await element.requestFullscreen();
        // Escape can arrive while the browser is still entering fullscreen.
        if (!active && doc.fullscreenElement === element) await doc.exitFullscreen();
        else native = doc.fullscreenElement === element;
      } catch {
        // Embedded browsers may disallow the native API. The isolated panel
        // already fills their viewport and retains the same exit controls.
      } finally { pending = false; }
    }

    async function exit() {
      if (!active) return;
      if (doc.fullscreenElement === element) {
        try { await doc.exitFullscreen(); }
        catch { announce('Press Escape to leave full screen.'); return; }
      }
      restore();
    }

    function fullscreenChanged() {
      if (doc.fullscreenElement === element) { if (active) native = true; return; }
      if (native) restore();
    }

    function keydown(event) {
      if (!active || event.defaultPrevented) return;
      if (event.key === 'Escape') { event.preventDefault(); void exit(); return; }
      if (event.key !== 'Tab') return;
      const focusable = [...element.querySelectorAll('button, input, select, summary, a[href], [tabindex]')]
        .filter(item => !item.disabled && item.tabIndex >= 0 && item.getClientRects().length);
      const first = focusable[0] || button, last = focusable.at(-1) || button;
      if (event.shiftKey && (doc.activeElement === first || !element.contains(doc.activeElement))) {
        event.preventDefault(); last.focus();
      } else if (!event.shiftKey && (doc.activeElement === last || !element.contains(doc.activeElement))) {
        event.preventDefault(); first.focus();
      }
    }

    doc.addEventListener('fullscreenchange', fullscreenChanged);
    doc.addEventListener('keydown', keydown);
    return {enter, exit, toggle: () => active ? exit() : enter(), get active() { return active; }};
  }
  root.DevgraphGraphSurface = {create};
})(globalThis);
