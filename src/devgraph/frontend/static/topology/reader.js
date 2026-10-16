/* A native modal borrows the existing reader, preserving its data and controls. */
(function (root) {
  'use strict';
  function create({dialog, panel, closeButton, modeButton, title, fallbackFocus, onRestore = () => {}}) {
    const doc = dialog.ownerDocument;
    let saved = null;

    function restore() {
      if (!saved) return;
      const previous = saved; saved = null;
      previous.placeholder.parentNode.insertBefore(panel, previous.placeholder);
      previous.placeholder.remove();
      panel.classList.toggle('collapsed', previous.collapsed);
      modeButton.hidden = previous.modeHidden;
      closeButton.textContent = previous.closeText;
      closeButton.setAttribute('aria-expanded', String(!previous.collapsed));
      doc.documentElement.style.overflow = previous.overflow;
      onRestore();
      const target = previous.focus?.isConnected && !previous.focus.closest('[inert]') ? previous.focus : fallbackFocus;
      target.focus({preventScroll: true});
    }

    function close() {
      if (dialog.open) dialog.close();
      restore();
    }

    function open(trigger) {
      if (dialog.open) { title.focus({preventScroll: true}); return; }
      const placeholder = doc.createElement('div');
      placeholder.className = panel.className;
      placeholder.setAttribute('aria-hidden', 'true');
      saved = {placeholder, collapsed: panel.classList.contains('collapsed'), modeHidden: modeButton.hidden,
        closeText: closeButton.textContent, focus: trigger || doc.activeElement, overflow: doc.documentElement.style.overflow};
      panel.parentNode.insertBefore(placeholder, panel);
      dialog.append(panel); panel.classList.remove('collapsed'); modeButton.hidden = true;
      closeButton.textContent = 'Close reader'; closeButton.setAttribute('aria-expanded', 'true');
      doc.documentElement.style.overflow = 'hidden';
      try { dialog.showModal(); title.focus({preventScroll: true}); }
      catch (error) { restore(); throw error; }
    }

    dialog.addEventListener('close', () => { if (!dialog.open) restore(); });
    dialog.addEventListener('cancel', event => { event.preventDefault(); close(); });
    dialog.addEventListener('keydown', event => {
      // Keep the fullscreen canvas's keyboard handler out of this nested modal.
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); close(); }
      else if (event.key === 'Tab') {
        event.stopPropagation();
        const focusable = [...dialog.querySelectorAll('button, input, select, summary, a[href], [tabindex]')]
          .filter(item => !item.disabled && !item.closest('[inert]') && item.tabIndex >= 0 && item.getClientRects().length);
        const first = focusable[0] || closeButton, last = focusable.at(-1) || closeButton;
        if (event.shiftKey && (doc.activeElement === first || !focusable.includes(doc.activeElement))) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && (doc.activeElement === last || !focusable.includes(doc.activeElement))) { event.preventDefault(); first.focus(); }
      }
    });
    let backdropDown = false;
    dialog.addEventListener('pointerdown', event => { backdropDown = event.target === dialog; });
    dialog.addEventListener('click', event => { if (backdropDown && event.target === dialog) close(); backdropDown = false; });
    return {open, close, get active() { return dialog.open; }};
  }
  root.DevgraphReaderModal = {create};
})(globalThis);
