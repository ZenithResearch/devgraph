/* Shared topology behavior. No DOM, credentials, dependencies, or network access. */
(function (root) {
  'use strict';
  const kinds = ['Proposal', 'Initiative', 'Project', 'Issue', 'Task'];
  const categories = ['arena', 'work', 'observation', 'receipt'];
  const registry = {
    Proposal: { label: 'Proposal', color: '#b49bff', shape: 'triangle', description: 'A suggested change awaiting a decision' },
    Initiative: { label: 'Initiative', color: '#7baaff', shape: 'circle', description: 'A larger effort spanning projects' },
    Project: { label: 'Project', color: '#60c7ba', shape: 'square', description: 'A bounded delivery effort' },
    Issue: { label: 'Issue', color: '#efbb63', shape: 'diamond', description: 'A trackable problem or implementation item' },
    Task: { label: 'Task', color: '#9ad580', shape: 'hexagon', description: 'A concrete unit of work' },
    Arena: { label: 'Arena', color: '#70d5ee', shape: 'capsule', description: 'A grouping of related work' },
    InitiativeObservation: { label: 'Observation', color: '#e69cb7', shape: 'bubble', description: 'An interpretation supported by evidence' },
    EventReceipt: { label: 'Record', color: '#b4bccb', shape: 'document', description: 'An unsigned record of a committed change' },
  };
  function visual(kind) { return registry[kind] || { label: kind, color: '#c6cbd2', shape: 'square', description: 'Other item type' }; }
  function shape(kind, r) {
    switch (visual(kind).shape) {
      case 'square': return `M ${-r} ${-r} H ${r} V ${r} H ${-r} Z`;
      case 'diamond': return `M 0 ${-r*1.25} L ${r*1.15} 0 L 0 ${r*1.25} L ${-r*1.15} 0 Z`;
      case 'hexagon': return `M ${-r} 0 L ${-r/2} ${-r} H ${r/2} L ${r} 0 L ${r/2} ${r} H ${-r/2} Z`;
      case 'triangle': return `M 0 ${-r*1.2} L ${r*1.1} ${r} H ${-r*1.1} Z`;
      case 'capsule': return `M ${-r*.55} ${-r*.7} H ${r*.55} A ${r*.7} ${r*.7} 0 0 1 ${r*.55} ${r*.7} H ${-r*.55} A ${r*.7} ${r*.7} 0 0 1 ${-r*.55} ${-r*.7} Z`;
      case 'bubble': return `M ${-r} ${-r} H ${r} V ${r*.65} H 0 L ${-r*.65} ${r*1.25} V ${r*.65} H ${-r} Z`;
      case 'document': return `M ${-r*.8} ${-r} H ${r*.2} L ${r*.8} ${-r*.4} V ${r} H ${-r*.8} Z M ${r*.2} ${-r} V ${-r*.4} H ${r*.8}`;
      default: return `M ${-r} 0 A ${r} ${r} 0 1 0 ${r} 0 A ${r} ${r} 0 1 0 ${-r} 0`;
    }
  }
  function index(nodes, edges) {
    const byKey = new Map(nodes.map(n => [n.key, n])), adjacent = new Map(), degree = new Map();
    for (const e of edges) {
      if (!byKey.has(e.source) || !byKey.has(e.target)) continue;
      for (const [a,b] of [[e.source,e.target],[e.target,e.source]]) {
        if (!adjacent.has(a)) adjacent.set(a, new Set());
        adjacent.get(a).add(b); degree.set(a,(degree.get(a)||0)+1);
      }
    }
    return {byKey, adjacent, degree};
  }
  function coordinates(nodes) {
    const positions = new Map(), ordered = [...nodes].sort((a,b) => a.kind.localeCompare(b.kind) || a.key.localeCompare(b.key));
    const columns = Math.max(1, Math.ceil(Math.sqrt(nodes.length * 1.618)));
    const rows = Math.ceil(nodes.length / columns), gap = 66;
    ordered.forEach((n,i) => positions.set(n.key, {x: (i%columns-(columns-1)/2)*gap, y: (Math.floor(i/columns)-(rows-1)/2)*gap, z: 0}));
    return positions;
  }
  // Spatial-grid collision relaxation: bounded neighbors, O(N + E) per iteration.
  // Long edges have a capped pull so the graph never collapses into a single hub.
  function layout(nodes, edges, positions, options={}) {
    const points = new Map([...positions].map(([k,p]) => [k,{...p}]));
    const spacing = 44 + 12*(options.separation || 1.5), cell = spacing;
    const rounds = Math.min(80, options.iterations || 30);
    for (let round=0;round<rounds;round++) {
      const grid = new Map(), force = new Map();
      for (const n of nodes) {
        const p=points.get(n.key); if(!p) continue;
        const key=`${Math.floor(p.x/cell)},${Math.floor(p.y/cell)}`;
        if(!grid.has(key))grid.set(key,[]);grid.get(key).push(n.key);force.set(n.key,{x:0,y:0});
      }
      for (const n of nodes) {
        const p=points.get(n.key); if(!p || n.key===options.pinned)continue;
        const cx=Math.floor(p.x/cell),cy=Math.floor(p.y/cell),f=force.get(n.key);
        for(let x=cx-1;x<=cx+1;x++)for(let y=cy-1;y<=cy+1;y++) {
          for(const other of (grid.get(`${x},${y}`)||[]).slice(0,64)) {
            if(other===n.key)continue;const q=points.get(other);let dx=p.x-q.x,dy=p.y-q.y,d=Math.hypot(dx,dy);
            if(d<.01){dx=n.key<other?-1:1;dy=.3;d=1;}
            if(d<spacing){const push=(spacing-d)*.18;f.x+=dx/d*push;f.y+=dy/d*push;}
          }
        }
      }
      for(const e of edges) {
        const a=points.get(e.source),b=points.get(e.target);if(!a||!b)continue;
        const dx=b.x-a.x,dy=b.y-a.y,d=Math.max(1,Math.hypot(dx,dy));
        const pull=Math.min(1.8,Math.max(-1.8,(d-110)*.003))*(options.strengths?.[e.relationship]??1);
        const fa=force.get(e.source),fb=force.get(e.target);if(!fa||!fb)continue;
        fa.x+=dx/d*pull;fa.y+=dy/d*pull;fb.x-=dx/d*pull;fb.y-=dy/d*pull;
      }
      for(const [key,p]of points){if(key===options.pinned)continue;const f=force.get(key);if(!f)continue;p.x+=Math.max(-8,Math.min(8,f.x));p.y+=Math.max(-8,Math.min(8,f.y));}
    }
    return [...points];
  }
  function filters(value={}) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) value = {};
    const enumFacet=(key,allowed)=>Array.isArray(value[key])?[...new Set(value[key].filter(x=>allowed.includes(x)))].sort():null;
    const text=(key,max)=>typeof value[key]==='string'?value[key].slice(0,max):'';
    const stateValues=['draft','review','accepted','archived','unknown'];
    return {category:enumFacet('category',categories),work_kind:enumFacet('work_kind',kinds),
      work_status:enumFacet('work_status',stateValues),observation_status:enumFacet('observation_status',['unclaimed','claimed','amended','rejected','unknown']),
      record_status:enumFacet('record_status',['pending','dispatched_dry_run','retry_scheduled','failed','unknown']),
      relationship:Array.isArray(value.relationship)?[...new Set(value.relationship.filter(x=>typeof x==='string'&&/^[A-Z][A-Z0-9_]{0,63}$/.test(x)))].sort():null,
      archived:['include','exclude','only'].includes(value.archived)?value.archived:'include',arena:text('arena',280),anchor:text('anchor',280),q:text('q',200).trim().toLowerCase()};
  }
  function query(value) {
    const f=filters(value), pairs=[];
    for(const key of Object.keys(f).sort()) {
      const v=f[key];if(v===null || v==='' || (key==='archived'&&v==='include'))continue;
      for(const item of Array.isArray(v)?v.length?v:['none']:[v])pairs.push([key,item]);
    }
    const encode=text=>encodeURIComponent(text).replace(/[!'()*]/g,c=>'%'+c.charCodeAt(0).toString(16).toUpperCase()).replace(/%20/g,'+');
    return pairs.map(([key,value])=>encode(key)+'='+encode(value)).join('&');
  }
  function preferences(value) {
    if(!value || value.version!==1)return {version:1,filters:filters(),labels:'selected',observerCollapsed:false,readerCollapsed:true,readerWidth:null};
    return {version:1,filters:filters(value.filters),labels:['selected','all','none'].includes(value.labels)?value.labels:'selected',
      observerCollapsed:value.observerCollapsed===true,readerCollapsed:value.readerCollapsed!==false,
      readerWidth:Number.isFinite(value.readerWidth)?Math.max(320,Math.min(600,value.readerWidth)):null};
  }
  function capture(view) {
    return {positions:[...view.nodePositions].map(([k,p])=>[k,{...p}]),pinned:view.pinnedKey,separation:view.repulsionStrength,strengths:[...view.edgeStrengths],camera:{yaw:view.yaw,pitch:view.pitch,zoom:view.zoom,panX:view.panX,panY:view.panY,fitted:view.fitted}};
  }
  function restore(view,entry,{camera=false,available=null}={}) {
    for(const [key,p]of entry.positions)if(!available||available.has(key)){view.nodePositions.set(key,{...p});view.nodeVelocities.set(key,{x:0,y:0,z:0});}
    view.pinnedKey=entry.pinned;view.repulsionStrength=entry.separation;view.edgeStrengths=new Map(entry.strengths);
    if(camera)Object.assign(view,entry.camera);
  }
  root.DevgraphTopology={kinds,categories,registry,visual,shape,index,coordinates,layout,filters,query,preferences,capture,restore};
})(globalThis);
