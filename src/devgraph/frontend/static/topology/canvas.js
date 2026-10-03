/* Large maps share the SVG camera, registry, selection, and label policy. */
(function(root) {
  'use strict';
  const paths = new Map();
  function draw(canvas, view, selectedKey, previewKey, adjacent, labelKeys, relationshipLabel, chooseLabels) {
    const ratio = Math.min(2, devicePixelRatio || 1), width = view.width, height = view.height;
    if (canvas.width !== Math.round(width*ratio) || canvas.height !== Math.round(height*ratio)) {
      canvas.width = Math.round(width*ratio); canvas.height = Math.round(height*ratio);
    }
    const ctx = canvas.getContext('2d');
    ctx.setTransform(ratio,0,0,ratio,0,0); ctx.clearRect(0,0,width,height);
    const candidates = [], occupied = [], positions = view.projected;
    for (const edge of view.edges) {
      const a=positions.get(edge.source), b=positions.get(edge.target); if(!a||!b)continue;
      if(Math.max(a.x,b.x)<-30||Math.min(a.x,b.x)>width+30||Math.max(a.y,b.y)<-30||Math.min(a.y,b.y)>height+30)continue;
      const dx=b.x-a.x,dy=b.y-a.y,d=Math.max(1,Math.hypot(dx,dy)),ux=dx/d,uy=dy/d;
      const sx=a.x+ux*a.radius,sy=a.y+uy*a.radius,tx=b.x-ux*(b.radius+3),ty=b.y-uy*(b.radius+3);
      const connected=edge.source===previewKey||edge.target===previewKey;
      ctx.globalAlpha=adjacent.size&&!connected?.14:.8;ctx.strokeStyle=connected?'#9dc9b7':'#527568';ctx.lineWidth=connected?2:1;
      ctx.beginPath();ctx.moveTo(sx,sy);ctx.lineTo(tx,ty);ctx.stroke();
      ctx.fillStyle=ctx.strokeStyle;ctx.beginPath();ctx.moveTo(tx,ty);ctx.lineTo(tx-ux*6+uy*3,ty-uy*6-ux*3);ctx.lineTo(tx-ux*6-uy*3,ty-uy*6+ux*3);ctx.fill();
      if(candidates.length<80&&view.labels!=='none'&&(view.labels==='all'||connected)) {
        const text=relationshipLabel(edge.relationship),w=text.length*6.8+8;
        candidates.push({key:`edge:${edge.source}|${edge.relationship}|${edge.target}`,text,x:(sx+tx)/2-w/2,y:(sy+ty)/2-23,width:w,height:18,priority:2});
      }
    }
    for(const node of view.nodes) {
      const p=positions.get(node.key),r=p.radius;if(p.x+r<0||p.x-r>width||p.y+r<0||p.y-r>height)continue;
      const visual=DevgraphTopology.visual(node.kind),selected=node.key===selectedKey;
      if(!paths.has(node.kind))paths.set(node.kind,new Path2D(DevgraphTopology.shape(node.kind,1)));
      ctx.save();ctx.globalAlpha=adjacent.size&&!adjacent.has(node.key)?.32:1;ctx.translate(p.x,p.y);ctx.scale(r,r);
      ctx.fillStyle=visual.color;ctx.strokeStyle=selected||node.key===previewKey?'#fff':'#17222b';ctx.lineWidth=(selected||node.key===previewKey?3:1.5)/r;
      ctx.fill(paths.get(node.kind));ctx.stroke(paths.get(node.kind));
      if(selected){ctx.strokeStyle='#7cf7cf';ctx.lineWidth=1.5/r;ctx.beginPath();ctx.arc(0,0,1+6/r,0,Math.PI*2);ctx.stroke();}ctx.restore();
      occupied.push({x:p.x-r-2,y:p.y-r-2,width:(r+2)*2,height:(r+2)*2});
      if(view.labels!=='none'&&(labelKeys.has(node.key)||(view.labels==='all'&&candidates.length<160))) {
        const text=node.title.length>36?node.title.slice(0,35)+'…':node.title,w=text.length*7.5+8;
        const c={key:`node:${node.key}`,text,width:w,height:19,priority:node.key===previewKey?0:1};
        candidates.push({...c,x:p.x+r+9,y:p.y-9},{...c,x:p.x-w-r-9,y:p.y-9},{...c,x:p.x-w/2,y:p.y-r-26},{...c,x:p.x-w/2,y:p.y+r+9});
      }
    }
    const labels=chooseLabels(candidates.sort((a,b)=>a.priority-b.priority),occupied,width,height);
    ctx.globalAlpha=1;ctx.font='13px system-ui';
    for(const label of labels){ctx.fillStyle='#0b1712';ctx.fillRect(label.x,label.y,label.width,label.height);ctx.fillStyle='#eef6f2';ctx.fillText(label.text,label.x+4,label.y+14);}
    return labels;
  }
  root.DevgraphTopologyCanvas={draw};
})(globalThis);
