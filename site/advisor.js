/*
 * Advisor Ann: the "Free consultation" chat, on crayon paper.
 *
 * A normal chat: Ann greets the student ("What can I do for you?"), the student types or
 * attaches pictures, audio and files, and Ann answers in text and out loud. On wide screens a
 * large crayon Ann talks and blinks beside the chat. Two pieces are placeholders, each one
 * function to swap: her brain (askAnn, for Claude) and her voice (annVoice.speak, for ElevenLabs).
 */

/* ── Ann's brain ───────────────────────────────────────────────────────────
 * askAnn({ messages, attachments, conversation }) resolves with Ann's reply: { reply, charts }.
 *   messages:     the whole conversation so far, oldest first: [{ role: "user" | "ann", text }]
 *   attachments:  files sent with the latest message: [{ name, type, size, dataUrl }]
 *   conversation: bumped by ↻, so the server starts a fresh profile for a restarted chat
 *   reply:        what she says, as plain text
 *   charts:       chart specs for charts.js (with her advice: "Where you line up" and more), drawn under it
 * The page asks site/ann_server.py (POST /api/ann), which holds the Anthropic key and runs the advisor
 * (never put a key in this page: anyone who opens it can read it). Opened without that server,
 * for example the built file from disk, Ann says her brain isn't connected.
 */
const annPage = crypto.randomUUID?.() || String(Math.random()).slice(2);   // one id per open page
let annConversation = `${annPage}-0`;   // the chat Ann is in; her voice lines are logged under it too
async function askAnn({ messages, attachments, conversation = 0 }) {
  annConversation = `${annPage}-${conversation}`;
  let r;
  try {
    r = await fetch('/api/ann', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ conversation: annConversation, messages, attachments }),
    });
  } catch {
    return { reply: "My brain isn't connected yet. Soon I'll answer your questions for real. For now, you can attach a file, or head back to explore the findings!" };
  }
  if (!r.ok) throw new Error(await r.text());   // the chat shows its "something went wrong" line
  return r.json();
}

/* ── Ann's voice ──────────────────────────────────────────────────────────
 * annVoice.speak(text, { onStart, onEnd, onBlocked }) says `text` out loud.
 * First choice: ElevenLabs, through site/ann_server.py (POST /api/voice, which holds the key).
 * Fallback when that server or key isn't there: the browser's built-in speech, with a female
 * English voice when the browser has one. The rest of the page needs no change.
 */
const annVoice = (() => {
  const synth = window.speechSynthesis || null;
  let voice = null, audio = null, token = 0;
  let eleven = location.protocol.startsWith('http');   // turns false after the first failure, so later lines skip the round trip
  const pick = () => {
    const vs = synth ? synth.getVoices() : [];
    for (const p of [/Samantha/, /Google US English/, /Aria/, /Jenny/, /Karen/, /Victoria/, /Zira/, /Female/i]) {
      const v = vs.find(x => p.test(x.name) && /^en/i.test(x.lang));
      if (v) return v;
    }
    return vs.find(x => /^en/i.test(x.lang)) || null;
  };
  if (synth) { voice = pick(); synth.addEventListener?.('voiceschanged', () => { voice = pick(); }); }
  function browserSpeak(text, { onStart, onEnd, onBlocked }) {
    if (!synth) return onEnd?.();
    const u = new SpeechSynthesisUtterance(text);
    if (voice) u.voice = voice;
    u.rate = 1.02; u.pitch = 1.15;
    u.onstart = () => onStart?.();
    u.onend = () => onEnd?.();
    u.onerror = e => { onEnd?.(); if (e.error === 'not-allowed') onBlocked?.(); };   // browsers need a tap before speech
    synth.speak(u);
  }
  const stop = () => { token++; audio?.pause(); audio = null; synth?.cancel(); };
  return {
    available: !!synth || eleven,
    async speak(text, cb = {}) {
      stop();
      const mine = token;   // a newer speak() or stop() makes this one drop its audio when it arrives
      if (eleven) {
        try {
          const r = await fetch('/api/voice', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text, conversation: annConversation }) });
          if (!r.ok) throw new Error(`voice ${r.status}`);
          const url = URL.createObjectURL(await r.blob());
          if (mine !== token) return URL.revokeObjectURL(url);
          audio = new Audio(url);
          audio.onplay = () => cb.onStart?.();
          audio.onended = () => { URL.revokeObjectURL(url); cb.onEnd?.(); };
          try { await audio.play(); } catch (e) { cb.onEnd?.(); if (e.name === 'NotAllowedError') cb.onBlocked?.(); }   // browsers need a tap before sound
          return;
        } catch { eleven = false; }
      }
      if (mine === token) browserSpeak(text, cb);
    },
    stop,
  };
})();

(() => {
  const A = window.anime;
  const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const $ = id => document.getElementById(id);
  const log = $('log'), box = $('msg'), tray = $('tray'), fileIn = $('fileIn');
  const messages = [];
  let pending = [];   // files waiting in the tray: { file, url }
  let busy = false, voiceOn = annVoice.available, blockedText = null;
  let session = 0;   // bumped by ↻ so replies from a conversation that was restarted are dropped

  /* ── status, mouth and voice ─────────────────────────────────────────── */
  const status = text => document.querySelectorAll('[data-status]').forEach(el => { el.textContent = text; });
  let mouth = null;
  const talking = on => {   // Ann's mouth (portrait and header avatar) opens and closes while she speaks
    clearInterval(mouth);
    document.querySelectorAll('.ann-talks').forEach(el => el.classList.remove('open'));
    if (on && !reduce) mouth = setInterval(() => document.querySelectorAll('.ann-talks').forEach(el => el.classList.toggle('open')), 140);
    status(on ? 'Speaking…' : busy ? 'Thinking…' : 'Online');
  };
  function speak(text) {
    if (!voiceOn) return;
    annVoice.speak(text, {
      onStart: () => talking(true),
      onEnd: () => talking(false),
      onBlocked: () => {   // the Voice button turns gold: the next tap anywhere plays this line
        blockedText = text;
        document.querySelectorAll('[data-voice]').forEach(b => { b.classList.add('nudge'); b.querySelector('.label').textContent = 'Tap to hear Ann'; });
      },
    });
  }
  addEventListener('pointerdown', () => {
    if (!blockedText) return;
    const t = blockedText; blockedText = null;
    document.querySelectorAll('[data-voice]').forEach(b => b.classList.remove('nudge'));
    setVoice(voiceOn); speak(t);
  });
  function setVoice(on) {
    voiceOn = on && annVoice.available;
    if (!voiceOn) { annVoice.stop(); talking(false); }
    document.querySelectorAll('[data-voice]').forEach(b => {
      b.setAttribute('aria-pressed', String(voiceOn));
      b.querySelector('.label').textContent = voiceOn ? 'Voice on' : 'Voice off';
      if (!annVoice.available) { b.disabled = true; b.title = 'This browser has no speech voice'; }
    });
  }
  document.querySelectorAll('[data-voice]').forEach(b => b.addEventListener('click', () => setVoice(!voiceOn)));

  /* ── messages ─────────────────────────────────────────────────────────── */
  const time = () => new Date().toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  const scrollDown = () => { log.scrollTop = log.scrollHeight; };
  const size = n => (n > 1e6 ? `${(n / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1e3))} KB`);

  function bubble(role) {
    const li = document.createElement('li');
    li.className = `msg ${role}`;
    li.innerHTML = role === 'ann'
      ? '<svg class="avatar" viewBox="0 0 100 100" aria-hidden="true"><use href="#annFace"/></svg><div class="body"><div class="bubble"><p class="text"></p></div><div class="meta"></div></div>'
      : '<div class="body"><div class="bubble"><p class="text"></p></div><div class="meta"></div></div><svg class="avatar" viewBox="0 0 100 100" aria-hidden="true"><use href="#youFace"/></svg>';
    li.querySelector('.meta').textContent = time();
    log.append(li);
    scrollDown();
    return li;
  }

  function attachmentView(file, url) {
    const el = document.createElement('div');
    el.className = 'file';
    if (file.type.startsWith('image/')) { const img = new Image(); img.src = url; img.alt = file.name; el.append(img); el.classList.add('pic'); }
    else if (file.type.startsWith('audio/')) { const au = document.createElement('audio'); au.controls = true; au.src = url; el.append(au); }
    else { const ic = document.createElement('span'); ic.className = 'ficon'; ic.setAttribute('aria-hidden', 'true'); el.append(ic); }
    if (!file.type.startsWith('image/')) { const n = document.createElement('span'); n.className = 'fname'; n.textContent = `${file.name} · ${size(file.size)}`; el.append(n); }
    return el;
  }

  // Ann's reply types out like a stream (a click finishes it), is spoken, and gets a replay button;
  // the charts she sends with it draw themselves in underneath once the text is out
  function annSays(text, charts = []) {
    const li = bubble('ann'), el = li.querySelector('.text');
    messages.push({ role: 'ann', text });
    speak(text);
    const replay = document.createElement('button');
    replay.type = 'button'; replay.className = 'replay'; replay.textContent = '▶ Play';
    replay.setAttribute('aria-label', 'Play this message out loud');
    replay.addEventListener('click', () => { if (!voiceOn) setVoice(true); speak(text); });
    li.querySelector('.meta').append(replay);
    const showCharts = () => {
      if (!charts?.length || !window.successCharts) return;
      li.classList.add('wide');
      const box = document.createElement('div');
      box.className = 'ann-charts';
      li.querySelector('.meta').before(box);
      for (const spec of charts) {
        const card = document.createElement('figure'), chart = document.createElement('div');
        card.className = 'ann-chart'; chart.className = 'chart';
        card.append(chart);
        box.append(card);
        window.successCharts.mount(chart, spec, { play: true });
      }
    };
    if (reduce) { el.textContent = text; showCharts(); scrollDown(); return Promise.resolve(); }
    return new Promise(done => {
      let i = 0;
      const finish = () => { clearInterval(t); el.textContent = text; li.removeEventListener('click', finish); showCharts(); scrollDown(); done(); };
      const t = setInterval(() => { el.textContent = text.slice(0, ++i); scrollDown(); if (i >= text.length) finish(); }, 16);
      li.addEventListener('click', finish);
    });
  }

  /* ── the tray of files waiting to be sent ─────────────────────────────── */
  function addFiles(files) {
    for (const file of files) pending.push({ file, url: URL.createObjectURL(file) });
    renderTray();
    box.focus();
  }
  function renderTray() {
    tray.replaceChildren();
    tray.hidden = !pending.length;
    pending.forEach((p, i) => {
      const chip = attachmentView(p.file, p.url);
      chip.classList.add('chip-file');
      const x = document.createElement('button');
      x.type = 'button'; x.className = 'remove'; x.textContent = '×';
      x.setAttribute('aria-label', `Remove ${p.file.name}`);
      x.addEventListener('click', () => { URL.revokeObjectURL(p.url); pending.splice(i, 1); renderTray(); });
      chip.append(x);
      tray.append(chip);
    });
    syncSend();
  }
  const syncSend = () => { $('send').disabled = busy || (!box.value.trim() && !pending.length); };

  /* ── sending ──────────────────────────────────────────────────────────── */
  const readFile = f => new Promise(res => { const r = new FileReader(); r.onload = () => res(r.result); r.onerror = () => res(null); r.readAsDataURL(f); });

  async function send(text) {
    text = text.trim();
    if (busy || (!text && !pending.length)) return;
    busy = true;
    const mine = session;   // a restart (↻ or a sample profile) while this is in flight drops it
    const files = pending; pending = []; renderTray();
    box.value = ''; autosize(); syncSend();
    $('suggest')?.remove();
    const li = bubble('you');
    li.querySelector('.text').textContent = text;
    if (!text) li.querySelector('.text').remove();
    const attachments = [];
    if (files.length) {
      const wrap = document.createElement('div');
      wrap.className = 'files';
      files.forEach(p => wrap.append(attachmentView(p.file, p.url)));
      li.querySelector('.bubble').append(wrap);
      for (const p of files) attachments.push({ name: p.file.name, type: p.file.type || 'application/octet-stream', size: p.file.size, dataUrl: await readFile(p.file) });
      if (mine !== session) return;
    }
    messages.push({ role: 'user', text });
    status('Thinking…');
    const typing = bubble('ann');
    typing.classList.add('typing');
    typing.querySelector('.text').innerHTML = '<span class="dots"><i></i><i></i><i></i></span>';
    let reply;
    try { reply = await askAnn({ messages: messages.slice(), attachments, conversation: session }); }
    catch { reply = { reply: 'Sorry, something went wrong on my side. Try again in a moment.' }; }
    if (mine !== session) return;   // the chat was restarted while Ann was thinking
    typing.remove();
    busy = false;
    status('Online');
    await annSays(String(reply.reply || '…'), reply.charts);
    syncSend();
    box.focus({ preventScroll: true });
  }

  const autosize = () => { box.style.height = 'auto'; box.style.height = `${Math.min(box.scrollHeight, 160)}px`; };
  box.addEventListener('input', () => { autosize(); syncSend(); });
  box.addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(box.value); } });
  $('composer').addEventListener('submit', e => { e.preventDefault(); send(box.value); });
  $('attach').addEventListener('click', () => fileIn.click());
  fileIn.addEventListener('change', () => { addFiles([...fileIn.files]); fileIn.value = ''; });
  addEventListener('dragover', e => { e.preventDefault(); document.body.classList.add('drop'); });
  addEventListener('dragleave', e => { if (!e.relatedTarget) document.body.classList.remove('drop'); });
  addEventListener('drop', e => { e.preventDefault(); document.body.classList.remove('drop'); const fs = [...(e.dataTransfer?.files || [])]; if (fs.length) addFiles(fs); });
  box.addEventListener('paste', e => { const fs = [...(e.clipboardData?.files || [])]; if (fs.length) { e.preventDefault(); addFiles(fs); } });

  $('closeBtn').addEventListener('click', e => { e.preventDefault(); if (history.length > 1) history.back(); else location.href = './'; });

  /* ── hello ────────────────────────────────────────────────────────────── */
  async function hello() {
    const mine = session;
    setVoice(voiceOn);
    syncSend();
    if (A && !reduce) A.animate('#annWave', { rotate: [0, -150, -120, -150, -120, 0], duration: 1600, delay: 300, ease: 'inOutSine' });   // she waves
    await annSays("Hi, I'm Advisor Ann! What can I do for you?");
    if (mine !== session) return;
    const s = document.createElement('li');
    s.id = 'suggest';
    s.className = 'suggest';
    for (const t of ['Help me plan my internships', 'What should I do this semester?', 'Look at my resume']) {
      const b = document.createElement('button');
      b.type = 'button'; b.textContent = t;
      b.addEventListener('click', () => send(t));
      s.append(b);
    }
    log.append(s);
    scrollDown();
  }
  // a fresh conversation: stop her voice, clear the chat and the file tray (↻ then greets again)
  function reset() {
    session++;
    annVoice.stop(); talking(false);
    busy = false;
    blockedText = null;
    document.querySelectorAll('[data-voice]').forEach(b => b.classList.remove('nudge'));
    messages.length = 0;
    pending.forEach(p => URL.revokeObjectURL(p.url)); pending = []; renderTray();
    box.value = ''; autosize();
    log.replaceChildren();
    status('Online');
    document.querySelectorAll('[data-profile]').forEach(b => b.setAttribute('aria-pressed', 'false'));
  }
  $('restartBtn').addEventListener('click', () => { reset(); hello(); box.focus({ preventScroll: true }); });

  /* ── sample students ─────────────────────────────────────────────────────
   * Three fictional UMBC Computer Science students. Each card hands Ann an unofficial transcript as a
   * .txt file, in a fresh conversation so two students never mix. Everything Ann's server asks for is
   * spelled out: major, track, credits, GPA, entry, first-gen, work hours, every course code with its
   * grade (codes from the course catalog, IP = in progress) and the student's experiences.
   */
  const COURSES = {
    CMSC201: ['Foundations of Computer Science I', 4], CMSC202: ['Foundations of Computer Science II', 4], CMSC203: ['Discrete Structures', 3],
    CMSC304: ['Social and Ethical Issues in Computing', 3], CMSC313: ['Computer Organization and Assembly', 3], CMSC331: ['Principles of Programming Languages', 3],
    CMSC341: ['Data Structures', 4], CMSC345: ['Software Design and Development', 3], CMSC411: ['Computer Architecture', 3],
    CMSC421: ['Principles of Operating Systems', 3], CMSC425: ['Cloud Computing and Distributed Systems', 3], CMSC426: ['Web Application Architecture', 3],
    CMSC441: ['Design and Analysis of Algorithms', 3], CMSC447: ['Software Engineering Capstone', 3], CMSC461: ['Database Management Systems', 3],
    CMSC462: ['Data Warehousing and Pipelines', 3], CMSC471: ['Introduction to Data Mining', 3], CMSC475: ['Data Visualization', 3],
    CMSC478: ['Introduction to Machine Learning', 3], CMSC479: ['Neural Networks and Deep Learning', 3], CMSC481: ['Natural Language Processing', 3],
    MATH151: ['Calculus and Analytic Geometry I', 4], MATH152: ['Calculus and Analytic Geometry II', 4], MATH301: ['Linear Algebra', 3],
    STAT355: ['Probability and Statistics for Computing', 3], PHYS121: ['Introductory Physics I', 4], ENGL100: ['Composition and Rhetoric', 3],
    ENGL393: ['Technical Communication', 3], HIST103: ['United States History since 1865', 3], ECON101: ['Principles of Microeconomics', 3],
    ECON102: ['Principles of Macroeconomics', 3], ARTH100: ['Introduction to Art History', 3], BIOL141: ['Foundations of Biology', 4], CHEM101: ['Principles of Chemistry', 4],
  };
  const PROFILES = [
    { level: 'Sophomore', track: 'Cybersecurity', entry: 'Fall 2025', firstGen: true, work: 15,
      terms: [['Fall 2025', 'CMSC201 B', 'MATH151 B', 'ENGL100 A', 'HIST103 B', 'ARTH100 A'],
              ['Spring 2026', 'CMSC202 B', 'CMSC203 C', 'MATH152 B', 'ECON101 B', 'PHYS121 W'],
              ['Fall 2026', 'CMSC313 IP', 'CMSC341 IP', 'STAT355 IP', 'ENGL393 IP']],
      experiences: [['Student Organization', 'Retriever Cyber Club', 'UMBC', 'Member', 'Fall 2025 to now'],
                    ['Campus Job', 'Circulation Desk Assistant', 'Albin O. Kuhn Library', 'Employee', 'Fall 2025 to now, 15 hours a week']] },
    { level: 'Junior', track: 'Software Engineering', entry: 'Fall 2024', firstGen: false, work: 6,
      terms: [['Fall 2024', 'CMSC201 A', 'MATH151 A', 'ENGL100 B', 'ECON101 A'],
              ['Spring 2025', 'CMSC202 A', 'CMSC203 B', 'MATH152 B', 'BIOL141 A'],
              ['Summer 2025', 'HIST103 A', 'ARTH100 A'],
              ['Fall 2025', 'CMSC313 B', 'CMSC331 A', 'CMSC341 A', 'STAT355 B'],
              ['Spring 2026', 'CMSC304 A', 'CMSC345 A', 'MATH301 B', 'PHYS121 B'],
              ['Fall 2026', 'CMSC411 IP', 'CMSC441 IP', 'CMSC461 IP', 'ENGL393 IP']],
      experiences: [['Internship', 'Software Engineering Intern', 'Northwind Platform Co.', 'Intern', 'Summer 2026'],
                    ['Student Organization', 'Association for Computing Machinery Student Chapter', 'UMBC', 'Officer', 'Fall 2025 to now'],
                    ['Hackathon', 'HackUMBC', 'UMBC', 'Participant', 'Fall 2025'],
                    ['Certification', 'Java Application Developer', 'Brightline Code School', null, 'Spring 2026'],
                    ['Tutoring', 'CMSC 201 Tutor', 'Department of Computer Science', 'Tutor', 'Spring 2026 to now, 6 hours a week']] },
    { level: 'Senior', track: 'Data Science', entry: 'Fall 2023', firstGen: true, work: 20,
      terms: [['Fall 2023', 'CMSC201 B', 'MATH151 C', 'ENGL100 B', 'HIST103 B'],
              ['Spring 2024', 'CMSC202 C', 'CMSC203 C', 'MATH152 D', 'ECON101 B'],
              ['Fall 2024', 'CMSC313 C', 'CMSC341 C', 'STAT355 B', 'BIOL141 B', 'CHEM101 W'],
              ['Spring 2025', 'CMSC331 B', 'CMSC304 A', 'MATH301 C', 'PHYS121 C', 'ECON102 B'],
              ['Summer 2025', 'ARTH100 A'],
              ['Fall 2025', 'CMSC411 C', 'CMSC461 B', 'CMSC471 B', 'CMSC426 B', 'ENGL393 A'],
              ['Spring 2026', 'CMSC421 C', 'CMSC441 C', 'CMSC475 A', 'CMSC478 B', 'CMSC462 B'],
              ['Fall 2026', 'CMSC447 IP', 'CMSC479 IP', 'CMSC481 IP', 'CMSC425 IP']],
      experiences: [['Internship', 'Data Analytics Intern', 'National Data Services Agency', 'Intern', 'Summer 2025'],
                    ['Internship', 'Data Engineering Intern', 'Meridian National Systems', 'Intern', 'Summer 2026'],
                    ['Undergraduate Research', 'Research Assistant', 'Applied Machine Learning Lab', 'Researcher', 'Spring 2026 to now'],
                    ['Campus Job', 'Help Desk Technician', 'Division of Information Technology', 'Employee', 'Fall 2024 to now, 20 hours a week']] },
  ];
  const POINTS = { A: 4, B: 3, C: 2, D: 1, F: 0 };
  const plural = (n, w) => `${n} ${w}${n === 1 ? '' : 's'}`;
  PROFILES.forEach((p, i) => {
    let points = 0, gpaCredits = 0, earned = 0;
    const terms = p.terms.map(([term, ...courses]) => {
      let tp = 0, tc = 0;
      const rows = courses.map(c => {
        const [id, grade] = c.split(' '), [title, credits] = COURSES[id];
        if (grade in POINTS) { tp += POINTS[grade] * credits; tc += credits; if (grade !== 'F') earned += credits; }
        return `  ${id.replace(/^[A-Z]+/, '$& ').padEnd(10)}${title.padEnd(42)}${credits.toFixed(1).padStart(4)}   ${grade}`;
      });
      points += tp; gpaCredits += tc;
      return tc ? `${term}\n${rows.join('\n')}\n  Term GPA: ${(tp / tc).toFixed(2)}` : `${term} (in progress)\n${rows.join('\n')}`;
    });
    p.gpa = (points / gpaCredits).toFixed(2);
    p.earned = earned;
    p.internships = p.experiences.filter(e => e[0] === 'Internship' || e[0] === 'Co-op').length;
    p.text = [
      'UNIVERSITY OF MARYLAND, BALTIMORE COUNTY (UMBC)',
      'Unofficial Transcript. SAMPLE: a fictional student made up for the Advisor Ann demo.',
      '',
      `Student: Sample Student ${i + 1} (Profile ${i + 1})`,
      'Major: Computer Science, B.S.',
      `Track: ${p.track}`,
      `Entered UMBC: ${p.entry}, First-Time Freshman`,
      `Class level: ${p.level}`,
      `Credits earned: ${p.earned}`,
      `Cumulative GPA: ${p.gpa}`,
      `First-generation college student: ${p.firstGen ? 'Yes' : 'No'}`,
      `Work hours per week: ${p.work}`,
      '',
      'COURSEWORK (grades: A B C D F, W = withdrew, IP = in progress)',
      terms.join('\n\n'),
      '',
      "EXPERIENCE (from the student's resume)",
      ...p.experiences.map(([type, name, org, role, when]) => `  ${type}: ${name}, ${org}${role ? `, ${role}` : ''}, ${when}`),
      '',
    ].join('\n');
  });

  const samples = document.querySelector('.samples');
  PROFILES.forEach((p, i) => {
    const b = document.createElement('button');
    b.type = 'button'; b.className = 'profile'; b.dataset.profile = i; b.setAttribute('aria-pressed', 'false');
    b.innerHTML = `<b>Profile ${i + 1}</b><span class="line">${p.level}<span class="more"> · ${p.track}</span></span>`   // phones keep only level and GPA
      + `<span class="line">GPA ${p.gpa}<span class="more"> · ${p.earned} credits</span></span>`
      + `<span class="line more">${p.internships ? plural(p.internships, 'internship') : 'No internships yet'}</span>`
      + '<span class="go">Give Ann this transcript →</span>';
    b.setAttribute('aria-label', `Profile ${i + 1}: ${p.level}, ${p.track} track, GPA ${p.gpa}. Send this sample transcript to Ann.`);
    b.addEventListener('click', () => {
      reset();
      b.setAttribute('aria-pressed', 'true');
      addFiles([new File([p.text], `profile-${i + 1}-transcript.txt`, { type: 'text/plain' })]);
      send(`Here's my unofficial transcript (Profile ${i + 1}). Can you analyze it?`);
    });
    samples.append(b);
  });

  hello();
})();
