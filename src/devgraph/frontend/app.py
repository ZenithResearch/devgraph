# ruff: noqa: E501
"""Dependency-free official DevGraph operator frontend."""

FRONTEND_HTML = r'''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Dev Graph Monitor</title>
  <style>
    :root {
      color-scheme: dark;
      --ink: #eef6f2;
      --muted: #8ea39a;
      --faint: #95aaa0;
      --canvas: #07110d;
      --panel: #0b1712;
      --panel-strong: #102019;
      --line: #20352c;
      --line-soft: #162820;
      --aqua: #7cf7cf;
      --aqua-soft: rgba(124, 247, 207, .12);
      --amber: #f6c86f;
      --coral: #ff9675;
      --violet: #b9a4ff;
      --radius: 14px;
      --mono: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      --sans: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      background:
        radial-gradient(circle at 76% 0%, rgba(34, 105, 79, .16), transparent 28rem),
        var(--canvas);
      color: var(--ink);
      font-family: var(--sans);
    }
    button, input, select { font: inherit; }
    button, a, input, select { outline-offset: 3px; }
    :focus-visible { outline: 2px solid var(--aqua); }
    .skip-link { position: fixed; left: 1rem; top: -4rem; z-index: 9; background: var(--aqua); color: #07110d; padding: .65rem 1rem; }
    .skip-link:focus { top: 1rem; }
    .shell { display: grid; grid-template-columns: 232px minmax(0, 1fr); min-height: 100vh; }
    .rail { border-right: 1px solid var(--line-soft); padding: 1.6rem 1.25rem; display: flex; flex-direction: column; gap: 2rem; background: rgba(5, 14, 10, .72); }
    .brand { display: flex; align-items: center; gap: .75rem; }
    .brand-mark { width: 34px; height: 34px; display: grid; place-items: center; border: 1px solid var(--aqua); color: var(--aqua); font-family: var(--mono); font-size: .75rem; }
    .brand strong { display: block; font-size: .92rem; letter-spacing: .02em; }
    .brand span { color: var(--muted); font: .68rem var(--mono); text-transform: uppercase; letter-spacing: .12em; }
    .rail nav { display: grid; gap: .25rem; }
    .rail nav a { color: var(--muted); text-decoration: none; padding: .65rem .75rem; border-radius: 8px; font-size: .84rem; }
    .rail nav a[aria-current="page"] { color: var(--ink); background: var(--aqua-soft); border: 1px solid rgba(124, 247, 207, .15); }
    .rail-meta { margin-top: auto; display: grid; gap: .75rem; color: var(--muted); font: .68rem/1.55 var(--mono); }
    .rail-meta span { color: var(--ink); display: block; }
    main { min-width: 0; padding: 1.65rem clamp(1rem, 3vw, 3rem) 3rem; }
    .topbar { display: flex; align-items: flex-start; justify-content: space-between; gap: 1.5rem; margin-bottom: 1.6rem; }
    .eyebrow { color: var(--aqua); font: .7rem var(--mono); letter-spacing: .13em; text-transform: uppercase; }
    h1 { margin: .35rem 0 .3rem; font-size: clamp(1.55rem, 3vw, 2.45rem); font-weight: 560; letter-spacing: -.04em; }
    .subtitle { margin: 0; color: var(--muted); max-width: 48rem; font-size: .9rem; line-height: 1.6; }
    .connection { display: flex; align-items: center; gap: .5rem; min-width: max-content; padding-top: .25rem; color: var(--muted); font: .7rem var(--mono); }
    .dot { width: 7px; height: 7px; border-radius: 999px; background: var(--faint); box-shadow: 0 0 0 4px rgba(82, 98, 92, .12); }
    .dot.ready { background: var(--aqua); box-shadow: 0 0 0 4px var(--aqua-soft); }
    .dot.error { background: var(--coral); box-shadow: 0 0 0 4px rgba(255, 150, 117, .12); }
    .auth-strip { display: grid; grid-template-columns: 1fr auto auto; gap: .6rem; margin-bottom: 1rem; padding: .75rem; border: 1px solid var(--line); border-radius: var(--radius); background: rgba(11, 23, 18, .74); }
    .auth-strip label { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); }
    .auth-strip input, .auth-strip select { min-width: 0; background: #07110d; color: var(--ink); border: 1px solid var(--line); border-radius: 9px; padding: .68rem .75rem; font: .74rem var(--mono); }
    .button { border: 1px solid var(--aqua); border-radius: 9px; padding: .65rem 1rem; color: #06110c; background: var(--aqua); cursor: pointer; font-weight: 650; }
    .button.secondary { color: var(--muted); background: transparent; border-color: var(--line); }
    .status-line { min-height: 1.3rem; color: var(--muted); font: .7rem var(--mono); margin: 0 0 1rem; }
    .kpis { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: .75rem; margin-bottom: .75rem; }
    .card { border: 1px solid var(--line); background: linear-gradient(145deg, rgba(16, 32, 25, .93), rgba(8, 19, 14, .93)); border-radius: var(--radius); }
    .kpi { min-height: 122px; padding: 1rem; position: relative; overflow: hidden; }
    .kpi::after { content: ""; position: absolute; right: -24px; bottom: -36px; width: 92px; height: 92px; border: 1px solid rgba(124, 247, 207, .09); transform: rotate(28deg); }
    .kpi-label { color: var(--muted); font: .67rem var(--mono); text-transform: uppercase; letter-spacing: .09em; }
    .kpi-value { margin-top: .65rem; font: 2rem var(--mono); letter-spacing: -.07em; }
    .kpi-note { margin-top: .5rem; color: var(--faint); font: .68rem var(--mono); }
    .grid { display: grid; grid-template-columns: minmax(0, 1.2fr) minmax(280px, .8fr); gap: .75rem; margin-top: .75rem; }
    .section { padding: 1.05rem; min-width: 0; }
    .section-head { display: flex; justify-content: space-between; gap: 1rem; align-items: baseline; margin-bottom: .9rem; }
    h2 { margin: 0; font-size: .9rem; font-weight: 600; letter-spacing: -.01em; }
    .section-meta { color: var(--faint); font: .64rem var(--mono); }
    .pipeline { display: grid; grid-template-columns: repeat(4, 1fr); gap: .45rem; }
    .stage { padding: .85rem .7rem; border: 1px solid var(--line-soft); border-radius: 10px; background: rgba(5, 14, 10, .55); }
    .stage strong { display: block; font: 1.15rem var(--mono); margin-bottom: .35rem; }
    .stage span { color: var(--muted); font-size: .68rem; }
    .stage:nth-child(1) strong { color: var(--amber); }
    .stage:nth-child(2) strong { color: var(--aqua); }
    .stage:nth-child(3) strong { color: var(--violet); }
    .stage:nth-child(4) strong { color: var(--coral); }
    .bars { display: grid; gap: .7rem; }
    .bar-row { display: grid; grid-template-columns: 92px 1fr 30px; align-items: center; gap: .7rem; font: .68rem var(--mono); color: var(--muted); }
    .bar-track { height: 6px; background: var(--line-soft); border-radius: 999px; overflow: hidden; }
    .bar-fill { height: 100%; background: var(--aqua); border-radius: inherit; transition: width .25s ease; }
    .activity, .observations { display: grid; gap: .15rem; }
    .activity-row { display: grid; grid-template-columns: 10px minmax(0, 1fr) auto; gap: .75rem; align-items: center; padding: .75rem .25rem; border-bottom: 1px solid var(--line-soft); }
    .activity-row:last-child { border-bottom: 0; }
    .activity-mark { width: 7px; height: 7px; border-radius: 2px; background: var(--faint); }
    .activity-mark.observation { background: var(--amber); }
    .activity-mark.receipt { background: var(--violet); }
    .activity-title { font-size: .78rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .activity-sub { color: var(--muted); font: .63rem var(--mono); margin-top: .28rem; }
    .activity-time { color: var(--faint); font: .62rem var(--mono); }
    .observation { padding: .9rem 0; border-bottom: 1px solid var(--line-soft); }
    .observation:first-child { padding-top: .2rem; }
    .observation:last-child { border-bottom: 0; }
    .observation-top { display: flex; justify-content: space-between; gap: 1rem; }
    .observation a { color: var(--ink); text-decoration: none; font-size: .8rem; font-weight: 590; }
    .observation a:hover { color: var(--aqua); }
    .pill { flex: 0 0 auto; padding: .2rem .45rem; border-radius: 999px; background: var(--aqua-soft); color: var(--aqua); font: .58rem var(--mono); text-transform: uppercase; }
    .observation p { color: var(--muted); font-size: .72rem; line-height: 1.55; margin: .45rem 0; }
    .observation-meta { color: var(--faint); font: .61rem var(--mono); display: flex; gap: .8rem; flex-wrap: wrap; }
    .empty { padding: 1.25rem 0; color: var(--faint); font: .7rem/1.6 var(--mono); }
    .graph-card { margin: .75rem 0; padding: 1.05rem; }
    .graph-toolbar { display: flex; align-items: flex-start; justify-content: space-between; gap: 1rem; margin-bottom: .9rem; }
    .graph-toolbar-actions { display: flex; align-items: center; justify-content: flex-end; gap: .55rem; flex-wrap: wrap; }
    .graph-control { padding: .42rem .65rem; border: 1px solid var(--line); border-radius: 8px; background: rgba(7, 17, 13, .82); color: var(--muted); cursor: pointer; font: .62rem var(--mono); }
    .graph-control:hover, .graph-control[aria-pressed="true"] { color: var(--ink); border-color: var(--aqua); background: var(--aqua-soft); }
    .graph-control:disabled { opacity: .4; cursor: default; }
    .graph-zoom { display: flex; align-items: center; gap: .4rem; }
    .graph-zoom input { width: 90px; accent-color: var(--aqua); }
    .graph-zoom output { min-width: 3.2rem; text-align: center; color: var(--ink); font: .65rem var(--mono); }
    .graph-legend { display: flex; align-items: center; justify-content: flex-end; gap: .8rem; flex-wrap: wrap; color: var(--muted); font: .62rem var(--mono); }
    .legend-item { display: inline-flex; align-items: center; gap: .4rem; min-height: 28px; cursor: pointer; }
    .legend-item input { width: 14px; height: 14px; margin: 0; accent-color: var(--aqua); cursor: pointer; }
    .legend-item input[data-graph-category="observation"] { accent-color: var(--amber); }
    .legend-item input[data-graph-category="receipt"] { accent-color: var(--violet); }
    .legend-item input:not(:checked) + span { color: var(--faint); }
    .graph-force-panel { display: grid; grid-template-columns: minmax(180px, .65fr) minmax(0, 1.35fr); gap: 1rem; align-items: start; margin: -.1rem 0 .9rem; padding: .75rem; border: 1px solid var(--line-soft); border-radius: 10px; background: rgba(7, 17, 13, .54); }
    .graph-force-copy strong { display: block; color: var(--ink); font-size: .72rem; }
    .graph-force-copy span { display: block; margin-top: .3rem; color: var(--faint); font: .6rem/1.45 var(--mono); }
    .graph-force-copy .graph-control { margin-top: .65rem; }
    .graph-force-controls { display: grid; gap: .55rem; }
    .graph-force-control { display: grid; grid-template-columns: minmax(100px, auto) minmax(90px, 1fr) 3.2rem; gap: .6rem; align-items: center; color: var(--muted); font: .6rem var(--mono); }
    .graph-force-control span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .graph-force-control input { width: 100%; accent-color: var(--aqua); cursor: ew-resize; }
    .graph-force-control output { color: var(--aqua); text-align: right; }
    .graph-force-empty { color: var(--faint); font: .6rem var(--mono); }
    .graph-layout { display: grid; grid-template-columns: minmax(0, 1fr) 230px; gap: .75rem; min-height: 0; }
    .graph-scroll { min-width: 0; height: var(--graph-height, 420px); overflow: hidden; border: 1px solid var(--line-soft); border-radius: 11px; background: radial-gradient(circle at 50% 42%, rgba(124, 247, 207, .08), transparent 22rem), linear-gradient(rgba(124, 247, 207, .018) 1px, transparent 1px), linear-gradient(90deg, rgba(124, 247, 207, .018) 1px, transparent 1px), rgba(5, 14, 10, .68); background-size: auto, 32px 32px, 32px 32px, auto; }
    .graph-svg { display: block; width: 100%; height: 100%; touch-action: none; user-select: none; }
    .graph-help { margin: .65rem 0 0; color: var(--muted); font: .61rem/1.6 var(--mono); }
    .graph-resize { display: flex; align-items: center; justify-content: center; gap: .6rem; width: 100%; min-height: 30px; margin-top: .6rem; border: 1px solid var(--line-soft); border-radius: 7px; color: var(--muted); background: rgba(7, 17, 13, .65); font: .6rem var(--mono); cursor: ns-resize; touch-action: none; user-select: none; }
    .graph-resize::before { content: ""; width: 28px; height: 5px; border-top: 1px solid var(--muted); border-bottom: 1px solid var(--muted); }
    .graph-resize:hover { color: var(--ink); border-color: var(--aqua); }
    body.graph-expanded { overflow: hidden; }
    .graph-card.expanded { position: fixed; inset: 1rem; z-index: 50; margin: 0; display: flex; flex-direction: column; background: var(--panel); overflow: auto; box-shadow: 0 0 0 100vmax rgba(0, 0, 0, .7); }
    .graph-card.expanded .graph-layout { flex: 1; min-height: 180px; }
    .graph-card.expanded .graph-scroll { height: 100%; min-height: 180px; }
    .graph-card.expanded .graph-details { overflow: auto; }
    .graph-card.expanded .graph-force-panel { max-height: 150px; overflow: auto; flex-shrink: 0; }
    .graph-card.expanded .graph-resize { display: none; }
    .graph-scene-hit { fill: transparent; cursor: grab; }
    .graph-svg.dragging .graph-scene-hit { cursor: grabbing; }
    .graph-edge { fill: none; stroke: #527568; marker-end: url(#graph-arrow); }
    .graph-edge-label { fill: var(--faint); font: 9px var(--mono); letter-spacing: .04em; text-anchor: middle; paint-order: stroke; stroke: #07110d; stroke-width: 5px; stroke-linejoin: round; }
    .graph-node { --graph-plasma: #7cf7cf; cursor: grab; }
    .graph-node.observation { --graph-plasma: #f6c86f; }
    .graph-node.receipt { --graph-plasma: #b9a4ff; }
    .graph-node:focus { outline: none; }
    .graph-svg.node-dragging .graph-node { cursor: grabbing; }
    .graph-shadow { fill: rgba(0, 0, 0, .34); filter: blur(2px); pointer-events: none; }
    .graph-sphere { stroke: rgba(238, 246, 242, .36); stroke-width: 1.2; transition: stroke .18s ease, stroke-opacity .18s ease, stroke-width .18s ease, filter .18s ease; }
    .graph-node:hover .graph-sphere { stroke: var(--graph-plasma); stroke-opacity: .86; stroke-width: 1.7; filter: drop-shadow(0 0 5px var(--graph-plasma)); }
    .graph-node.selected .graph-sphere { stroke: var(--graph-plasma); stroke-opacity: .62; stroke-width: 1.25; filter: drop-shadow(0 0 3px var(--graph-plasma)); }
    .graph-node:focus-visible .graph-sphere { stroke: var(--graph-plasma); stroke-opacity: .76; stroke-width: 1.45; filter: drop-shadow(0 0 4px var(--graph-plasma)); }
    .graph-node-hit { fill: transparent; }
    .graph-node-highlight { fill: rgba(255, 255, 255, .7); pointer-events: none; }
    .graph-plasma-halo { fill: none; stroke: var(--graph-plasma); stroke-width: 2.2; stroke-dasharray: 1 4 8 5; opacity: .26; filter: blur(1px) drop-shadow(0 0 3px var(--graph-plasma)); pointer-events: none; animation: graph-plasma-flow 5.5s linear infinite; }
    .graph-selection-ring { fill: none; stroke: var(--graph-plasma); stroke-width: .65; stroke-dasharray: .8 5.2; opacity: .42; filter: drop-shadow(0 0 2px var(--graph-plasma)); pointer-events: none; animation: graph-plasma-flow 7s linear infinite reverse; }
    @keyframes graph-plasma-flow { to { stroke-dashoffset: -36; } }
    .graph-node-kind { fill: var(--muted); font: 7.5px var(--mono); text-transform: uppercase; letter-spacing: .06em; paint-order: stroke; stroke: #07110d; stroke-width: 3px; stroke-linejoin: round; pointer-events: none; }
    .graph-depth-hint { fill: var(--faint); font: 8px var(--mono); letter-spacing: .05em; pointer-events: none; }
    .graph-tooltip { pointer-events: none; }
    .graph-tooltip-panel { fill: rgba(7, 17, 13, .97); stroke: #4a7565; stroke-width: 1; filter: drop-shadow(0 8px 14px rgba(0, 0, 0, .38)); }
    .graph-tooltip-title { fill: var(--ink); font: 600 12px var(--sans); }
    .graph-tooltip-meta { fill: var(--aqua); font: 8px var(--mono); letter-spacing: .055em; text-transform: uppercase; }
    .graph-tooltip-copy { fill: var(--muted); font: 8px var(--mono); }
    .graph-details { min-width: 0; padding: .9rem; border: 1px solid var(--line-soft); border-radius: 11px; background: rgba(7, 17, 13, .72); }
    .graph-details-kicker { color: var(--aqua); font: .6rem var(--mono); text-transform: uppercase; letter-spacing: .1em; }
    .graph-details h3 { margin: .5rem 0 .35rem; font-size: .88rem; line-height: 1.35; overflow-wrap: anywhere; }
    .graph-details-copy { color: var(--muted); font: .68rem/1.55 var(--mono); overflow-wrap: anywhere; }
    .graph-progress { margin-top: .9rem; padding-top: .75rem; border-top: 1px solid var(--line-soft); }
    .graph-progress-head { display: flex; align-items: baseline; justify-content: space-between; gap: .75rem; color: var(--muted); font: .61rem var(--mono); }
    .graph-progress-head strong { color: var(--ink); font-size: .78rem; }
    .graph-progress-track { height: 8px; margin-top: .55rem; overflow: hidden; border: 1px solid rgba(124, 247, 207, .16); border-radius: 999px; background: var(--line-soft); }
    .graph-progress-fill { height: 100%; border-radius: inherit; background: linear-gradient(90deg, #3cae8a, var(--aqua)); box-shadow: 0 0 9px rgba(124, 247, 207, .28); transition: width .25s ease; }
    .graph-progress-meta { margin-top: .45rem; color: var(--muted); font: .6rem/1.55 var(--mono); }
    .graph-connections { display: grid; gap: .5rem; margin-top: .85rem; }
    .graph-connection { padding-top: .5rem; border-top: 1px solid var(--line-soft); color: var(--muted); font: .61rem/1.45 var(--mono); overflow-wrap: anywhere; }
    @media (max-width: 980px) {
      .shell { grid-template-columns: 1fr; }
      .rail { display: none; }
      .kpis { grid-template-columns: repeat(2, 1fr); }
      .graph-layout { grid-template-columns: 1fr; }
      .graph-force-panel { grid-template-columns: 1fr; }
      .graph-card.expanded .graph-layout { grid-template-rows: minmax(180px, 1fr) auto; }
      .graph-card.expanded .graph-details { max-height: 140px; }
    }
    @media (max-width: 690px) {
      main { padding: 1rem; }
      .topbar { display: block; }
      .connection { margin-top: 1rem; }
      .auth-strip { grid-template-columns: 1fr 1fr; }
      .auth-strip input { grid-column: 1 / -1; }
      .grid, .kpis { grid-template-columns: 1fr; }
      .pipeline { grid-template-columns: repeat(2, 1fr); }
      .activity-time { display: none; }
      .graph-toolbar { display: block; }
      .graph-toolbar-actions { justify-content: flex-start; margin-top: .65rem; }
      .graph-legend { justify-content: flex-start; margin-top: .65rem; }
      .graph-force-control { grid-template-columns: minmax(86px, auto) minmax(70px, 1fr) 3rem; }
    }
    @media (prefers-reduced-motion: reduce) { * { scroll-behavior: auto !important; transition: none !important; } .graph-plasma-halo, .graph-selection-ring { animation: none !important; } }

    /* Content reading and graph navigation. */
    [hidden] { display: none !important; }
    .graph-toolbar { display: block; }
    .graph-title-row, .graph-primary, .graph-filter-row { display: flex; align-items: center; justify-content: space-between; gap: .75rem; flex-wrap: wrap; }
    .graph-primary, .graph-filter-row { margin-top: .75rem; }
    .graph-title-row h2 { font-size: 1.1rem; }
    .section-meta, .graph-help, .graph-resize, .graph-force-copy span, .graph-force-empty { font-size: .75rem; line-height: 1.5; }
    .graph-control { min-height: 36px; font: .8rem var(--sans); color: var(--ink); padding: .4rem .65rem; }
    .graph-zoom { flex-wrap: wrap; gap: .3rem; }
    .graph-zoom input { width: 72px; }
    .graph-zoom output { font-size: .75rem; }
    .graph-legend { font: .8rem var(--sans); justify-content: flex-start; margin: 0; gap: .8rem; }
    .legend-item { min-height: 36px; }
    .legend-item input { width: 18px; height: 18px; }
    .legend-item span span { color: var(--muted); font-size: .75rem; }
    .graph-label-select { display: flex; align-items: center; gap: .4rem; color: var(--muted); font-size: .8rem; }
    .graph-label-select select { min-width: 0; max-width: 180px; background: var(--canvas); border: 1px solid var(--line); color: var(--ink); border-radius: 7px; padding: .45rem; }
    .graph-arena-row { display: flex; align-items: center; gap: .75rem; flex-wrap: wrap; margin-top: .75rem; }
    .graph-arena-row select { max-width: min(260px, 65vw); }
    .graph-arena-note { flex: 1 1 240px; margin: 0; color: var(--muted); font-size: .75rem; line-height: 1.5; }
    .graph-search { position: relative; flex: 1 1 230px; min-width: 0; }
    .graph-search label { display: block; color: var(--muted); font-size: .75rem; margin-bottom: .25rem; }
    .graph-search input { width: 100%; min-width: 0; background: var(--canvas); color: var(--ink); border: 1px solid var(--line); border-radius: 8px; padding: .6rem .7rem; font-size: .9rem; }
    .search-results { position: absolute; top: 100%; left: 0; right: 0; z-index: 60; background: var(--panel-strong); padding: .5rem; border: 1px solid var(--line); border-radius: 8px; max-height: 300px; overflow: auto; box-shadow: 0 10px 24px #0008; }
    .search-result { display: block; text-align: left; width: 100%; border: 0; border-radius: 6px; background: transparent; color: var(--ink); padding: .6rem; cursor: pointer; font-size: .9rem; }
    .search-result:hover, .search-result:focus-visible { background: var(--aqua-soft); }
    .search-result small { display: block; color: var(--muted); margin-top: .25rem; }
    .graph-settings { margin: 0 0 .8rem; border: 1px solid var(--line); border-radius: 8px; padding: .5rem .75rem; flex-shrink: 0; }
    .graph-settings summary { cursor: pointer; color: var(--muted); font-size: .85rem; min-height: 28px; }
    .graph-settings .graph-toolbar-actions { justify-content: flex-start; margin: .5rem 0; }
    .graph-force-panel { margin: .5rem 0; }
    .graph-force-control { font: .8rem var(--sans); grid-template-columns: minmax(0, 120px) minmax(60px, 1fr) 3.2rem; min-height: 32px; }
    .graph-force-copy strong { font-size: .9rem; }
    .graph-layout { grid-template-columns: minmax(0, 1fr) 8px minmax(0, var(--reader-width, 360px)); gap: .35rem; }
    .graph-details { display: flex; flex-direction: column; padding: 0; height: var(--graph-height, 420px); overflow: hidden; min-height: 0; }
    .reader-header { padding: .75rem 1rem; border-bottom: 1px solid var(--line); background: var(--panel); flex-shrink: 0; }
    .reader-buttons { display: flex; justify-content: space-between; gap: .5rem; }
    .graph-details h3 { font-size: 1.05rem; line-height: 1.4; margin: .6rem 0 .3rem; overflow-wrap: anywhere; }
    .graph-details-copy, .reader-note, .graph-connection { font: .8rem/1.5 var(--sans); }
    .reader-body { padding: 0 1rem 1rem; overflow: auto; flex: 1; min-height: 0; overscroll-behavior: contain; }
    #reader-status { font-size: .8rem; color: var(--muted); }
    #reader-status:empty { margin: 0; }
    .reader-section { border-top: 1px solid var(--line); padding-top: .85rem; margin-top: .85rem; }
    .reader-section h4 { margin: 0 0 .65rem; font-size: .9rem; }
    .reader-description { white-space: pre-wrap; overflow-wrap: anywhere; font: .95rem/1.65 var(--sans); color: var(--ink); }
    .reader-note { color: var(--muted); overflow-wrap: anywhere; }
    .reader-link { display: block; text-align: left; width: 100%; padding: .65rem 0; border: 0; border-bottom: 1px solid var(--line-soft); background: transparent; color: var(--aqua); cursor: pointer; font-size: .9rem; line-height: 1.45; overflow-wrap: anywhere; }
    .reader-link small { display: block; color: var(--muted); font-size: .75rem; }
    .reader-actions { display: flex; flex-wrap: wrap; gap: .45rem; margin: .75rem 0; }
    .reader-section summary { cursor: pointer; padding: .4rem 0; font-size: .9rem; color: var(--ink); }
    .support-card { border-top: 1px solid var(--line-soft); padding: .8rem 0; }
    .support-card h5 { font-size: .95rem; margin: 0 0 .4rem; }
    .support-card a { color: var(--aqua); overflow-wrap: anywhere; display: inline-block; padding: .45rem 0; }
    .document-content { white-space: pre-wrap; overflow-wrap: anywhere; font: .9rem/1.65 var(--sans); }
    .reader-resize { cursor: ew-resize; border-radius: 5px; touch-action: none; background: var(--line-soft); }
    .reader-resize:hover, .reader-resize:focus-visible { background: var(--aqua); }
    .graph-card.expanded { overflow: hidden; }
    .graph-card.expanded .graph-toolbar { flex-shrink: 0; }
    .graph-card.expanded .graph-layout { min-height: 180px; }
    .graph-card.expanded .graph-details { height: 100%; overflow: hidden; }
    .graph-card.expanded .graph-force-panel { max-height: none; overflow: visible; }
    .graph-card.expanded .graph-settings[open] { max-height: 40vh; overflow: auto; }
    .graph-node.selected .graph-sphere { stroke: #fff; stroke-width: 3; filter: drop-shadow(0 0 6px var(--aqua)); }
    .graph-node:focus-visible .graph-sphere { stroke: #fff; stroke-width: 3; }
    .graph-node-kind { font: 10px var(--mono); fill: var(--ink); }
    .graph-edge-label { font: 12px var(--sans); fill: var(--muted); }
    .graph-node-label { font: 600 13px var(--sans); fill: var(--ink); paint-order: stroke; stroke: var(--canvas); stroke-width: 5px; pointer-events: none; }
    .graph-details.collapsed .reader-body { display: none; }
    .graph-details.collapsed { height: auto; align-self: start; }
    .graph-layout:has(> .graph-details.collapsed) { grid-template-columns: minmax(0, 1fr); grid-template-rows: minmax(0, 1fr) auto; }
    .graph-layout:has(> .graph-details.collapsed) > .reader-resize { display: none; }
    .graph-details.collapsed .reader-header { display: flex; align-items: center; flex-wrap: wrap; gap: .5rem 1rem; padding: .5rem .75rem; border-bottom: 0; }
    .graph-details.collapsed .reader-buttons { flex-shrink: 0; gap: .5rem; }
    .graph-details.collapsed h3 { margin: 0; font-size: .85rem; flex: 1; min-width: 0; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
    .graph-details.collapsed #reader-meta { display: none; }
    .reading-view .graph-scroll, .reading-view .reader-resize, .reading-view .graph-toolbar, .reading-view .graph-settings, .reading-view .graph-help, .reading-view .graph-resize { display: none; }
    .reading-view .graph-layout { display: block !important; min-height: 0; }
    .reading-view .graph-details { height: min(75vh, 900px); max-height: none; }
    .reading-view.expanded .graph-details { height: 100%; }
    @media (max-width: 1100px) { .shell { grid-template-columns: 1fr; } .rail { display: flex; flex-direction: row; flex-wrap: wrap; align-items: center; gap: .75rem; padding: .75rem 1rem; border-bottom: 1px solid var(--line); } .rail nav { display: flex; flex-wrap: wrap; } .rail-meta { display: none; } }
    @media (max-width: 980px) {
      .graph-layout { grid-template-columns: 1fr; gap: .65rem; }
      .reader-resize { display: none; }
      .graph-details { height: 340px; max-height: 45vh; }
      .graph-card.expanded .graph-layout { grid-template-rows: minmax(180px, 1fr) auto; }
      .graph-card.expanded .graph-details { height: 240px; max-height: 35vh; }
      .graph-card.expanded .graph-details.collapsed { height: auto; }
      .graph-card.reading-view .graph-details { height: 72vh; max-height: none; }
      .graph-card.reading-view.expanded .graph-details { height: 100%; }
      .graph-primary { align-items: flex-end; }
    }
    @media (max-width: 480px) {
      .graph-card { padding: .65rem; }
      .graph-card.expanded { inset: .5rem; }
      .graph-primary { gap: .4rem; }
      .graph-search { flex-basis: 100%; }
      .graph-title-row h2 { font-size: 1rem; }
      .graph-filter-row { gap: .3rem; }
      .graph-legend { gap: .65rem; font-size: .75rem; }
      .graph-force-panel { padding: .5rem; }
      .graph-help { font-size: .7rem; }
    }
  </style>
</head>
<body>
  <a class="skip-link" href="#main">Skip to graph monitor</a>
  <div class="shell">
    <aside class="rail" aria-label="Dev Graph monitor navigation">
      <div class="brand"><div class="brand-mark">DG</div><div><strong>Dev Graph</strong><span>Observer console</span></div></div>
      <nav><a href="#overview" aria-current="page">Overview</a><a href="#topology">Topology</a><a href="#pipeline">Initiative pipeline</a><a href="#activity">Graph activity</a><a href="#observations">Observations</a><a href="/monitor/selection">Project selection</a></nav>
      <div class="rail-meta"><div>AUTHORITY<span>Scoped read credential</span></div><div>TRANSPORT<span>App-native projection</span></div><div>SCHEMA<span>initiative-observation.v0</span></div></div>
    </aside>
    <main id="main">
      <header class="topbar" id="overview">
        <div><div class="eyebrow">Live local state</div><h1>Graph operations monitor</h1><p class="subtitle">One read-only view across work objects, inferred initiative observations, and transactional mutation receipts.</p></div>
        <div class="connection"><span class="dot" id="connection-dot"></span><span id="connection-label">Disconnected</span></div>
      </header>
      <form class="auth-strip" id="auth-form">
        <label for="token">Scoped bearer credential</label><input id="token" name="token" type="password" autocomplete="off" placeholder="Paste a local devgraph.read credential">
        <label for="refresh-rate">Refresh rate</label><select id="refresh-rate"><option value="0">Manual</option><option value="10" selected>10 seconds</option><option value="30">30 seconds</option></select>
        <button class="button" type="submit">Connect</button>
      </form>
      <p class="status-line" id="status-line" role="status" aria-live="polite">Enter a scoped read credential to inspect the graph.</p>
      <section class="kpis" aria-label="Graph summary">
        <article class="card kpi"><div class="kpi-label">Work objects</div><div class="kpi-value" id="total-work">—</div><div class="kpi-note">across the active work ontology</div></article>
        <article class="card kpi"><div class="kpi-label">Active initiatives</div><div class="kpi-value" id="active-initiatives">—</div><div class="kpi-note">canonical, non-archived work</div></article>
        <article class="card kpi"><div class="kpi-label">Inferred observations</div><div class="kpi-value" id="observation-count">—</div><div class="kpi-note">evidence-backed interpretations</div></article>
        <article class="card kpi"><div class="kpi-label">Pending receipts</div><div class="kpi-value" id="pending-receipts">—</div><div class="kpi-note" id="receipt-note">local transactional outbox</div></article>
      </section>
      <section class="card graph-card" id="topology">
        <div class="graph-toolbar">
          <div class="graph-title-row"><div><h2>Graph topology</h2><span class="section-meta" id="graph-meta">not refreshed</span></div><button class="graph-control" id="graph-expand" type="button" aria-expanded="false" aria-controls="topology">Expand view</button></div>
          <div class="graph-primary">
            <div class="graph-search"><label for="graph-search">Find work or a node</label><input type="search" id="graph-search" placeholder="Search titles or IDs" autocomplete="off" aria-controls="graph-search-results"><div id="graph-search-results" class="search-results" hidden></div></div>
            <div class="graph-zoom" role="group" aria-label="Graph zoom"><button class="graph-control" id="graph-zoom-out" type="button" aria-label="Zoom out">−</button><input id="graph-zoom" type="range" min="25" max="400" value="100" step="1" aria-label="Graph zoom percentage"><output id="graph-zoom-value" for="graph-zoom">100%</output><button class="graph-control" id="graph-zoom-in" type="button" aria-label="Zoom in">+</button><button class="graph-control" id="graph-fit" type="button">Fit</button></div>
          </div>
          <div class="graph-arena-row"><label class="graph-label-select" for="graph-arena">From Arena <select id="graph-arena" aria-controls="graph-svg" aria-describedby="graph-arena-note"><option value="">All nodes</option><option value="*">Any Arena</option></select></label><p class="graph-arena-note" id="graph-arena-note" role="status" aria-live="polite">Choose an Arena to follow its outgoing connections.</p></div>
          <div class="graph-filter-row"><div class="graph-legend" role="group" aria-label="Visible node categories"><label class="legend-item"><input type="checkbox" data-graph-category="arena" aria-controls="graph-svg" checked><span>Arena <span id="category-arena-count">0</span></span></label><label class="legend-item"><input type="checkbox" data-graph-category="work" aria-controls="graph-svg" checked><span>Work <span id="category-work-count">0</span></span></label><label class="legend-item" title="Evidence-backed interpretations, initially inferred and unclaimed"><input type="checkbox" data-graph-category="observation" aria-controls="graph-svg" checked><span>Observation <span id="category-observation-count">0</span></span></label><label class="legend-item"><input type="checkbox" data-graph-category="receipt" aria-controls="graph-svg" checked><span>Receipt <span id="category-receipt-count">0</span></span></label></div><label class="graph-label-select">Labels <select id="graph-labels"><option value="selected">Selected neighborhood</option><option value="all">All labels</option><option value="none">No labels</option></select></label><button class="graph-control" id="graph-show-all" type="button" hidden>Exit neighborhood</button></div>
        </div>
        <details class="graph-settings"><summary>Layout settings</summary><div class="graph-toolbar-actions"><button class="graph-control" id="graph-orbit" type="button" aria-pressed="false">Orbit: off</button><button class="graph-control" id="graph-settle" type="button">Settle graph</button><button class="graph-control" id="graph-reset" type="button">Reset layout</button></div><div class="graph-force-panel"><div class="graph-force-copy"><strong>Graph forces</strong><span>Adjust spacing and the pull between connected nodes.</span><button class="graph-control" id="graph-force-reset" type="button">Reset force defaults</button></div><div class="graph-force-controls" id="graph-force-controls" aria-label="Graph force controls"></div></div></details>
        <div class="graph-layout">
          <div class="graph-scroll"><svg class="graph-svg" id="graph-svg" viewBox="0 0 1080 420" tabindex="0" role="group" aria-label="Interactive Dev Graph topology" aria-describedby="graph-help"></svg></div>
          <div class="reader-resize" id="reader-resize" role="separator" tabindex="0" aria-orientation="vertical" aria-label="Details width" aria-valuemin="280" aria-valuemax="600" aria-valuenow="360"></div>
          <aside class="graph-details" id="graph-details"><div class="reader-header"><div class="reader-buttons"><button class="graph-control" id="reader-toggle" aria-expanded="true" aria-controls="reader-body">Hide details</button><button class="graph-control" id="reader-mode">Reading view</button></div><h3 id="reader-title">No node selected</h3><div class="graph-details-copy" id="reader-meta">Select a node or search to read its details.</div></div><div id="reader-body" class="reader-body"><p id="reader-status" role="status" aria-live="polite"></p><div id="reader-content"></div></div></aside>
        </div>
        <p class="graph-help" id="graph-help">Click a node to read · Scroll/pinch to zoom · Shift-drag to pan · Drag to orbit or move a node · F to fit</p>
        <div class="graph-resize" id="graph-resize" role="separator" tabindex="0" aria-label="Graph height" aria-orientation="horizontal" aria-valuemin="280" aria-valuemax="1200" aria-valuenow="420" aria-valuetext="420 pixels" aria-controls="graph-svg">Drag to resize · Arrow keys adjust height</div>
      </section>
      <section class="grid" id="pipeline">
        <article class="card section"><div class="section-head"><h2>Observation status</h2><span class="section-meta">inferred · claim actions unavailable</span></div><div class="pipeline" id="pipeline-stages"></div></article>
        <article class="card section"><div class="section-head"><h2>Work distribution</h2><span class="section-meta">by canonical kind</span></div><div class="bars" id="work-bars"></div></article>
      </section>
      <section class="grid">
        <article class="card section" id="activity"><div class="section-head"><h2>Recent graph activity</h2><span class="section-meta" id="generated-at">not refreshed</span></div><div class="activity" id="activity-list"><div class="empty">Connect to load safe graph activity.</div></div></article>
        <article class="card section" id="observations"><div class="section-head"><h2>Initiative observations</h2><span class="section-meta">evidence-backed · inferred</span></div><div class="observations" id="observation-list"><div class="empty">No observation data loaded.</div></div></article>
      </section>
    </main>
  </div>
  <script>
    const state = { timer: null, snapshot: null, observations: [], selectedGraphKey: null, authEpoch: 0, refreshPromise: null, refreshController: null, refreshQueued: false, pendingSnapshot: null, lastSuccess: null };
    const detailState = { node: null, data: null, error: null, loading: false, serial: 0, controller: null, promise: null, relations: new Map(), support: null, supportLoading: false, supportError: null, documents: new Map(), rendered: null, resourcesSerial: 0, credential: '' };
    const graphView = { yaw: -.38, pitch: .22, zoom: 1, panX: 0, panY: 0, width: 1080, height: 420, fitted: false, expanded: false, pointers: new Map(), pinch: null, orbit: false, frame: null, lastFrame: 0, drag: null, nodes: [], edges: [], nodePositions: new Map(), nodeVelocities: new Map(), edgeStrengths: new Map(), repulsionStrength: 1.5, pinnedKey: null, visibleCategories: new Set(['arena', 'work', 'observation', 'receipt']), arenaKey: '', arenaNodes: [], neighborhood: null, labels: 'selected' };
    const tokenInput = document.querySelector('#token');
    tokenInput.value = sessionStorage.getItem('devgraph-monitor-token') || '';
    const text = (id, value) => { const el = document.getElementById(id); if (el.textContent !== String(value)) el.textContent = String(value); };
    const headers = () => ({ Authorization: `Bearer ${tokenInput.value.trim()}` });
    const make = (tag, className, value) => { const node = document.createElement(tag); if (className) node.className = className; if (value !== undefined) node.textContent = value; return node; };
    const svgMake = (tag, attributes = {}) => { const node = document.createElementNS('http://www.w3.org/2000/svg', tag); Object.entries(attributes).forEach(([key, value]) => node.setAttribute(key, String(value))); return node; };
    const count = (record, key) => Number(record?.[key] || 0);

    async function getJson(path, options = {}) {
      const controller = new AbortController(); let timedOut = false;
      const cancel = () => controller.abort();
      options.signal?.addEventListener('abort', cancel, { once: true });
      if (options.signal?.aborted) cancel();
      const timer = setTimeout(() => { timedOut = true; controller.abort(); }, 30000);
      try {
        const response = await fetch(path, { headers: headers(), signal: controller.signal, redirect: 'error' });
        let payload;
        try { payload = await response.json(); } catch (error) { if (controller.signal.aborted || response.ok) throw error; payload = {}; }
        if (!response.ok) { const error = new Error(payload.title || `Request failed (${response.status})`); error.status = response.status; throw error; }
        return payload;
      } catch (error) {
        if (timedOut) { const timeout = new Error('The read timed out. Retry when the service is available.'); timeout.name = 'TimeoutError'; throw timeout; }
        throw error;
      } finally { clearTimeout(timer); options.signal?.removeEventListener('abort', cancel); }
    }

    function renderPipeline(record) {
      const stages = [
        ['Unclaimed', count(record, 'unclaimed')],
        ['Claimed', count(record, 'claimed')],
        ['Amended', count(record, 'amended')],
        ['Rejected', count(record, 'rejected')]
      ];
      const root = document.getElementById('pipeline-stages'); root.replaceChildren();
      stages.forEach(([label, value]) => { const stage = make('div', 'stage'); stage.append(make('strong', '', value), make('span', '', label)); root.append(stage); });
    }

    function renderBars(record) {
      const root = document.getElementById('work-bars'); root.replaceChildren();
      const entries = ['Initiative', 'Project', 'Issue', 'Task', 'Proposal'].map(key => [key, Number(record?.[key] || 0)]);
      const maximum = Math.max(1, ...entries.map(([, value]) => value));
      entries.forEach(([label, value]) => {
        const row = make('div', 'bar-row'); const track = make('div', 'bar-track'); const fill = make('div', 'bar-fill');
        fill.style.width = `${Math.round((value / maximum) * 100)}%`; track.append(fill); row.append(make('span', '', label), track, make('span', '', value)); root.append(row);
      });
    }

    function relativeTime(timestamp) {
      if (!timestamp) return 'unknown'; const delta = Date.now() - new Date(timestamp).getTime();
      if (!Number.isFinite(delta)) return 'unknown'; const minutes = Math.max(0, Math.round(delta / 60000));
      if (minutes < 1) return 'now'; if (minutes < 60) return `${minutes}m`; const hours = Math.round(minutes / 60); if (hours < 24) return `${hours}h`; return `${Math.round(hours / 24)}d`;
    }

    function renderActivity(items) {
      const root = document.getElementById('activity-list'); root.replaceChildren();
      if (!items.length) { root.append(make('div', 'empty', 'No graph activity has been recorded yet.')); return; }
      items.forEach(item => {
        const row = make('div', 'activity-row'); const mark = make('span', `activity-mark ${item.type}`); const copy = make('div');
        copy.append(make('div', 'activity-title', item.title), make('div', 'activity-sub', `${item.label} · ${item.status}`));
        row.append(mark, copy, make('time', 'activity-time', relativeTime(item.timestamp))); root.append(row);
      });
    }

    function renderObservations(items) {
      const root = document.getElementById('observation-list'); root.replaceChildren();
      if (!items.length) { root.append(make('div', 'empty', 'No initiative observations have been mined yet.')); return; }
      items.slice(0, 8).forEach(item => {
        const article = make('article', 'observation'); const top = make('div', 'observation-top');
        const link = make('a', '', item.title); link.href = item.subject_url; link.target = '_blank'; link.rel = 'noreferrer';
        top.append(link, make('span', 'pill', item.claim_status));
        const meta = make('div', 'observation-meta'); meta.append(make('span', '', `${Math.round(item.confidence * 100)}% confidence`), make('span', '', item.project_id), make('span', '', `${item.evidence_urls.length} evidence links`));
        article.append(top, make('p', '', item.problem), meta); root.append(article);
      });
    }

    function graphLane(kind) {
      return ({ Arena: -1, Proposal: 0, Initiative: 0, Project: 1, Issue: 2, InitiativeObservation: 2, Task: 3, EventReceipt: 4 })[kind] ?? 2;
    }

    function stableGraphDepth(key) {
      let hash = 0; for (const character of key) hash = ((hash * 31) + character.charCodeAt(0)) >>> 0;
      return (hash % 241) - 120;
    }

    function baseGraphCoordinates(nodes) {
      const lanes = new Map();
      nodes.forEach(node => { const lane = graphLane(node.kind); if (!lanes.has(lane)) lanes.set(lane, []); lanes.get(lane).push(node); });
      lanes.forEach(items => items.sort((a, b) => `${a.kind}:${a.id}`.localeCompare(`${b.kind}:${b.id}`)));
      const coordinates = new Map();
      lanes.forEach((items, lane) => {
        items.forEach((node, index) => {
          const span = Math.min(300, (items.length - 1) * 88); const y = items.length === 1 ? 0 : (-span / 2) + index * (span / (items.length - 1));
          const categoryDepth = node.category === 'observation' ? 70 : node.category === 'receipt' ? -70 : 0;
          coordinates.set(node.key, { x: (lane - 2) * 185, y, z: stableGraphDepth(node.key) + categoryDepth });
        });
      });
      return coordinates;
    }

    function syncGraphPhysics(nodes) {
      const base = baseGraphCoordinates(nodes); let changed = false; const retained = new Set((state.snapshot?.graph_nodes || nodes).map(node => node.key));
      nodes.forEach(node => { if (!graphView.nodePositions.has(node.key)) { graphView.nodePositions.set(node.key, { ...base.get(node.key) }); graphView.nodeVelocities.set(node.key, { x: 0, y: 0, z: 0 }); changed = true; } });
      for (const key of graphView.nodePositions.keys()) if (!retained.has(key)) { graphView.nodePositions.delete(key); graphView.nodeVelocities.delete(key); changed = true; }
      if (graphView.pinnedKey && !nodes.some(node => node.key === graphView.pinnedKey)) graphView.pinnedKey = null;
      return changed;
    }

    function filterGraph(nodes, edges, categories) {
      const visibleNodes = nodes.filter(node => categories.has(node.category));
      const keys = new Set(visibleNodes.map(node => node.key));
      return { nodes: visibleNodes, edges: edges.filter(edge => keys.has(edge.source) && keys.has(edge.target)) };
    }

    function filterGraphByArena(nodes, edges, arenaKey) {
      const available = new Set(nodes.map(node => node.key));
      if (!arenaKey) return { nodes, edges: edges.filter(edge => available.has(edge.source) && available.has(edge.target)) };
      const roots = nodes.filter(node => node.category === 'arena' && node.kind === 'Arena' && (arenaKey === '*' || node.key === arenaKey));
      const outgoing = new Map();
      for (const edge of edges) {
        if (!available.has(edge.source) || !available.has(edge.target)) continue;
        if (!outgoing.has(edge.source)) outgoing.set(edge.source, []);
        outgoing.get(edge.source).push(edge.target);
      }
      const reached = new Set(roots.map(node => node.key)), queue = [...reached];
      for (let index = 0; index < queue.length; index += 1) {
        for (const key of outgoing.get(queue[index]) || []) {
          if (reached.has(key)) continue;
          reached.add(key); queue.push(key);
        }
      }
      return { nodes: nodes.filter(node => reached.has(node.key)), edges: edges.filter(edge => reached.has(edge.source) && reached.has(edge.target)) };
    }

    function renderArenaFilter(nodes) {
      const select = document.getElementById('graph-arena'), desired = make('select');
      const arenas = nodes.filter(node => node.category === 'arena' && node.kind === 'Arena').sort((a, b) => a.title.localeCompare(b.title) || a.key.localeCompare(b.key));
      const selected = graphView.arenaKey || '';
      const entries = [['', 'All nodes'], ['*', 'Any Arena'], ...arenas.map(node => [node.key, `${node.title}${node.archived ? ' (archived)' : ''}`])];
      if (selected && selected !== '*' && !arenas.some(node => node.key === selected)) entries.push([selected, `${selected} (not in snapshot)`]);
      for (const [key, title] of entries) { const option = make('option', '', title); option.setAttribute('value', key); option.dataset.uiKey = key || 'all'; desired.append(option); }
      reconcileChildren(select, desired); if (select.value !== selected) select.value = selected;
      const arena = arenas.find(node => node.key === selected);
      const message = !selected ? 'Choose an Arena to follow its outgoing connections.' : selected !== '*' && !arena ? 'The selected Arena is not in this snapshot. Choose another Arena or All nodes.' : `Reachable from ${arena?.title || 'any Arena'} in the loaded graph. Dependencies can cross Arena boundaries.`;
      const note = document.getElementById('graph-arena-note'); if (note.textContent !== message) note.textContent = message;
    }

    function setGraphArena(key) {
      graphView.arenaKey = key; graphView.neighborhood = null;
      pauseGraphOrbit(); updateGraphVisibility(); fitGraph();
    }

    function graphCoordinates(nodes) {
      syncGraphPhysics(nodes); return new Map(nodes.map(node => [node.key, graphView.nodePositions.get(node.key)]));
    }

    function projectGraphPoint(point) {
      const cy = Math.cos(graphView.yaw); const sy = Math.sin(graphView.yaw); const cp = Math.cos(graphView.pitch); const sp = Math.sin(graphView.pitch);
      const rotatedX = point.x * cy - point.z * sy; const yawDepth = point.x * sy + point.z * cy;
      const rotatedY = point.y * cp - yawDepth * sp; const depth = point.y * sp + yawDepth * cp;
      const scale = (720 / (720 + depth)) * graphView.zoom * graphViewportScale();
      return { x: graphView.width / 2 + graphView.panX + rotatedX * scale, y: graphView.height / 2 + graphView.panY + rotatedY * scale, depth, scale, radius: Math.max(4, Math.min(64, 16 * scale)) };
    }

    function graphViewportScale() {
      return Math.min(graphView.width / 1080, graphView.height / 420);
    }

    function syncZoomControls() {
      const percent = Math.round(graphView.zoom * 100);
      document.getElementById('graph-zoom').value = String(percent);
      text('graph-zoom-value', `${percent}%`);
      document.getElementById('graph-zoom-out').disabled = graphView.zoom <= .25;
      document.getElementById('graph-zoom-in').disabled = graphView.zoom >= 4;
    }

    function setGraphZoom(value, anchor = { x: graphView.width / 2, y: graphView.height / 2 }) {
      const zoom = Math.max(.25, Math.min(4, value)); if (!Number.isFinite(zoom)) return;
      const ratio = zoom / graphView.zoom; const x = anchor.x - graphView.width / 2; const y = anchor.y - graphView.height / 2;
      graphView.panX = x - (x - graphView.panX) * ratio; graphView.panY = y - (y - graphView.panY) * ratio;
      graphView.zoom = zoom; graphView.fitted = false; syncZoomControls(); renderGraph(graphView.nodes, graphView.edges);
    }

    function fitGraph() {
      pauseGraphOrbit(); graphView.fitted = true; graphView.zoom = 1; graphView.panX = 0; graphView.panY = 0;
      const points = [...graphCoordinates(graphView.nodes).values()].map(projectGraphPoint);
      if (points.length) {
        const left = Math.min(...points.map(point => point.x - point.radius)); const right = Math.max(...points.map(point => point.x + point.radius));
        const top = Math.min(...points.map(point => point.y - point.radius)); const bottom = Math.max(...points.map(point => point.y + point.radius));
        graphView.zoom = Math.max(.25, Math.min(4, (graphView.width - 80) / Math.max(1, right - left), (graphView.height - 80) / Math.max(1, bottom - top)));
        graphView.panX = (graphView.width / 2 - (left + right) / 2) * graphView.zoom;
        graphView.panY = (graphView.height / 2 - (top + bottom) / 2) * graphView.zoom;
      }
      syncZoomControls(); renderGraph(graphView.nodes, graphView.edges);
    }

    function resizeGraphViewport() {
      const svg = document.getElementById('graph-svg'); const width = svg.clientWidth; const height = svg.clientHeight;
      if (!width || !height || (width === graphView.width && height === graphView.height)) return;
      const previousScale = graphViewportScale(); graphView.width = width; graphView.height = height;
      if (graphView.fitted) { fitGraph(); return; }
      const ratio = graphViewportScale() / previousScale; graphView.panX *= ratio; graphView.panY *= ratio;
      renderGraph(graphView.nodes, graphView.edges);
    }

    function setGraphHeight(value) {
      const height = Math.round(Math.max(280, Math.min(1200, value)));
      document.getElementById('topology').style.setProperty('--graph-height', `${height}px`);
      const handle = document.getElementById('graph-resize'); handle.setAttribute('aria-valuenow', String(height)); handle.setAttribute('aria-valuetext', `${height} pixels`);
    }

    function toggleGraphExpanded() {
      const card = document.getElementById('topology'); const button = document.getElementById('graph-expand');
      graphView.expanded = !graphView.expanded; card.classList.toggle('expanded', graphView.expanded); document.body.classList.toggle('graph-expanded', graphView.expanded);
      button.setAttribute('aria-expanded', String(graphView.expanded)); button.textContent = graphView.expanded ? 'Restore view' : 'Expand view';
      // Prevent keyboard focus from moving behind the expanded graph.
      [...document.querySelector('main').children].filter(element => element !== card).forEach(element => { element.inert = graphView.expanded; });
      document.querySelector('.rail').inert = graphView.expanded;
      if (!graphView.expanded) button.focus();
    }

    function graphScreenVector(deltaX, deltaY, scale) {
      const cy = Math.cos(graphView.yaw); const sy = Math.sin(graphView.yaw); const cp = Math.cos(graphView.pitch); const sp = Math.sin(graphView.pitch); const safeScale = Math.max(.01, scale);
      const cameraX = deltaX / safeScale; const cameraY = deltaY / safeScale; const cameraDepth = -cameraY * sp;
      return { x: cameraX * cy + cameraDepth * sy, y: cameraY * cp, z: -cameraX * sy + cameraDepth * cy };
    }

    function moveGraphNode(key, deltaX, deltaY, scale) {
      const delta = graphScreenVector(deltaX, deltaY, scale);
      const current = graphView.nodePositions.get(key); if (!current) return;
      graphView.nodePositions.set(key, { x: current.x + delta.x, y: current.y + delta.y, z: current.z + delta.z }); graphView.nodeVelocities.set(key, { x: 0, y: 0, z: 0 });
    }

    function graphClientDelta(deltaX, deltaY) {
      const matrix = document.getElementById('graph-svg').getScreenCTM(); const screenScale = matrix ? Math.hypot(matrix.a, matrix.b) : 1;
      return { x: deltaX / Math.max(.01, screenScale), y: deltaY / Math.max(.01, screenScale) };
    }

    function graphEdgeStrength(relationship) {
      return graphView.edgeStrengths.get(relationship) ?? 1;
    }

    function relaxGraph(iterations = 40, pinnedKey = graphView.pinnedKey) {
      const nodes = graphView.nodes; const edges = graphView.edges; if (nodes.length < 2) return; const positions = graphCoordinates(nodes);
      const passCount = Math.max(1, Math.min(iterations, Math.floor(40000 / Math.max(1, nodes.length * nodes.length))));
      for (let iteration = 0; iteration < passCount; iteration += 1) {
        const forces = new Map(nodes.map(node => [node.key, { x: 0, y: 0, z: 0 }]));
        edges.forEach(edge => {
          const source = positions.get(edge.source); const target = positions.get(edge.target); if (!source || !target) return;
          const dx = target.x - source.x; const dy = target.y - source.y; const dz = target.z - source.z; const distance = Math.max(1, Math.hypot(dx, dy, dz)); const magnitude = (distance - 155) * .0035 * graphEdgeStrength(edge.relationship);
          const force = { x: dx / distance * magnitude, y: dy / distance * magnitude, z: dz / distance * magnitude }; const sourceForce = forces.get(edge.source); const targetForce = forces.get(edge.target);
          sourceForce.x += force.x; sourceForce.y += force.y; sourceForce.z += force.z; targetForce.x -= force.x; targetForce.y -= force.y; targetForce.z -= force.z;
        });
        for (let left = 0; left < nodes.length; left += 1) for (let right = left + 1; right < nodes.length; right += 1) {
          const first = positions.get(nodes[left].key); const second = positions.get(nodes[right].key); const dx = second.x - first.x; const dy = second.y - first.y; const dz = second.z - first.z; const distance = Math.max(18, Math.hypot(dx, dy, dz));
          const longRange = 4200 * graphView.repulsionStrength / (distance * distance); const collision = Math.max(0, 115 - distance) * .045 * graphView.repulsionStrength; const magnitude = longRange + collision; const force = { x: dx / distance * magnitude, y: dy / distance * magnitude, z: dz / distance * magnitude };
          const firstForce = forces.get(nodes[left].key); const secondForce = forces.get(nodes[right].key); firstForce.x -= force.x; firstForce.y -= force.y; firstForce.z -= force.z; secondForce.x += force.x; secondForce.y += force.y; secondForce.z += force.z;
          const firstProjected = projectGraphPoint(first); const secondProjected = projectGraphPoint(second); let projectedX = secondProjected.x - firstProjected.x; let projectedY = secondProjected.y - firstProjected.y; let projectedDistance = Math.hypot(projectedX, projectedY);
          if (projectedDistance < 72) { if (projectedDistance < 1) { projectedX = left % 2 ? -1 : 1; projectedY = right % 2 ? 1 : -1; projectedDistance = Math.hypot(projectedX, projectedY); } const screenMagnitude = (72 - projectedDistance) * .075 * graphView.repulsionStrength; const screenForce = graphScreenVector(projectedX / projectedDistance * screenMagnitude, projectedY / projectedDistance * screenMagnitude, (firstProjected.scale + secondProjected.scale) / 2); firstForce.x -= screenForce.x; firstForce.y -= screenForce.y; firstForce.z -= screenForce.z; secondForce.x += screenForce.x; secondForce.y += screenForce.y; secondForce.z += screenForce.z; }
        }
        nodes.forEach(node => {
          const position = positions.get(node.key); if (node.key === pinnedKey) { graphView.nodeVelocities.set(node.key, { x: 0, y: 0, z: 0 }); return; }
          const force = forces.get(node.key); force.x -= position.x * .00065; force.y -= position.y * .00065; force.z -= position.z * .00045;
          const prior = graphView.nodeVelocities.get(node.key) || { x: 0, y: 0, z: 0 }; const velocity = { x: (prior.x + force.x) * .76, y: (prior.y + force.y) * .76, z: (prior.z + force.z) * .76 }; const speed = Math.max(1, Math.hypot(velocity.x, velocity.y, velocity.z)); const limit = Math.min(1, 9 / speed); velocity.x *= limit; velocity.y *= limit; velocity.z *= limit;
          const next = { x: Math.max(-470, Math.min(470, position.x + velocity.x)), y: Math.max(-175, Math.min(175, position.y + velocity.y)), z: Math.max(-280, Math.min(280, position.z + velocity.z)) }; positions.set(node.key, next); graphView.nodePositions.set(node.key, next); graphView.nodeVelocities.set(node.key, velocity);
        });
      }
    }

    function settleGraph() {
      graphView.pinnedKey = null; relaxGraph(90, null); renderGraph(graphView.nodes, graphView.edges);
    }

    function resetForceStrengths() {
      graphView.repulsionStrength = 1.5; new Set(graphView.edges.map(edge => edge.relationship)).forEach(relationship => graphView.edgeStrengths.set(relationship, 1)); renderForceControls(graphView.edges); settleGraph();
    }

    function drawGraphTooltip(layer, node, position, edges) {
      layer.replaceChildren(); const width = Math.min(270, graphView.width - 20); const height = 72;
      let x = position.x + position.radius + 13; if (x + width > graphView.width - 10) x = position.x - position.radius - width - 13;
      x = Math.max(10, Math.min(graphView.width - width - 10, x));
      const y = Math.max(10, Math.min(graphView.height - height - 10, position.y - height / 2)); const connected = edges.filter(edge => edge.source === node.key || edge.target === node.key).length;
      const group = svgMake('g', { class: 'graph-tooltip', role: 'tooltip' });
      group.append(svgMake('rect', { class: 'graph-tooltip-panel', x, y, width, height, rx: 9 }));
      const title = svgMake('text', { class: 'graph-tooltip-title', x: x + 12, y: y + 21 }); title.textContent = node.title.length > 38 ? `${node.title.slice(0, 37)}…` : node.title;
      const meta = svgMake('text', { class: 'graph-tooltip-meta', x: x + 12, y: y + 39 }); meta.textContent = `${node.kind} · ${node.status}${node.archived ? ' · archived' : ''}`;
      const copy = svgMake('text', { class: 'graph-tooltip-copy', x: x + 12, y: y + 57 }); copy.textContent = `${connected} stored relationship${connected === 1 ? '' : 's'} · drag to pull graph · click to inspect`;
      group.append(title, meta, copy); layer.append(group);
    }

    function safeSourceUrl(value) {
      try { const url = new URL(value); return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password ? url.href : null; } catch { return null; }
    }

    function workNode(work) {
      return (state.snapshot?.graph_nodes || []).find(node => node.kind === work.kind && node.id === work.id) || { key: `${work.kind}:${work.id}`, category: 'work', archived: work.status === 'archived', ...work };
    }

    function readPath(node) {
      if (node.category === 'arena' && node.kind === 'Arena') return `/arenas/${encodeURIComponent(node.id)}`;
      if (node.category === 'work' && ['Proposal', 'Initiative', 'Project', 'Issue', 'Task'].includes(node.kind)) return `/work/${node.kind}/${encodeURIComponent(node.id)}`;
      if (node.category === 'observation') return `/initiative-observations/${encodeURIComponent(node.id)}`;
      return null;
    }

    function resetDetailState() {
      detailState.controller?.abort(); detailState.serial += 1; detailState.resourcesSerial = (detailState.resourcesSerial || 0) + 1;
      for (const page of detailState.relations.values()) page.controller?.abort();
      detailState.supportRequest?.controller.abort();
      for (const doc of detailState.documents.values()) doc.controller?.abort();
      Object.assign(detailState, { node: null, data: null, error: null, loading: false, promise: null, controller: null, support: null, supportRequest: null, supportStale: false, supportLoading: false, supportError: null, rendered: null });
      detailState.relations.clear(); detailState.documents.clear();
    }

    function selectGraphNode(node, focus = false) {
      if (!node) return;
      pauseGraphOrbit();
      if (detailState.node?.key !== node.key) {
        resetDetailState(); detailState.node = node;
        document.getElementById('reader-body').scrollTop = 0;
      }
      state.selectedGraphKey = node.key;
      const panel = document.getElementById('graph-details'); panel.classList.remove('collapsed');
      document.getElementById('reader-toggle').setAttribute('aria-expanded', 'true'); text('reader-toggle', 'Hide details');
      renderGraph(graphView.nodes, graphView.edges); renderGraphSelection();
      loadSelectedDetail();
      if (focus) document.querySelector(`[data-node-key="${CSS.escape(node.key)}"]`)?.focus();
    }

    async function loadSelectedDetail() {
      const node = detailState.node; if (!node || detailState.promise) return detailState.promise;
      const path = readPath(node); if (!path) return;
      const serial = detailState.serial, epoch = state.authEpoch;
      const controller = new AbortController(); detailState.controller = controller;
      detailState.loading = !detailState.data; renderGraphSelection();
      const request = (async () => {
        let updated = false, changed = false;
        try {
          const data = await getJson(path, { signal: controller.signal });
          if (serial !== detailState.serial || epoch !== state.authEpoch) return;
          changed = Boolean(detailState.data && detailState.data.version !== data.version);
          detailState.data = data; detailState.error = null;
          updated = true;
          if (changed) {
            detailState.resourcesSerial = (detailState.resourcesSerial || 0) + 1;
            for (const page of detailState.relations.values()) { page.controller?.abort(); page.loading = false; page.stale = true; }
            detailState.supportRequest?.controller.abort(); detailState.supportLoading = false; detailState.supportStale = Boolean(detailState.support);
            for (const doc of detailState.documents.values()) { doc.controller?.abort(); doc.loading = false; doc.refreshing = false; doc.stale = true; }
          }
        } catch (error) {
          if (serial !== detailState.serial || epoch !== state.authEpoch || error.name === 'AbortError') return;
          detailState.error = error.status === 404 ? 'This record is no longer available.' : error.status === 401 || error.status === 403 ? 'Access to these details was denied. Reconnect with a valid read credential.' : 'Could not update details. Retry to check the connection.';
        } finally {
          if (serial === detailState.serial && epoch === state.authEpoch) {
            detailState.loading = false; detailState.promise = null; renderGraphSelection();
            if (updated) refreshSelectedResources(changed);
          }
        }
      })();
      detailState.promise = request; return request;
    }

    function refreshSelectedResources() {
      if (detailState.node?.category !== 'work') return;
      // Read only sections the user has opened. Reconciliation can preserve an
      // open section across selections without emitting another toggle event.
      const sections = document.getElementById('reader-content').querySelectorAll('details[data-relationship][open]');
      for (const section of sections) loadRelationship(section.dataset.relationship, false, true);
      const requested = detailState.supportRequest;
      if (detailState.support || (requested?.serial === detailState.serial && requested?.epoch === state.authEpoch)) loadSupporting(false, true);
    }

    async function loadRelationship(relationship, more = false, refresh = false) {
      const node = detailState.node; if (node?.category !== 'work') return;
      const old = detailState.relations.get(relationship);
      if (old?.loading || (!more && old?.loaded && !old.stale && !refresh)) return;
      const page = old || { items: [], loaded: false, more: false, loading: false, error: null };
      const targetCount = !more ? Math.max(50, page.items.length) : 50;
      const controller = new AbortController(); page.controller = controller;
      page.loading = true; page.error = null; detailState.relations.set(relationship, page); renderGraphSelection();
      const serial = detailState.serial, epoch = state.authEpoch, resourceSerial = detailState.resourcesSerial;
      const current = () => serial === detailState.serial && epoch === state.authEpoch && resourceSerial === detailState.resourcesSerial && detailState.relations.get(relationship) === page && page.controller === controller;
      try {
        let items = [], last = more ? page.items.at(-1) : null, hasMore = false;
        do {
          const cursor = last ? `${last.kind}/${last.id}` : null;
          const after = cursor ? `&after_resource=${encodeURIComponent(cursor)}` : '';
          const data = await getJson(`${readPath(node)}/relationships/${relationship}?limit=50${after}`, { signal: controller.signal });
          if (!current()) return;
          items.push(...data.items); hasMore = data.items.length === 50;
          last = data.items.at(-1);
          if (hasMore && last && `${last.kind}/${last.id}` === cursor) throw new Error('Relationship pagination did not advance.');
        } while (!more && hasMore && items.length < targetCount);
        const combined = more ? [...page.items, ...items] : items;
        page.items = [...new Map(combined.map(item => [`${item.kind}:${item.id}`, item])).values()]; page.more = hasMore; page.loaded = true; page.stale = false;
      } catch (error) { if (current() && error.name !== 'AbortError') { page.error = 'Could not load related work. Retry.'; page.stale = Boolean(page.loaded); } }
      finally { if (current()) { page.loading = false; renderGraphSelection(); } }
    }

    async function loadSupporting(more = false, refresh = false) {
      const node = detailState.node; if (node?.category !== 'work' || detailState.supportLoading) return;
      const serial = detailState.serial, epoch = state.authEpoch, resourceSerial = detailState.resourcesSerial;
      const controller = new AbortController();
      const request = { serial, epoch, resourceSerial, controller }; detailState.supportRequest = request;
      const prior = detailState.support;
      const targetCount = !more ? Math.max(50, prior?.items.length || 0) : 50;
      const current = () => serial === detailState.serial && epoch === state.authEpoch && resourceSerial === detailState.resourcesSerial && detailState.supportRequest === request;
      detailState.supportLoading = true; detailState.supportError = null; renderGraphSelection();
      try {
        let items = [], cursor = more ? prior?.next_cursor : null, data;
        do {
          const after = cursor ? `&after=${encodeURIComponent(cursor)}` : '';
          data = await getJson(`${readPath(node)}/supporting-material?limit=50${after}`, { signal: controller.signal });
          if (!current()) return;
          items.push(...data.items);
          if (data.next_cursor && data.next_cursor === cursor) throw new Error('Supporting pagination did not advance.');
          cursor = data.next_cursor;
        } while (!more && cursor && items.length < targetCount);
        const combined = more ? [...(prior?.items || []), ...items] : items;
        detailState.support = { ...data, items: [...new Map(combined.map(item => [`${item.kind}:${item.id}`, item])).values()] };
        detailState.supportStale = false;
        const artifacts = new Map(detailState.support.items.filter(item => item.kind === 'Artifact').map(item => [item.id, item]));
        for (const [id, doc] of detailState.documents) {
          const attached = artifacts.get(id);
          if ((attached && attached.resolution !== 'available') || (!attached && !data.next_cursor)) { doc.controller?.abort(); detailState.documents.delete(id); }
          else if (!more || refresh) loadDocument(id, true);
        }
      } catch (error) { if (current() && error.name !== 'AbortError') { detailState.supportError = error.status === 404 ? 'Supporting-material access is unavailable on this server.' : 'Could not load supporting material. Retry from the first page.'; detailState.supportStale = Boolean(prior); } }
      finally { if (current()) { detailState.supportLoading = false; renderGraphSelection(); } }
    }

    async function loadDocument(id, refresh = false) {
      const old = detailState.documents.get(id);
      if (old?.loading || old?.refreshing) return;
      const node = detailState.node, serial = detailState.serial, epoch = state.authEpoch, resourceSerial = detailState.resourcesSerial;
      if (node?.category !== 'work') return;
      const controller = new AbortController(), cached = old?.content != null;
      const pending = { ...old, controller, loading: !cached, refreshing: cached, error: null, refreshError: null };
      detailState.documents.set(id, pending); renderGraphSelection();
      const current = () => serial === detailState.serial && epoch === state.authEpoch && resourceSerial === detailState.resourcesSerial && detailState.documents.get(id) === pending;
      try {
        const data = await getJson(`${readPath(node)}/supporting-material/Artifact/${encodeURIComponent(id)}/document`, { signal: controller.signal });
        if (!current()) return;
        detailState.documents.set(id, data);
      } catch (error) {
        if (!current() || error.name === 'AbortError') return;
        if (error.status === 404) detailState.documents.set(id, { state: 'missing', message: 'This document is no longer attached or available.' });
        else if (cached && error.status !== 401 && error.status !== 403) detailState.documents.set(id, { ...old, loading: false, refreshing: false, stale: true, refreshError: 'Could not update this document. Showing the last loaded text.' });
        else detailState.documents.set(id, { error: 'Could not read this document. Retry.' });
      } finally { if (serial === detailState.serial && epoch === state.authEpoch && resourceSerial === detailState.resourcesSerial) renderGraphSelection(); }
    }

    function reconcileChildren(parent, desired) {
      const wanted = [...desired.childNodes]; const keyed = new Map([...parent.children].filter(el => el.dataset.uiKey).map(el => [el.dataset.uiKey, el]));
      const retained = new Set();
      wanted.forEach((next, index) => {
        const key = next.nodeType === 1 && next.dataset.uiKey;
        let current = key ? keyed.get(key) : parent.childNodes[index];
        if (current && (retained.has(current) || current.nodeName !== next.nodeName || (current.nodeType === 1 && current.dataset.uiKey !== next.dataset.uiKey))) current = null;
        if (!current) { current = next; parent.insertBefore(current, parent.childNodes[index] || null); }
        else {
          if (parent.childNodes[index] !== current) parent.insertBefore(current, parent.childNodes[index] || null);
          if (current.nodeType === 3) { if (current.nodeValue !== next.nodeValue) current.nodeValue = next.nodeValue; }
          else {
            for (const attr of [...current.attributes]) if (!next.hasAttribute(attr.name) && !(current.tagName === 'DETAILS' && attr.name === 'open')) current.removeAttribute(attr.name);
            for (const attr of [...next.attributes]) if (current.getAttribute(attr.name) !== attr.value) current.setAttribute(attr.name, attr.value);
            reconcileChildren(current, next);
          }
        }
        retained.add(current);
      });
      for (const current of [...parent.childNodes]) if (!retained.has(current)) current.remove();
    }

    function readerButton(label, action, extra = {}) {
      const button = make('button', 'graph-control', label); button.type = 'button'; Object.assign(button.dataset, { action, ...extra }); return button;
    }

    function readerSection(title, key) {
      const section = make('section', 'reader-section'); section.dataset.uiKey = key; section.append(make('h4', '', title)); return section;
    }

    function appendSource(parent, value, label = 'Open source') {
      const safe = safeSourceUrl(value); if (!safe) return false;
      const link = make('a', '', label); link.href = safe; link.target = '_blank'; link.rel = 'noopener noreferrer'; parent.append(link); return true;
    }

    function renderGraphSelection() {
      const node = detailState.node;
      const latest = (state.snapshot?.graph_nodes || []).find(item => item.key === node?.key);
      if (latest) detailState.node = { ...node, ...latest };
      text('reader-title', detailState.data?.title || node?.title || 'No node selected');
      text('reader-meta', node ? `${node.kind} · ${detailState.data?.status || node.status} · ${node.id}` : 'Select a node or search to read its details.');
      text('reader-status', detailState.loading ? 'Loading details…' : detailState.error ? `${detailState.error}${detailState.data ? ' Showing the last loaded details.' : ''}` : '');
      const content = make('div');
      if (!node) { content.append(make('p', 'reader-note', 'Find an item to read its description, related work, and supporting material.')); reconcileChildren(document.getElementById('reader-content'), content); return; }
      const actions = make('div', 'reader-actions'); actions.dataset.uiKey = 'actions';
      const visible = graphView.nodes.some(item => item.key === node.key);
      const outsideArena = latest && graphView.arenaKey && !graphView.arenaNodes.some(item => item.key === node.key);
      actions.append(readerButton(outsideArena ? 'Show in all nodes' : visible ? 'Focus in graph' : 'Reveal in graph', 'focus'));
      if (!outsideArena) actions.append(readerButton('Show neighborhood', 'neighborhood'));
      if (latest?.category === 'arena') actions.append(readerButton('Filter from this Arena', 'arena-filter'));
      if (readPath(node)) actions.append(readerButton('Refresh details', 'refresh-detail'));
      content.append(actions);
      if (!latest) content.append(make('p', 'reader-note', 'This item is outside the current graph snapshot. Its available details are shown below.'));
      else if (!visible) content.append(make('p', 'reader-note', outsideArena ? 'This item is outside the selected Arena reachability filter.' : 'This item is hidden by the graph filters.'));
      const data = detailState.data;
      if (node.category === 'work' && data) {
        const description = readerSection('Description / plan', 'description');
        description.append(make('div', 'reader-note', `Priority ${data.priority} · Version ${data.version}`), make('div', data.description ? 'reader-description' : 'reader-note', data.description || 'No description has been written for this item.'));
        content.append(description);
        if (latest?.progress) { const p = latest.progress; const section = readerSection('Progress', 'progress'); section.append(make('p', 'reader-note', `${p.percent}% · ${p.completed} of ${p.total} terminal work items (accepted or archived)`)); content.append(section); }
        const relationships = [['parent', 'Parent'], ['children', 'Child work'], ['dependencies', 'Depends on'], ['dependents', 'Needed by'], ...(node.kind === 'Task' ? [['blockers', 'Blocked by'], ['blocked', 'Blocks']] : [])];
        const section = readerSection('Related work', 'relationships');
        for (const [rel, label] of relationships) {
          const entry = detailState.relations.get(rel); const group = make('details'); group.dataset.uiKey = rel; group.dataset.relationship = rel;
          group.append(make('summary', '', label)); const body = make('div');
          if (!entry?.loaded) body.append(make('p', 'reader-note', entry?.loading ? 'Loading…' : entry?.error || 'Open to load related work.'));
          for (const work of entry?.items || []) { const link = make('button', 'reader-link', work.title); Object.assign(link.dataset, { action: 'related', relationship: rel, id: work.id, kind: work.kind, uiKey: `${work.kind}:${work.id}` }); link.append(make('small', '', `${work.kind} · ${work.status}`)); body.append(link); }
          if (entry?.loaded && !entry.items.length) body.append(make('p', 'reader-note', 'No related work in this section.'));
          if (entry?.loaded && (entry.loading || entry.error || entry.stale)) body.append(make('p', 'reader-note', entry.error ? `${entry.error} Showing the last loaded related work.` : entry.loading ? 'Updating related work…' : 'Showing the last loaded related work.'));
          if (entry?.error) body.append(readerButton('Retry', 'relationship-retry', { relationship: rel }));
          else if (entry?.more) body.append(readerButton(entry.loading ? 'Loading…' : 'Load more', 'relationship-more', { relationship: rel }));
          group.append(body); section.append(group);
        }
        content.append(section);
        const support = readerSection('Supporting material', 'support');
        const refs = [...(data.artifact_ids || []).map(id => `Artifact: ${id}`), ...(data.external_link_ids || []).map(id => `Link: ${id}`)];
        if (!detailState.support) {
          support.append(make('p', 'reader-note', refs.length ? `${refs.length} declared references. Load to resolve them and any attached requirements or criteria.` : 'No declared document/link references. Load to check attached material and requirements.'));
          refs.forEach(ref => support.append(make('p', 'reader-note', ref)));
        }
        for (const item of detailState.support?.items || []) {
          const card = make('article', 'support-card'); card.dataset.uiKey = `${item.kind}:${item.id}`;
          const meta = item.metadata || {};
          card.append(make('h5', '', meta.title || item.id), make('p', 'reader-note', `${item.kind}${meta.role ? ` · ${meta.role.replaceAll('_', ' ')}` : ''} · ${item.resolution}`));
          if (meta.status) card.append(make('p', 'reader-note', `${meta.status} · Priority ${meta.priority} · Version ${meta.version}`));
          if (meta.description || meta.summary) card.append(make('div', 'reader-description', meta.description || meta.summary));
          const location = meta.url || meta.uri;
          if (location) { card.append(make('p', 'reader-note', location)); appendSource(card, location); }
          if (item.resolution !== 'available') card.append(make('p', 'reader-note', 'This reference could not be fully resolved. It is not an empty document.'));
          if (item.kind === 'Artifact' && item.resolution === 'available' && !safeSourceUrl(location)) {
            card.append(readerButton('Read document', 'document', { id: item.id }));
            const doc = detailState.documents.get(item.id);
            if (doc?.loading) card.append(make('p', 'reader-note', 'Reading document…'));
            else if (doc?.error) card.append(make('p', 'reader-note', doc.error));
            else if (doc?.content != null) {
              card.append(make('pre', 'document-content', doc.content));
              if (doc.refreshing || doc.refreshError || doc.stale) card.append(make('p', 'reader-note', doc.refreshError || (doc.refreshing ? 'Updating document… Showing the last loaded text.' : 'Showing the last loaded document. Refresh to verify current content.')));
            }
            else if (doc) card.append(make('p', 'reader-note', documentStateMessage(doc)));
          }
          support.append(card);
        }
        if (detailState.support && !detailState.support.items.length) support.append(make('p', 'reader-note', 'No supporting material is attached to this item.'));
        if (detailState.supportError) support.append(make('p', 'reader-note', detailState.supportError));
        else if (detailState.support && (detailState.supportLoading || detailState.supportStale)) support.append(make('p', 'reader-note', detailState.supportLoading ? 'Updating material… Showing the last loaded references.' : 'Showing the last loaded references. Refresh to verify attachments.'));
        if (!detailState.support || detailState.supportError) support.append(readerButton(detailState.supportLoading ? 'Loading…' : 'Load supporting material', 'support'));
        else if (detailState.support.next_cursor) support.append(readerButton(detailState.supportLoading ? 'Loading…' : 'Load more material', 'support-more'));
        else support.append(readerButton('Refresh material', 'support'));
        content.append(support);
      } else if (node.category === 'arena' && data) {
        const section = readerSection('Arena', 'arena');
        section.append(make('p', 'reader-note', `${data.archived ? 'Archived' : 'Active'} · Version ${data.version}`), make('div', 'reader-description', data.description || 'No description has been written for this Arena.'));
        section.append(make('p', 'reader-note', 'Membership belongs to the top Work parent. Its projects, issues, and child tasks inherit this Arena. Direct members appear in Connections below.'));
        content.append(section);
      } else if (node.category === 'observation' && data) {
        for (const [label, value] of [['Problem', data.problem], ['Desired outcome', data.desired_state]]) { const section = readerSection(label, label); section.append(make('div', 'reader-description', value)); content.append(section); }
        const evidence = readerSection('Evidence and provenance', 'evidence');
        evidence.append(make('p', 'reader-note', `Inferred · ${data.claim_status} · ${Math.round(data.confidence * 100)}% producer confidence (not calibrated) · Observed by ${data.observed_by}`));
        evidence.append(make('p', 'reader-note', 'An interpretation supported by linked evidence. Maintainer claim actions are not available in this monitor.'));
        appendSource(evidence, data.subject_url, 'Open subject');
        for (const url of data.evidence_urls || []) { const row = make('p'); appendSource(row, url, url); evidence.append(row); }
        content.append(evidence);
      } else if (node.category === 'receipt') {
        const section = readerSection('Receipt summary', 'receipt'); section.append(make('p', 'reader-description', 'A receipt records a committed graph mutation. Pending means the local outbox has not advanced; it does not mean the mutation is uncommitted.')); content.append(section);
      }
      const connections = readerSection('Connections in this snapshot', 'connections');
      const allEdges = (state.snapshot?.graph_edges || []).filter(edge => edge.source === node.key || edge.target === node.key);
      const visibleEdges = graphView.edges.filter(edge => edge.source === node.key || edge.target === node.key);
      connections.append(make('p', 'reader-note', `${visibleEdges.length} visible · ${allEdges.length} in this snapshot`));
      if (!allEdges.length) connections.append(make('p', 'reader-note', 'No connections in this snapshot. This does not include every supporting-material relationship.'));
      for (const edge of allEdges) {
        const outbound = edge.source === node.key; const key = outbound ? edge.target : edge.source;
        const other = (state.snapshot?.graph_nodes || []).find(item => item.key === key);
        const link = make('button', 'reader-link', `${outbound ? '→' : '←'} ${relationshipLabel(edge.relationship)} · ${other?.title || key}`); Object.assign(link.dataset, { action: 'snapshot-node', key, uiKey: `${edge.source}:${edge.relationship}:${edge.target}` }); connections.append(link);
      }
      content.append(connections); reconcileChildren(document.getElementById('reader-content'), content);
    }

    function documentStateMessage(doc) {
      if (doc.message) return doc.message;
      const stateName = doc.state || doc.status || doc.resolution;
      return ({ unconfigured: 'Local document reading has not been configured for this host.', missing: 'The linked document could not be found.', unsupported: 'This document type or location cannot be previewed.', too_large: 'This document exceeds the preview size limit.', denied: 'This document is outside the configured reading locations.', invalid: 'This document reference is not valid.', unavailable: 'Document content is unavailable.' })[stateName] || `Document preview unavailable (${stateName || 'unknown state'}).`;
    }

    function relationshipLabel(value) {
      return ({ CONTAINS_WORK: 'Arena member', HAS_CHILD: 'Child work', DEPENDS_ON: 'Depends on', BLOCKS: 'Blocks', EMITTED_EVENT: 'Receipt', HAS_ARTIFACT: 'Artifact', CONVERTED_TO: 'Converted to' })[value] || value.replaceAll('_', ' ').toLowerCase();
    }

    function neighborhoodKeys(nodes, edges, key) {
      const available = new Set(nodes.map(node => node.key)); const keys = new Set(); if (!available.has(key)) return keys;
      keys.add(key); edges.forEach(edge => { if (edge.source === key && available.has(edge.target)) keys.add(edge.target); if (edge.target === key && available.has(edge.source)) keys.add(edge.source); }); return keys;
    }

    function chooseGraphLabels(candidates, occupied, width, height) {
      const boxes = [...occupied], used = new Set(), result = [];
      for (const candidate of candidates) {
        if (used.has(candidate.key) || ![candidate.x, candidate.y, candidate.width, candidate.height].every(Number.isFinite)) continue;
        if (candidate.x < 4 || candidate.y < 4 || candidate.x + candidate.width > width - 4 || candidate.y + candidate.height > height - 4) continue;
        if (boxes.some(box => candidate.x < box.x + box.width + 4 && candidate.x + candidate.width + 4 > box.x && candidate.y < box.y + box.height + 4 && candidate.y + candidate.height + 4 > box.y)) continue;
        result.push(candidate); boxes.push(candidate); used.add(candidate.key);
      }
      return result;
    }

    function updateGraphVisibility() {
      const allNodes = state.snapshot?.graph_nodes || [], allEdges = state.snapshot?.graph_edges || [];
      const scoped = filterGraphByArena(allNodes, allEdges, graphView.arenaKey); graphView.arenaNodes = scoped.nodes;
      renderArenaFilter(allNodes);
      let { nodes, edges } = filterGraph(scoped.nodes, scoped.edges, graphView.visibleCategories);
      if (graphView.neighborhood) {
        const keys = neighborhoodKeys(allNodes, allEdges, graphView.neighborhood);
        nodes = nodes.filter(node => keys.has(node.key)); const shown = new Set(nodes.map(node => node.key)); edges = edges.filter(edge => shown.has(edge.source) && shown.has(edge.target));
      }
      graphView.nodes = nodes; graphView.edges = edges;
      const changed = syncGraphPhysics(nodes); renderForceControls(scoped.edges);
      if (changed) relaxGraph(90, null);
      if (graphView.fitted) fitGraph(); else renderGraph(nodes, edges);
      for (const category of ['arena', 'work', 'observation', 'receipt']) text(`category-${category}-count`, scoped.nodes.filter(node => node.category === category).length);
      document.getElementById('graph-show-all').hidden = !graphView.neighborhood;
      renderGraphSelection(); renderGraphSearch();
    }

    function renderForceControls(edges) {
      const root = document.getElementById('graph-force-controls');
      const names = ['separation', ...new Set(edges.map(edge => edge.relationship))].sort((a, b) => a === 'separation' ? -1 : b === 'separation' ? 1 : a.localeCompare(b));
      const rows = new Map([...root.children].map(row => [row.dataset.force, row]));
      for (const name of names) {
        let row = rows.get(name);
        if (!row) {
          row = make('label', 'graph-force-control'); row.dataset.force = name;
          const label = make('span', '', name === 'separation' ? 'Node separation' : relationshipLabel(name)); label.title = name;
          const input = document.createElement('input'); input.type = 'range'; input.min = name === 'separation' ? '.5' : '0'; input.max = name === 'separation' ? '3' : '2.5'; input.step = '.25';
          input.setAttribute('aria-label', name === 'separation' ? 'Node separation strength' : `${name} attraction strength`);
          const output = document.createElement('output');
          input.addEventListener('input', () => {
            const value = Number(input.value); if (name === 'separation') graphView.repulsionStrength = value; else graphView.edgeStrengths.set(name, value);
            output.textContent = `${value.toFixed(2)}×`; graphView.pinnedKey = null; relaxGraph(55, null); renderGraph(graphView.nodes, graphView.edges);
          });
          row.append(label, input, output); root.append(row);
        }
        const input = row.querySelector('input'), output = row.querySelector('output'); const value = name === 'separation' ? graphView.repulsionStrength : graphEdgeStrength(name);
        if (document.activeElement !== input) input.value = String(value); output.textContent = `${value.toFixed(2)}×`;
      }
      for (const [name, row] of rows) if (!names.includes(name) && !row.contains(document.activeElement)) row.remove();
    }

    function nodeShape(kind, radius) {
      const r = radius;
      if (kind === 'Project') return `M ${-r} ${-r} H ${r} V ${r} H ${-r} Z`;
      if (kind === 'Issue') return `M 0 ${-r * 1.25} L ${r * 1.15} 0 L 0 ${r * 1.25} L ${-r * 1.15} 0 Z`;
      if (kind === 'Task') return `M ${-r} 0 L ${-r / 2} ${-r} H ${r / 2} L ${r} 0 L ${r / 2} ${r} H ${-r / 2} Z`;
      if (kind === 'Proposal') return `M 0 ${-r * 1.2} L ${r * 1.1} ${r} H ${-r * 1.1} Z`;
      return `M ${-r} 0 A ${r} ${r} 0 1 0 ${r} 0 A ${r} ${r} 0 1 0 ${-r} 0`;
    }

    function setSvg(element, attributes) {
      for (const [key, value] of Object.entries(attributes)) if (element.getAttribute(key) !== String(value)) element.setAttribute(key, String(value));
    }

    function ensureGraphScene(svg) {
      if (svg.querySelector('#scene-nodes')) return;
      svg.replaceChildren(); const defs = svgMake('defs');
      for (const [id, bright, dark] of [['arena', '#b9e6ff', '#235f9e'], ['work', '#a8ffe0', '#137954'], ['observation', '#ffe5ad', '#9a6d20'], ['receipt', '#e3d8ff', '#6951ad']]) {
        const gradient = svgMake('radialGradient', { id: `sphere-${id}`, cx: '30%', cy: '25%', r: '75%' }); gradient.append(svgMake('stop', { offset: '0%', 'stop-color': bright }), svgMake('stop', { offset: '100%', 'stop-color': dark })); defs.append(gradient);
      }
      const marker = svgMake('marker', { id: 'graph-arrow', viewBox: '0 0 10 10', refX: 9, refY: 5, markerWidth: 6, markerHeight: 6, orient: 'auto-start-reverse' }); marker.append(svgMake('path', { d: 'M 0 0 L 10 5 L 0 10 z', fill: '#8aa99a' })); defs.append(marker);
      svg.append(defs, svgMake('rect', { id: 'scene-hit', class: 'graph-scene-hit', x: 0, y: 0 }), svgMake('g', { id: 'scene-edges' }), svgMake('g', { id: 'scene-nodes' }), svgMake('g', { id: 'scene-labels', 'pointer-events': 'none' }), svgMake('text', { id: 'scene-empty', x: '50%', y: '50%', 'text-anchor': 'middle', class: 'graph-node-kind' }));
    }

    function renderGraph(nodes, edges) {
      const svg = document.getElementById('graph-svg'); ensureGraphScene(svg);
      setSvg(svg, { viewBox: `0 0 ${graphView.width} ${graphView.height}` }); setSvg(document.getElementById('scene-hit'), { width: graphView.width, height: graphView.height });
      graphView.nodes = nodes; graphView.edges = edges;
      text('graph-meta', state.snapshot ? `${nodes.length} of ${state.snapshot.graph_nodes.length} nodes · ${edges.length} connections in view` : 'Connect to load your graph');
      const empty = document.getElementById('scene-empty'); empty.textContent = nodes.length ? '' : !state.snapshot ? 'Connect to load the graph.' : graphView.arenaKey ? 'No reachable nodes match these filters.' : state.snapshot.graph_nodes.length ? 'No nodes match these filters.' : 'No graph nodes yet.';
      const positions = new Map([...graphCoordinates(nodes)].map(([key, point]) => [key, projectGraphPoint(point)]));
      const adjacent = neighborhoodKeys(nodes, edges, state.selectedGraphKey);
      const edgeLayer = document.getElementById('scene-edges'), nodeLayer = document.getElementById('scene-nodes'), labelLayer = document.getElementById('scene-labels');
      const existingNodes = new Map([...nodeLayer.children].map(el => [el.dataset.nodeKey, el]));
      const existingEdges = new Map([...edgeLayer.children].map(el => [el.dataset.edgeKey, el]));
      const edgeKeys = new Set(), candidates = [], occupied = [];
      for (const node of nodes) {
        const position = positions.get(node.key), r = position.radius; let group = existingNodes.get(node.key);
        if (!group) {
          group = svgMake('g', { tabindex: 0, role: 'button', 'data-node-key': node.key });
          group.append(svgMake('title'), svgMake('circle', { class: 'graph-node-hit', cx: 0, cy: 0 }), svgMake('path', { class: 'graph-sphere' }), svgMake('circle', { class: 'graph-selection-ring', cx: 0, cy: 0 }));
          nodeLayer.append(group);
        }
        const selected = node.key === state.selectedGraphKey, nearby = adjacent.has(node.key), count = edges.filter(edge => edge.source === node.key || edge.target === node.key).length;
        setSvg(group, { class: `graph-node ${node.category}${selected ? ' selected' : ''}`, transform: `translate(${position.x} ${position.y})`, opacity: adjacent.size && !nearby ? '.32' : '1', 'aria-pressed': selected, 'aria-label': `${node.kind}: ${node.title}, ${node.status}, ${count} visible connections. Press Enter to read; arrow keys move the node.` });
        group.querySelector('title').textContent = `${node.kind}: ${node.title} · ${node.status}`;
        setSvg(group.querySelector('.graph-node-hit'), { r: Math.max(16, r + 5) });
        setSvg(group.querySelector('.graph-sphere'), { d: nodeShape(node.kind, r), fill: `url(#sphere-${node.category})` });
        setSvg(group.querySelector('.graph-selection-ring'), { r: r + 6, visibility: selected ? 'visible' : 'hidden' });
        occupied.push({ x: position.x - r - 2, y: position.y - r - 2, width: (r + 2) * 2, height: (r + 2) * 2 });
        if (graphView.labels !== 'none' && (selected || nearby || graphView.labels === 'all')) {
          const title = node.title.length > 36 ? `${node.title.slice(0, 35)}…` : node.title, width = title.length * 7.5 + 8;
          const candidate = { key: `node:${node.key}`, text: title, className: 'graph-node-label', width, height: 19, priority: selected ? 0 : 1 };
          candidates.push({ ...candidate, x: position.x + r + 9, y: position.y - 9 }, { ...candidate, x: position.x - width - r - 9, y: position.y - 9 }, { ...candidate, x: position.x - width / 2, y: position.y - r - 26 }, { ...candidate, x: position.x - width / 2, y: position.y + r + 9 });
        }
      }
      for (const [key, element] of existingNodes) if (!positions.has(key)) { if (element.contains(document.activeElement)) svg.focus(); element.remove(); }
      for (const edge of edges) {
        const source = positions.get(edge.source), target = positions.get(edge.target); if (!source || !target) continue;
        const key = `${edge.source}|${edge.relationship}|${edge.target}`; edgeKeys.add(key);
        let path = existingEdges.get(key); if (!path) { path = svgMake('path', { class: 'graph-edge', 'data-edge-key': key }); edgeLayer.append(path); }
        const dx = target.x - source.x, dy = target.y - source.y, distance = Math.max(1, Math.hypot(dx, dy)), ux = dx / distance, uy = dy / distance;
        const sx = source.x + ux * source.radius, sy = source.y + uy * source.radius, tx = target.x - ux * (target.radius + 3), ty = target.y - uy * (target.radius + 3);
        const connected = edge.source === state.selectedGraphKey || edge.target === state.selectedGraphKey;
        setSvg(path, { d: `M ${sx} ${sy} L ${tx} ${ty}`, 'stroke-width': connected ? 2 : 1, stroke: connected ? '#9dc9b7' : '#527568', opacity: adjacent.size && !connected ? .14 : .8 });
        if (graphView.labels === 'all' || (graphView.labels === 'selected' && connected)) {
          const label = relationshipLabel(edge.relationship), width = label.length * 6.8 + 8;
          candidates.push({ key: `edge:${key}`, text: label, className: 'graph-edge-label', x: (sx + tx) / 2 - width / 2, y: (sy + ty) / 2 - 23, width, height: 18, priority: 2 });
        }
      }
      for (const [key, element] of existingEdges) if (!edgeKeys.has(key)) element.remove();
      const labels = chooseGraphLabels(candidates.sort((a, b) => a.priority - b.priority), occupied, graphView.width, graphView.height);
      const nextLabels = svgMake('g');
      for (const label of labels) { const el = svgMake('text', { class: label.className, x: label.x + 4, y: label.y + 14, 'text-anchor': 'start', 'data-ui-key': label.key }); el.textContent = label.text; nextLabels.append(el); }
      reconcileChildren(labelLayer, nextLabels);
    }

    function renderGraphSearch() {
      const input = document.getElementById('graph-search'), root = document.getElementById('graph-search-results'), query = input.value.trim().toLowerCase();
      root.hidden = !query; if (!query) return;
      const scoped = filterGraphByArena(state.snapshot?.graph_nodes || [], state.snapshot?.graph_edges || [], graphView.arenaKey);
      const nodes = scoped.nodes.filter(node => `${node.title} ${node.id}`.toLowerCase().includes(query));
      const desired = make('div'); desired.append(make('p', 'reader-note', `${nodes.length} matches ${graphView.arenaKey ? 'reachable from the Arena filter' : 'in the loaded graph'}`));
      for (const node of nodes.slice(0, 30)) { const button = make('button', 'search-result', node.title); button.dataset.nodeKey = node.key; button.dataset.uiKey = node.key; button.append(make('small', '', `${node.kind} · ${node.status}${graphView.visibleCategories.has(node.category) ? '' : ' · hidden category'}`)); desired.append(button); }
      if (nodes.length > 30) desired.append(make('p', 'reader-note', 'Showing 30 results. Refine your search for more.'));
      reconcileChildren(root, desired);
    }

    function focusSelectedNode(neighborhood = false) {
      document.getElementById('topology').classList.remove('reading-view'); text('reader-mode', 'Reading view');
      const node = detailState.node; if (!node || !(state.snapshot?.graph_nodes || []).some(item => item.key === node.key)) return;
      if (graphView.arenaKey && !graphView.arenaNodes.some(item => item.key === node.key)) graphView.arenaKey = '';
      graphView.neighborhood = neighborhood ? node.key : null; graphView.visibleCategories.add(node.category);
      document.querySelector(`[data-graph-category="${node.category}"]`).checked = true; updateGraphVisibility();
      if (neighborhood) fitGraph();
      else { setGraphZoom(Math.max(1.2, graphView.zoom)); const point = projectGraphPoint(graphView.nodePositions.get(node.key)); graphView.panX += graphView.width / 2 - point.x; graphView.panY += graphView.height / 2 - point.y; renderGraph(graphView.nodes, graphView.edges); }
    }

    function pauseGraphOrbit() {
      graphView.orbit = false; if (graphView.frame) window.cancelAnimationFrame(graphView.frame); graphView.frame = null; graphView.lastFrame = 0;
      const button = document.getElementById('graph-orbit'); button.setAttribute('aria-pressed', 'false'); button.textContent = 'Orbit: off';
    }

    function graphOrbitFrame(timestamp) {
      if (!graphView.orbit) return; if (graphView.lastFrame) graphView.yaw += Math.min(40, timestamp - graphView.lastFrame) * .00022; graphView.lastFrame = timestamp;
      renderGraph(graphView.nodes, graphView.edges); graphView.frame = window.requestAnimationFrame(graphOrbitFrame);
    }

    function toggleGraphOrbit() {
      if (graphView.orbit) { pauseGraphOrbit(); return; }
      graphView.fitted = false; graphView.orbit = true; graphView.lastFrame = 0; const button = document.getElementById('graph-orbit'); button.setAttribute('aria-pressed', 'true'); button.textContent = 'Orbit: on'; graphView.frame = window.requestAnimationFrame(graphOrbitFrame);
    }

    function resetGraphView() {
      pauseGraphOrbit(); graphView.yaw = -.38; graphView.pitch = .22; graphView.zoom = 1; graphView.panX = 0; graphView.panY = 0; graphView.fitted = false; syncZoomControls(); graphView.pinnedKey = null; graphView.nodePositions.clear(); graphView.nodeVelocities.clear(); syncGraphPhysics(graphView.nodes); relaxGraph(90, null); renderGraph(graphView.nodes, graphView.edges);
    }

    function render(snapshot, observations) {
      state.snapshot = snapshot;
      text('total-work', snapshot.total_work); text('active-initiatives', snapshot.active_initiatives); text('observation-count', snapshot.observation_count); text('pending-receipts', snapshot.pending_receipts);
      text('receipt-note', `${snapshot.receipt_count} receipts in the local outbox`); const age = relativeTime(snapshot.generated_at); text('generated-at', age === 'now' ? 'updated just now' : `updated ${age} ago`);
      updateGraphVisibility(); renderPipeline(snapshot.observation_by_status); renderBars(snapshot.work_by_kind); renderActivity(snapshot.recent_activity); renderObservations(observations);
      document.getElementById('connection-dot').className = `dot ${snapshot.storage.ready ? 'ready' : 'error'}`;
      text('connection-label', snapshot.storage.ready ? 'Graph ready' : 'Graph degraded');
      text('status-line', `${snapshot.storage.detail} · Updated ${new Date(snapshot.generated_at).toLocaleTimeString()} · Read-only`);
    }

    function synchronizeCredential() {
      const credential = tokenInput.value.trim(); if (credential === detailState.credential) return;
      state.refreshController?.abort(); state.refreshController = null;
      detailState.credential = credential; state.authEpoch += 1; state.refreshPromise = null; state.refreshQueued = false; state.pendingSnapshot = null; state.lastSuccess = null;
      resetDetailState(); state.snapshot = null; state.observations = []; state.selectedGraphKey = null; graphView.neighborhood = null; graphView.arenaKey = '';
      graphView.nodePositions.clear(); graphView.nodeVelocities.clear(); updateGraphVisibility();
      ['total-work', 'active-initiatives', 'observation-count', 'pending-receipts'].forEach(id => text(id, '—'));
      renderActivity([]); renderObservations([]); renderPipeline({}); renderBars({});
    }

    function applySnapshot(snapshot, observations) {
      if (graphView.pointers.size || state.controlDragging) { state.pendingSnapshot = { snapshot, observations }; return; }
      state.pendingSnapshot = null; state.snapshot = snapshot; state.observations = observations; state.lastSuccess = snapshot.generated_at;
      render(snapshot, observations); loadSelectedDetail();
    }

    function flushPendingSnapshot() {
      if (!state.pendingSnapshot || graphView.pointers.size || state.controlDragging) return;
      const { snapshot, observations } = state.pendingSnapshot; applySnapshot(snapshot, observations);
    }

    async function refresh() {
      synchronizeCredential();
      if (!tokenInput.value.trim()) { text('status-line', 'Enter a scoped read credential to inspect the graph.'); return; }
      if (state.refreshPromise) { state.refreshQueued = true; return state.refreshPromise; }
      const epoch = state.authEpoch, controller = new AbortController(); state.refreshController = controller;
      if (!state.snapshot) text('status-line', 'Loading graph…');
      const request = (async () => {
        try {
          const [snapshot, observations] = await Promise.all([getJson('/monitor/snapshot', { signal: controller.signal }), getJson('/initiative-observations?descending=true&limit=100', { signal: controller.signal })]);
          if (epoch !== state.authEpoch) return;
          applySnapshot(snapshot, observations.items);
        } catch (error) {
          controller.abort();
          if (epoch !== state.authEpoch) return;
          state.refreshQueued = false; state.pendingSnapshot = null;
          document.getElementById('connection-dot').className = 'dot error';
          text('connection-label', error.status === 401 || error.status === 403 ? 'Access denied' : 'Connection interrupted');
          text('status-line', `${state.snapshot ? `Showing saved snapshot from ${new Date(state.lastSuccess).toLocaleTimeString()}. ` : ''}${error.status === 401 || error.status === 403 ? 'Reconnect with a valid read credential.' : 'Refresh failed. Retrying on the next update.'}`);
        } finally {
          if (epoch === state.authEpoch) { state.refreshPromise = null; state.refreshController = null; if (state.refreshQueued) { state.refreshQueued = false; queueMicrotask(refresh); } }
        }
      })();
      state.refreshPromise = request; return request;
    }

    function graphDragMoved(drag, x, y) {
      return Math.hypot(x - drag.startX, y - drag.startY) >= 5;
    }

    function schedule() {
      if (state.timer) window.clearInterval(state.timer); const seconds = Number(document.getElementById('refresh-rate').value);
      if (seconds > 0) state.timer = window.setInterval(refresh, seconds * 1000);
    }
    const graphSvg = document.getElementById('graph-svg');
    const graphClientPoint = (x, y) => {
      const matrix = graphSvg.getScreenCTM(); return matrix ? new DOMPoint(x, y).matrixTransform(matrix.inverse()) : { x, y };
    };
    const pinchPosition = () => {
      const [first, second] = [...graphView.pointers.values()];
      return { distance: Math.max(1, Math.hypot(first.x - second.x, first.y - second.y)), midpoint: graphClientPoint((first.x + second.x) / 2, (first.y + second.y) / 2) };
    };
    graphSvg.addEventListener('pointerdown', event => {
      if (event.button !== 0 || graphView.pointers.size >= 2) return;
      graphView.pointers.set(event.pointerId, { x: event.clientX, y: event.clientY }); graphSvg.setPointerCapture(event.pointerId);
      const nodeTarget = event.target.closest('.graph-node'); pauseGraphOrbit(); event.preventDefault();
      if (graphView.pointers.size === 2) { graphView.pinch = pinchPosition(); graphView.drag = null; graphSvg.classList.remove('dragging', 'node-dragging'); return; }
      const mode = event.shiftKey ? 'pan' : nodeTarget ? 'node' : 'scene';
      graphView.drag = { mode, key: nodeTarget?.dataset.nodeKey || null, x: event.clientX, y: event.clientY, startX: event.clientX, startY: event.clientY, moved: false, pointerId: event.pointerId };
      graphSvg.classList.add(mode === 'node' ? 'node-dragging' : 'dragging');
    });
    graphSvg.addEventListener('pointermove', event => {
      if (!graphView.pointers.has(event.pointerId)) return;
      graphView.pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
      if (graphView.pinch && graphView.pointers.size === 2) {
        const next = pinchPosition(); const previous = graphView.pinch;
        setGraphZoom(graphView.zoom * next.distance / previous.distance, previous.midpoint);
        graphView.panX += next.midpoint.x - previous.midpoint.x; graphView.panY += next.midpoint.y - previous.midpoint.y;
        graphView.pinch = next; renderGraph(graphView.nodes, graphView.edges); return;
      }
      if (!graphView.drag || graphView.drag.pointerId !== event.pointerId) return;
      if (!graphView.drag.moved && !graphDragMoved(graphView.drag, event.clientX, event.clientY)) return;
      graphView.drag.moved = true;
      const deltaX = event.clientX - graphView.drag.x; const deltaY = event.clientY - graphView.drag.y;
      graphView.fitted = false;
      if (graphView.drag.mode === 'node') { const point = graphCoordinates(graphView.nodes).get(graphView.drag.key); const delta = graphClientDelta(deltaX, deltaY); graphView.pinnedKey = graphView.drag.key; if (point) { moveGraphNode(graphView.drag.key, delta.x, delta.y, projectGraphPoint(point).scale); relaxGraph(3, graphView.drag.key); } }
      else if (graphView.drag.mode === 'pan') { const delta = graphClientDelta(deltaX, deltaY); graphView.panX += delta.x; graphView.panY += delta.y; }
      else { graphView.yaw += deltaX * .008; graphView.pitch = Math.max(-1.15, Math.min(1.15, graphView.pitch + deltaY * .006)); }
      graphView.drag.x = event.clientX; graphView.drag.y = event.clientY; renderGraph(graphView.nodes, graphView.edges);
    });
    const endGraphDrag = event => {
      if (!graphView.pointers.has(event.pointerId) && graphView.drag?.pointerId !== event.pointerId) return;
      graphView.pointers.delete(event.pointerId); if (graphView.pinch) graphView.pinch = null;
      if (graphSvg.hasPointerCapture(event.pointerId)) graphSvg.releasePointerCapture(event.pointerId);
      if (!graphView.drag || graphView.drag.pointerId !== event.pointerId) { flushPendingSnapshot(); return; } const completed = graphView.drag; graphView.drag = null; graphSvg.classList.remove('dragging', 'node-dragging'); if (graphSvg.hasPointerCapture(event.pointerId)) graphSvg.releasePointerCapture(event.pointerId);
      if (completed.mode === 'node' && event.type === 'pointerup') { if (completed.moved) relaxGraph(30, completed.key); selectGraphNode(graphView.nodes.find(node => node.key === completed.key)); }
      flushPendingSnapshot();
    };
    graphSvg.addEventListener('pointerup', endGraphDrag); graphSvg.addEventListener('pointercancel', endGraphDrag); graphSvg.addEventListener('lostpointercapture', endGraphDrag);
    graphSvg.addEventListener('wheel', event => {
      event.preventDefault(); if (graphView.pointers.size) return; pauseGraphOrbit();
      const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? graphView.height : 1;
      const delta = Math.max(-250, Math.min(250, event.deltaY * unit));
      setGraphZoom(graphView.zoom * Math.exp(-delta * .0025), graphClientPoint(event.clientX, event.clientY));
    }, { passive: false });
    graphSvg.addEventListener('keydown', event => {
      if (event.ctrlKey || event.metaKey || event.altKey) return;
      const group = event.target.closest('.graph-node');
      if (group) {
        const node = graphView.nodes.find(item => item.key === group.dataset.nodeKey);
        if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); selectGraphNode(node); return; }
        const arrows = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };
        if (node && arrows[event.key]) { event.preventDefault(); pauseGraphOrbit(); const distance = event.shiftKey ? 24 : 8; const delta = graphClientDelta(arrows[event.key][0] * distance, arrows[event.key][1] * distance); graphView.pinnedKey = node.key; moveGraphNode(node.key, delta.x, delta.y, projectGraphPoint(graphView.nodePositions.get(node.key)).scale); relaxGraph(24, node.key); selectGraphNode(node); return; }
      }
      if (event.key === '+' || event.key === '=') { event.preventDefault(); setGraphZoom(graphView.zoom * 1.2); }
      else if (event.key === '-') { event.preventDefault(); setGraphZoom(graphView.zoom / 1.2); }
      else if (event.key === '0') { event.preventDefault(); graphView.panX = 0; graphView.panY = 0; setGraphZoom(1); }
      else if (event.key.toLowerCase() === 'f') { event.preventDefault(); fitGraph(); }
      if (['+', '=', '-', '0', 'f'].includes(event.key.toLowerCase())) graphSvg.focus();
    });
    document.getElementById('graph-zoom-in').addEventListener('click', () => setGraphZoom(graphView.zoom * 1.2));
    document.getElementById('graph-zoom-out').addEventListener('click', () => setGraphZoom(graphView.zoom / 1.2));
    document.getElementById('graph-zoom').addEventListener('input', event => setGraphZoom(Number(event.target.value) / 100));
    document.getElementById('graph-fit').addEventListener('click', fitGraph);
    document.getElementById('graph-arena').addEventListener('change', event => setGraphArena(event.target.value));
    document.querySelectorAll('[data-graph-category]').forEach(input => {
      input.addEventListener('change', () => {
        if (input.checked) graphView.visibleCategories.add(input.dataset.graphCategory); else graphView.visibleCategories.delete(input.dataset.graphCategory);
        pauseGraphOrbit(); updateGraphVisibility();
      });
    });
    document.getElementById('graph-expand').addEventListener('click', toggleGraphExpanded);
    document.addEventListener('keydown', event => { if (event.key === 'Escape' && graphView.expanded) { event.preventDefault(); toggleGraphExpanded(); } });
    const graphResize = document.getElementById('graph-resize'); let resizeDrag = null;
    graphResize.addEventListener('pointerdown', event => {
      if (event.button !== 0 || resizeDrag) return; event.preventDefault();
      resizeDrag = { pointerId: event.pointerId, y: event.clientY, height: Number(graphResize.getAttribute('aria-valuenow')) };
      graphResize.setPointerCapture(event.pointerId); graphResize.focus();
    });
    graphResize.addEventListener('pointermove', event => { if (resizeDrag?.pointerId === event.pointerId) setGraphHeight(resizeDrag.height + event.clientY - resizeDrag.y); });
    const endGraphResize = event => { if (resizeDrag?.pointerId !== event.pointerId) return; resizeDrag = null; if (graphResize.hasPointerCapture(event.pointerId)) graphResize.releasePointerCapture(event.pointerId); };
    graphResize.addEventListener('pointerup', endGraphResize); graphResize.addEventListener('pointercancel', endGraphResize); graphResize.addEventListener('lostpointercapture', endGraphResize);
    graphResize.addEventListener('keydown', event => {
      const current = Number(graphResize.getAttribute('aria-valuenow')); const step = event.shiftKey ? 100 : 20;
      const value = { ArrowUp: current - step, ArrowDown: current + step, Home: 280, End: 1200 }[event.key];
      if (value !== undefined) { event.preventDefault(); setGraphHeight(value); }
    });
    document.getElementById('graph-search').addEventListener('input', renderGraphSearch);
    document.getElementById('graph-search').addEventListener('keydown', event => { if (event.key === 'ArrowDown') { event.preventDefault(); document.querySelector('.search-result')?.focus(); } if (event.key === 'Escape') document.getElementById('graph-search-results').hidden = true; });
    document.getElementById('graph-search-results').addEventListener('click', event => { const button = event.target.closest('[data-node-key]'); if (!button) return; selectGraphNode(state.snapshot.graph_nodes.find(node => node.key === button.dataset.nodeKey)); document.getElementById('graph-search-results').hidden = true; document.getElementById('graph-search').value = ''; document.getElementById('reader-toggle').focus(); });
    document.getElementById('graph-labels').addEventListener('change', event => { graphView.labels = event.target.value; renderGraph(graphView.nodes, graphView.edges); });
    document.getElementById('graph-show-all').addEventListener('click', () => { graphView.neighborhood = null; updateGraphVisibility(); });
    document.getElementById('reader-content').addEventListener('toggle', event => { if (event.target.matches('details[data-relationship]') && event.target.open) loadRelationship(event.target.dataset.relationship); }, true);
    document.getElementById('reader-content').addEventListener('click', event => {
      const button = event.target.closest('[data-action]'); if (!button) return;
      const { action, relationship, id, kind, key } = button.dataset;
      if (action === 'focus' || action === 'neighborhood') focusSelectedNode(action === 'neighborhood');
      else if (action === 'arena-filter' && detailState.node?.category === 'arena') setGraphArena(detailState.node.key);
      else if (action === 'refresh-detail') loadSelectedDetail();
      else if (action === 'related') { const work = detailState.relations.get(relationship)?.items.find(item => item.id === id && item.kind === kind); if (work) selectGraphNode(workNode(work)); }
      else if (action === 'snapshot-node') selectGraphNode(state.snapshot.graph_nodes.find(node => node.key === key));
      else if (action === 'relationship-more') loadRelationship(relationship, true);
      else if (action === 'relationship-retry') { detailState.relations.delete(relationship); loadRelationship(relationship); }
      else if (action === 'support' || action === 'support-more') loadSupporting(action === 'support-more');
      else if (action === 'document') loadDocument(id);
    });
    document.getElementById('reader-toggle').addEventListener('click', () => { const collapsed = document.getElementById('graph-details').classList.toggle('collapsed'); if (collapsed) { document.getElementById('topology').classList.remove('reading-view'); text('reader-mode', 'Reading view'); } document.getElementById('reader-toggle').setAttribute('aria-expanded', String(!collapsed)); text('reader-toggle', collapsed ? 'Show details' : 'Hide details'); });
    document.getElementById('reader-mode').addEventListener('click', () => { const reading = document.getElementById('topology').classList.toggle('reading-view'); document.getElementById('graph-details').classList.remove('collapsed'); document.getElementById('reader-toggle').setAttribute('aria-expanded', 'true'); text('reader-toggle', 'Hide details'); text('reader-mode', reading ? 'Back to graph' : 'Reading view'); });
    const readerResize = document.getElementById('reader-resize'); let readerDrag = null;
    const setReaderWidth = width => { const value = Math.max(280, Math.min(600, width)); document.getElementById('topology').style.setProperty('--reader-width', `${value}px`); readerResize.setAttribute('aria-valuenow', String(value)); };
    readerResize.addEventListener('pointerdown', event => { if (event.button !== 0) return; event.preventDefault(); readerDrag = { id: event.pointerId, x: event.clientX, width: Number(readerResize.getAttribute('aria-valuenow')) }; readerResize.setPointerCapture(event.pointerId); });
    readerResize.addEventListener('pointermove', event => { if (readerDrag?.id === event.pointerId) setReaderWidth(readerDrag.width + readerDrag.x - event.clientX); });
    const endReaderResize = event => { if (readerDrag?.id !== event.pointerId) return; readerDrag = null; if (readerResize.hasPointerCapture(event.pointerId)) readerResize.releasePointerCapture(event.pointerId); };
    ['pointerup', 'pointercancel', 'lostpointercapture'].forEach(name => readerResize.addEventListener(name, endReaderResize));
    readerResize.addEventListener('keydown', event => { const width = Number(readerResize.getAttribute('aria-valuenow')); const next = { ArrowLeft: width + 20, ArrowRight: width - 20, Home: 280, End: 600 }[event.key]; if (next !== undefined) { event.preventDefault(); setReaderWidth(next); } });
    document.addEventListener('pointerdown', event => { if (event.target.matches('input[type="range"], [role="separator"]')) state.controlDragging = true; });
    ['pointerup', 'pointercancel'].forEach(name => document.addEventListener(name, () => { state.controlDragging = false; flushPendingSnapshot(); }));
    document.getElementById('graph-details').classList.add('collapsed'); text('reader-toggle', 'Show details'); document.getElementById('reader-toggle').setAttribute('aria-expanded', 'false');
    new ResizeObserver(resizeGraphViewport).observe(graphSvg); resizeGraphViewport(); syncZoomControls();
    document.getElementById('graph-reset').addEventListener('click', resetGraphView);
    document.getElementById('graph-settle').addEventListener('click', settleGraph); document.getElementById('graph-force-reset').addEventListener('click', resetForceStrengths);
    const orbitButton = document.getElementById('graph-orbit'); orbitButton.addEventListener('click', toggleGraphOrbit);
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) { orbitButton.disabled = true; orbitButton.textContent = 'Orbit: reduced motion'; orbitButton.title = 'Continuous orbit follows your reduced-motion preference'; }
    document.getElementById('auth-form').addEventListener('submit', event => { event.preventDefault(); sessionStorage.setItem('devgraph-monitor-token', tokenInput.value.trim()); schedule(); refresh(); });
    document.getElementById('refresh-rate').addEventListener('change', schedule);
    schedule(); if (tokenInput.value) refresh();
  </script>
</body>
</html>'''
