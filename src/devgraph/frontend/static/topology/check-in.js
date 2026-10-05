(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.DevgraphCheckIn = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const PAGE_SIZE = 6;

  function query(filters = {}, after = null, source = 'todo') {
    const params = new URLSearchParams({limit:String(PAGE_SIZE), archived:'exclude', queue:'not_started', order:'priority'});
    if (filters.q?.trim()) params.set('q', filters.q.trim().slice(0, 200));
    if (source === 'task') {
      params.set('kind', 'Task'); params.set('parentage', 'standalone'); params.set('column', 'backlog');
      if (after) { params.set('after', after.key); params.set('revision', after.revision); }
      return '/monitor/kanban/v1?' + params;
    }
    if (after) { params.set('after_id', after.key); params.set('after_priority', after.priority); }
    return '/monitor/todos/v1?' + params;
  }

  function decodeTodos(data) {
    if (data?.schema !== 'devgraph.todos.v1' || !Array.isArray(data.items) || data.items.length > PAGE_SIZE ||
        !Number.isSafeInteger(data.matching_count) || data.matching_count < data.items.length ||
        typeof data.has_more !== 'boolean' || !data.counts || data.counts.total !== data.matching_count)
      throw new Error('Invalid Todo queue response');
    const seen = new Set();
    const items = data.items.map(item => {
      if (item.kind !== 'Todo' || typeof item.id !== 'string' || !item.id || seen.has(item.id) ||
          typeof item.title !== 'string' || typeof item.priority !== 'string' || !/^-?\d+$/.test(item.priority) ||
          typeof item.version !== 'string' || !/^[1-9]\d*$/.test(item.version) || item.archived !== false ||
          !['draft', 'review', 'accepted'].includes(item.status)) throw new Error('Invalid Todo queue item');
      seen.add(item.id); return {...item, key:`Todo/${item.id}`};
    });
    if (data.has_more ? (!items.length || data.next_after_id !== items.at(-1).id || data.next_after_priority !== items.at(-1).priority) :
        (data.next_after_id !== null || data.next_after_priority !== null)) throw new Error('Invalid Todo queue cursor');
    return {...data, source:'todo', items, counts:{total:data.matching_count},
      next_after_id:data.has_more ? {key:data.next_after_id,priority:data.next_after_priority} : null};
  }

  function decode(board, source = 'todo') {
    if (source === 'todo') return decodeTodos(board);
    if (board?.schema !== 'devgraph.kanban.v1' || typeof board.revision !== 'string' || !board.revision ||
        !Array.isArray(board.columns)) throw new Error('Invalid work queue response');
    const columns = board.columns.filter(column => column.id === 'backlog');
    if (columns.length !== 1) throw new Error('Invalid work queue column');
    const column = columns[0];
    if (!Array.isArray(column.items) || column.items.length > PAGE_SIZE || !Number.isSafeInteger(column.count) ||
        column.count < column.items.length || column.count !== board.total) throw new Error('Invalid work queue count');
    const keys = new Set();
    const items = column.items.map(item => {
      if (item.kind !== 'Task' || item.parent !== null || typeof item.id !== 'string' || !item.id || item.key !== `${item.kind}/${item.id}` || keys.has(item.key) ||
          typeof item.title !== 'string' || typeof item.priority !== 'string' || !/^-?\d+$/.test(item.priority) ||
          typeof item.version !== 'string' || !/^[1-9]\d*$/.test(item.version) || item.column !== 'backlog' ||
          !['draft','review','accepted'].includes(item.lifecycle) || !(item.stage === 'backlog' || item.stage === null && item.lifecycle === 'draft')) throw new Error('Invalid work queue item');
      keys.add(item.key);
      return {...item, status:item.lifecycle, updated_at:null};
    });
    if (column.next_cursor !== null && (!items.length || column.next_cursor !== items.at(-1).key)) throw new Error('Invalid work queue cursor');
    return {source:'task', items, counts:{total:column.count}, matching_count:column.count,
      generated_at:board.read_at || new Date().toISOString(), has_more:column.next_cursor !== null,
      next_after_id:column.next_cursor === null ? null : {key:column.next_cursor,revision:board.revision}};
  }

  function createController({ request, onChange = () => {} }) {
    let serial = 0, controller, pending, source = 'todo';
    const view = { status: 'disconnected', data: null, error: null, filters: {q:''}, cursors: [null], page: 0 };
    const emit = () => onChange(view);
    function reset() {
      serial++; controller?.abort(); pending = null; source = 'todo';
      Object.assign(view, { status: 'disconnected', data: null, error: null, cursors: [null], page: 0, restart:false }); emit();
    }
    async function refresh({ force = false, page = view.page } = {}) {
      if (pending && !force) return pending;
      if (view.status === 'unsupported' && !force) return;
      if (view.restart) page = 0;
      const epoch = ++serial; controller?.abort(); controller = new AbortController();
      view.status = 'loading'; view.error = null; emit();
      const task = (async () => {
        try {
          const signal = controller.signal;
          let payload;
          try { payload = await request(query(view.filters, view.cursors[page], source), {signal}); }
          catch (error) {
            if (epoch !== serial) return;
            if (source !== 'todo' || ![404, 501].includes(error.status)) throw error;
            source = 'task'; page = 0; view.cursors = [null];
            payload = await request(query(view.filters, null, source), {signal});
          }
          const result = decode(payload, source);
          if (epoch !== serial) return;
          view.data = result; view.page = page; view.restart = false;
          view.cursors = view.cursors.slice(0, page + 1);
          if (result.has_more) view.cursors.push(result.next_after_id);
          view.status = 'ready'; view.error = null;
        } catch (error) {
          if (epoch !== serial) return;
          view.status = [404, 501].includes(error.status) ? 'unsupported' : 'error';
          if ([401, 403, 404, 501].includes(error.status)) view.data = null;
          if (error.status === 409) { view.cursors = [null]; view.restart = true; }
          view.error = error.status === 409 ? 'The list changed. Retry to continue from the first page.' : [401, 403].includes(error.status) ? 'Access denied. Reconnect with a valid read key.' :
            view.status === 'unsupported' ? 'This server does not yet support the work queue.' : 'Could not update Todos. Try again.';
        } finally { if (epoch === serial) { pending = null; emit(); } }
      })();
      pending = task; return task;
    }
    function filter(patch) {
      serial++; controller?.abort(); pending = null;
      Object.assign(view.filters, patch); Object.assign(view, { data: null, cursors: [null], page: 0, restart:false });
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
      status.textContent = view.error ? `${view.error}${view.data ? ' Showing previously loaded Todos.' : ''}` : view.status === 'loading' ? (view.data ? 'Checking for updates…' : 'Loading Todos…') : view.data ? `Updated ${new Date(view.data.generated_at).toLocaleTimeString([], {hour:'numeric', minute:'2-digit'})}` : 'Connect to load your Todos.';
      if (view.status === 'unsupported') status.textContent += ' Work on the map remains available below.';
      const signature = JSON.stringify([view.data?.items, view.data?.counts, view.page, view.filters, view.data?.source, view.data ? null : view.status]);
      if (signature === renderedSignature) { deferredView = null; update.hidden = true; preserveControlFocus(); return; }
      if (view.data && list.contains(doc.activeElement) && !force && !navigation) { deferredView = view; update.hidden = false; return; }
      deferredView = null; update.hidden = true;
      renderedSignature = signature; displayedItems = new Map((view.data?.items || []).map(item => [item.key, item]));
      find('todo-count-total').textContent = view.data ? view.data.counts.total : '—';
      find('todo-source').textContent = view.data?.source === 'task' ? 'Standalone Tasks · no parent Issue, Project, or Initiative. Older Draft records count as not started.' : 'Base Todos · not started. Older Draft records count as not started.';
      const content = el('div');
      if (view.data?.items.length) for (const item of view.data.items) {
        const row = el('li', 'todo-row'); row.dataset.uiKey = item.key;
        const button = el('button', 'todo-item'); button.type = 'button'; button.dataset.todoId = item.key;
        const copy = el('span', 'todo-item-copy'); copy.append(el('span', 'todo-item-title', item.title));
        const meta = el('span', 'todo-item-meta'); meta.append(el('span', 'todo-state', `${item.kind} · ${item.stage === null ? 'Draft · Stage not set' : 'Not started'}`), el('span', '', 'Priority ' + item.priority));
        if (item.updated_at) { const time = el('time', '', new Date(item.updated_at).toLocaleDateString([], {month:'short',day:'numeric'})); time.dateTime = item.updated_at; time.title = 'Last changed ' + new Date(item.updated_at).toLocaleString(); meta.append(time); } copy.append(meta);
        const mark = el('span', 'todo-item-mark', '↗'); mark.setAttribute('aria-hidden', 'true'); button.append(copy, mark); row.append(button); content.append(row);
      } else {
        const empty = el('li', 'todo-empty'); empty.dataset.uiKey = 'empty';
        const title = view.status === 'unsupported' ? 'Todo unavailable on this server' : view.status === 'error' ? 'Todos could not be loaded' : view.data ? 'No Todos in this view' : view.status === 'loading' ? 'Loading your check-in' : 'Your daily Todo list';
        empty.append(el('strong', '', title), el('p', '', view.status === 'unsupported' ? 'Connect to a server that supports the current Work board API.' : view.data ? 'No not-started items match this view. Clear your search to see the full list.' : 'Find work waiting to start. Select an item to read its context.'));
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
      try { if (preferenceKey) localStorage.setItem(preferenceKey, JSON.stringify({version:3, ...controller.view.filters})); } catch { /* Filtering remains available without browser storage. */ }
      return result;
    }
    let searchTimer;
    find('todo-search').addEventListener('input', event => { clearTimeout(searchTimer); searchTimer = setTimeout(() => filter({q:event.target.value}), 250); });
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
        const filters = {q:''};
        if (value?.version === 3 && typeof value.q === 'string') filters.q = value.q.slice(0,200);
        Object.assign(controller.view.filters, filters); find('todo-search').value = filters.q; render(controller.view);
      },
      reset() {
        clearTimeout(searchTimer); cancelNavigation(); deferredView = null; preferenceKey = null;
        Object.assign(controller.view.filters, {q:''});
        find('todo-search').value = ''; controller.reset();
      },
    };
  }
  return { PAGE_SIZE, query, decode, createController, mount };
});
