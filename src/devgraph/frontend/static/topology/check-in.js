(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.DevgraphCheckIn = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const PAGE_SIZE = 6;
  const statuses = ['draft', 'review', 'accepted', 'archived'];
  const labels = { draft: 'Draft', review: 'In review', accepted: 'Accepted', archived: 'Archived' };

  function query(filters = {}, after = null) {
    const params = new URLSearchParams({ limit: String(PAGE_SIZE), archived: filters.archived === 'only' ? 'only' : 'exclude' });
    if (statuses.includes(filters.status)) params.set('status', filters.status);
    if (filters.q?.trim()) params.set('q', filters.q.trim().slice(0, 200));
    if (after) params.set('after_id', after);
    return '/monitor/todos/v1?' + params;
  }

  function decode(page) {
    if (page?.schema !== 'devgraph.todos.v1' || !Array.isArray(page.items) || page.items.length > PAGE_SIZE ||
        typeof page.has_more !== 'boolean' || !Number.isFinite(Date.parse(page.generated_at))) throw new Error('Invalid Todo response');
    const ids = new Set();
    for (const item of page.items) {
      if (item?.kind !== 'Todo' || typeof item.id !== 'string' || !item.id || ids.has(item.id) ||
          typeof item.title !== 'string' || !statuses.includes(item.status) || typeof item.priority !== 'string' || !/^-?\d+$/.test(item.priority) ||
          typeof item.version !== 'string' || !/^[1-9]\d*$/.test(item.version) || (item.updated_at !== null && !Number.isFinite(Date.parse(item.updated_at)))) throw new Error('Invalid Todo item');
      ids.add(item.id);
    }
    if (page.has_more && (!page.items.length || page.next_after_id !== page.items.at(-1).id)) throw new Error('Invalid Todo cursor');
    for (const name of ['total', ...statuses]) if (!Number.isSafeInteger(page.counts?.[name]) || page.counts[name] < 0) throw new Error('Invalid Todo counts');
    return page;
  }

  function createController({ request, onChange = () => {} }) {
    let serial = 0, controller, pending;
    const view = { status: 'disconnected', data: null, error: null, filters: { status: '', archived: 'exclude', q: '' }, cursors: [null], page: 0 };
    const emit = () => onChange(view);
    function reset() {
      serial++; controller?.abort(); pending = null;
      Object.assign(view, { status: 'disconnected', data: null, error: null, cursors: [null], page: 0 }); emit();
    }
    async function refresh({ force = false, page = view.page } = {}) {
      if (pending && !force) return pending;
      if (view.status === 'unsupported' && !force) return;
      const epoch = ++serial; controller?.abort(); controller = new AbortController();
      view.status = 'loading'; view.error = null; emit();
      const task = (async () => {
        try {
          const result = decode(await request(query(view.filters, view.cursors[page]), { signal: controller.signal }));
          if (epoch !== serial) return;
          view.data = result; view.page = page;
          view.cursors = view.cursors.slice(0, page + 1);
          if (result.has_more) view.cursors.push(result.next_after_id);
          view.status = 'ready'; view.error = null;
        } catch (error) {
          if (epoch !== serial) return;
          view.status = [404, 501].includes(error.status) ? 'unsupported' : 'error';
          if ([401, 403, 404, 501].includes(error.status)) view.data = null;
          view.error = [401, 403].includes(error.status) ? 'Access denied. Reconnect with a valid read key.' :
            view.status === 'unsupported' ? 'This server does not yet support base Todo reads.' : 'Could not update Todos. Try again.';
        } finally { if (epoch === serial) { pending = null; emit(); } }
      })();
      pending = task; return task;
    }
    function filter(patch) {
      serial++; controller?.abort(); pending = null;
      Object.assign(view.filters, patch); Object.assign(view, { data: null, cursors: [null], page: 0 });
      return refresh({ force: true });
    }
    function next() { if (view.data?.has_more && view.status !== 'loading') return refresh({ page: view.page + 1 }); }
    function previous() { if (view.page > 0 && view.status !== 'loading') return refresh({ page: view.page - 1 }); }
    return { view, refresh, reset, filter, next, previous };
  }

  function mount({ root, request, onSelect, reconcile }) {
    const doc = root.ownerDocument;
    const el = (tag, cls, text) => { const node = doc.createElement(tag); if (cls) node.className = cls; if (text !== undefined) node.textContent = text; return node; };
    const find = id => root.querySelector('#' + id);
    let deferredView = null, renderedSignature = '', preferenceKey = null, navigation = null;
    let displayedItems = new Map();
    const list = find('todo-items'), status = find('todo-status'), update = find('todo-updates');
    const controller = createController({ request, onChange: render });
    function render(view, force = false) {
      const previous = find('todo-previous'), next = find('todo-next');
      const focusedControl = [previous, next].includes(doc.activeElement) ? doc.activeElement : null;
      const loading = view.status === 'loading';
      function preserveControlFocus() {
        if (!navigation && focusedControl?.disabled && (!doc.activeElement || doc.activeElement === doc.body || doc.activeElement === doc.documentElement)) {
          (list.querySelector('button') || find('todo-search')).focus({preventScroll:true});
        }
      }
      root.setAttribute('aria-busy', String(view.status === 'loading' && !view.data));
      find('todo-retry').hidden = !['error', 'unsupported'].includes(view.status);
      for (const [button, unavailable] of [[previous, view.page === 0], [next, !view.data?.has_more]]) {
        // Polling must not blur a focused control. Navigation is guarded while loading.
        button.disabled = unavailable || (loading && button !== focusedControl);
        button.setAttribute('aria-disabled', String(unavailable || loading));
      }
      for (const button of root.querySelectorAll('[data-todo-status]')) button.setAttribute('aria-pressed', String(button.dataset.todoStatus === view.filters.status));
      status.textContent = view.error ? `${view.error}${view.data ? ' Showing previously loaded Todos.' : ''}` : view.status === 'loading' ? (view.data ? 'Checking for updates…' : 'Loading Todos…') : view.data ? `Updated ${new Date(view.data.generated_at).toLocaleTimeString([], {hour:'numeric', minute:'2-digit'})}` : 'Connect to load your Todos.';
      if (view.status === 'unsupported') status.textContent += ' Work on the map remains available below.';
      const signature = JSON.stringify([view.data?.items, view.data?.counts, view.page, view.filters, view.data ? null : view.status]);
      if (signature === renderedSignature) { deferredView = null; update.hidden = true; preserveControlFocus(); return; }
      if (view.data && list.contains(doc.activeElement) && !force && !navigation) { deferredView = view; update.hidden = false; return; }
      deferredView = null; update.hidden = true;
      renderedSignature = signature; displayedItems = new Map((view.data?.items || []).map(item => [item.id, item]));
      for (const name of ['total', 'review', 'draft', 'accepted']) find('todo-count-' + name).textContent = view.data ? view.data.counts[name] : '—';
      const content = el('div');
      if (view.data?.items.length) for (const item of view.data.items) {
        const row = el('li', 'todo-row'); row.dataset.uiKey = item.id;
        const button = el('button', 'todo-item'); button.type = 'button'; button.dataset.todoId = item.id;
        const copy = el('span', 'todo-item-copy'); copy.append(el('span', 'todo-item-title', item.title));
        const meta = el('span', 'todo-item-meta'); meta.append(el('span', 'todo-state', labels[item.status]), el('span', '', 'Priority ' + item.priority));
        if (item.updated_at) { const time = el('time', '', new Date(item.updated_at).toLocaleDateString([], {month:'short',day:'numeric'})); time.dateTime = item.updated_at; time.title = 'Last changed ' + new Date(item.updated_at).toLocaleString(); meta.append(time); } copy.append(meta);
        const mark = el('span', 'todo-item-mark', '↗'); mark.setAttribute('aria-hidden', 'true'); button.append(copy, mark); row.append(button); content.append(row);
      } else {
        const empty = el('li', 'todo-empty'); empty.dataset.uiKey = 'empty';
        const title = view.status === 'unsupported' ? 'Todo unavailable on this server' : view.status === 'error' ? 'Todos could not be loaded' : view.data ? 'No Todos in this view' : view.status === 'loading' ? 'Loading your check-in' : 'Your daily Todo list';
        empty.append(el('strong', '', title), el('p', '', view.status === 'unsupported' ? 'The installed API needs the new Todo reader. Tasks and other work types are kept out of this list.' : view.data ? 'Only base Todo records appear here. Try another status or clear your search.' : 'Review priorities and items awaiting a decision. Select a Todo to read its context.'));
        content.append(empty);
      }
      if (reconcile) reconcile(list, content); else list.replaceChildren(...content.children);
      find('todo-page').textContent = view.data ? `Page ${view.page + 1} · ${view.data.items.length} shown${view.data.has_more ? ' · more available' : ''}` : '6 items per page';
      preserveControlFocus();
    }
    function cancelNavigation() {
      navigation?.removeListener(); navigation = null;
    }
    async function navigate(action, button) {
      if (controller.view.status === 'loading') return;
      cancelNavigation();
      const origin = doc.activeElement;
      const operation = { moved: false, ownedFocus: origin === button || list.contains(origin) };
      const trackFocus = event => {
        if (![button, origin, doc.body, doc.documentElement].includes(event.target)) operation.moved = true;
      };
      doc.addEventListener('focusin', trackFocus);
      operation.removeListener = () => doc.removeEventListener('focusin', trackFocus);
      navigation = operation;
      try { await action(); }
      finally {
        operation.removeListener();
        if (navigation === operation) {
          navigation = null;
          const lostFocus = !doc.activeElement || doc.activeElement === doc.body || doc.activeElement === doc.documentElement;
          if (operation.ownedFocus && !operation.moved && (doc.activeElement === button || lostFocus)) {
            // Native disabling blurs Next/Previous. Recover only our own lost focus.
            const target = button.disabled || origin !== button ? list.querySelector('button') || find('todo-search') : button;
            target.focus({preventScroll:true});
          }
        }
      }
    }
    function filter(patch) {
      cancelNavigation();
      const result = controller.filter(patch);
      try { if (preferenceKey) localStorage.setItem(preferenceKey, JSON.stringify({version:1, ...controller.view.filters})); } catch { /* Filtering remains available without browser storage. */ }
      return result;
    }
    for (const button of root.querySelectorAll('[data-todo-status]')) button.addEventListener('click', () => filter({status: button.dataset.todoStatus}));
    let searchTimer;
    find('todo-search').addEventListener('input', event => { clearTimeout(searchTimer); searchTimer = setTimeout(() => filter({q:event.target.value}), 250); });
    find('todo-archive').addEventListener('change', event => filter({archived:event.target.value, status:''}));
    find('todo-retry').addEventListener('click', () => controller.refresh({force:true}));
    find('todo-next').addEventListener('click', () => navigate(() => controller.next(), find('todo-next')));
    find('todo-previous').addEventListener('click', () => navigate(() => controller.previous(), find('todo-previous')));
    update.addEventListener('click', () => { const view = deferredView; if (view) { render(view, true); (list.querySelector('button') || find('todo-search')).focus(); } });
    function select(event, modal) {
      const button = event.target.closest('[data-todo-id]'); if (!button) return;
      const item = displayedItems.get(button.dataset.todoId); if (!item) return;
      if (modal) event.preventDefault(); onSelect(item, {modal, returnFocus:button});
    }
    list.addEventListener('click', event => select(event, false));
    list.addEventListener('contextmenu', event => select(event, true));
    list.addEventListener('keydown', event => { if (event.key === 'ContextMenu' || (event.shiftKey && event.key === 'F10')) select(event, true); });
    render(controller.view);
    return {
      ...controller,
      setPreferenceKey(key) {
        if (!key || key === preferenceKey) return; preferenceKey = key;
        let value; try { value = JSON.parse(localStorage.getItem(key)); } catch { value = null; }
        const filters = {status:'',archived:'exclude',q:''};
        if (value?.version === 1) { if (statuses.includes(value.status)) filters.status = value.status; if (value.archived === 'only') filters.archived = 'only'; if (typeof value.q === 'string') filters.q = value.q.slice(0,200); }
        Object.assign(controller.view.filters, filters); find('todo-search').value = filters.q; find('todo-archive').value = filters.archived; render(controller.view);
      },
      reset() {
        clearTimeout(searchTimer); cancelNavigation(); deferredView = null; preferenceKey = null;
        Object.assign(controller.view.filters, {status:'',archived:'exclude',q:''});
        find('todo-search').value = ''; find('todo-archive').value = 'exclude'; controller.reset();
      },
    };
  }
  return { PAGE_SIZE, query, decode, createController, mount };
});
