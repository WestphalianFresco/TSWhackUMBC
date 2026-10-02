/*
 * The full-screen crayon-collage scene at the top of the page.
 *
 * The top bar's Student Life / Strategies / Job Trajectory tabs move the student
 * between three spots: a freshman by the UMBC campus (left third), an intern at
 * the signpost (middle), a graduate in a suit by the city (right third). Motion
 * uses anime.js 4 (loaded from cdnjs before this script): he walks, slides into
 * the job, and flips like a paper doll to change clothes. The crayon look comes
 * from the #crayon SVG filter, whose noise seed changes a few times a second so
 * the outlines wobble like stop-motion. Without anime.js, or with reduced
 * motion, he moves between spots instantly. Each stage can have a section of
 * charts below the scene (.stage-panel[data-stage]); only the selected stage's shows.
 */
(() => {
  const A = window.anime;
  const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const $ = id => document.getElementById(id);
  const scene = $('scene');
  if (!scene) return;
  const wrap = $('kidWrap'), kid = $('kid'), facing = $('facing'), flipper = $('flipper'), bob = $('bob');
  const sticker = $('sticker'), speed = $('speed');
  const legs = [$('legB'), $('legF')], arms = [$('armB'), $('armF')];
  const set = A ? (A.utils && A.utils.set) || A.set : (el, p) => { if ('x' in p) el.style.transform = `translateX(${p.x}px)`; };
  const still = reduce || !A;

  // Each stage: where the student stands (share of the scene width), what he wears, and the sticker's words.
  const STATES = {
    life: { zone: 1 / 6, fit: 'student', label: 'A student in a hoodie with a backpack', step: 'Student Life', title: 'Day one on campus' },
    strategy: { zone: 0.58, fit: 'strategy', label: 'A student in a shirt with an intern badge, holding a notebook', step: 'Smart Moves', title: 'Stack up experience' },
    job: { zone: 5 / 6, fit: 'job', label: 'A graduate in a suit and tie, carrying a briefcase', step: 'Job Trajectory', title: 'Suit up' },
  };

  /* ── scene dressing built in code: city and campus windows, torn grass edge ── */
  const NS = 'http://www.w3.org/2000/svg';
  const add = (parent, attrs) => { const r = document.createElementNS(NS, 'rect'); for (const k in attrs) r.setAttribute(k, attrs[k]); parent.appendChild(r); };
  [[18, 146, 3, 9], [102, 52, 3, 13], [196, 110, 3, 10], [282, 176, 4, 7]].forEach(([x0, y0, cols, rows]) => {
    for (let r = 0; r < rows; r++) for (let c = 0; c < cols; c++) {
      if ((r * 7 + c * 3) % 5 === 0) continue;   // a few windows dark
      add($('windows'), { x: x0 + c * 20, y: y0 + r * 20, width: 11, height: 12, fill: '#F2D27A' });
    }
  });
  for (let f = 0; f < 12; f++) {   // the admin tower: tan floor bands and columns of dark windows
    const y = 30 + f * 20;
    add($('towerFloors'), { x: 272, y, width: 106, height: 4, fill: '#E3CFAE' });
    for (let c = 0; c < 8; c++) add($('towerFloors'), { x: 280 + c * 12, y: y + 6, width: 6, height: 11, fill: '#3A3A42' });
  }
  for (let r = 0; r < 4; r++) for (let c = 0; c < 4; c++) add($('leftWindows'), { x: 12 + c * 15, y: 162 + r * 24, width: 8, height: 12, fill: '#3A3A42' });
  let edge = 'M0 220 V34';
  for (let x = 0; x <= 1200; x += 24) edge += ` L${x} ${30 + ((x * 37) % 17) - (x % 48 ? 0 : 6)}`;
  $('grass').setAttribute('d', edge + ' V220 Z');

  /* ── motion ───────────────────────────────────────────────────────────── */
  let current = null, flipTimer = null;
  const posX = s => scene.clientWidth * STATES[s].zone - wrap.offsetWidth / 2;
  const currentX = () => wrap.getBoundingClientRect().left - scene.getBoundingClientRect().left;
  const placeSticker = s => {
    const w = sticker.offsetWidth, W = scene.clientWidth;
    set(sticker, { x: Math.min(Math.max(W * STATES[s].zone - w / 2, 16), W - w - 16) });
  };

  function rest(ms = 280) {
    A.animate([...legs, ...arms], { rotate: 0, duration: ms, ease: 'outQuad' });
    A.animate(bob, { y: 0, rotate: 0, duration: ms, ease: 'outQuad' });
    A.animate(speed, { opacity: 0, duration: 200 });
  }
  function walk(step = 300) {
    const swing = (el, deg) => A.animate(el, { rotate: [deg, -deg], duration: step, loop: true, alternate: true, ease: 'inOutSine' });
    swing(legs[0], 22); swing(legs[1], -22); swing(arms[0], -20); swing(arms[1], 20);
    A.animate(bob, { y: [0, -7], rotate: 0, duration: step / 2, loop: true, alternate: true, ease: 'inOutSine' });
  }
  function slide() {   // glide pose: lean in, one leg forward, arms back, speed lines behind
    A.animate(legs[0], { rotate: 18, duration: 240, ease: 'outQuad' });
    A.animate(legs[1], { rotate: -28, duration: 240, ease: 'outQuad' });
    A.animate(arms[0], { rotate: 42, duration: 240, ease: 'outQuad' });
    A.animate(arms[1], { rotate: -34, duration: 240, ease: 'outQuad' });
    A.animate(bob, { rotate: 10, y: 6, duration: 260, ease: 'outQuad' });
    A.animate(speed, { opacity: [0, 1], duration: 260 });
  }
  // outfit change: the cut-out turns edge-on like a paper doll, swaps clothes, and snaps back
  function paperFlip(fit, label) {
    A.animate(flipper, { scaleX: [1, 0.04], duration: 150, ease: 'inQuad', onComplete: () => {
      kid.dataset.fit = fit; kid.setAttribute('aria-label', label);
      A.animate(flipper, { scaleX: [0.04, 1], duration: 320, ease: 'outBack' });
    } });
  }
  function showSticker(s, instant) {
    const st = STATES[s];
    const panel = document.querySelector(`.stage-panel[data-stage="${s}"]`), more = $('stickerMore');
    const fill = () => {
      $('stickerStep').textContent = st.step; $('stickerTitle').textContent = st.title;
      if (more) { more.hidden = !panel; if (panel) more.href = `#${panel.id}`; }   // link down to this stage's charts, if it has any
      placeSticker(s);
    };
    if (still) { fill(); return; }
    if (instant) { fill(); A.animate(sticker, { opacity: [0, 1], rotate: [4, -1.5], y: [16, 0], duration: 700, ease: 'outBack' }); return; }
    A.animate(sticker, { opacity: 0, rotate: -6, y: -12, duration: 170, ease: 'inQuad', onComplete: () => {
      fill();
      A.animate(sticker, { opacity: [0, 1], rotate: [5, -1.5], y: [14, 0], duration: 560, ease: 'outBack' });
    } });
  }

  function go(s, first = false) {
    if (s === current) return;
    const st = STATES[s];
    document.querySelectorAll('.tab[data-state]').forEach(t => t.setAttribute('aria-pressed', String(t.dataset.state === s)));
    document.querySelectorAll('.stage-panel').forEach(p => { p.hidden = p.dataset.stage !== s; });   // each stage shows its own charts below the scene
    const x = posX(s);
    showSticker(s, first);
    clearTimeout(flipTimer);

    if (first || still) {
      set(wrap, { x });
      kid.dataset.fit = st.fit; kid.setAttribute('aria-label', st.label);
      if (first && !still) A.animate(wrap, { y: [-90, 0], opacity: [0, 1], duration: 1000, ease: 'outBounce' });
      current = s;
      return;
    }

    const from = currentX(), dir = x >= from ? 1 : -1, dist = Math.abs(x - from);
    const gliding = s === 'job' && dir > 0;   // he slides into the job; everything else he walks
    const dur = Math.max(700, Math.min(2600, dist * (gliding ? 1.25 : 2.7)));
    A.animate(facing, { scaleX: dir, duration: 220, ease: 'outQuad' });
    gliding ? slide() : walk();
    if (st.fit !== kid.dataset.fit) flipTimer = setTimeout(() => paperFlip(st.fit, st.label), dur * 0.45);
    A.animate(wrap, { x, duration: dur, ease: gliding ? 'inOutQuart' : 'inOutSine', onComplete: () => {
      rest();
      A.animate(facing, { scaleX: 1, duration: 260, delay: 120, ease: 'outQuad' });   // turn back to face the page
    } });
    current = s;
  }

  const findingsTab = document.querySelector('.tab[href="#findings"]');
  findingsTab.addEventListener('click', () => findingsTab.setAttribute('aria-current', 'location'));
  document.querySelectorAll('[data-state]').forEach(el => el.addEventListener('click', () => {
    findingsTab.removeAttribute('aria-current');
    if (scrollY > 8) scrollTo({ top: 0, behavior: reduce ? 'auto' : 'smooth' });
    go(el.dataset.state);
  }));
  addEventListener('resize', () => { if (current) { set(wrap, { x: posX(current) }); placeSticker(current); } });

  /* ── ambient: drifting clouds, turning sun, waving flag, boiling crayon lines ── */
  if (!still) {
    A.animate('.cloud.c1', { x: [-18, 26], duration: 9000, loop: true, alternate: true, ease: 'inOutSine' });
    A.animate('.cloud.c2', { x: [22, -20], duration: 11000, loop: true, alternate: true, ease: 'inOutSine' });
    A.animate('#rays', { rotate: 360, duration: 40000, loop: true, ease: 'linear' });
    A.animate('#flag', { skewY: [-5, 4], scaleX: [1, 0.94], duration: 1400, loop: true, alternate: true, ease: 'inOutSine' });
    let visible = true, seed = 1;
    new IntersectionObserver(([e]) => { visible = e.isIntersecting; }).observe(scene);
    const boil = [...document.querySelectorAll('.boil')];
    setInterval(() => { if (!visible) return; seed = (seed % 3) + 1; boil.forEach(t => t.setAttribute('seed', seed)); }, 125);
  }

  go('life', true);
})();
