/*
 * Success Metrics chart renderer.
 *
 * Hand-drawn SVG charts driven by the JSON specs build.py exports from
 * analysis.py. No charting library: each chart type is one entry in TYPES with
 * a draw(ctx) that returns the drawing's height, and a table(spec) that returns
 * the chart's data as a table (every chart has a table view).
 *
 * Motion is tied to scroll position: as a chart rises up the screen its bars
 * grow, lines draw, pies sweep, and labels fade in after their marks
 * (staggered by --i); scrolling back up rewinds it, and stopping mid-scroll
 * holds it part-way. Reduced motion shows the final state. Charts are laid out
 * for the container's width, then drawn up to SCALE times larger (text and marks
 * alike) where there is room, and redraw at their current progress on resize.
 *
 * window.successCharts.mount(el, spec, { play: true }) draws a chart anywhere,
 * for example in Advisor Ann's chat, where it plays in once instead of
 * following the scrollbar.
 */
(() => {
  const NS = 'http://www.w3.org/2000/svg';
  const DATA = JSON.parse(document.getElementById('chart-data')?.textContent || '{}');   // the chat page has none
  // the Python behind each chart (a notebook's setup + chart cells, or the analysis.py call), for "Copy code"
  const CODE = (() => { try { return JSON.parse(document.getElementById('chart-code')?.textContent || '{}'); } catch { return {}; } })();
  const color = i => `var(--s${(i % 4) + 1})`;

  const sign = v => (v < 0 ? '−' : '');
  const FMT = {
    usd: v => sign(v) + '$' + Math.round(Math.abs(v)).toLocaleString('en-US'),
    usdK: v => sign(v) + '$' + Math.round(Math.abs(v) / 1000) + 'K',
    usdK1: v => sign(v) + '$' + (Math.abs(v) / 1000).toFixed(1) + 'K',
    signedUsdK: v => (v < 0 ? '−' : v > 0 ? '+' : '') + '$' + Math.round(Math.abs(v) / 1000) + 'K',
    signedUsdK1: v => (v < 0 ? '−' : '+') + '$' + (Math.abs(v) / 1000).toFixed(1) + 'K',
    pct0: v => Math.round(v * 100) + '%',          // a fraction (0.44 -> 44%)
    pct1: v => (v * 100).toFixed(1) + '%',
    pctRaw0: v => Math.round(v) + '%',             // already a percent (44 -> 44%)
    pctRaw1: v => v.toFixed(1) + '%',
    pts1: v => (v < 0 ? '−' : '+') + Math.abs(v).toFixed(1) + ' pts',   // percentage-point changes
    pts2: v => (v < 0 ? '−' : '+') + Math.abs(v).toFixed(2) + ' pts',
    signed0: v => (Math.abs(v) < 1e-9 ? '0' : (v < 0 ? '−' : '+') + +Math.abs(v).toFixed(1)),   // axis ticks for changes
    num1: v => (+v).toFixed(1),
    mo1: v => (+v).toFixed(1) + ' mo',
    yr1: v => (+v).toFixed(1) + ' yr',
    int: v => Math.round(v).toLocaleString('en-US'),
    num: v => String(v),
  };

  /* ── helpers ─────────────────────────────────────────────────────────── */

  const mk = (tag, attrs, parent) => {
    const n = document.createElementNS(NS, tag);
    for (const k in attrs) if (attrs[k] != null) n.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(n);
    return n;
  };
  const txt = (parent, x, y, s, cls = '', anchor = 'start', i) => {
    const t = mk('text', { x, y, class: cls, 'text-anchor': anchor, style: i != null ? `--i:${i}` : null }, parent);
    t.textContent = s;
    return t;
  };
  const html = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  };
  const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);

  const measure = document.createElement('canvas').getContext('2d');
  const FONT = { sans: '12px "DM Sans", sans-serif', sansB: '500 13px "DM Sans", sans-serif', mono: '11px "JetBrains Mono", monospace' };
  const textW = (s, font = FONT.sans) => { measure.font = font; return measure.measureText(s).width; };

  const lin = (d0, d1, r0, r1) => v => r0 + ((v - d0) / (d1 - d0 || 1)) * (r1 - r0);

  /** A pointer event's position in the chart's own coordinates (the drawing may be scaled up on screen). */
  const local = (svg, e) => {
    const r = svg.getBoundingClientRect(), vb = svg.viewBox.baseVal;
    return [(e.clientX - r.left) * vb.width / r.width, (e.clientY - r.top) * vb.height / r.height];
  };

  /** Rounded tick values covering [min, max]. */
  function niceTicks(min, max, count = 5) {
    const span = max - min || Math.abs(max) || 1;
    const raw = span / count, mag = Math.pow(10, Math.floor(Math.log10(raw))), norm = raw / mag;
    const step = (norm >= 5 ? 10 : norm >= 2 ? 5 : norm >= 1 ? 2 : 1) * mag;
    const out = [];
    for (let v = Math.floor(min / step) * step; v <= max + step * 0.999; v += step) out.push(+v.toFixed(10));
    return out;
  }

  /** A bar with its data end rounded and its baseline end square. dir: right | left | up. */
  function bar(x, y, w, h, dir = 'right', r = 4) {
    if (w <= 0.5 || h <= 0.5) return '';
    if (dir === 'down') {
      const rr = Math.min(r, w / 2, h);
      return `M${x},${y} V${y + h - rr} a${rr},${rr} 0 0 0 ${rr},${rr} H${x + w - rr} a${rr},${rr} 0 0 0 ${rr},${-rr} V${y} Z`;
    }
    if (dir === 'up') {
      const rr = Math.min(r, w / 2, h);
      return `M${x},${y + h} V${y + rr} a${rr},${rr} 0 0 1 ${rr},${-rr} H${x + w - rr} a${rr},${rr} 0 0 1 ${rr},${rr} V${y + h} Z`;
    }
    const rr = Math.min(r, w, h / 2);
    if (dir === 'left') return `M${x + w},${y} H${x + rr} a${rr},${rr} 0 0 0 ${-rr},${rr} V${y + h - rr} a${rr},${rr} 0 0 0 ${rr},${rr} H${x + w} Z`;
    return `M${x},${y} H${x + w - rr} a${rr},${rr} 0 0 1 ${rr},${rr} V${y + h - rr} a${rr},${rr} 0 0 1 ${-rr},${rr} H${x} Z`;
  }

  /** Horizontal gridlines with left-hand tick labels. */
  function yGrid(g, ticks, y, x0, x1, fmt) {
    ticks.forEach((t, k) => {
      mk('line', { x1: x0, x2: x1, y1: y(t), y2: y(t), class: k === 0 ? 'base' : 'grid' }, g);
      txt(g, x0 - 8, y(t) + 4, fmt(t), '', 'end');
    });
  }

  /** Crosshair + tooltip layer over a continuous x axis. */
  function crosshair(ctx, x0, x1, y0, y1, onMove) {
    const { svg, tip } = ctx;
    const line = mk('line', { y1: y0, y2: y1, class: 'xhair', visibility: 'hidden' }, svg);
    const dots = mk('g', { visibility: 'hidden' }, svg);
    const hit = mk('rect', { x: x0, y: y0, width: x1 - x0, height: y1 - y0, fill: 'transparent' }, svg);
    hit.addEventListener('pointermove', e => {
      const px = Math.max(x0, Math.min(x1, local(svg, e)[0]));
      const res = onMove(px);
      if (!res) return;
      line.setAttribute('x1', res.x); line.setAttribute('x2', res.x); line.setAttribute('visibility', 'visible');
      dots.replaceChildren();
      res.points.forEach(p => mk('circle', { cx: res.x, cy: p.y, r: 4.5, class: 'xdot', style: `fill:${p.color}` }, dots));
      dots.setAttribute('visibility', 'visible');
      tip.show(res.html, res.x, Math.min(...res.points.map(p => p.y)));
    });
    hit.addEventListener('pointerleave', () => {
      line.setAttribute('visibility', 'hidden'); dots.setAttribute('visibility', 'hidden'); tip.hide();
    });
  }

  /** Transparent hover target that shows a tooltip anchored at (ax, ay). */
  function hover(ctx, attrs, htmlText, ax, ay) {
    const r = mk('rect', { ...attrs, fill: 'transparent' }, ctx.svg);
    r.addEventListener('pointerenter', () => ctx.tip.show(htmlText, ax, ay));
    r.addEventListener('pointerleave', () => ctx.tip.hide());
    return r;
  }

  const table = (head, rows) =>
    `<table><thead><tr>${head.map((h, i) => `<th${i ? ' class="num"' : ''}>${esc(h)}</th>`).join('')}</tr></thead><tbody>${
      rows.map(r => `<tr>${r.map((c, i) => `<td${i ? ' class="num"' : ''}>${esc(c)}</td>`).join('')}</tr>`).join('')}</tbody></table>`;

  /* ── box: vertical box plots, optionally grouped by series ───────────── */

  function drawBoxes(ctx, spec) {
    const { W, g } = ctx, fmt = FMT[spec.yFmt || 'usdK'], showFliers = spec.fliers !== false;
    const boxes = spec.groups.flatMap(gr => gr.boxes);
    const all = boxes.flatMap(b => [b.lo, b.hi, ...(showFliers ? b.fliers : [])]).concat(spec.zero ? [0] : []);
    const ticks = niceTicks(Math.min(...all), Math.max(...all), 5);
    const padL = Math.max(...ticks.map(t => textW(fmt(t), FONT.mono))) + 16;
    const top = 14, plotH = 300, H = top + plotH + (spec.xLabel ? 62 : 44);
    const x0 = padL, x1 = W - 8, yb = top + plotH;
    const y = lin(ticks[0], ticks[ticks.length - 1], yb, top);
    yGrid(g, ticks, y, x0, x1, fmt);
    if (spec.zero) mk('line', { x1: x0, x2: x1, y1: y(0), y2: y(0), class: 'ref' }, g);   // 0 = same as similar grads

    const gw = (x1 - x0) / spec.groups.length;
    let i = 0;
    spec.groups.forEach((gr, gi) => {
      const k = gr.boxes.length, gx = x0 + gw * (gi + 0.5), slot = (gw * 0.8) / Math.max(k, 1), bw = Math.min(slot * 0.8, 96);
      if (!k) txt(g, gx, yb + 15, 'n=0', '', 'middle');   // an empty group keeps its slot
      gr.boxes.forEach((b, j) => {
        const cx = gx + (j - (k - 1) / 2) * slot, c = color(b.s);
        const whisk = mk('g', { class: 'fade', style: `--i:${i}`, stroke: c, 'stroke-width': 1.5 }, g);
        mk('line', { x1: cx, x2: cx, y1: y(b.lo), y2: y(b.q1) }, whisk);
        mk('line', { x1: cx, x2: cx, y1: y(b.q3), y2: y(b.hi) }, whisk);
        mk('line', { x1: cx - bw / 4, x2: cx + bw / 4, y1: y(b.lo), y2: y(b.lo) }, whisk);
        mk('line', { x1: cx - bw / 4, x2: cx + bw / 4, y1: y(b.hi), y2: y(b.hi) }, whisk);
        if (showFliers) {
          const fl = mk('g', { class: 'fade', style: `--i:${i}`, fill: c, 'fill-opacity': 0.4 }, g);
          b.fliers.forEach(v => mk('circle', { cx, cy: y(v), r: 2.5 }, fl));
        }
        mk('rect', { x: cx - bw / 2, y: y(b.q3), width: bw, height: Math.max(y(b.q1) - y(b.q3), 1), rx: 3, fill: c, class: 'grow-y mid', style: `--i:${i}` }, g);
        mk('line', { x1: cx - bw / 2, x2: cx + bw / 2, y1: y(b.med), y2: y(b.med), stroke: '#fff', 'stroke-width': 2, class: 'fade', style: `--i:${i}` }, g);
        if (spec.medianLabels !== false) txt(g, cx, y(b.med) - 4, fmt(b.med), 'in fade', 'middle', i);
        txt(g, cx, yb + 15, `n=${FMT.int(b.n)}`, '', 'middle');
        const series = spec.series ? `${spec.series[b.s]} · ` : '';
        hover(ctx, { x: cx - slot / 2, y: y(b.hi) - 4, width: slot, height: y(b.lo) - y(b.hi) + 8 },
          `<b>${esc(series + gr.label)}</b><br>Median ${fmt(b.med)}<br>Middle 50% ${fmt(b.q1)} to ${fmt(b.q3)}<br>n=${FMT.int(b.n)}`, cx, y(b.q3));
        i++;
      });
      txt(g, gx, yb + 34, gr.label, 'lab-strong', 'middle');
    });
    if (spec.xLabel) txt(g, (x0 + x1) / 2, yb + 54, spec.xLabel, 'lab', 'middle');
    return H;
  }

  const boxTable = spec => table(
    [spec.groupName || 'Group', ...(spec.series ? ['Series'] : []), 'n', 'Q1', 'Median', 'Q3'],
    spec.groups.flatMap(gr => gr.boxes.map(b => [gr.label, ...(spec.series ? [spec.series[b.s]] : []), FMT.int(b.n), FMT.usd(b.q1), FMT.usd(b.med), FMT.usd(b.q3)])));

  /* ── line-band: median lines with a shaded middle-50% band ───────────── */

  function drawLineBand(ctx, spec) {
    const { W, g } = ctx, fmt = FMT[spec.yFmt || 'usdK'];
    const vals = spec.series.flatMap(s => [...s.p25, ...s.p75].filter(v => v != null));
    const ticks = niceTicks(Math.min(...vals), Math.max(...vals), 5);
    const padL = Math.max(...ticks.map(t => textW(fmt(t), FONT.mono))) + 16;
    const endText = s => `${W < 560 ? s.short : s.label}  ${fmt(s.median[s.median.length - 1])}`;
    const padR = Math.max(...spec.series.map(s => textW(endText(s), FONT.sans))) + 22;
    const top = 12, plotH = 280, H = top + plotH + 48;
    const x0 = padL, x1 = W - padR, yb = top + plotH;
    const xs = spec.x, x = lin(xs[0], xs[xs.length - 1], x0, x1), y = lin(ticks[0], ticks[ticks.length - 1], yb, top);
    yGrid(g, ticks, y, x0, x1, fmt);
    xs.forEach(v => txt(g, x(v), yb + 16, v, '', 'middle'));
    if (spec.xLabel) txt(g, (x0 + x1) / 2, yb + 38, spec.xLabel, 'lab', 'middle');

    spec.series.forEach((s, si) => {
      const pts = xs.map((v, k) => [v, s.median[k], s.p25[k], s.p75[k]]).filter(p => p[1] != null);
      const band = pts.map(p => `${x(p[0])},${y(p[3])}`).join(' L') + ' L' + pts.slice().reverse().map(p => `${x(p[0])},${y(p[2])}`).join(' L');
      mk('path', { d: `M${band} Z`, fill: color(si), 'fill-opacity': 0.12, class: 'fade', style: `--i:${si}` }, g);
    });
    const ends = [];
    spec.series.forEach((s, si) => {
      const pts = xs.map((v, k) => [v, s.median[k]]).filter(p => p[1] != null);
      mk('path', { d: 'M' + pts.map(p => `${x(p[0])},${y(p[1])}`).join(' L'), pathLength: 1, fill: 'none', stroke: color(si), 'stroke-width': 2, 'stroke-linejoin': 'round', class: 'draw', style: `--i:${si}` }, g);
      pts.forEach((p, k) => mk('circle', { cx: x(p[0]), cy: y(p[1]), r: 3.5, fill: color(si), stroke: 'var(--surface)', 'stroke-width': 1.5, class: 'fade', style: `--i:${k + si}` }, g));
      const last = pts[pts.length - 1];
      ends.push({ s, y: y(last[1]), x: x(last[0]) });
    });
    ends.sort((a, b) => a.y - b.y).forEach((e, k, arr) => {   // nudge end labels apart
      if (k && e.y - arr[k - 1].y < 16) e.y = arr[k - 1].y + 16;
      txt(g, e.x + 10, e.y + 4, endText(e.s), 'lab fade', 'start', 8);
    });

    const step = (x1 - x0) / (xs.length - 1);
    crosshair(ctx, x0, x1, top, yb, px => {
      const k = Math.round((px - x0) / step), v = xs[k];
      const points = spec.series.map((s, si) => ({ y: y(s.median[k]), color: color(si), s })).filter(p => p.s.median[k] != null);
      return { x: x(v), points, html: `<b>${esc(spec.xName || 'x')} ${v}</b><br>` + points.map(p => `${esc(p.s.label)}: <b>${FMT.usd(p.s.median[k])}</b>`).join('<br>') };
    });
    return H;
  }

  const lineBandTable = spec => table(
    [spec.xName || 'x', ...spec.series.flatMap(s => [`${s.short} median`, `${s.short} middle 50%`])],
    spec.x.map((v, k) => [v, ...spec.series.flatMap(s => s.median[k] == null ? ['', ''] : [FMT.usd(s.median[k]), `${FMT.usdK(s.p25[k])}–${FMT.usdK(s.p75[k])}`])]));

  /* ── step: cumulative share over a continuous x (e.g. months to hire) ── */

  const cumAt = (s, v) => { let c = 0; for (let k = 0; k < s.x.length && s.x[k] <= v; k++) c = s.y[k]; return c; };

  function drawStep(ctx, spec) {
    const { W, g } = ctx;
    const xMax = Math.max(...spec.series.map(s => s.x[s.x.length - 1]));
    const xt = niceTicks(0, xMax, 6), yt = [0, 0.2, 0.4, 0.6, 0.8, 1];
    const padL = textW('100%', FONT.mono) + 16, top = 12, plotH = 280, H = top + plotH + 48;
    const x0 = padL, x1 = W - 12, yb = top + plotH;
    const x = lin(0, xt[xt.length - 1], x0, x1), y = lin(0, 1, yb, top);
    yGrid(g, yt, y, x0, x1, FMT.pct0);
    xt.forEach(v => txt(g, x(v), yb + 16, v, '', 'middle'));
    if (spec.xLabel) txt(g, (x0 + x1) / 2, yb + 38, spec.xLabel, 'lab', 'middle');
    spec.series.forEach((s, si) => {
      let d = `M${x(0)},${y(0)}`;
      s.x.forEach((v, k) => { d += ` H${x(v)} V${y(s.y[k])}`; });
      mk('path', { d, pathLength: 1, fill: 'none', stroke: color(si), 'stroke-width': 2, class: 'draw', style: `--i:${si}` }, g);
    });
    (spec.marks || []).forEach((m, k) => {   // labeled points along the curve
      mk('circle', { cx: x(m.x), cy: y(m.y), r: 5, fill: color(0), stroke: 'var(--surface)', 'stroke-width': 2, class: 'fade', style: `--i:${k + 1}` }, g);
      txt(g, x(m.x) + 10, y(m.y) + 14, m.label, 'lab fade', 'start', k + 1);
    });
    crosshair(ctx, x0, x1, top, yb, px => {
      const v = (px - x0) / (x1 - x0) * xt[xt.length - 1];
      const points = spec.series.map((s, si) => ({ y: y(cumAt(s, v)), color: color(si), s }));
      return { x: px, points, html: `<b>By month ${v.toFixed(1)}</b><br>` + points.map(p => `${esc(p.s.label)}: <b>${FMT.pct0(cumAt(p.s, v))}</b>`).join('<br>') };
    });
    return H;
  }

  const stepTable = spec => {
    const xMax = Math.ceil(Math.max(...spec.series.map(s => s.x[s.x.length - 1])));
    const months = Array.from({ length: xMax }, (_, k) => k + 1);
    return table([spec.xName || 'x', ...spec.series.map(s => s.label)], months.map(m => [m, ...spec.series.map(s => FMT.pct0(cumAt(s, m)))]));
  };

  /* ── pie: small multiples; each slice sweeps in clockwise from 12 o'clock ── */

  function drawPies(ctx, spec) {
    const { W, g } = ctx, n = spec.pies.length, cw = W / n;
    const R = Math.max(40, Math.min(cw / 2 - 20, 100)), H = 34 + 2 * R + 12;
    spec.pies.forEach((p, pi) => {
      const cx = cw * (pi + 0.5), cy = 34 + R, total = p.slices.reduce((a, s) => a + s.count, 0);
      txt(g, cx, 16, p.title, 'lab-strong', 'middle');
      const C = Math.PI * R;   // circumference of the r = R/2 stroke circle
      let start = 0;
      p.slices.forEach((s, si) => {
        const frac = s.count / total;
        mk('circle', { cx, cy, r: R / 2, fill: 'none', stroke: color(s.s), 'stroke-width': R, class: 'sweep',
          transform: `rotate(${-90 + start * 360} ${cx} ${cy})`, style: `--c:${C};--dash:${frac * C} ${C};--i:${pi}` }, g);
        const mid = (start + frac / 2) * 2 * Math.PI - Math.PI / 2;
        const lx = cx + 0.55 * R * Math.cos(mid), ly = cy + 0.55 * R * Math.sin(mid);
        txt(g, lx, ly - 2, FMT.usdK1(s.median), 'in fade', 'middle', pi + 3);
        txt(g, lx, ly + 12, `(${FMT.pct0(frac)})`, 'in fade', 'middle', pi + 3);
        start += frac;
      });
      let acc = 0;   // white separators between slices
      p.slices.forEach(s => {
        const a = acc * 2 * Math.PI - Math.PI / 2;
        mk('line', { x1: cx, y1: cy, x2: cx + R * Math.cos(a), y2: cy + R * Math.sin(a), stroke: 'var(--surface)', 'stroke-width': 2, class: 'fade', style: `--i:${pi + 2}` }, g);
        acc += s.count / total;
      });
      const hit = mk('circle', { cx, cy, r: R, fill: 'transparent' }, ctx.svg);
      hit.addEventListener('pointermove', e => {
        const [px, py] = local(ctx.svg, e);
        let ang = Math.atan2(py - cy, px - cx) + Math.PI / 2;
        if (ang < 0) ang += 2 * Math.PI;
        let f = ang / (2 * Math.PI), k = 0;
        while (k < p.slices.length - 1 && f > p.slices[k].count / total) { f -= p.slices[k].count / total; k++; }
        const s = p.slices[k];
        ctx.tip.show(`<b>${esc(spec.series[s.s])}</b> · ${esc(p.title)}<br>Median ${FMT.usd(s.median)}<br>${FMT.int(s.count)} alumni (${FMT.pct0(s.count / total)})`, cx, cy - R);
      });
      hit.addEventListener('pointerleave', () => ctx.tip.hide());
    });
    return H;
  }

  const pieTable = spec => table(['Group', 'Slice', 'Alumni', 'Share', 'Median'], spec.pies.flatMap(p => {
    const total = p.slices.reduce((a, s) => a + s.count, 0);
    return p.slices.map(s => [p.title, spec.series[s.s], FMT.int(s.count), FMT.pct0(s.count / total), FMT.usd(s.median)]);
  }));

  /* ── bar: vertical bars with value labels, n, and a reference line ───── */

  const wrapWords = (s, max) => {
    const out = [];
    let line = '';
    String(s).split(' ').forEach(w => { if (line && (line + ' ' + w).length > max) { out.push(line); line = w; } else line = line ? `${line} ${w}` : w; });
    if (line) out.push(line);
    return out;
  };

  function drawBars(ctx, spec) {
    const { W, g } = ctx, fmt = FMT[spec.valueFmt || 'pct1'];
    const x0 = 8, x1 = W - 8, slot = (x1 - x0) / spec.bars.length, bw = Math.min(slot * 0.6, 120);
    const lines = spec.bars.map(b => wrapWords(b.label, Math.max(5, Math.floor(slot / 7))));   // long names wrap under their bar
    const nLines = Math.max(...lines.map(l => l.length)), hasN = spec.bars.some(b => b.n != null);
    const top = 26, plotH = 250, yb = top + plotH, labelY = yb + (hasN ? 34 : 20);
    const H = labelY + (nLines - 1) * 14 + (spec.xLabel ? 34 : 12);
    const y = lin(0, Math.max(...spec.bars.map(b => b.value), spec.ref ? spec.ref.value : 0) * 1.08, yb, top);
    mk('line', { x1: x0, x2: x1, y1: yb, y2: yb, class: 'base' }, g);
    spec.bars.forEach((b, i) => {
      const cx = x0 + slot * (i + 0.5);
      mk('path', { d: bar(cx - bw / 2, y(b.value), bw, yb - y(b.value), 'up'), fill: b.hl === false ? grey : color(0), class: 'grow-y', style: `--i:${i}` }, g);
      txt(g, cx, y(b.value) - 7, fmt(b.value), 'val fade', 'middle', i);
      if (b.n != null) txt(g, cx, yb + 15, `n=${FMT.int(b.n)}`, '', 'middle');
      const t = txt(g, cx, labelY, '', nLines > 1 ? 'lab' : 'lab-strong', 'middle');
      lines[i].forEach((ln, k) => { const ts = mk('tspan', { x: cx, dy: k ? 14 : 0 }, t); ts.textContent = ln; });
      hover(ctx, { x: cx - slot / 2, y: top, width: slot, height: plotH }, `<b>${esc(spec.xName || '')} ${esc(b.label)}</b><br>${fmt(b.value)}${b.n != null ? `<br>n=${FMT.int(b.n)}` : ''}`, cx, y(b.value));
    });
    if (spec.ref) {
      const rg = mk('g', { class: 'fade', style: `--i:${spec.bars.length}` }, g);
      mk('line', { x1: x0, x2: x1, y1: y(spec.ref.value), y2: y(spec.ref.value), class: 'ref' }, rg);
      const key = `${spec.ref.label} ${fmt(spec.ref.value)}`, kw = textW(key, FONT.mono);
      mk('line', { x1: x1 - kw - 30, x2: x1 - kw - 8, y1: 9, y2: 9, class: 'ref' }, rg);
      txt(rg, x1, 13, key, '', 'end');
    }
    if (spec.xLabel) txt(g, (x0 + x1) / 2, H - 10, spec.xLabel, 'lab', 'middle');
    return H;
  }

  const barTable = spec => table([spec.xName || 'Group', 'Value', 'n'], spec.bars.map(b => [b.label, FMT[spec.valueFmt || 'pct1'](b.value), b.n == null ? '' : FMT.int(b.n)]));

  /* ── hbar: ranked horizontal bars; negative values diverge left; optional lollipop style ── */

  const rowSuffix = r => (r.n != null ? `  (n=${r.n})` : r.sub ? `  (${r.sub})` : '');

  function drawHBars(ctx, spec) {
    const { W, g } = ctx, fmt = FMT[spec.valueFmt || 'pct0'], xfmt = FMT[spec.xFmt || spec.valueFmt || 'pct0'];
    const lollipop = spec.style === 'lollipop';
    const valueText = r => (r.text != null ? r.text : fmt(r.value));
    const fill = (r, pos) => (r.hl === false ? grey : color(pos ? 0 : 1));   // hl: false greys out the rows that aren't the story
    const labelW = Math.min(Math.max(...spec.rows.map(r => textW(r.label + rowSuffix(r)))) + 14, W * 0.5);
    const vals = spec.rows.map(r => r.value), lo = Math.min(0, ...vals), hi = Math.max(0, ...vals);
    const room = rs => (rs.length ? Math.max(...rs.map(r => textW(valueText(r), FONT.mono))) + (lollipop ? 20 : 14) : 8);
    const negW = lo < 0 ? room(spec.rows.filter(r => r.value < 0)) : 0;   // space for labels left of negative bars
    const padR = room(spec.rows.filter(r => r.value >= 0));
    const rowH = 30, top = 6, yb = top + spec.rows.length * rowH, H = yb + (spec.xLabel ? 44 : 24);
    const x0 = labelW + negW, x1 = W - padR;
    const ticks = niceTicks(lo, hi, 5);
    const x = lin(ticks[0], ticks[ticks.length - 1], x0, x1);
    ticks.forEach(t => {
      mk('line', { x1: x(t), x2: x(t), y1: top, y2: yb, class: t === 0 ? 'base' : 'grid' }, g);
      txt(g, x(t), yb + 16, xfmt(t), '', 'middle');
    });
    spec.rows.forEach((r, i) => {
      const yy = top + i * rowH, cy = yy + rowH / 2, bh = 18, pos = r.value >= 0, ex = x(r.value), zx = x(0);
      const lab = txt(g, labelW - 10, cy + 4, r.label, 'lab', 'end');
      if (rowSuffix(r)) { const t = mk('tspan', { class: 'n' }, lab); t.textContent = rowSuffix(r); }
      const grow = `grow${pos ? '' : ' from-right'}`;
      if (lollipop) {
        mk('line', { x1: zx, x2: ex, y1: cy, y2: cy, stroke: fill(r, true), 'stroke-width': 2.5, class: grow, style: `--i:${i}` }, g);
        mk('circle', { cx: ex, cy, r: 6, fill: fill(r, true), stroke: 'var(--surface)', 'stroke-width': 2, class: 'fade', style: `--i:${i}` }, g);
      } else {
        mk('path', { d: bar(pos ? zx : ex, yy + (rowH - bh) / 2, Math.abs(ex - zx), bh, pos ? 'right' : 'left'), fill: fill(r, pos), class: grow, style: `--i:${i}` }, g);
      }
      const off = lollipop ? 12 : 6;
      txt(g, pos ? ex + off : ex - off, cy + 4, valueText(r), 'val fade', pos ? 'start' : 'end', i);
      hover(ctx, { x: 0, y: yy, width: W, height: rowH }, `<b>${esc(r.label)}</b>${r.sub ? ` (${esc(r.sub)})` : ''}<br>${esc(valueText(r))}${r.n != null ? `<br>n=${r.n}` : ''}`, ex, yy + 4);
    });
    if (spec.xLabel) txt(g, (x0 + x1) / 2, yb + 38, spec.xLabel, 'lab', 'middle');
    return H;
  }

  const hbarTable = spec => table([spec.rowName || 'Group', 'Value', ...(spec.rows[0].n != null ? ['n'] : [])],
    spec.rows.map(r => [r.label + (r.sub ? ` (${r.sub})` : ''), FMT[spec.valueFmt || 'pct0'](r.value), ...(r.n != null ? [FMT.int(r.n)] : [])]));

  /* ── lines: many series over years; a few highlighted in color, the rest in grey ── */

  function drawLines(ctx, spec) {
    const { W, g } = ctx, fmt = FMT[spec.yFmt || 'pctRaw0'], xs = spec.x;
    const ticks = niceTicks(0, Math.max(...spec.series.flatMap(s => s.values.filter(v => v != null))), 5);
    const padL = Math.max(...ticks.map(t => textW(fmt(t), FONT.mono))) + 16;
    const hi = spec.series.filter(s => s.s != null), rest = spec.series.filter(s => s.s == null);
    const last = s => s.values[s.values.length - 1];
    const endText = s => `${s.label} ${fmt(last(s))}`;
    const padR = Math.max(0, ...hi.map(s => textW(endText(s)))) + 22;
    const top = 12, plotH = 280, yb = top + plotH, H = yb + (spec.xLabel ? 48 : 30);
    const x0 = padL, x1 = W - padR;
    const x = lin(xs[0], xs[xs.length - 1], x0, x1), y = lin(0, ticks[ticks.length - 1], yb, top);
    yGrid(g, ticks, y, x0, x1, fmt);
    const every = Math.ceil(xs.length / Math.max(2, Math.floor((x1 - x0) / 46)));
    xs.forEach((v, k) => { if (k % every === 0) txt(g, x(v), yb + 16, v, '', 'middle'); });
    if (spec.xLabel) txt(g, (x0 + x1) / 2, yb + 38, spec.xLabel, 'lab', 'middle');
    const path = s => 'M' + xs.map((v, k) => [v, s.values[k]]).filter(p => p[1] != null).map(p => `${x(p[0])},${y(p[1])}`).join(' L');
    rest.forEach(s => mk('path', { d: path(s), fill: 'none', stroke: 'var(--muted)', 'stroke-opacity': 0.45, 'stroke-width': 1.2, pathLength: 1, class: 'draw', style: '--i:0' }, g));
    const ends = [];
    hi.forEach((s, k) => {
      mk('path', { d: path(s), fill: 'none', stroke: color(s.s), 'stroke-width': 2.6, 'stroke-linejoin': 'round', pathLength: 1, class: 'draw', style: `--i:${k + 1}` }, g);
      ends.push({ s, y: y(last(s)) });
    });
    ends.sort((a, b) => a.y - b.y).forEach((e, k, arr) => {
      if (k && e.y - arr[k - 1].y < 16) e.y = arr[k - 1].y + 16;
      txt(g, x1 + 8, e.y + 4, endText(e.s), 'lab fade', 'start', hi.length + 1);
    });
    const step = (x1 - x0) / (xs.length - 1);
    crosshair(ctx, x0, x1, top, yb, px => {
      const k = Math.round((px - x0) / step);
      const points = hi.map(s => ({ y: y(s.values[k]), color: color(s.s), s })).filter(p => p.s.values[k] != null);
      const others = rest.map(s => s.values[k]).filter(v => v != null);
      return { x: x(xs[k]), points, html: `<b>${xs[k]}</b><br>` + points.map(p => `${esc(p.s.label)}: <b>${fmt(p.s.values[k])}</b>`).join('<br>')
        + (others.length ? `<br>${esc(spec.otherLabel || 'Others')}: ${fmt(Math.min(...others))} to ${fmt(Math.max(...others))}` : '') };
    });
    return H;
  }

  const linesTable = spec => table([spec.xName || 'Year', ...spec.series.map(s => s.label)],
    spec.x.map((v, k) => [v, ...spec.series.map(s => (s.values[k] == null ? '' : FMT[spec.tableFmt || spec.yFmt || 'pctRaw0'](s.values[k])))]));

  /* ── dumbbell: each row's value in two periods, joined by a line ─────── */

  const light = 'color-mix(in srgb, var(--s1) 45%, var(--surface))';

  function drawDumbbell(ctx, spec) {
    const { W, g } = ctx, fmt = FMT[spec.xFmt || 'pctRaw1'], df = FMT[spec.diffFmt || 'pts2'];
    const pointText = r => (spec.pointLabel === 'to' ? fmt(r.to) : df(r.to - r.from));
    const labelW = Math.min(Math.max(...spec.rows.map(r => textW(r.label + rowSuffix(r)))) + 14, W * 0.5);
    const above = spec.pointLabel === 'to';   // value labels sit above their own dot; change labels go to the right
    const padR = above ? 28 : Math.max(...spec.rows.map(r => textW(pointText(r), FONT.mono))) + 24;
    const rowH = above ? 38 : 30, top = above ? 16 : 6, yb = top + spec.rows.length * rowH, H = yb + (spec.xLabel ? 44 : 24);
    const x0 = labelW, x1 = W - padR;
    const ends = spec.rows.flatMap(r => [r.from, r.to]);
    const ticks = niceTicks(spec.fromZero === false ? Math.min(...ends) : 0, Math.max(...ends), 5);
    const x = lin(ticks[0], ticks[ticks.length - 1], x0, x1);
    ticks.forEach(t => {
      mk('line', { x1: x(t), x2: x(t), y1: top, y2: yb, class: t === 0 ? 'base' : 'grid' }, g);
      txt(g, x(t), yb + 16, fmt(t), '', 'middle');
    });
    spec.rows.forEach((r, i) => {
      const cy = top + i * rowH + rowH / 2, a = x(r.from), b = x(r.to), up = r.to >= r.from;
      const lab = txt(g, x0 - 10, cy + 4, r.label, 'lab', 'end');
      if (rowSuffix(r)) { const t = mk('tspan', { class: 'n' }, lab); t.textContent = rowSuffix(r); }
      mk('line', { x1: a, x2: b, y1: cy, y2: cy, stroke: 'var(--muted)', 'stroke-opacity': 0.55, 'stroke-width': 2.5, class: `grow${up ? '' : ' from-right'}`, style: `--i:${i}` }, g);
      mk('circle', { cx: a, cy, r: 6, fill: light, stroke: 'var(--surface)', 'stroke-width': 2, class: 'fade', style: `--i:${i}` }, g);
      mk('circle', { cx: b, cy, r: 6, fill: color(0), stroke: 'var(--surface)', 'stroke-width': 2, class: 'fade', style: `--i:${i}` }, g);
      if (above) txt(g, b, cy - 10, pointText(r), 'val fade', 'middle', i);
      else txt(g, Math.max(a, b) + 12, cy + 4, pointText(r), 'val fade', 'start', i);
      hover(ctx, { x: 0, y: cy - rowH / 2, width: W, height: rowH },
        `<b>${esc(r.label)}</b><br>${esc(spec.series[0])}: ${fmt(r.from)}<br>${esc(spec.series[1])}: ${fmt(r.to)}<br>${df(r.to - r.from)}`, b, cy - 8);
    });
    if (spec.xLabel) txt(g, (x0 + x1) / 2, yb + 38, spec.xLabel, 'lab', 'middle');
    return H;
  }

  const dumbbellTable = spec => table([spec.rowName || 'Group', spec.series[0], spec.series[1], 'Change'],
    spec.rows.map(r => [r.label, FMT[spec.xFmt || 'pctRaw1'](r.from), FMT[spec.xFmt || 'pctRaw1'](r.to), FMT[spec.diffFmt || 'pts2'](r.to - r.from)]));

  /* ── stack100: each row split into two shares of 100% ────────────────── */

  const grey = 'color-mix(in srgb, var(--muted) 30%, var(--surface))';

  function drawStack100(ctx, spec) {
    const { W, g } = ctx, fmt = FMT.pctRaw0;
    const labelW = Math.min(Math.max(...spec.rows.map(r => textW(r.label))) + 14, W * 0.56);
    const rowH = 30, top = 6, yb = top + spec.rows.length * rowH, H = yb + (spec.xLabel ? 44 : 24);
    const x0 = labelW, x1 = W - 12, x = lin(0, 100, x0, x1);
    [0, 20, 40, 60, 80, 100].forEach(t => {
      mk('line', { x1: x(t), x2: x(t), y1: top, y2: yb, class: t === 0 ? 'base' : 'grid' }, g);
      txt(g, x(t), yb + 16, fmt(t), '', 'middle');
    });
    spec.rows.forEach((r, i) => {
      const yy = top + i * rowH, cy = yy + rowH / 2, bh = 20, split = x(r.value);
      txt(g, x0 - 10, cy + 4, r.label, 'lab', 'end');
      mk('rect', { x: x0, y: cy - bh / 2, width: split - x0, height: bh, fill: color(0), class: 'grow', style: `--i:${i}` }, g);
      mk('path', { d: bar(split + 2, cy - bh / 2, x1 - split - 2, bh), fill: grey, class: 'fade', style: `--i:${i}` }, g);
      txt(g, (x0 + split) / 2, cy + 4, fmt(r.value), 'in fade', 'middle', i);
      hover(ctx, { x: 0, y: yy, width: W, height: rowH },
        `<b>${esc(r.label)}</b><br>${esc(spec.series[0])}: ${fmt(r.value)}<br>${esc(spec.series[1])}: ${fmt(100 - r.value)}`, split, yy + 4);
    });
    if (spec.xLabel) txt(g, (x0 + x1) / 2, yb + 38, spec.xLabel, 'lab', 'middle');
    return H;
  }

  const stack100Table = spec => table([spec.rowName || 'Group', spec.series[0], spec.series[1]],
    spec.rows.map(r => [r.label, FMT.pctRaw1(r.value), FMT.pctRaw1(100 - r.value)]));

  /* ── forest: a dot per row with an optional confidence interval, against a reference line ── */

  function drawForest(ctx, spec) {
    const { W, g } = ctx, fmt = FMT[spec.valueFmt || 'pctRaw0'];
    const labelW = Math.min(Math.max(...spec.rows.map(r => textW(r.label + rowSuffix(r)))) + 14, W * 0.5);
    const padR = spec.labels ? Math.max(...spec.rows.map(r => textW(fmt(r.value), FONT.mono))) + 22 : 16;
    const top = spec.ref ? 24 : 6, rowH = 30, yb = top + spec.rows.length * rowH, H = yb + (spec.xLabel ? 44 : 24);
    const ends = spec.rows.flatMap(r => [r.lo ?? r.value, r.hi ?? r.value]).concat(spec.ref ? [spec.ref.value] : []);
    const ticks = niceTicks(Math.min(...ends), Math.max(...ends), 5);
    const x0 = labelW, x1 = W - padR, x = lin(ticks[0], ticks[ticks.length - 1], x0, x1);
    ticks.forEach(t => {
      mk('line', { x1: x(t), x2: x(t), y1: top, y2: yb, class: 'grid' }, g);
      txt(g, x(t), yb + 16, fmt(t), '', 'middle');
    });
    if (spec.ref) {
      const rg = mk('g', { class: 'fade', style: '--i:0' }, g);
      mk('line', { x1: x(spec.ref.value), x2: x(spec.ref.value), y1: top - 6, y2: yb, class: 'ref' }, rg);
      txt(rg, x(spec.ref.value) + 6, top - 10, `${spec.ref.label}: ${fmt(spec.ref.value)}`, '', 'start');
    }
    spec.rows.forEach((r, i) => {
      const yy = top + i * rowH, cy = yy + rowH / 2, ci = r.lo != null && r.hi > r.lo;
      const lab = txt(g, x0 - 10, cy + 4, r.label, 'lab', 'end');
      if (rowSuffix(r)) { const t = mk('tspan', { class: 'n' }, lab); t.textContent = rowSuffix(r); }
      if (ci) mk('line', { x1: x(r.lo), x2: x(r.hi), y1: cy, y2: cy, stroke: light, 'stroke-width': 3, 'stroke-linecap': 'round', class: 'grow mid', style: `--i:${i}` }, g);
      mk('circle', { cx: x(r.value), cy, r: 6.5, fill: r.hl === false ? grey : color(0), stroke: 'var(--surface)', 'stroke-width': 2, class: 'fade', style: `--i:${i}` }, g);
      if (spec.labels) txt(g, x(r.value) + 12, cy + 4, fmt(r.value), 'val fade', 'start', i);
      hover(ctx, { x: 0, y: yy, width: W, height: rowH },
        `<b>${esc(r.label)}</b><br>${fmt(r.value)}${ci ? ` (95% CI ${fmt(r.lo)} to ${fmt(r.hi)})` : ''}${r.n != null ? `<br>n=${r.n}` : ''}`, x(r.value), cy - 8);
    });
    if (spec.xLabel) txt(g, (x0 + x1) / 2, yb + 38, spec.xLabel, 'lab', 'middle');
    return H;
  }

  const forestTable = spec => {
    const f = FMT[spec.valueFmt || 'pctRaw0'], ciF = !spec.valueFmt || spec.valueFmt === 'pctRaw0' ? FMT.pctRaw1 : f;   // interval ends get a decimal
    return table([spec.rowName || 'Group', 'Value', ...(spec.rows[0].lo != null ? ['95% CI'] : []), 'n'],
      spec.rows.map(r => [r.label, f(r.value), ...(r.lo != null ? [`${ciF(r.lo)} to ${ciF(r.hi)}`] : []), r.n == null ? '' : FMT.int(r.n)]));
  };

  /* ── heatmap: rows × columns shaded on a light-to-dark blue ramp ─────── */

  let gradients = 0;
  const hexRgb = h => [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16));
  const ramp = (stops, t) => {
    const seg = (stops.length - 1) * Math.max(0, Math.min(1, t)), i = Math.min(Math.floor(seg), stops.length - 2), f = seg - i;
    const a = hexRgb(stops[i]), b = hexRgb(stops[i + 1]);
    return `rgb(${a.map((v, k) => Math.round(v + (b[k] - v) * f)).join(',')})`;
  };

  function drawHeatmap(ctx, spec) {
    const { W, g, svg } = ctx, fmt = FMT[spec.valueFmt || 'usdK'], stops = spec.ramp || ['#cde2fb', '#2a78d6', '#0d366b'];
    const vals = spec.values.flat().filter(v => v != null), lo = Math.min(...vals), hi = Math.max(...vals);
    const labelW = Math.max(...spec.rows.map(r => textW(r))) + 14, legendW = 70;
    const cw = (W - labelW - legendW) / spec.cols.length, ch = 30, top = 6, yb = top + spec.rows.length * ch;
    const H = yb + (spec.xLabel ? 46 : 26);
    spec.rows.forEach((rl, r) => {
      txt(g, labelW - 10, top + r * ch + ch / 2 + 4, rl, 'lab', 'end');
      spec.cols.forEach((cl, c) => {
        const v = spec.values[r][c];
        if (v == null) return;
        const cx = labelW + c * cw, cy = top + r * ch;
        mk('rect', { x: cx, y: cy, width: cw - 2, height: ch - 2, rx: 2, fill: ramp(stops, (v - lo) / (hi - lo || 1)), class: 'fade', style: `--i:${c}` }, g);
        hover(ctx, { x: cx, y: cy, width: cw, height: ch }, `<b>${esc(rl)}</b> · ${esc(cl)}<br>${esc(spec.valueName || 'Value')}: <b>${fmt(v)}</b>`, cx + cw / 2, cy);
      });
    });
    const every = cw >= 34 ? 1 : 2;
    spec.cols.forEach((cl, c) => { if (c % every === 0) txt(g, labelW + c * cw + cw / 2 - 1, yb + 14, cl, '', 'middle'); });
    if (spec.xLabel) txt(g, labelW + (W - labelW - legendW) / 2, yb + 36, spec.xLabel, 'lab', 'middle');
    // color key: a vertical ramp with its top and bottom values
    const id = `heat-grad-${++gradients}`, gx = W - legendW + 16;
    const grad = mk('linearGradient', { id, x1: 0, y1: 1, x2: 0, y2: 0 }, mk('defs', {}, svg));
    stops.forEach((s, k) => mk('stop', { offset: k / (stops.length - 1), 'stop-color': s }, grad));
    mk('rect', { x: gx, y: top, width: 12, height: yb - top - 2, rx: 2, fill: `url(#${id})` }, g);
    txt(g, gx + 18, top + 10, fmt(hi), '', 'start');
    txt(g, gx + 18, yb - 4, fmt(lo), '', 'start');
    return H;
  }

  const heatmapTable = spec => table([spec.rowName || 'Group', ...spec.cols.map(String)],
    spec.rows.map((rl, r) => [rl, ...spec.values[r].map(v => (v == null ? '' : FMT[spec.valueFmt || 'usdK'](v)))]));

  /* ── hbox-diverge: horizontal box plots beside a diverging bar panel ── */

  function drawHBoxDiverge(ctx, spec) {
    const { W, g } = ctx, bf = FMT[spec.boxFmt || 'usdK'], df = FMT[spec.diffFmt || 'signedUsdK1'];
    const rowLabel = r => `${r.label}  (n=${r.n})`;
    const labelW = Math.max(...spec.rows.map(r => textW(rowLabel(r)))) + 14;
    const rest = W - labelW - 8, gap = 28, lw = rest * 0.58, rw = rest - lw - gap;
    const rowH = 34, top = 30, yb = top + spec.rows.length * rowH, H = yb + 26;
    const lx0 = labelW, lx1 = labelW + lw, rx0 = lx1 + gap, rx1 = rx0 + rw;
    const all = spec.rows.flatMap(r => [r.box.lo, r.box.hi, ...r.box.fliers]);
    const ticks = niceTicks(Math.min(...all), Math.max(...all), 5);
    const x = lin(ticks[0], ticks[ticks.length - 1], lx0, lx1);
    const lim = Math.max(...spec.rows.map(r => Math.abs(r.diff))) * 1.6;
    const d = lin(-lim, lim, rx0, rx1);
    txt(g, lx0, 14, spec.leftTitle, 'lab-strong');
    txt(g, rx0, 14, spec.rightTitle, 'lab-strong');
    ticks.forEach(t => {
      mk('line', { x1: x(t), x2: x(t), y1: top - 6, y2: yb, class: 'grid' }, g);
      txt(g, x(t), yb + 16, bf(t), '', 'middle');
    });
    mk('line', { x1: d(0), x2: d(0), y1: top - 6, y2: yb, class: 'zero' }, g);
    spec.rows.forEach((r, i) => {
      const yy = top + i * rowH, cy = yy + rowH / 2, bh = 18, b = r.box, c = color(0);
      const lab = txt(g, lx0 - 10, cy + 4, r.label, 'lab', 'end');
      const t = mk('tspan', { class: 'n' }, lab); t.textContent = `  (n=${r.n})`;
      const wg = mk('g', { class: 'fade', style: `--i:${i}`, stroke: c, 'stroke-width': 1.5 }, g);
      mk('line', { x1: x(b.lo), x2: x(b.q1), y1: cy, y2: cy }, wg);
      mk('line', { x1: x(b.q3), x2: x(b.hi), y1: cy, y2: cy }, wg);
      mk('line', { x1: x(b.lo), x2: x(b.lo), y1: cy - 5, y2: cy + 5 }, wg);
      mk('line', { x1: x(b.hi), x2: x(b.hi), y1: cy - 5, y2: cy + 5 }, wg);
      const fl = mk('g', { class: 'fade', style: `--i:${i}`, fill: c, 'fill-opacity': 0.4 }, g);
      b.fliers.forEach(v => mk('circle', { cx: x(v), cy, r: 2 }, fl));
      mk('rect', { x: x(b.q1), y: cy - bh / 2, width: Math.max(x(b.q3) - x(b.q1), 1), height: bh, rx: 3, fill: c, class: 'grow mid', style: `--i:${i}` }, g);
      mk('line', { x1: x(b.med), x2: x(b.med), y1: cy - bh / 2, y2: cy + bh / 2, stroke: '#fff', 'stroke-width': 2, class: 'fade', style: `--i:${i}` }, g);
      const pos = r.diff >= 0, w = Math.abs(d(r.diff) - d(0));
      mk('path', { d: bar(pos ? d(0) : d(0) - w, cy - bh / 2, w, bh, pos ? 'right' : 'left'), fill: color(pos ? 0 : 1), class: `grow${pos ? '' : ' from-right'}`, style: `--i:${i}` }, g);
      txt(g, d(r.diff) + (pos ? 6 : -6), cy + 4, df(r.diff), 'val fade', pos ? 'start' : 'end', i);
      hover(ctx, { x: 0, y: yy, width: W, height: rowH },
        `<b>${esc(r.label)}</b><br>Median first salary ${FMT.usd(b.med)}<br>Middle 50% ${bf(b.q1)}–${bf(b.q3)}<br>Now vs peers ${df(r.diff)}<br>n=${FMT.int(r.n)}`, x(b.med), cy - bh / 2);
    });
    return H;
  }

  const hboxTable = spec => table([spec.rowName || 'Group', 'n', 'Q1', 'Median', 'Q3', 'Now vs peers'],
    spec.rows.map(r => [r.label, FMT.int(r.n), FMT.usd(r.box.q1), FMT.usd(r.box.med), FMT.usd(r.box.q3), FMT[spec.diffFmt || 'signedUsdK1'](r.diff)]));

  /* ── area100: shares that add up to 100% each year, stacked as bands ── */

  function drawArea100(ctx, spec) {
    const { W, g } = ctx, fmt = FMT.pctRaw0, xs = spec.x, n = xs.length - 1;
    const endText = s => `${s.label} ${fmt(s.values[n])}`;
    const padL = textW('100%', FONT.mono) + 16, padR = Math.max(...spec.series.map(s => textW(endText(s)))) + 20;
    const top = 12, plotH = 280, yb = top + plotH, H = yb + (spec.xLabel ? 48 : 30);
    const x0 = padL, x1 = W - padR, x = lin(xs[0], xs[n], x0, x1), y = lin(0, 100, yb, top);
    yGrid(g, [0, 20, 40, 60, 80, 100], y, x0, x1, fmt);
    const every = Math.ceil(xs.length / Math.max(2, Math.floor((x1 - x0) / 46)));
    xs.forEach((v, k) => { if (k % every === 0) txt(g, x(v), yb + 16, v, '', 'middle'); });
    if (spec.xLabel) txt(g, (x0 + x1) / 2, yb + 38, spec.xLabel, 'lab', 'middle');
    const cum = xs.map((_, k) => { let acc = 0; return spec.series.map(s => (acc += s.values[k])); });   // each band's top edge
    const base = (k, si) => (si ? cum[k][si - 1] : 0);
    spec.series.forEach((s, si) => {
      const upper = xs.map((v, k) => `${x(v)},${y(cum[k][si])}`), lower = xs.map((v, k) => `${x(v)},${y(base(k, si))}`).reverse();
      mk('path', { d: `M${upper.join(' L')} L${lower.join(' L')} Z`, fill: color(s.s), stroke: 'var(--surface)', 'stroke-width': 1.5, class: 'fade', style: `--i:${si}` }, g);
    });
    const ends = spec.series.map((s, si) => ({ s, y: y((base(n, si) + cum[n][si]) / 2) }));   // label each band at its middle
    ends.sort((a, b) => a.y - b.y).forEach((e, k, arr) => {
      if (k && e.y - arr[k - 1].y < 15) e.y = arr[k - 1].y + 15;
      txt(g, x1 + 8, e.y + 4, endText(e.s), 'lab fade', 'start', spec.series.length);
    });
    const step = (x1 - x0) / n;
    crosshair(ctx, x0, x1, top, yb, px => {
      const k = Math.round((px - x0) / step);
      return { x: x(xs[k]), points: spec.series.map((s, si) => ({ y: y(cum[k][si]), color: color(s.s) })),
        html: `<b>${xs[k]}</b><br>` + spec.series.map(s => `${esc(s.label)}: <b>${fmt(s.values[k])}</b>`).reverse().join('<br>') };
    });
    return H;
  }

  const area100Table = spec => table([spec.xName || 'Year', ...spec.series.map(s => s.label)],
    spec.x.map((v, k) => [v, ...spec.series.map(s => FMT.pctRaw1(s.values[k]))]));

  /* ── stackcols: 100% stacked columns, one color per level (light = junior, dark = senior) ── */

  function drawStackCols(ctx, spec) {
    const { W, g } = ctx, fmt = FMT.pctRaw0;
    const padL = textW('100%', FONT.mono) + 16, top = 12, plotH = 260, yb = top + plotH, H = yb + (spec.xLabel ? 52 : 32);
    const x0 = padL, x1 = W - 8, slot = (x1 - x0) / spec.cols.length, bw = Math.min(slot * 0.7, 80), y = lin(0, 100, yb, top);
    yGrid(g, [0, 25, 50, 75, 100], y, x0, x1, fmt);
    spec.cols.forEach((c, i) => {
      const cx = x0 + slot * (i + 0.5);
      let acc = 0;
      c.values.forEach((v, li) => {
        if (v <= 0) return;
        const yTop = y(acc + v), yBot = y(acc);
        acc += v;
        mk('rect', { x: cx - bw / 2, y: yTop, width: bw, height: Math.max(yBot - yTop - 1.5, 0.5), fill: spec.colors[li], class: 'grow-y', style: `--i:${i}` }, g);
      });
      txt(g, cx, yb + 16, c.label, 'lab-strong', 'middle');
      hover(ctx, { x: cx - slot / 2, y: top, width: slot, height: plotH },
        `<b>${esc(spec.xName || '')} ${esc(c.label)}</b><br>` + spec.levels.map((l, li) => `${esc(l)}: ${fmt(c.values[li])}`).reverse().join('<br>'), cx, top + 10);
    });
    if (spec.xLabel) txt(g, (x0 + x1) / 2, yb + 40, spec.xLabel, 'lab', 'middle');
    return H;
  }

  const stackColsTable = spec => table([spec.xName || 'Group', ...spec.levels], spec.cols.map(c => [c.label, ...c.values.map(v => FMT.pctRaw1(v))]));

  /* ── multibar: small multiples, one bar chart per panel with its own scale (bars may go below zero) ── */

  function drawMultiBar(ctx, spec) {
    const { W, g } = ctx, n = spec.panels.length, gap = 28, pw = (W - gap * (n - 1)) / n;
    const top = 34, plotH = 200, yb = top + plotH, H = yb + (spec.xLabel ? 44 : 26);
    let i = 0;
    spec.panels.forEach((p, pi) => {
      const px = pi * (pw + gap), fmt = FMT[p.fmt || 'num'];
      txt(g, px, 14, p.title, 'lab-strong');
      const vals = p.bars.map(b => b.value), lo = Math.min(0, ...vals), hi = Math.max(0, ...vals), pad = (hi - lo) * 0.2 || 1;
      const y = lin(lo < 0 ? lo - pad : 0, hi + pad, yb, top);
      const slot = pw / p.bars.length, bw = Math.min(slot * 0.62, 64);
      mk('line', { x1: px, x2: px + pw, y1: y(0), y2: y(0), class: 'base' }, g);
      p.bars.forEach((b, k) => {
        const cx = px + slot * (k + 0.5), pos = b.value >= 0, h = Math.abs(y(b.value) - y(0));
        mk('path', { d: pos ? bar(cx - bw / 2, y(b.value), bw, h, 'up') : bar(cx - bw / 2, y(0), bw, h, 'down'), fill: color(0),
          class: `grow-y${pos ? '' : ' from-top'}`, style: `--i:${i}` }, g);
        txt(g, cx, pos ? y(b.value) - 7 : y(b.value) + 15, fmt(b.value), 'val fade', 'middle', i);
        txt(g, cx, yb + 18, b.label, 'lab-strong', 'middle');
        hover(ctx, { x: cx - slot / 2, y: top, width: slot, height: plotH }, `<b>${esc(p.title)}</b><br>${esc(spec.xName || '')} ${esc(b.label)}: <b>${fmt(b.value)}</b>`, cx, Math.min(y(b.value), y(0)));
        i++;
      });
      if (spec.xLabel) txt(g, px + pw / 2, yb + 38, spec.xLabel, 'lab', 'middle');
    });
    return H;
  }

  const multiBarTable = spec => table([spec.xName || 'Group', ...spec.panels.map(p => p.title)],
    spec.panels[0].bars.map((b, k) => [b.label, ...spec.panels.map(p => FMT[p.fmt || 'num'](p.bars[k].value))]));

  /* ── bubble: x and y per item, bubble area = count; colored when the gap is significant ── */

  function drawBubble(ctx, spec) {
    const { W, g } = ctx, xf = FMT[spec.xFmt || 'pctRaw0'], yf = FMT[spec.yFmt || 'signedUsdK'];
    const xs = spec.points.map(p => p.x), ys = spec.points.map(p => p.y).concat([0]);
    const span = Math.max(...ys) - Math.min(...ys) || 1;
    const yt = niceTicks(Math.min(...ys) - span * 0.18, Math.max(...ys) + span * 0.1, 5), xt = niceTicks(Math.min(...xs) * 0.8, Math.max(...xs) * 1.05, 6);
    const padL = Math.max(...yt.map(t => textW(yf(t), FONT.mono))) + 16, top = 22, plotH = 300, yb = top + plotH, H = yb + (spec.xLabel ? 48 : 30);
    const x0 = padL, x1 = W - 16, x = lin(xt[0], xt[xt.length - 1], x0, x1), y = lin(yt[0], yt[yt.length - 1], yb, top);
    yGrid(g, yt, y, x0, x1, yf);
    mk('line', { x1: x0, x2: x1, y1: y(0), y2: y(0), class: 'zero' }, g);
    xt.forEach(t => txt(g, x(t), yb + 16, xf(t), '', 'middle'));
    if (spec.xLabel) txt(g, (x0 + x1) / 2, yb + 38, spec.xLabel, 'lab', 'middle');
    const rMax = Math.min(28, W / 28), k = rMax / Math.sqrt(Math.max(...spec.points.map(p => p.n)));
    [...spec.points].sort((a, b) => b.n - a.n).forEach((p, i) => {   // big bubbles first so small ones stay on top
      const cx = x(p.x), cy = y(p.y), r = Math.max(4, k * Math.sqrt(p.n));
      mk('circle', { cx, cy, r, fill: p.hl ? color(0) : grey, 'fill-opacity': 0.8, stroke: 'var(--surface)', 'stroke-width': 2, class: 'pop', style: `--i:${i}` }, g);
      const side = p.side || 'top';
      if (side === 'top') txt(g, cx, cy - r - 6, p.label, 'lab fade', 'middle', i);
      else txt(g, side === 'left' ? cx - r - 6 : cx + r + 6, cy + 4, p.label, 'lab fade', side === 'left' ? 'end' : 'start', i);
      hover(ctx, { x: cx - r, y: cy - r, width: 2 * r, height: 2 * r },
        `<b>${esc(p.label)}</b><br>${xf(p.x)} did it (n=${FMT.int(p.n)})<br>Salary gap: <b>${yf(p.y)}</b>${p.p != null ? `<br>p = ${p.p.toExponential(1)}` : ''}`, cx, cy - r);
    });
    return H;
  }

  const bubbleTable = spec => table([spec.rowName || 'Item', spec.xName || 'x', spec.yName || 'y', 'n', ...(spec.points[0].p != null ? ['p'] : [])],
    spec.points.map(p => [p.label, FMT[spec.xFmt || 'pctRaw0'](p.x), FMT[spec.yFmt || 'signedUsdK'](p.y), FMT.int(p.n), ...(p.p != null ? [p.p.toExponential(1)] : [])]));

  /* ── dotline: a few categories joined by a line, each dot with a two-line label ── */

  function drawDotLine(ctx, spec) {
    const { W, g } = ctx, yf = FMT[spec.yFmt || 'signedUsdK1'];
    const vals = spec.points.map(p => p.value).concat([0]), pad = (Math.max(...vals) - Math.min(...vals)) * 0.6 || 1;
    const yt = niceTicks(Math.min(...vals) - pad, Math.max(...vals) + pad, 5);
    const tf = FMT.signedUsdK;
    const padL = Math.max(...yt.map(t => textW(tf(t), FONT.mono))) + 16, top = 12, plotH = 240, yb = top + plotH, H = yb + 34;
    const x0 = padL, x1 = W - 12, slot = (x1 - x0) / spec.points.length, y = lin(yt[0], yt[yt.length - 1], yb, top);
    yGrid(g, yt, y, x0, x1, tf);
    mk('line', { x1: x0, x2: x1, y1: y(0), y2: y(0), class: 'zero' }, g);
    const px = k => x0 + slot * (k + 0.5);
    mk('path', { d: 'M' + spec.points.map((p, k) => `${px(k)},${y(p.value)}`).join(' L'), fill: 'none', stroke: color(0), 'stroke-width': 2.5, pathLength: 1, class: 'draw', style: '--i:0' }, g);
    spec.points.forEach((p, k) => {
      mk('circle', { cx: px(k), cy: y(p.value), r: 8, fill: color(0), stroke: 'var(--surface)', 'stroke-width': 2, class: 'pop', style: `--i:${k}` }, g);
      txt(g, px(k), y(p.value) - 30, yf(p.value), 'val halo fade', 'middle', k);
      txt(g, px(k), y(p.value) - 15, p.sub, 'lab halo fade', 'middle', k);
      const lab = txt(g, px(k), yb + 20, p.label, 'lab-strong', 'middle');
      if (p.n != null) { const t = mk('tspan', { class: 'n' }, lab); t.textContent = `  (n=${FMT.int(p.n)})`; }
      hover(ctx, { x: px(k) - slot / 2, y: top, width: slot, height: plotH }, `<b>${esc(p.label)}</b><br>${yf(p.value)}<br>${esc(p.sub)}`, px(k), y(p.value) - 34);
    });
    return H;
  }

  const dotLineTable = spec => table([spec.rowName || 'Group', spec.yName || 'Value', 'Detail', 'n'],
    spec.points.map(p => [p.label, FMT[spec.yFmt || 'signedUsdK1'](p.value), p.sub, p.n == null ? '' : FMT.int(p.n)]));

  /* ── hexbin: point density on matplotlib's hexagon grid, with a median line on top ── */

  function drawHexbin(ctx, spec) {
    const { W, g, svg } = ctx, xf = FMT[spec.xFmt || 'num'], yf = FMT[spec.yFmt || 'signedUsdK'];
    const stops = spec.ramp || ['#cde2fb', '#2a78d6', '#0d366b'], cMax = Math.max(...spec.hexes.map(h => h.c));
    const xt = niceTicks(spec.xMin, spec.xMax, 6), yt = niceTicks(spec.yMin, spec.yMax, 6);
    const padL = Math.max(...yt.map(t => textW(yf(t), FONT.mono))) + 16, legendW = 64;
    const top = 12, plotH = 320, yb = top + plotH, H = yb + (spec.xLabel ? 48 : 30);
    const x0 = padL, x1 = W - legendW, x = lin(xt[0], xt[xt.length - 1], x0, x1), y = lin(yt[0], yt[yt.length - 1], yb, top);
    yGrid(g, yt, y, x0, x1, yf);
    xt.forEach(t => txt(g, x(t), yb + 16, xf(t), '', 'middle'));
    if (spec.xLabel) txt(g, (x0 + x1) / 2, yb + 38, spec.xLabel, 'lab', 'middle');
    const dx = [0.5, 0.5, 0, -0.5, -0.5, 0].map(v => v * spec.sx), dy = [-0.5, 0.5, 1, 0.5, -0.5, -1].map(v => v * spec.sy / 3);   // matplotlib's hexagon
    spec.hexes.forEach(h => {
      const pts = dx.map((ox, k) => `${x(h.x + ox)},${y(h.y + dy[k])}`).join(' ');
      mk('polygon', { points: pts, fill: ramp(stops, (h.c - 1) / Math.max(cMax - 1, 1)), stroke: 'var(--surface)', 'stroke-width': 0.6,
        class: 'fade', style: `--i:${Math.round((h.x - spec.xMin) / (spec.xMax - spec.xMin) * 8)}` }, g);
    });
    const line = spec.line;
    mk('path', { d: 'M' + line.map(p => `${x(p.x)},${y(p.y)}`).join(' L'), fill: 'none', stroke: color(1), 'stroke-width': 2.8, 'stroke-linejoin': 'round', pathLength: 1, class: 'draw', style: '--i:9' }, g);
    line.forEach((p, k) => {
      mk('circle', { cx: x(p.x), cy: y(p.y), r: 4.5, fill: color(1), stroke: 'var(--surface)', 'stroke-width': 1.5, class: 'pop', style: `--i:${9 + k}` }, g);
      hover(ctx, { x: x(p.x) - 12, y: y(p.y) - 12, width: 24, height: 24 }, `<b>${esc(spec.lineName || 'Median')}</b><br>${esc(p.label || xf(p.x))}: <b>${yf(p.y)}</b>${p.n != null ? `<br>n=${FMT.int(p.n)}` : ''}`, x(p.x), y(p.y) - 8);
    });
    const id = `hex-grad-${++gradients}`, gx = W - legendW + 16;   // color key: people per hexagon
    const grad = mk('linearGradient', { id, x1: 0, y1: 1, x2: 0, y2: 0 }, mk('defs', {}, svg));
    stops.forEach((s, k) => mk('stop', { offset: k / (stops.length - 1), 'stop-color': s }, grad));
    mk('rect', { x: gx, y: top, width: 12, height: plotH, rx: 2, fill: `url(#${id})` }, g);
    txt(g, gx + 18, top + 10, FMT.int(cMax), '', 'start');
    txt(g, gx + 18, yb - 2, '1', '', 'start');
    return H;
  }

  const hexbinTable = spec => table([spec.xName || 'x', spec.lineName || 'Median', 'n'], spec.line.map(p => [p.label || FMT[spec.xFmt || 'num'](p.x), FMT[spec.yFmt || 'signedUsdK'](p.y), p.n == null ? '' : FMT.int(p.n)]));

  /* ── charts Advisor Ann sends in her chat: each row's name sits on its own line, so they fit a phone ── */

  /** Words wrapped into lines no wider than w. */
  const fitLines = (s, w, font) => {
    const out = [];
    let line = '';
    String(s).split(' ').forEach(word => {
      const next = line ? `${line} ${word}` : word;
      if (line && textW(next, font) > w) { out.push(line); line = word; } else line = next;
    });
    if (line) out.push(line);
    return out;
  };
  const ordinal = n => `${n}${n % 100 >= 11 && n % 100 <= 13 ? 'th' : { 1: 'st', 2: 'nd', 3: 'rd' }[n % 10] || 'th'}`;

  /* lineup: one student's percentile among their peers on each measure, over the shaded middle half;
     a row's dot takes its series color (s), or grey when it has none (context, not a score) */

  function drawLineup(ctx, spec) {
    const { W, g } = ctx;
    const sub = r => `you ${r.you} · peer median ${r.median}`;
    const pctW = textW('100th', FONT.mono) + 16;
    const inline = spec.rows.every(r => textW(`${r.label}  ${sub(r)}`, FONT.sansB) + pctW <= W);   // else the values get their own line
    const rowH = inline ? 48 : 64, top = 2, H = top + spec.rows.length * rowH + 22;
    const x0 = 7, x1 = W - 7, x = lin(0, 100, x0, x1);
    spec.rows.forEach((r, i) => {
      const yy = top + i * rowH, ty = yy + rowH - 17, c = r.s == null ? 'var(--muted)' : color(r.s);
      const lab = txt(g, 0, yy + 14, r.label, 'lab-strong');
      if (inline) { const t = mk('tspan', { class: 'n' }, lab); t.textContent = `  ${sub(r)}`; }
      else txt(g, 0, yy + 31, sub(r), 'n');
      txt(g, W, yy + 14, ordinal(r.pct), 'val fade', 'end', i);
      mk('line', { x1: x0, x2: x1, y1: ty, y2: ty, class: 'base' }, g);
      mk('rect', { x: x(25), y: ty - 6, width: x(75) - x(25), height: 12, rx: 6, fill: 'var(--grid)' }, g);
      mk('line', { x1: x(50), x2: x(50), y1: ty - 9, y2: ty + 9, class: 'zero' }, g);
      mk('circle', { cx: x(r.pct), cy: ty, r: 7, fill: c, stroke: 'var(--surface)', 'stroke-width': 2, class: 'pop', style: `--i:${i}` }, g);
      hover(ctx, { x: 0, y: yy, width: W, height: rowH },
        `<b>${esc(r.label)}</b><br>You ${esc(r.you)} · peer median ${esc(r.median)}<br><b>${ordinal(r.pct)} percentile</b>${r.status ? ` · ${esc(r.status)}` : ''}`, x(r.pct), ty - 10);
    });
    const yb = top + spec.rows.length * rowH + 14;
    [[0, '0', 'start'], [25, '25th', 'middle'], [50, 'median', 'middle'], [75, '75th', 'middle'], [100, '100th', 'end']]
      .forEach(([v, s, a]) => txt(g, x(v), yb, s, '', a));
    return H;
  }

  const lineupTable = spec => table(['Measure', 'You', 'Peer median', 'Percentile', 'Standing'],
    spec.rows.map(r => [r.label, r.you, r.median, ordinal(r.pct), r.status || '']));

  /* ranked: a few positive values with long names, largest first */

  function drawRanked(ctx, spec) {
    const { W, g } = ctx, fmt = FMT[spec.valueFmt || 'signedUsdK1'];
    const padR = Math.max(...spec.rows.map(r => textW(fmt(r.value), FONT.mono))) + 12;
    const lines = spec.rows.map(r => {   // the name, wrapped; the detail joins its last line when it fits
      const ls = fitLines(r.label, W, FONT.sansB);
      if (!r.sub) return { ls, sub: null };
      return textW(`${ls[ls.length - 1]}  ${r.sub}`, FONT.sansB) <= W ? { ls, sub: 'same' } : { ls, sub: 'own' };
    });
    const x0 = 0, x1 = W - padR, x = lin(0, Math.max(0, ...spec.rows.map(r => r.value)), x0, x1), bh = 18;
    let yy = 2;
    spec.rows.forEach((r, i) => {
      const { ls, sub } = lines[i];
      ls.forEach((l, k) => {
        const t = txt(g, 0, yy + 14 + k * 17, l, 'lab-strong');
        if (sub === 'same' && k === ls.length - 1) { const s = mk('tspan', { class: 'n' }, t); s.textContent = `  ${r.sub}`; }
      });
      const nLines = ls.length + (sub === 'own' ? 1 : 0);
      if (sub === 'own') txt(g, 0, yy + 14 + ls.length * 17, r.sub, 'n');
      const by = yy + nLines * 17 + 6, ex = x(Math.max(r.value, 0));
      mk('path', { d: bar(x0, by, ex - x0, bh, 'right'), fill: color(0), class: 'grow', style: `--i:${i}` }, g);
      txt(g, ex + 6, by + bh / 2 + 4, fmt(r.value), 'val fade', 'start', i);
      hover(ctx, { x: 0, y: yy, width: W, height: by + bh - yy }, `<b>${esc(r.label)}</b><br>${fmt(r.value)}${r.sub ? `<br>${esc(r.sub)}` : ''}`, ex, by);
      yy = by + bh + 14;
    });
    return yy - 6;
  }

  const rankedTable = spec => table([spec.rowName || 'Item', spec.valueName || 'Value', 'Detail'],
    spec.rows.map(r => [r.label, FMT[spec.valueFmt || 'signedUsdK1'](r.value), r.sub || '']));

  /* range: an expected value and its likely range on each row, against a reference line */

  function drawRange(ctx, spec) {
    const { W, g } = ctx, fmt = FMT[spec.valueFmt || 'signedUsdK'], rangeName = spec.rangeName || 'Range';
    const ends = spec.rows.flatMap(r => [r.lo, r.hi]).concat(spec.ref ? [spec.ref.value] : []);
    const ticks = niceTicks(Math.min(...ends), Math.max(...ends), W < 420 ? 3 : 5);
    const rowH = 50, top = spec.ref ? 22 : 4, yb = top + spec.rows.length * rowH, H = yb + 24;
    const x0 = 8, x1 = W - 8, x = lin(ticks[0], ticks[ticks.length - 1], x0, x1);
    ticks.forEach((t, k) => txt(g, x(t), yb + 16, fmt(t), '', k === 0 ? 'start' : k === ticks.length - 1 ? 'end' : 'middle'));
    mk('line', { x1: x0, x2: x1, y1: yb, y2: yb, class: 'base' }, g);
    if (spec.ref) {
      const rx = x(spec.ref.value), key = `${spec.ref.label}: ${fmt(spec.ref.value)}`, right = rx + 6 + textW(key, FONT.mono) <= W;
      const rg = mk('g', { class: 'fade', style: '--i:0' }, g);
      mk('line', { x1: rx, x2: rx, y1: top - 6, y2: yb, class: 'ref' }, rg);
      txt(rg, right ? rx + 6 : rx - 6, top - 10, key, '', right ? 'start' : 'end');
    }
    spec.rows.forEach((r, i) => {
      const yy = top + i * rowH, cy = yy + 34;
      const lab = txt(g, 0, yy + 16, r.label, 'lab-strong halo');
      const t = mk('tspan', { class: 'n' }, lab); t.textContent = `  expected ${fmt(r.value)}`;
      mk('line', { x1: x(r.lo), x2: x(r.hi), y1: cy, y2: cy, stroke: light, 'stroke-width': 5, 'stroke-linecap': 'round', class: 'grow mid', style: `--i:${i}` }, g);
      mk('circle', { cx: x(r.value), cy, r: 7, fill: color(0), stroke: 'var(--surface)', 'stroke-width': 2, class: 'pop', style: `--i:${i}` }, g);
      hover(ctx, { x: 0, y: yy, width: W, height: rowH },
        `<b>${esc(r.label)}</b><br>Expected ${fmt(r.value)}<br>${esc(rangeName)}: ${fmt(r.lo)} to ${fmt(r.hi)}`, x(r.value), cy - 8);
    });
    return H;
  }

  const rangeTable = spec => {
    const f = FMT[spec.valueFmt || 'signedUsdK'];
    return table([spec.rowName || 'Group', 'Expected', spec.rangeName || 'Range'], spec.rows.map(r => [r.label, f(r.value), `${f(r.lo)} to ${f(r.hi)}`]));
  };

  const TYPES = {
    box: { draw: drawBoxes, table: boxTable },
    'line-band': { draw: drawLineBand, table: lineBandTable },
    step: { draw: drawStep, table: stepTable },
    pie: { draw: drawPies, table: pieTable },
    bar: { draw: drawBars, table: barTable },
    hbar: { draw: drawHBars, table: hbarTable },
    'hbox-diverge': { draw: drawHBoxDiverge, table: hboxTable },
    lines: { draw: drawLines, table: linesTable },
    dumbbell: { draw: drawDumbbell, table: dumbbellTable },
    stack100: { draw: drawStack100, table: stack100Table },
    forest: { draw: drawForest, table: forestTable },
    heatmap: { draw: drawHeatmap, table: heatmapTable },
    area100: { draw: drawArea100, table: area100Table },
    stackcols: { draw: drawStackCols, table: stackColsTable },
    multibar: { draw: drawMultiBar, table: multiBarTable },
    bubble: { draw: drawBubble, table: bubbleTable },
    dotline: { draw: drawDotLine, table: dotLineTable },
    hexbin: { draw: drawHexbin, table: hexbinTable },
    lineup: { draw: drawLineup, table: lineupTable },
    ranked: { draw: drawRanked, table: rankedTable },
    range: { draw: drawRange, table: rangeTable },
  };

  /* ── scroll-linked motion ─────────────────────────────────────────────── */

  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const clamp01 = v => Math.max(0, Math.min(1, v));
  const smooth = t => t * t * (3 - 2 * t);   // smoothstep: eases both ways, so rewinding feels like playing
  // Scroll distance a chart's animation takes: 0 with the plot's top at the viewport bottom, 1 once it is
  // 25% down (the bottom 75% of the screen). Lower END = longer and slower; higher = shorter and faster.
  const END = 0.25;
  // Share of the remaining distance the shown progress closes each frame. Lower = trails the scroll longer (slower).
  const EASE_IN = 0.1;
  // How long a chart mounted with { play: true } takes to draw itself in, in milliseconds.
  const PLAY_MS = 1400;

  /**
   * Put every mark at chart progress p. Starts are staggered by --i across the
   * first 45% of the timeline; labels (.fade) follow their marks.
   */
  function applyMarks(marks, n, p) {
    const stag = n > 1 ? 0.45 / (n - 1) : 0;
    for (const m of marks) {
      const t0 = m.i * stag;
      if (m.kind === 'fade') { m.el.style.opacity = smooth(clamp01((p - t0 - 0.3) / 0.25)); continue; }
      const e = smooth(clamp01((p - t0) / 0.55));
      if (m.kind === 'grow') m.el.style.transform = `scaleX(${e})`;
      else if (m.kind === 'grow-y') m.el.style.transform = `scaleY(${e})`;
      else if (m.kind === 'pop') m.el.style.transform = `scale(${e})`;
      else if (m.kind === 'draw') m.el.style.strokeDashoffset = 1 - e;
      else m.el.style.strokeDasharray = `${e * m.dash} ${m.c}`;   // sweep
    }
  }

  const charts = [];
  let ticking = false;

  /** One rAF loop for every chart: ease each chart's shown progress toward its scroll position. */
  function frame() {
    ticking = false;
    const vh = innerHeight;
    const atBottom = scrollY + vh >= document.documentElement.scrollHeight - 2;
    for (const c of charts) {
      if (!c.svg || !c.svg.isConnected) continue;   // not drawn yet, or removed (a restarted chat)
      let target;
      if (c.play) target = reduced.matches ? 1 : clamp01((performance.now() - c.t0) / PLAY_MS);
      else {
        const top = c.svg.getBoundingClientRect().top;
        target = reduced.matches ? 1 : clamp01((vh - top) / (vh * (1 - END)));
        if (atBottom && top < vh) target = 1;   // the page can't scroll further, so let what's on screen finish
      }
      const next = c.play || reduced.matches || Math.abs(target - c.p) < 0.002 ? target : c.p + (target - c.p) * EASE_IN;
      if (next !== c.p || c.dirty) { c.p = next; c.dirty = false; applyMarks(c.marks, c.n, c.p); }
      if (c.p !== target || (c.play && target < 1)) ticking = true;   // a playing chart keeps going until it's drawn
    }
    if (ticking) requestAnimationFrame(frame);
  }
  const kick = () => { if (!ticking) { ticking = true; requestAnimationFrame(frame); } };
  addEventListener('scroll', kick, { passive: true });
  addEventListener('resize', kick);
  reduced.addEventListener('change', kick);

  /* ── Copy code ───────────────────────────────────────────────────────── */

  const legacyCopy = text => {
    const ta = document.createElement('textarea');
    ta.value = text; ta.setAttribute('readonly', ''); ta.style.cssText = 'position:fixed;opacity:0;pointer-events:none';
    document.body.append(ta); ta.select();
    let ok = false;
    try { ok = document.execCommand('copy'); } catch { ok = false; }
    ta.remove();
    return ok;
  };

  function copyButton(chartEl, code) {
    const btn = html('button', 'copy-code');
    btn.type = 'button';
    btn.title = 'Copy the Python that draws this chart';
    btn.innerHTML = '<svg viewBox="0 0 16 16" aria-hidden="true"><rect x="5" y="5" width="9" height="9" rx="1.5"/><path d="M11 5V3.5A1.5 1.5 0 0 0 9.5 2h-6A1.5 1.5 0 0 0 2 3.5v6A1.5 1.5 0 0 0 3.5 11H5"/></svg><span>Copy code</span>';
    const label = btn.querySelector('span');
    const done = ok => {
      if (ok) {
        label.textContent = 'Copied!'; btn.classList.add('done');
        setTimeout(() => { label.textContent = 'Copy code'; btn.classList.remove('done'); }, 1600);
        return;
      }
      // the clipboard was refused: show the code, selected, so it can be copied by hand
      let pre = chartEl.querySelector('.code-fallback');
      if (!pre) { pre = html('pre', 'code-fallback'); pre.textContent = code; pre.tabIndex = 0; chartEl.append(pre); }
      const range = document.createRange(); range.selectNodeContents(pre);
      const sel = getSelection(); sel.removeAllRanges(); sel.addRange(range);
      pre.focus({ preventScroll: true });
      label.textContent = 'Selected: press Ctrl/⌘+C';
    };
    btn.addEventListener('click', () => {   // writeText must be called inside the click itself
      if (!(navigator.clipboard && navigator.clipboard.writeText)) { done(legacyCopy(code)); return; }
      let settled = false;
      const settle = ok => { if (!settled) { settled = true; done(ok); } };
      navigator.clipboard.writeText(code).then(() => settle(true), () => settle(legacyCopy(code)));
      setTimeout(() => settle(false), 1500);   // a clipboard that never answers: show the code instead
    });
    return btn;
  }

  /* ── mounting, resize ─────────────────────────────────────────────────── */

  // A chart is laid out for its box at 1× and drawn up to SCALE times larger, so text, marks and gaps grow
  // together. The layout keeps at least MIN_W (or the chart's own minWidth) 1× pixels for its labels, so a
  // narrow box (a phone, a half-width card) scales up less, and below that it scrolls as before.
  const SCALE = 1.3, MIN_W = 440;

  function mount(el, spec, { play = false } = {}) {
    const type = TYPES[spec && spec.type];
    if (!type) { el.textContent = `No chart data for "${el.dataset.chart}".`; return; }
    const caption = el.querySelector(':scope > .chart-caption');   // text from page.html, kept inside the card
    el.replaceChildren();

    const head = html('div', 'chart-head');
    const titles = html('div');
    titles.append(html('div', 'chart-title', spec.title));
    if (spec.subtitle) titles.append(html('div', 'chart-sub', spec.subtitle));
    head.append(titles);
    // a legend whenever a chart has 2+ series; identity is the swatch, text stays in ink
    // entries are names (colored by series order) or {label, color}
    const names = spec.legend || (spec.series || []).map(s => (typeof s === 'string' ? s : s.label));
    if (names.length > 1 || (spec.legend && names.length)) {
      const ul = html('ul', 'legend');
      names.forEach((l, i) => {
        const li = html('li'); const sw = html('i');
        sw.style.background = typeof l === 'object' ? l.color : color(i);
        li.append(sw, typeof l === 'object' ? l.label : l); ul.append(li);
      });
      head.append(ul);
    }
    const scroller = html('div', 'chart-scroll');
    const inner = html('div', 'chart-inner');
    scroller.append(inner);
    el.append(head, scroller);
    if (spec.note) el.append(html('p', 'chart-note', spec.note));
    if (caption) el.append(caption);
    const det = html('details', 'chart-table');
    det.innerHTML = `<summary>Table view</summary><div class="table-wrap">${type.table(spec)}</div>`;
    const foot = html('div', 'chart-foot');   // Table view on the left, Copy code in the bottom-right corner
    foot.append(det);
    if (CODE[el.dataset.chart]) foot.append(copyButton(el, CODE[el.dataset.chart]));
    el.append(foot);

    const c = { svg: null, marks: [], n: 1, p: 0, dirty: true, play, t0: performance.now() };
    charts.push(c);
    let lastW = 0;
    const width = () => Math.floor(scroller.clientWidth);
    const draw = () => {
      const box = width();
      if (!box) return;
      lastW = box;
      const k = Math.max(1, Math.min(SCALE, box / Math.max(MIN_W, spec.minWidth || 0)));
      const W = Math.max(box / k, spec.minWidth || 0);   // the 1× layout width
      inner.replaceChildren();
      const svg = mk('svg', { class: 'chart-svg', role: 'img', 'aria-label': `${spec.title}. Full data in the table view.` });
      inner.append(svg);
      const plot = mk('g', { class: 'plot' }, svg);
      const tipEl = html('div', 'chart-tip');
      tipEl.hidden = true;
      inner.append(tipEl);
      const tip = {
        // above the anchor (given in chart coordinates), clamped inside the chart so the scroller never clips it
        show(h, x, y) {
          tipEl.innerHTML = h;
          tipEl.hidden = false;
          const w = tipEl.offsetWidth, th = tipEl.offsetHeight, ax = x * k, ay = y * k;
          const top = ay - th - 10 < 0 ? ay + 14 : ay - th - 10;
          tipEl.style.left = `${Math.min(Math.max(ax - w / 2, 4), W * k - w - 4)}px`;
          tipEl.style.top = `${top}px`;
        },
        hide() { tipEl.hidden = true; },
      };
      const H = type.draw({ W, g: plot, svg, tip }, spec);
      svg.setAttribute('width', W * k); svg.setAttribute('height', H * k); svg.setAttribute('viewBox', `0 0 ${W} ${H}`);

      // register the animated marks; a redraw keeps the chart's current progress
      c.svg = svg;
      c.marks = [...plot.querySelectorAll('.grow, .grow-y, .pop, .fade, .draw, .sweep')].map(m => {
        const cl = m.classList, st = m.style;
        const kind = cl.contains('grow') ? 'grow' : cl.contains('grow-y') ? 'grow-y' : cl.contains('pop') ? 'pop' : cl.contains('draw') ? 'draw' : cl.contains('sweep') ? 'sweep' : 'fade';
        return { el: m, kind, i: parseFloat(st.getPropertyValue('--i')) || 0,
          dash: parseFloat(st.getPropertyValue('--dash')), c: parseFloat(st.getPropertyValue('--c')) };
      });
      c.n = Math.max(0, ...c.marks.map(m => m.i)) + 1;
      applyMarks(c.marks, c.n, c.p);
      kick();
    };
    draw();

    let pending = false;
    new ResizeObserver(() => {
      if (pending) return;
      pending = true;
      requestAnimationFrame(() => { pending = false; if (width() !== lastW) draw(); });
    }).observe(scroller);
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(draw);   // re-measure labels once webfonts land
  }

  document.querySelectorAll('[data-chart]').forEach(el => mount(el, DATA[el.dataset.chart]));
  window.successCharts = { mount };
})();
