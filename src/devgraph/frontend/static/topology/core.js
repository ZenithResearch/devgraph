/* Shared topology behavior. No DOM, credentials, dependencies, or network access. */
(function (root) {
  'use strict';
  const kinds = ['Todo', 'Proposal', 'Initiative', 'Project', 'Issue', 'Task'];
  const categories = ['arena', 'work', 'observation', 'receipt'];
  const registry = {
    Todo: { label: "Todo", color: "#c6cbd2", shape: "octahedron", pose: [.4,.1,.7], description: "A simple item to do" },
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
  const primaryKinds = ['Proposal', 'Initiative', 'Project'], overviewKinds = [...kinds];
  const defaultCamera = {yaw:-.32,pitch:.24};
  function depthSeed(key) {
    let hash=0;for(const character of key)hash=(hash*31+character.charCodeAt(0))>>>0;
    return (hash%181)-90;
  }
  function overviewFilters(value = {}) {
    const f = filters(value), selected = f.work_kind?.filter(kind => overviewKinds.includes(kind));
    return {...f, category:['arena','work'], work_kind:selected?.length || f.work_kind?.length === 0 ? selected : [...overviewKinds], observation_status:null, record_status:null};
  }

  function overviewProjection(nodes, edges, context = {nodes,edges}, roots = []) {
    const byKey=new Map(nodes.map(n=>[n.key,n])), parents=new Map(), children=new Map();
    for(const edge of context.edges)if(edge.relationship==='HAS_CHILD'){
      if(!parents.has(edge.target))parents.set(edge.target,edge.source);
      if(!children.has(edge.source))children.set(edge.source,new Set());children.get(edge.source).add(edge.target);
    }
    const base=new Set(nodes.filter(n=>n.kind==='Arena'||primaryKinds.includes(n.kind)||(kinds.includes(n.kind)&&!parents.has(n.key))).map(n=>n.key));
    const visible=new Set(base), families=new Map();
    for(const root of new Set(roots)){
      if(!kinds.includes(byKey.get(root)?.kind)||!children.get(root)?.size)continue;
      // Retain the open ancestor path while drilling into a revealed child.
      // Each open parent contributes only its own immediate children.
      const visited=new Set();let current=root;
      while(current&&!visited.has(current)){
        visited.add(current);
        const family=new Set([current,...(children.get(current)||[])]);
        families.set(current,family);for(const key of family)if(byKey.has(key))visible.add(key);
        if(base.has(current))break;
        current=parents.get(current);
      }
    }
    // Hidden ancestors classify roots and organize the visible branch, but are
    // never promoted into results. Display filters still govern the loaded set.
    const ancestors=new Map();for(const edge of context.edges)if(['HAS_CHILD','CONTAINS_WORK'].includes(edge.relationship)){
      if(!ancestors.has(edge.target))ancestors.set(edge.target,[]);ancestors.get(edge.target).push(edge.source);
    }
    const layoutKeys=new Set(visible),queue=[...visible];
    for(const key of queue)for(const parent of ancestors.get(key)||[])if(!layoutKeys.has(parent)){layoutKeys.add(parent);queue.push(parent);}
    return {nodes:nodes.filter(n=>visible.has(n.key)),edges:edges.filter(e=>visible.has(e.source)&&visible.has(e.target)),
      context:{nodes:context.nodes.filter(n=>layoutKeys.has(n.key)),edges:context.edges.filter(e=>layoutKeys.has(e.source)&&layoutKeys.has(e.target))},
      byKey,parents,children,base,families};
  }
  function attentionRoot(key,previous,model) {
    if(!key||!model?.byKey.has(key))return null;
    const visited=new Set();let current=key;
    while(current&&!visited.has(current)){
      if(kinds.includes(model.byKey.get(current)?.kind)&&model.children.get(current)?.size)return current;
      visited.add(current);current=model.parents.get(current);
    }
    return null;
  }

  // Containment alone owns neighborhoods. Dependencies never reparent a node.
  // Pack each subtree as a block, then pack Arena blocks with a generous gutter.
  function hierarchy(nodes, edges) {
    const ordered = [...nodes].sort((a,b)=>(a.key < b.key ? -1 : a.key > b.key ? 1 : 0));
    const byKey = new Map(ordered.map(n=>[n.key,n])), parents = new Map(), children = new Map();
    for (const e of [...edges].sort((a,b)=>a.source.localeCompare(b.source)||a.target.localeCompare(b.target))) {
      if (!byKey.has(e.source) || !byKey.has(e.target) || e.source===e.target || parents.has(e.target)) continue;
      if (!(e.relationship==='HAS_CHILD' && byKey.get(e.source).kind!=='Arena') && !(e.relationship==='CONTAINS_WORK' && byKey.get(e.source).kind==='Arena')) continue;
      parents.set(e.target,e.source);
    }
    // Defensive cycle handling is deterministic even for incomplete input.
    const finished=new Set();
    for (const node of ordered) {
      const path=new Set();let key=node.key;
      while (parents.has(key) && !finished.has(key)) {
        if(path.has(key)){parents.delete(key);break;}
        path.add(key);key=parents.get(key);
      }
      for(const key of path)finished.add(key);
    }
    for(const [key,parent] of parents){if(!children.has(parent))children.set(parent,[]);children.get(parent).push(key);}
    for(const list of children.values())list.sort();
    const gap=144, level=154, gutter=210, positions=new Map(), groups=[];
    function pack(blocks, spacing, minimumColumns=1) {
      if(!blocks.length)return {width:gap,height:80,items:[]};
      let columns=1,best=Infinity;
      // Variable-sized subtrees need area-aware packing, not a square node grid.
      for(let count=Math.max(1,minimumColumns);count<=Math.min(blocks.length,Math.ceil(Math.sqrt(blocks.length)*2));count++){
        const widths=Array(count).fill(0),heights=[];
        blocks.forEach((b,i)=>{widths[i%count]=Math.max(widths[i%count],b.width);heights[Math.floor(i/count)]=Math.max(heights[Math.floor(i/count)]||0,b.height);});
        const width=widths.reduce((a,b)=>a+b,0)+(count-1)*spacing,height=heights.reduce((a,b)=>a+b,0)+(heights.length-1)*spacing;
        const cost=Math.max(width/1.618,height);if(cost<best){best=cost;columns=count;}
      }
      const widths=Array(columns).fill(0), heights=[];
      blocks.forEach((b,i)=>{widths[i%columns]=Math.max(widths[i%columns],b.width);heights[Math.floor(i/columns)]=Math.max(heights[Math.floor(i/columns)]||0,b.height);});
      const xs=[],ys=[];let width=0,height=0;
      widths.forEach((w,i)=>{xs[i]=width;width+=w+spacing;});
      heights.forEach((h,i)=>{ys[i]=height;height+=h+spacing;});
      return {width:width-spacing,height:height-spacing,items:blocks.map((b,i)=>({block:b,x:xs[i%columns]+(widths[i%columns]-b.width)/2,y:ys[Math.floor(i/columns)]}))};
    }
    const trees=new Map();
    // Iterative postorder avoids call-stack growth for long valid hierarchies.
    const work=ordered.filter(n=>n.kind!=='Arena');
    const pending=work.filter(n=>!parents.has(n.key)||byKey.get(parents.get(n.key))?.kind==='Arena').map(n=>[n.key,false]);
    while(pending.length){const [key,ready]=pending.pop();if(trees.has(key))continue;
      const childKeys=children.get(key)||[];
      if(!ready){pending.push([key,true]);for(const child of childKeys)pending.push([child,false]);continue;}
      const packed=pack(childKeys.map(key=>trees.get(key)).filter(Boolean),38,Math.min(3,childKeys.length));
      trees.set(key,{key,width:Math.max(gap,childKeys.length?packed.width:gap),height:childKeys.length?level+packed.height:64,children:childKeys.length?packed.items:[]});
    }
    const arenaOf=new Map();
    for(const node of work){let key=node.key;const trail=[];while(!arenaOf.has(key)&&byKey.get(key)?.kind!=='Arena'&&parents.has(key)){trail.push(key);key=parents.get(key);}
      const arena=arenaOf.get(key) || (byKey.get(key)?.kind==='Arena'?key:'unassigned');arenaOf.set(node.key,arena);for(const k of trail)arenaOf.set(k,arena);}
    const arenas=ordered.filter(n=>n.kind==='Arena').map(n=>({key:n.key,title:n.title||n.key}));
    if(work.some(n=>arenaOf.get(n.key)==='unassigned'))arenas.push({key:'unassigned',title:'Outside an Arena'});
    const blocks=arenas.map(arena=>{const roots=work.filter(n=>arenaOf.get(n.key)===arena.key&&(!parents.has(n.key)||byKey.get(parents.get(n.key))?.kind==='Arena'));const packed=pack(roots.map(n=>trees.get(n.key)),90);return {...arena,width:Math.max(540,packed.width+128),height:packed.height+160,packed,keys:work.filter(n=>arenaOf.get(n.key)===arena.key).map(n=>n.key)};});
    const world=pack(blocks,gutter);
    for(const item of world.items){const b=item.block,x=item.x-world.width/2,y=item.y-world.height/2;
      const arenaDepth=depthSeed(b.key)*1.5;
      groups.push({key:b.key,title:b.title,x,y,z:arenaDepth-100,width:b.width,height:b.height,depth:260,keys:b.keys});
      const stack=b.packed.items.map(child=>({tree:child.block,x:x+(b.width-b.packed.width)/2+child.x,y:y+90+child.y,z:arenaDepth+depthSeed(child.block.key)}));
      // Each family occupies a depth band. Children step back from their parent,
      // with a small sibling fan so orbiting reveals a volume, not a tilted sheet.
      while(stack.length){const {tree,x,y,z}=stack.pop();positions.set(tree.key,{x:x+tree.width/2,y,z});tree.children.forEach((child,i)=>stack.push({tree:child.block,x:x+child.x,y:y+level+child.y,z:z+156+(i%3-1)*32}));}
    }
    return {positions,groups,parents,arenaOf};
  }
  function coordinates(nodes, edges=[]) { return hierarchy(nodes,edges).positions; }
  // Revealing a branch must not repack the map around one active root. Keep
  // every visible item fixed and attach new descendants to their nearest
  // visible ancestor, including when several families are open at once.
  function anchorHierarchy(plan, anchors) {
    const offsets=new Map(), positions=new Map();
    for(const [key,p] of plan.positions){
      const anchor=anchors.get(key);
      if(anchor)offsets.set(key,{x:anchor.x-p.x,y:anchor.y-p.y,z:anchor.z-p.z});
    }
    for(const [key,p] of plan.positions){
      const trail=[],visited=new Set();let ancestor=key;
      while(ancestor&&!offsets.has(ancestor)&&!visited.has(ancestor)){
        visited.add(ancestor);trail.push(ancestor);ancestor=plan.parents.get(ancestor);
      }
      const delta=offsets.get(ancestor)||{x:0,y:0,z:0};
      for(const entry of trail)offsets.set(entry,delta);
      positions.set(key,anchors.has(key)?{...anchors.get(key)}:{x:p.x+delta.x,y:p.y+delta.y,z:p.z+delta.z});
    }
    return {...plan,positions};
  }
  function projectPoint(point, camera) {
    const cy=Math.cos(camera.yaw),sy=Math.sin(camera.yaw),cp=Math.cos(camera.pitch),sp=Math.sin(camera.pitch);
    const x=point.x*cy-point.z*sy,yawDepth=point.x*sy+point.z*cy;
    const y=point.y*cp-yawDepth*sp,depth=point.y*sp+yawDepth*cp,distance=camera.cameraDistance||900;
    const scale=distance/Math.max(distance*.15,distance+depth)*camera.zoom*Math.min(camera.width/1080,camera.height/420);
    return {x:camera.width/2+camera.panX+x*scale,y:camera.height/2+camera.panY+y*scale,depth,scale,radius:Math.max(4,Math.min(64,16*scale))};
  }
  // A short local spring/collision pass in projected pixels. Only newly
  // revealed children can move; parent anchors and the camera stay untouched.
  // Converting the displacement back to world space retains each depth band.
  function revealLayout(nodes, positions, options) {
    const camera=options.camera,movable=new Set(options.movable),parents=new Map(options.parents);
    const projected=new Map(nodes.filter(n=>positions.has(n.key)).map(n=>[n.key,projectPoint(positions.get(n.key),camera)]));
    const families=new Map(),targets=new Map(),points=new Map([...projected].map(([k,p])=>[k,{...p}]));
    for(const key of [...movable].sort()){
      const parent=parents.get(key);if(!projected.has(key)||!projected.has(parent)){movable.delete(key);continue;}
      if(!families.has(parent))families.set(parent,[]);families.get(parent).push(key);
    }
    for(const [parent,children] of families){
      const anchor=projected.get(parent),columns=Math.ceil(Math.sqrt(children.length*1.618));
      const spacing=Math.max(32,...children.map(key=>projected.get(key).radius*2+12));
      children.forEach((key,i)=>{
        const count=Math.min(columns,children.length-Math.floor(i/columns)*columns);
        const target={x:anchor.x+(i%columns-(count-1)/2)*spacing,y:anchor.y+anchor.radius+spacing+Math.floor(i/columns)*spacing};
        targets.set(key,target);Object.assign(points.get(key),target);
      });
    }
    const cell=Math.max(64,...[...projected.values()].map(p=>p.radius*2+12));
    for(let round=0;round<60;round++){
      const grid=new Map(),forces=new Map();
      for(const [key,p] of points){const bucket=`${Math.floor(p.x/cell)},${Math.floor(p.y/cell)}`;if(!grid.has(bucket))grid.set(bucket,[]);grid.get(bucket).push(key);}
      for(const key of movable){
        const p=points.get(key),target=targets.get(key),f={x:(target.x-p.x)*.025,y:(target.y-p.y)*.025};
        const cx=Math.floor(p.x/cell),cy=Math.floor(p.y/cell);
        for(let x=cx-1;x<=cx+1;x++)for(let y=cy-1;y<=cy+1;y++)for(const other of (grid.get(`${x},${y}`)||[]).slice(0,64)){
          if(other===key)continue;const q=points.get(other);let dx=p.x-q.x,dy=p.y-q.y,d=Math.hypot(dx,dy);
          if(d<.01){const angle=(depthSeed(key)+depthSeed(other))*2.399963;dx=Math.cos(angle)*(key<other?-1:1);dy=Math.sin(angle)*(key<other?-1:1);d=1;}
          const gap=Math.max(28,p.radius+q.radius+12);
          if(d<gap){const push=(gap-d)*(movable.has(other)?.48:.9);f.x+=dx/d*push;f.y+=dy/d*push;}
        }
        forces.set(key,f);
      }
      for(const [key,f] of forces){const p=points.get(key),target=targets.get(key);p.x=Math.max(target.x-96,Math.min(target.x+96,p.x+Math.max(-8,Math.min(8,f.x))));p.y=Math.max(target.y-64,Math.min(target.y+96,p.y+Math.max(-8,Math.min(8,f.y))));}
    }
    const result=new Map([...positions].map(([k,p])=>[k,{...p}]));
    const cy=Math.cos(camera.yaw),sy=Math.sin(camera.yaw),cp=Math.cos(camera.pitch),sp=Math.sin(camera.pitch);
    for(const key of movable){
      const before=projected.get(key),after=points.get(key),p=result.get(key);
      const dx=(after.x-before.x)/Math.max(.001,before.scale),dy=(after.y-before.y)/Math.max(.001,before.scale),dz=-dy*sp;
      p.x+=dx*cy+dz*sy;p.y+=dy*cp;p.z+=-dx*sy+dz*cy;
    }
    return [...result];
  }
  function regions(groups, positions) {
    return (groups||[]).map(group=>{
      const points=group.keys.map(key=>positions.get(key)).filter(Boolean);
      if(!points.length)return group;
      const x=Math.min(...points.map(p=>p.x))-72,y=Math.min(...points.map(p=>p.y))-78,z=Math.min(...points.map(p=>p.z))-64;
      return {...group,x,y,z,width:Math.max(240,Math.max(...points.map(p=>p.x))-x+72),height:Math.max(...points.map(p=>p.y))-y+76,depth:Math.max(200,Math.max(...points.map(p=>p.z))-z+64)};
    });
  }
  function regionCorners(region) {
    return [region.z,region.z+region.depth].flatMap(z=>[[region.x,region.y],[region.x+region.width,region.y],[region.x+region.width,region.y+region.height],[region.x,region.y+region.height]].map(([x,y])=>({x,y,z})));
  }
  function depthOrder(nodes,positions) {
    return [...nodes].sort((a,b)=>(positions.get(b.key)?.depth||0)-(positions.get(a.key)?.depth||0));
  }
  // Spatial-grid collision relaxation: bounded neighbors, O(N + E) per iteration.
  // Long edges have a capped pull so the graph never collapses into a single hub.
  function layout(nodes, edges, positions, options={}) {
    const points = new Map([...positions].map(([k,p]) => [k,{...p}]));
    const spacing = 44 + 12*(options.separation || 1.5), cell = spacing;
    const rounds = Math.min(80, options.iterations || 30);
    const anchors = new Map(options.anchors || hierarchy(nodes,edges).positions);
    for (let round=0;round<rounds;round++) {
      const grid = new Map(), force = new Map();
      for (const n of nodes) {
        const p=points.get(n.key); if(!p) continue;
        const key=`${Math.floor(p.x/cell)},${Math.floor(p.y/cell)},${Math.floor(p.z/cell)}`;
        if(!grid.has(key))grid.set(key,[]);grid.get(key).push(n.key);const anchor=anchors.get(n.key)||p;force.set(n.key,{x:(anchor.x-p.x)*.18,y:(anchor.y-p.y)*.24,z:(anchor.z-p.z)*.2});
      }
      for (const n of nodes) {
        const p=points.get(n.key); if(!p || n.key===options.pinned)continue;
        const cx=Math.floor(p.x/cell),cy=Math.floor(p.y/cell),cz=Math.floor(p.z/cell),f=force.get(n.key);
        for(let x=cx-1;x<=cx+1;x++)for(let y=cy-1;y<=cy+1;y++)for(let z=cz-1;z<=cz+1;z++) {
          for(const other of (grid.get(`${x},${y},${z}`)||[]).slice(0,64)) {
            if(other===n.key)continue;const q=points.get(other);let dx=p.x-q.x,dy=p.y-q.y,dz=p.z-q.z,d=Math.hypot(dx,dy,dz);
            if(d<.01){dx=n.key<other?-1:1;dy=.3;dz=.2;d=1;}
            if(d<spacing){const push=(spacing-d)*.18;f.x+=dx/d*push;f.y+=dy/d*push;f.z+=dz/d*push;}
          }
        }
      }
      for(const e of edges) {
        const a=points.get(e.source),b=points.get(e.target);if(!a||!b)continue;
        const dx=b.x-a.x,dy=b.y-a.y,dz=b.z-a.z,d=Math.max(1,Math.hypot(dx,dy,dz));
        const pull=Math.min(1.8,Math.max(-1.8,(d-154)*(['HAS_CHILD','CONTAINS_WORK'].includes(e.relationship)?.003:.0002)))*(options.strengths?.[e.relationship]??1);
        const fa=force.get(e.source),fb=force.get(e.target);if(!fa||!fb)continue;
        fa.x+=dx/d*pull;fa.y+=dy/d*pull;fa.z+=dz/d*pull;fb.x-=dx/d*pull;fb.y-=dy/d*pull;fb.z-=dz/d*pull;
      }
      for(const [key,p]of points){if(key===options.pinned)continue;const f=force.get(key);if(!f)continue;p.x+=Math.max(-8,Math.min(8,f.x));p.y+=Math.max(-8,Math.min(8,f.y));p.z+=Math.max(-8,Math.min(8,f.z));const anchor=anchors.get(key);if(anchor){p.x=Math.max(anchor.x-32,Math.min(anchor.x+32,p.x));p.y=Math.max(anchor.y-24,Math.min(anchor.y+24,p.y));p.z=Math.max(anchor.z-24,Math.min(anchor.z+24,p.z));}}
    }
    return [...points];
  }
  function filters(value={}) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) value = {};
    const enumFacet=(key,allowed)=>Array.isArray(value[key])?[...new Set(value[key].filter(x=>allowed.includes(x)))].sort():null;
    const text=(key,max)=>typeof value[key]==='string'?value[key].slice(0,max):'';
    const stateValues=['draft','review','accepted','archived','unknown'];
    return {category:enumFacet('category',categories),work_kind:enumFacet('work_kind',kinds),
      progress:enumFacet('progress',['not_started','in_progress','done']),work_status:enumFacet('work_status',stateValues),observation_status:enumFacet('observation_status',['unclaimed','claimed','amended','rejected','unknown']),
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
    return {positions:[...view.nodePositions].map(([k,p])=>[k,{...p}]),pinned:view.pinnedKey,separation:view.repulsionStrength,strengths:[...view.edgeStrengths],camera:{cameraDistance:view.cameraDistance,yaw:view.yaw,pitch:view.pitch,zoom:view.zoom,panX:view.panX,panY:view.panY,fitted:view.fitted}};
  }
  function restore(view,entry,{camera=false,available=null}={}) {
    for(const [key,p]of entry.positions)if(!available||available.has(key)){view.nodePositions.set(key,{...p});view.nodeVelocities.set(key,{x:0,y:0,z:0});}
    view.pinnedKey=entry.pinned;view.repulsionStrength=entry.separation;view.edgeStrengths=new Map(entry.strengths);
    if(camera)Object.assign(view,entry.camera);
  }
  root.DevgraphTopology={kinds,categories,registry,visual,shape,solids,solid,index,primaryKinds,overviewKinds,overviewFilters,overviewProjection,attentionRoot,defaultCamera,hierarchy,anchorHierarchy,projectPoint,revealLayout,regions,regionCorners,depthOrder,coordinates,layout,filters,query,preferences,capture,restore};
})(globalThis);
