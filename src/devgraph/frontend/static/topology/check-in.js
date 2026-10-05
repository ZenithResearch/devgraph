(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.DevgraphCheckIn = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const PAGE_SIZE = 6;

  const KINDS = ['Todo','Proposal','Initiative','Project','Issue','Task'];
  // This is a scoped reading aid, not an inferred workflow or completion rollup.
  function briefing(snapshot) {
    const nodes = new Map((snapshot?.graph_nodes || []).filter(n => KINDS.includes(n.kind)).map(n => [n.key, n]));
    const archived = node => node.archived === true || node.status === 'archived';
    const work = [...nodes.values()].filter(n => !archived(n));
    const attention = new Map(), dependencies = new Set();
    const add = (node, reason) => {
      if (!node || archived(node) || node.todo_progress === 'done') return;
      if (!attention.has(node.key)) attention.set(node.key, {node, reasons:new Set()});
      attention.get(node.key).reasons.add(reason);
    };
    for (const edge of snapshot?.graph_edges || []) {
      const [dependent, prerequisite] = edge.relationship === 'BLOCKS' ? [edge.target, edge.source] :
        edge.relationship === 'DEPENDS_ON' ? [edge.source, edge.target] : [];
      if (!dependent) continue;
      const node = nodes.get(dependent), other = nodes.get(prerequisite);
      if (!node || archived(node) || node.todo_progress === 'done' || other?.todo_progress === 'done') continue;
      dependencies.add(node.key);
      add(node, other ? `Check dependency: ${other.title}` : 'Dependency outside this view');
    }
    let reviewing = 0;
    for (const node of work) {
      // Only explicit canonical progress and a recorded stage can establish a review.
      if (node.todo_progress !== 'in_progress') continue;
      if (['review','plan_approval'].includes(node.workflow?.column)) { reviewing++; add(node, node.workflow.label || 'Review required'); }
      if (node.workflow?.column === 'waiting') add(node, node.workflow.label || 'Waiting for input');
    }
    const changed = new Map((snapshot?.recent_activity || []).filter(row => row.type === 'work')
      .map(row => [`${row.label}:${row.id}`, Date.parse(row.timestamp) || 0]));
    const order = (a, b) => Number(attention.has(b.key)) - Number(attention.has(a.key)) ||
      (changed.get(`${b.kind}:${b.id}`) || 0) - (changed.get(`${a.kind}:${a.id}`) || 0) ||
      a.title.localeCompare(b.title) || a.key.localeCompare(b.key);
    const projects = work.filter(n => ['Project','Initiative'].includes(n.kind)).sort(order);
    const actions = [...attention.values()].sort((a, b) => order(a.node,b.node));
    return {projects:projects.slice(0,6), projectTotal:projects.length,
      attention:actions.slice(0,4).map(row => ({node:row.node, reason:[...row.reasons].join(' · ')})), attentionTotal:actions.length,
      started:work.filter(n => n.todo_progress === 'in_progress').length, reviewing, dependencies:dependencies.size,
      unclassified:work.filter(n => !['not_started','in_progress','done'].includes(n.todo_progress)).length};
  }
  function query(filters = {}, after = null) {
    const params = new URLSearchParams({limit:String(PAGE_SIZE), archived:'exclude', progress:'not_started'});
    if (filters.q?.trim()) params.set('q', filters.q.trim().slice(0, 200));
    if (KINDS.includes(filters.kind)) params.set('kind', filters.kind);
    if (after) { params.set('after', after.key); params.set('revision', after.revision); }
    return '/todos/v2?' + params;
  }
  function decode(data) {
    if (data?.schema !== 'devgraph.todos.v2' || typeof data.revision !== 'string' || !data.revision ||
        !Array.isArray(data.items) || data.items.length > PAGE_SIZE || typeof data.complete !== 'boolean' ||
        !Number.isSafeInteger(data.total) || data.total < data.items.length ||
        !Number.isSafeInteger(data.classification_required) || data.classification_required < 0)
      throw new Error('Invalid Todo queue response');
    const seen = new Set();
    const items = data.items.map(item => {
      if (!KINDS.includes(item.kind) || typeof item.id !== 'string' || !item.id || item.key !== `${item.kind}/${item.id}` || seen.has(item.key) ||
          typeof item.title !== 'string' || typeof item.priority !== 'string' || !/^-?\d+$/.test(item.priority) ||
          typeof item.version !== 'string' || !/^[1-9]\d*$/.test(item.version) || item.archived !== false ||
          item.progress !== 'not_started') throw new Error('Invalid Todo queue item');
      seen.add(item.key); return item;
    });
    if (data.next_cursor !== null && (!items.length || data.next_cursor !== items.at(-1).key)) throw new Error('Invalid Todo queue cursor');
    return {...data, source:'todo', items, counts:{total:data.total}, generated_at:new Date().toISOString(),
      has_more:data.next_cursor !== null,
      next_after_id:data.next_cursor === null ? null : {key:data.next_cursor, revision:data.revision}};
  }

  function createController({ request, onChange = () => {} }) {
    let serial = 0, controller, pending;
    const view = { status: 'disconnected', data: null, error: null, filters: {q:'',kind:''}, cursors: [null], page: 0 };
    const emit = () => onChange(view);
    function reset() {
      serial++; controller?.abort(); pending = null;
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
          const payload = await request(query(view.filters, view.cursors[page]), {signal});
          const result = decode(payload);
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
            view.status === 'unsupported' ? 'This server needs the shared Todo progress API before it can show this count.' : 'Could not update Todos. Try again.';
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
      find('todo-count-total').textContent = view.data?.complete ? view.data.counts.total : '—';
      find('todo-source').textContent = view.data ? `${view.filters.kind || 'All Todo types'} · Not started · not archived. ${view.data.classification_required} historical items need classification and are excluded.${view.data.complete ? '' : ' Complete coverage is unavailable on the installed server.'}` : 'All Todo types · Not started · not archived. Unclassified records are excluded.';
      const content = el('div');
      if (view.data?.items.length) for (const item of view.data.items) {
        const row = el('li', 'todo-row'); row.dataset.uiKey = item.key;
        const button = el('button', 'todo-item'); button.type = 'button'; button.dataset.todoId = item.key;
        const copy = el('span', 'todo-item-copy'); copy.append(el('span', 'todo-item-title', item.title));
        const meta = el('span', 'todo-item-meta'); meta.append(el('span', 'todo-state', `${item.kind} · Not started${item.parent ? ' · ' + item.parent : ''}`), el('span', '', 'Priority ' + item.priority));
        if (item.updated_at) { const time = el('time', '', new Date(item.updated_at).toLocaleDateString([], {month:'short',day:'numeric'})); time.dateTime = item.updated_at; time.title = 'Last changed ' + new Date(item.updated_at).toLocaleString(); meta.append(time); } copy.append(meta);
        const mark = el('span', 'todo-item-mark', '↗'); mark.setAttribute('aria-hidden', 'true'); button.append(copy, mark); row.append(button); content.append(row);
      } else {
        const empty = el('li', 'todo-empty'); empty.dataset.uiKey = 'empty';
        const title = view.status === 'unsupported' ? 'Todo unavailable on this server' : view.status === 'error' ? 'Todos could not be loaded' : view.data?.classification_required && !view.filters.q && !view.filters.kind ? 'Progress needs classification' : view.data ? 'No Todos in this view' : view.status === 'loading' ? 'Loading your check-in' : 'Your daily Todo list';
        empty.append(el('strong', '', title), el('p', '', view.status === 'unsupported' ? 'Connect to a server that supports the current Work board API.' : view.data?.classification_required && !view.filters.q && !view.filters.kind ? 'Historical items will appear here once their progress is explicitly classified as Not started.' : view.data ? 'No not-started items match this view. Adjust the filters to explore the list.' : 'Find work waiting to start. Select an item to read its context.'));
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
      try { if (preferenceKey) localStorage.setItem(preferenceKey, JSON.stringify({version:4, ...controller.view.filters})); } catch { /* Filtering remains available without browser storage. */ }
      return result;
    }
    let searchTimer;
    find('todo-search').addEventListener('input', event => { clearTimeout(searchTimer); searchTimer = setTimeout(() => filter({q:event.target.value}), 250); });
    find('todo-kind').addEventListener('change', event => filter({kind:event.target.value}));
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
        const filters = {q:'',kind:''};
        if (value?.version === 4 && typeof value.q === 'string') filters.q = value.q.slice(0,200);
        if (value?.version === 4 && KINDS.includes(value.kind)) filters.kind = value.kind;
        Object.assign(controller.view.filters, filters); find('todo-kind').value = filters.kind; find('todo-search').value = filters.q; render(controller.view);
      },
      reset() {
        clearTimeout(searchTimer); cancelNavigation(); deferredView = null; preferenceKey = null;
        Object.assign(controller.view.filters, {q:'',kind:''}); find('todo-kind').value = '';
        find('todo-search').value = ''; controller.reset();
      },
    };
  }
  return { PAGE_SIZE, query, decode, createController, mount, briefing };
});
