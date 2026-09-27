/*
 * Advisor Ann: the "Free consultation" chat, on crayon paper.
 *
 * A normal chat: Ann greets the student ("What can I do for you?"), the student types or
 * attaches pictures, audio and files, and Ann answers in text and out loud. On wide screens a
 * large crayon Ann talks and blinks beside the chat. Two pieces are placeholders, each one
 * function to swap: her brain (askAnn, for Gemini) and her voice (annVoice.speak, for ElevenLabs).
 */

/* ── Ann's brain ───────────────────────────────────────────────────────────
 * askAnn({ messages, attachments, conversation }) resolves with Ann's reply as plain text.
 *   messages:     the whole conversation so far, oldest first: [{ role: "user" | "ann", text }]
 *   attachments:  files sent with the latest message: [{ name, type, size, dataUrl }]
 *   conversation: bumped by ↻, so the server starts a fresh profile for a restarted chat
 * The page asks site/ann_server.py (POST /api/ann), which holds the Gemini key and runs the advisor
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
    return "My brain isn't connected yet. Soon I'll answer your questions for real. For now, you can attach a file, or head back to explore the findings!";
  }
  if (!r.ok) throw new Error(await r.text());   // the chat shows its "something went wrong" line
  return (await r.json()).reply;
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

  // Ann's reply types out like a stream (a click finishes it), is spoken, and gets a replay button
  function annSays(text) {
    const li = bubble('ann'), el = li.querySelector('.text');
    messages.push({ role: 'ann', text });
    speak(text);
    const replay = document.createElement('button');
    replay.type = 'button'; replay.className = 'replay'; replay.textContent = '▶ Play';
    replay.setAttribute('aria-label', 'Play this message out loud');
    replay.addEventListener('click', () => { if (!voiceOn) setVoice(true); speak(text); });
    li.querySelector('.meta').append(replay);
    if (reduce) { el.textContent = text; return Promise.resolve(); }
    return new Promise(done => {
      let i = 0;
      const finish = () => { clearInterval(t); el.textContent = text; li.removeEventListener('click', finish); scrollDown(); done(); };
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
    }
    messages.push({ role: 'user', text });
    status('Thinking…');
    const typing = bubble('ann');
    typing.classList.add('typing');
    typing.querySelector('.text').innerHTML = '<span class="dots"><i></i><i></i><i></i></span>';
    const mine = session;
    let reply;
    try { reply = await askAnn({ messages: messages.slice(), attachments, conversation: session }); }
    catch { reply = 'Sorry, something went wrong on my side. Try again in a moment.'; }
    if (mine !== session) return;   // the chat was restarted while Ann was thinking
    typing.remove();
    busy = false;
    status('Online');
    await annSays(String(reply || '…'));
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
  // ↻ starts a fresh conversation: stop her voice, clear the chat and the file tray, greet again
  $('restartBtn').addEventListener('click', () => {
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
    hello();
    box.focus({ preventScroll: true });
  });

  hello();
})();
