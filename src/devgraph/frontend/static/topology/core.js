/* Shared topology behavior. No DOM, credentials, dependencies, or network access. */
(function (root) {
  'use strict';
  const kinds = ['Proposal', 'Initiative', 'Project', 'Issue', 'Task'];
  const categories = ['arena', 'work', 'observation', 'receipt'];
  const registry = {
    Proposal: { label: 'Proposal', color: '#b49bff', shape: 'tetrahedron', pose: [-.12,.38,.04], description: 'A suggested change awaiting a decision' },
    Initiative: { label: 'Initiative', color: '#7baaff', shape: 'icosahedron', pose: [.18,.3,.08], description: 'A larger effort spanning projects' },
    Project: { label: 'Project', color: '#60c7ba', shape: 'cube', pose: [-.42,.58,0], description: 'A bounded delivery effort' },
    Issue: { label: 'Issue', color: '#efbb63', shape: 'octahedron', pose: [-.12,.42,0], description: 'A trackable problem or implementation item' },
    Task: { label: 'Task', color: '#9ad580', shape: 'dodecahedron', pose: [-.2,.32,.08], description: 'A concrete unit of work' },
    Arena: { label: 'Arena', color: '#70d5ee', shape: 'icosahedron', pose: [-.3,.65,.4], description: 'A grouping of related work' },
    InitiativeObservation: { label: 'Observation', color: '#e69cb7', shape: 'tetrahedron', pose: [-.12,.38,Math.PI], description: 'An interpretation supported by evidence' },
    EventReceipt: { label: 'Record', color: '#b4bccb', shape: 'cube', pose: [-.3,-.62,.3], description: 'An unsigned record of a committed change' },
  };
  function visual(kind) { return Object.hasOwn(registry,kind)?registry[kind]:{ label: kind, color: '#c6cbd2', shape: 'cube', pose: registry.Project.pose, description: 'Other item type' }; }
  const subtract=(a,b)=>a.map((v,i)=>v-b[i]), dot=(a,b)=>a.reduce((s,v,i)=>s+v*b[i],0);
  const cross=(a,b)=>[a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]];
  const unit=a=>a.map(v=>v/Math.hypot(...a));
  // Build the five regular convex solids once, including complete (back) faces.
  // Coplanar vertices form one face, so cubes and dodecahedra are not triangulated.
  function polyhedron(vertices) {
    const faces=[],seen=new Set(),epsilon=1e-7;
    for(let a=0;a<vertices.length;a++)for(let b=a+1;b<vertices.length;b++)for(let c=b+1;c<vertices.length;c++) {
      let normal=cross(subtract(vertices[b],vertices[a]),subtract(vertices[c],vertices[a]));
      if(Math.hypot(...normal)<epsilon)continue;normal=unit(normal);
      const distances=vertices.map(v=>dot(normal,subtract(v,vertices[a])));
      if(distances.some(d=>d>epsilon)&&distances.some(d=>d<-epsilon))continue;
      const face=distances.flatMap((d,i)=>Math.abs(d)<epsilon?[i]:[]),key=face.join(',');
      if(seen.has(key))continue;seen.add(key);
      if(dot(normal,vertices[a])<0)normal=normal.map(v=>-v);
      const center=[0,1,2].map(axis=>face.reduce((sum,i)=>sum+vertices[i][axis],0)/face.length);
      const u=unit(subtract(vertices[face[0]],center)),v=cross(normal,u);
      face.sort((i,j)=>Math.atan2(dot(subtract(vertices[i],center),v),dot(subtract(vertices[i],center),u))-Math.atan2(dot(subtract(vertices[j],center),v),dot(subtract(vertices[j],center),u)));
      faces.push({indices:face,normal});
    }
    return {vertices,faces};
  }
  const phi=(1+Math.sqrt(5))/2,signs=[-1,1];
  const cube=signs.flatMap(x=>signs.flatMap(y=>signs.map(z=>[x,y,z])));
  const cycle=(a,b)=>signs.flatMap(s=>signs.flatMap(t=>[[0,s*a,t*b],[t*b,0,s*a],[s*a,t*b,0]]));
  const solids={
    tetrahedron:polyhedron([[0,-1,0],[Math.sqrt(8)/3,1/3,0],[-Math.sqrt(2)/3,1/3,Math.sqrt(2/3)],[-Math.sqrt(2)/3,1/3,-Math.sqrt(2/3)]]),
    cube:polyhedron(cube),octahedron:polyhedron([[1,0,0],[-1,0,0],[0,1,0],[0,-1,0],[0,0,1],[0,0,-1]]),
    dodecahedron:polyhedron([...cube,...cycle(1/phi,phi)]),icosahedron:polyhedron(cycle(1,phi)),
  };
  function rotate(point,angles) {
    let [x,y,z]=point;const [a,b,c]=angles;
    [y,z]=[y*Math.cos(a)-z*Math.sin(a),y*Math.sin(a)+z*Math.cos(a)];
    [x,z]=[x*Math.cos(b)+z*Math.sin(b),-x*Math.sin(b)+z*Math.cos(b)];
    return [x*Math.cos(c)-y*Math.sin(c),x*Math.sin(c)+y*Math.cos(c),z];
  }
  function polygon(points,r=1) {return points.map((p,i)=>`${i?'L':'M'} ${(p[0]*r).toFixed(4)} ${(p[1]*r).toFixed(4)}`).join(' ')+' Z';}
  function silhouette(points) {
    const ordered=[...points].sort((a,b)=>a[0]-b[0]||a[1]-b[1]);
    const turn=(a,b,c)=>(b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0]);
    const half=list=>{const hull=[];for(const p of list){while(hull.length>1&&turn(hull.at(-2),hull.at(-1),p)<=0)hull.pop();hull.push(p);}return hull.slice(0,-1);};
    return [...half(ordered),...half(ordered.reverse())];
  }
  const artwork=new Map(),light=unit([-.6,-.8,1]);
  function solid(kind) {
    const key=Object.hasOwn(registry,kind)?kind:'other';if(artwork.has(key))return artwork.get(key);
    const style=visual(kind),mesh=solids[style.shape],rotated=mesh.vertices.map(v=>rotate(v,style.pose));
    const scale=1/Math.max(...rotated.map(v=>Math.hypot(v[0],v[1]))),points=rotated.map(v=>v.map(n=>n*scale));
    const rgb=style.color.match(/\w\w/g).map(value=>parseInt(value,16));
    const faces=mesh.faces.map(face=>({...face,normal:rotate(face.normal,style.pose)})).filter(face=>face.normal[2]>1e-7).map(face=>{
      const diffuse=Math.max(0,dot(face.normal,light)),brightness=.38+.62*diffuse,shine=Math.max(0,(diffuse-.8)/.2)*.22;
      const fill='#'+rgb.map(value=>Math.round(value*brightness+(255-value)*shine).toString(16).padStart(2,'0')).join('');
      return {d:polygon(face.indices.map(i=>points[i])),fill};
    });
    const outline=silhouette(points),result={faces,outline,d:polygon(outline)};artwork.set(key,result);return result;
  }
  function shape(kind,r) {return polygon(solid(kind).outline,r);}
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
  root.DevgraphTopology={kinds,categories,registry,visual,shape,solids,solid,index,coordinates,layout,filters,query,preferences,capture,restore};
})(globalThis);
