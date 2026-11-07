// X Pixel Map - session UI client.
// Renders Session.view() from the server and sends only the actions Session.act() accepts:
// tick, continue, approve, review, retry/start, back.

const API = {
  state: '/api/state',
  act: '/api/act',
  tile: '/api/tile',
  file: '/api/file',
  sessions: '/api/sessions',
  newSession: '/api/new',
  resume: '/api/resume',
  shutdown: '/api/shutdown'
};

let S = null;                 // last state from /api/state
let selected = new Set();     // review step: "x,y" keys
let reviewKey = null;         // resets the selection when the review step (re)appears
let reviewFilter = 'all';
let showOther = false;      // review: also list pixels there is nothing to tick for
let busy = false;
let fetching = false;
let lastCardKey = '';
let lastIndex = -1;
let toastTimer = null;
let demoRunning = false;
let demoStop = false;
let homeKey = '';
let sessionsCache = null;
let sessionsAt = 0;
let homeBusy = false;
let page = 'app';             // 'app' (home or session) | 'about' (the plain-language guide)
let appScroll = 0;            // where the app was scrolled when the guide opened

const $ = id => document.getElementById(id);
const els = {
  steps: $('steps'), card: $('card'), log: $('log'), loglast: $('loglast'),
  cam: $('cam'), img: $('img'), sim: $('sim'), model: $('model'), rb: $('rb'),
  controls: $('controls'), guide: $('guidebody'),
  home: $('home'), homeCards: $('home-cards'), about: $('about'),
  session: $('session'), progress: $('progress')
};

const ICON = {
  continue: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="9 18 15 12 9 6"/></svg>',
  retry: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>',
  start: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="6 3 21 12 6 21 6 3"/></svg>',
  back: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="15 18 9 12 15 6"/></svg>',
  review: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg>',
  demo: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><polygon points="10 8 16 12 10 16 10 8"/></svg>',
  exit: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg>',
  lock: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/></svg>',
  info: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><line x1="12" y1="11" x2="12" y2="16"/><line x1="12" y1="8" x2="12" y2="8"/></svg>'
};

// ---- small DOM helpers --------------------------------------------------------------------------------

function h(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined && text !== null) e.textContent = text;
  return e;
}

function add(parent, ...kids) {
  kids.forEach(k => { if (k) parent.appendChild(k); });
  return parent;
}

function current() {
  return S && S.steps && S.index < S.steps.length ? S.steps[S.index] : null;
}

function clamp(v) {
  return Math.max(0, Math.min(1, Number(v) || 0));
}

// ---- polling ------------------------------------------------------------------------------------------

let stateSeq = 0, stateApplied = 0;
async function refresh(force) {
  if (stopped || (fetching && !force)) return;
  fetching = true;
  const mine = ++stateSeq;
  try {
    const res = await fetch(API.state, { cache: 'no-store' });
    if (!res.ok) return;
    const data = await res.json();
    if (mine < stateApplied) return;            // an older answer that arrived late must not undo a newer one (ticks flickering back)
    stateApplied = mine; S = data;
  } catch (e) {
    return;                       // keep the last good state; the next poll retries
  } finally {
    fetching = false;
  }
  const home = !!(S && S.home);
  applyPage();
  renderHeader();
  if (home) { renderHome(!!force); return; }

  syncReview();
  renderSteps();
  renderCard(!!force);
  renderGuide();
  renderLog();
}

// The guide is a page of its own, not a session step: it can be opened from the home menu or the header and
// Escape (or Back) returns to whatever the app was showing. Refresh runs every second, so page visibility is
// applied from here each time or the home/session view would reappear underneath it.
function applyPage() {
  const about = page === 'about';
  document.body.classList.toggle('about-open', about);
  if (els.about) els.about.hidden = !about;
  if (about) {
    if (els.home) els.home.hidden = true;
    if (els.session) els.session.hidden = true;
    if (els.progress) els.progress.hidden = true;
    return;
  }
  if (!S) {
    if (els.home) els.home.hidden = true;
    if (els.session) els.session.hidden = true;
    if (els.progress) els.progress.hidden = true;
    return;
  }
  const home = !!(S && S.home);
  if (els.home) els.home.hidden = !home;
  if (els.session) els.session.hidden = home;
  if (els.progress) els.progress.hidden = home;
}

function setHash(tag) {
  try { history.replaceState(null, '', tag ? '#' + tag : location.pathname + location.search); }
  catch (e) { /* some file:// or blocked-history contexts refuse; the page still switches */ }
}

function openAbout() {
  if (page === 'about') return;
  appScroll = window.scrollY || 0;
  page = 'about';
  setHash('about');
  applyPage();
  if (els.controls) renderHeader();
  window.scrollTo(0, 0);
}

function closeAbout() {
  page = 'app';
  setHash('');
  applyPage();
  if (els.controls) renderHeader();
  window.scrollTo(0, appScroll || 0);
}

function init() {
  if (/^#about(?:-|$)/.test(location.hash)) page = 'about';
  applyPage();
  refresh(true);
  setInterval(() => refresh(false), 1000);
}

// ---- header -------------------------------------------------------------------------------------------

function renderHeader() {
  if (!S) return;

  const mode = S.camera || 'none';
  const dev = S.device || {};
  els.cam.className = 'pill ' + mode;
  els.cam.textContent = { normal: 'Normal mode', service: 'Service mode', none: 'Not connected' }[mode] || mode;
  els.cam.title = 'What the computer sees on USB right now';
  if (els.model) els.model.textContent = dev.model ? `FUJIFILM ${dev.model}` + (dev.firmware ? ` · firmware ${dev.firmware}` : '') : 'No camera';

  const now = S.holds && S.holds.now, rb = S.holds && S.holds.rollback;
  els.img.textContent = now ? `In camera: ${now.name}` : 'In camera: not read yet';
  els.img.title = now ? `image ${now.sha12}, from its locked read-back backup` : 'read the camera once in service mode';
  if (els.rb) {
    els.rb.hidden = !rb;
    if (rb) { els.rb.textContent = `Previous: ${rb.name}`; els.rb.title = `locked backup ${rb.sha12}, saved ${rb.date}`; }
  }

  els.sim.hidden = !S.simulate;

  if (!els.controls) return;
  els.controls.innerHTML = '';                    // the step's own button lives in the card; only Demo up here

  const steps = S.steps || [];
  const prog = $('prog');
  if (prog) prog.style.width = (steps.length ? 100 * steps.filter(x => x.status === 'done' || x.status === 'skipped').length / steps.length : 0).toFixed(1) + '%';
  renderLock();
  if (page === 'about') {
    const back = h('button', 'control-btn', 'Back');
    back.type = 'button';
    back.title = 'Leave the guide and return to the app';
    back.innerHTML = ICON.back;
    back.appendChild(h('span', null, 'Back to the app'));
    back.onclick = closeAbout;
    els.controls.appendChild(back);
    els.controls.appendChild(exitButton());
    return;
  }
  if (S.home) { els.controls.appendChild(aboutButton()); els.controls.appendChild(exitButton()); return; }

  const running = steps.some(x => x.status === 'running');
  const hb = h('button', 'control-btn', 'Home');
  hb.type = 'button'; hb.title = 'Back to the home menu; this session stays saved';
  hb.disabled = running;
  hb.onclick = () => goHome();
  els.controls.appendChild(hb);

  const sw = (recipe, label, title) => {
    const b = h('button', 'control-btn'); b.type = 'button'; b.title = title; b.disabled = running;
    b.appendChild(h('span', null, label));
    b.onclick = () => { if (confirm(title + '?\n\nThe current session is saved and can be resumed later.')) newSession(recipe); };
    els.controls.appendChild(b);
  };
  if (S.recipe !== 'rollback' && S.holds && S.holds.rollback) sw('rollback', 'Roll back…', 'Start a guided roll back to an earlier pixel map');
  if (S.recipe === 'rollback') sw('map', 'Pixel mapping', 'Start a new pixel-mapping session');

  if (S.simulate) {
    const b = h('button', 'control-btn demo' + (demoRunning ? ' on' : ''));
    b.type = 'button';
    b.title = demoRunning ? 'Stop after the current action' : 'Simulation only: run the rest of the recipe automatically';
    b.innerHTML = ICON.demo;
    b.appendChild(h('span', null, demoRunning ? 'Stop demo' : 'Demo'));
    b.onclick = () => {
      if (demoRunning) demoStop = true;
      else startDemo();
    };
    els.controls.appendChild(b);
  }
  els.controls.appendChild(aboutButton());
  els.controls.appendChild(exitButton());
}

function aboutButton() {
  const b = h('button', 'control-btn', 'How it works');
  b.type = 'button';
  b.title = 'A plain-language guide to hot pixels and what this tool changes';
  b.innerHTML = ICON.info;
  b.appendChild(h('span', null, 'How it works'));
  b.onclick = () => openAbout();
  return b;
}

// ---- exit lock ----------------------------------------------------------------------------------------
// While a step runs the software is talking to the camera: Exit is locked, the browser warns before the tab
// closes, and the server refuses to stop (Ctrl+C in the terminal waits for the step to finish).

function locked() {
  return !!(S && S.lock && S.lock.locked);
}

function exitButton() {
  const lk = locked();
  const b = h('button', 'control-btn exit' + (lk ? ' locked' : ''));
  b.type = 'button';
  b.disabled = lk;
  b.title = lk ? S.lock.reason : 'Stop the local server and close this page';
  b.innerHTML = lk ? ICON.lock : ICON.exit;
  b.appendChild(h('span', null, 'Exit'));
  b.onclick = () => exitApp();
  return b;
}

function renderLock() {
  const bar = $('lockbar');
  if (!bar) return;
  const lk = locked();
  bar.hidden = !lk;
  bar.className = 'lockbar' + (lk && S.lock.writing ? ' writing' : '');
  if (lk) {
    bar.innerHTML = ICON.lock;
    bar.appendChild(h('span', null, (S.lock.writing ? 'Locked: ' : 'Busy: ') + S.lock.reason));
  }
  const dlg = $('exitdlg');
  if (dlg && dlg.open && lk) dlg.close();          // a step started while the dialog was open
}

window.addEventListener('beforeunload', e => {
  if (!locked()) return;
  e.preventDefault();
  e.returnValue = '';                              // the browser shows its own "Leave site?" warning
});

function exitWarning() {
  if (!S || S.home) return ['Saved sessions stay on disk. Start the server again to come back.'];
  const lines = [];
  if (current()) {
    lines.push('This session is not finished. Its progress is saved; pick it up later from Resume on the home menu.');
    if (S.camera === 'service') lines.push('The camera is still in service mode. Before using it, finish the session, or turn it off, take the battery out and put it back in.');
    const restore = (S.steps || []).find(x => x.id === 'restore');
    if (restore && restore.status !== 'done' && (S.steps || []).some(x => x.writes && x.status === 'done'))
      lines.push('Your camera settings and clock are not restored yet; that step is still ahead.');
  } else {
    lines.push('The session is complete. Everything is saved.');
  }
  return lines;
}

function exitApp() {
  if (locked()) { toast(S.lock.reason); return; }
  let dlg = $('exitdlg');
  if (!dlg) { dlg = h('dialog', 'dlg'); dlg.id = 'exitdlg'; document.body.appendChild(dlg); }
  dlg.innerHTML = '';
  const unfinished = S && !S.home && current();
  const app = inAppWindow();
  add(dlg, h('div', 'phase-l', unfinished ? 'Exit before the end' : 'Exit'),
           h('h2', null, app ? 'Close X Pixel Map?' : 'Stop the local server?'));
  exitWarning().forEach(t => dlg.appendChild(h('p', 'why', t)));
  if (app) dlg.appendChild(h('p', 'why', 'Closing also stops the local server.'));
  const btns = h('div', 'btns');
  const stop = h('button', unfinished ? 'warn' : 'primary', app ? 'Close X Pixel Map' : 'Stop server and exit');
  stop.type = 'button';
  stop.onclick = async () => { stop.disabled = true; await stopServer(); };
  const keep = h('button', null, 'Keep working');
  keep.type = 'button';
  keep.onclick = () => dlg.close();
  add(btns, stop, keep);
  dlg.appendChild(btns);
  dlg.showModal();
  keep.focus();                                     // Enter keeps working; stopping takes a deliberate click
}

let stopped = false;

function inAppWindow() {
  return !!window.pywebview;                        // pywebview's bridge object: the desktop app window, not a browser tab
}

async function stopServer() {
  try {
    const res = await fetch(API.shutdown, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
    const d = await res.json().catch(() => ({}));
    if (!d.ok) { const g = $('exitdlg'); if (g) g.close(); toast(d.error || 'the server did not stop'); return; }
  } catch (e) {
    toast(e.message || String(e)); return;
  }
  stopped = true; demoStop = true;
  if (inAppWindow()) return;                        // the app closes its window once the server has stopped
  document.body.innerHTML = '';
  const end = h('section', 'stopped');
  add(end,
    h('div', 'phase-l', 'X Pixel Map'),
    h('h1', null, 'The local server has stopped.'),
    h('p', 'why', 'You can close this tab. To come back, start the server again; saved sessions are listed under Resume.')
  );
  const b = h('button', 'primary', 'Close this tab');
  b.type = 'button';
  b.onclick = () => { window.close(); b.textContent = 'Close it with the browser (Ctrl+W)'; b.disabled = true; };
  end.appendChild(b);
  document.body.appendChild(end);
  window.close();                                   // only works when the tab was opened by a script
}

// ---- home menu ----------------------------------------------------------------------------------------

function homeStateKey() {
  return JSON.stringify({ c: S.camera, d: S.device, h: S.holds, s: S.simulate });
}

function renderHome(force) {
  if (!S || !S.home || !els.homeCards) return;
  const key = homeStateKey();
  if (!force && key === homeKey) return;
  homeKey = key;
  renderHomeCards();
}

function homeStatusCard() {
  const dev = S.device || {};
  const now = S.holds && S.holds.now, rb = S.holds && S.holds.rollback;
  const mode = { normal: 'Normal mode', service: 'Service mode', none: 'Not connected' }[S.camera] || S.camera;
  const rows = h('div', 'hrows');
  const row = (k, v, sub, cls) => {
    const d = h('div', 'hstat' + (cls ? ' ' + cls : ''));
    add(d, h('div', 'hk', k), h('div', 'hv', v), sub ? h('div', 'hs', sub) : null);
    rows.appendChild(d);
  };
  row('Camera', dev.model ? `FUJIFILM ${dev.model}` : 'No camera connected',
      [dev.firmware ? `firmware ${dev.firmware}` : null, mode].filter(Boolean).join(' · '),
      S.camera === 'none' ? 'bad' : 'good');
  row('Pixel map in the camera', now ? now.name : 'Not read yet',
      now ? `ID ${now.sha12}` : 'it is read once, in service mode', now ? '' : 'warn');
  row('Previous pixel map', rb ? rb.name : 'None yet',
      rb ? `ID ${rb.sha12}` : 'available after the next write, for roll back', rb ? '' : 'warn');
  const card = h('article', 'hcard wide camera-card');
  add(card, h('div', 'kicker', 'Status'), rows);
  return card;
}

function homeCard({ kicker, title, desc, tag, tagKind, body, actions, note, wide }) {
  const card = h('article', 'hcard' + (wide ? ' wide' : ''));
  const top = h('div', 'hcard-top');
  add(top, h('div', 'kicker', kicker), tag ? h('span', 'tag' + (tagKind ? ' ' + tagKind : ''), tag) : null);
  add(card, top, h('h2', null, title), h('p', 'hdesc', desc));
  if (body) card.appendChild(body);

  const foot = h('div', 'hfoot');
  (actions || []).forEach(a => {
    const b = h('button', a.primary ? 'primary' : '', a.label);
    b.type = 'button';
    b.disabled = !!a.disabled;
    if (a.title) b.title = a.title;
    b.onclick = () => a.onClick(b);
    foot.appendChild(b);
  });
  if (note) foot.appendChild(h('span', 'hint', note));
  if (foot.childNodes.length) card.appendChild(foot);
  return card;
}

function routeFlow(names) {
  const flow = h('div', 'route-flow');
  names.forEach((name, i) => {
    flow.appendChild(h('span', 'rf', name));
    if (i < names.length - 1) flow.appendChild(h('span', 'rfa', '→'));
  });
  return flow;
}

function firmwareCard() {
  const pick = h('label', 'fw-pick');
  const input = h('input'); input.type = 'file'; input.accept = '.DAT,.dat';
  const msg = h('p', 'hint');
  add(pick, document.createTextNode('Choose the firmware file…'), input);
  input.onchange = async () => {
    const file = input.files[0];
    if (!file) return;
    msg.textContent = 'Checking the file…';
    const r = await fetch('/api/firmware', { method: 'POST', body: file });
    const d = await r.json().catch(() => ({}));
    if (d.ok) { homeKey = ''; await refresh(true); return; }
    msg.textContent = d.error || 'That file could not be used.';
  };
  const body = h('div');
  const link = h('a', null, 'Download it from Fujifilm');
  link.href = S.firmware.url; link.target = '_blank'; link.rel = 'noopener';
  add(body, h('p', 'hint', 'Make sure your camera is on firmware 5.52, the latest. Then download the same file (FWUP0005.DAT) and choose it here.'), link, pick, msg);
  return homeCard({
    kicker: 'One-time setup', title: 'Add the firmware file', wide: true,
    desc: 'Before a save, the tool rehearses it in an emulated camera, and that needs Fujifilm\'s firmware file. It stays on this computer and you only do this once. You can scan without it.',
    body
  });
}

function renderHomeCards() {
  if (!els.homeCards) return;
  els.homeCards.innerHTML = '';
  const now = S.holds && S.holds.now, rb = S.holds && S.holds.rollback;
  els.homeCards.appendChild(homeStatusCard());
  if (S.firmware && !S.firmware.ready) els.homeCards.appendChild(firmwareCard());

  els.homeCards.appendChild(homeCard({
    kicker: 'Fix',
    title: 'Pixel mapping',
    desc: 'Find the hot and dead pixels, build a corrected pixel map and write it to the camera.',
    tag: 'Writes with approval', tagKind: 'warn',
    body: routeFlow(['Save settings', 'Find', 'Build', 'Write', 'Check']),
    actions: [{ label: 'Start pixel mapping', primary: true, onClick: () => newSession('map') }]
  }));

  els.homeCards.appendChild(homeCard({
    kicker: 'Undo',
    title: 'Roll back',
    desc: 'Put an earlier saved pixel map back into the camera.',
    tag: 'Writes with approval', tagKind: 'warn',
    body: rb ? null : h('p', 'hint', 'No earlier pixel map is saved yet, so there is nothing to roll back to.'),
    actions: [{
      label: 'Start roll back', primary: true, disabled: !rb,
      title: rb ? 'Choose a saved pixel map to put back' : 'No earlier pixel map is available yet',
      onClick: () => newSession('rollback')
    }]
  }));

  const resumeBody = h('div', 'resume-body');
  resumeBody.id = 'resume-body';
  resumeBody.appendChild(h('p', 'hint', 'Looking for unfinished sessions…'));
  els.homeCards.appendChild(homeCard({
    kicker: 'Continue',
    title: 'Resume last session',
    desc: 'Continue a session you left unfinished. Your progress is saved.',
    body: resumeBody,
    note: 'No writes just by resuming.'
  }));

  els.homeCards.appendChild(homeCard({
    kicker: 'History', title: 'Past sessions',
    desc: 'See the before and after of a finished session, and the pixels it fixed.',
    actions: [{ label: 'Open the latest', disabled: true, onClick: () => {} }, { label: 'Clear history…', disabled: true, onClick: () => {} }]
  }));
  const [histCard, clearBtn] = els.homeCards.lastChild.querySelectorAll('.hfoot button');

  const facts = h('div', 'hfacts');
  [
    ['The problem', 'A hot pixel is one tiny spot that leaks charge and shows as a bright dot in dark areas of a photo.'],
    ['The fix', 'The tool adds that spot to the camera\'s own defect list, so the camera leaves it out of every photo.'],
    ['The promise', 'Firmware, photos and the memory card are never touched, and every write is read back and checked.']
  ].forEach(([k, v]) => {
    const row = h('div');
    add(row, h('b', null, k), h('span', null, v));
    facts.appendChild(row);
  });
  els.homeCards.appendChild(homeCard({
    kicker: 'Guide',
    title: 'How it works',
    desc: 'A plain-language explanation of hot pixels, what the camera already does, and exactly what this tool changes.',
    body: facts,
    wide: true,
    actions: [{ label: 'Read how it works', primary: true, onClick: openAbout }]
  }));

  loadSessions().then(list => {
    const body = $('resume-body');
    if (body && S && S.home) fillResume(body, list);
    const past = finishedSessions(list);
    if (histCard && past.length) { histCard.disabled = false; histCard.classList.add('primary'); histCard.onclick = () => openHistory(past[0].folder); }
    if (clearBtn && past.length) { clearBtn.disabled = false; clearBtn.onclick = () => clearHistoryDialog(past.length, historyInfo); }
  });
}

async function loadSessions(force) {
  if (!force && sessionsCache && Date.now() - sessionsAt < 5000) return sessionsCache;
  try {
    const res = await fetch(API.sessions, { cache: 'no-store' });
    const d = await res.json();
    sessionsCache = d.sessions || []; historyInfo = d.history || { count: 0, bytes: 0 };
    sessionsAt = Date.now();
  } catch (e) {
    sessionsCache = sessionsCache || [];
  }
  return sessionsCache;
}

function fillResume(body, list) {
  body.innerHTML = '';
  const mine = (list || []).filter(x => !!x.simulate === !!S.simulate);
  const unfinished = mine.filter(x => !x.finished && x.resumable !== false);
  if (!unfinished.length) {
    add(body,
      h('p', 'hint', 'No unfinished session.'),
      h('p', 'hint', 'Start a recipe above; it will appear here after the first step.')
    );
    const card = body.closest('.hcard');
    if (card) card.classList.add('muted');
    return;
  }
  const s = unfinished[0];
  const recipe = s.recipe === 'rollback' ? 'Roll back' : 'Pixel mapping';
  const done = Math.max(0, Math.min(s.index, s.total));
  add(body,
    h('div', 'resume-title', recipe),
    h('div', 'resume-line', `Step ${done + 1} of ${s.total}${s.step ? ' · ' + s.step : ''}`),
    h('div', 'hint', `folder ${s.folder}`),
    unfinished.length > 1 ? h('div', 'hint', `${unfinished.length - 1} more unfinished session${unfinished.length === 2 ? '' : 's'}`) : null
  );
  const b = h('button', 'primary', `Resume ${recipe.toLowerCase()}`);
  b.type = 'button';
  b.onclick = () => resumeSession(s.folder);
  body.appendChild(b);
}

let historyInfo = { count: 0, bytes: 0 };

function mb(bytes) { return bytes >= 1e9 ? (bytes / 1e9).toFixed(1) + ' GB' : Math.max(1, Math.round(bytes / 1e6)) + ' MB'; }

function clearHistoryDialog(count, info) {
  let dlg = $('cleardlg');
  if (!dlg) { dlg = h('dialog', 'dlg'); dlg.id = 'cleardlg'; document.body.appendChild(dlg); }
  dlg.innerHTML = '';
  add(dlg, h('div', 'phase-l', 'History'), h('h2', null, 'Clear history?'),
      h('p', 'why', `This removes ${count} finished session${count === 1 ? '' : 's'} and their before and after photos` +
                    (info && info.bytes ? ` (about ${mb(info.bytes)})` : '') + '. It cannot be undone.'),
      h('p', 'why', 'Your saved pixel maps and Roll back are not touched, and neither is a session you have not finished.'));
  const btns = h('div', 'btns');
  const go = h('button', 'warn', 'Clear history'); go.type = 'button';
  go.onclick = async () => {
    go.disabled = true;
    try {
      const res = await fetch('/api/history/clear', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ confirm: true }) });
      const d = await res.json().catch(() => ({}));
      dlg.close();
      if (!d.ok) { toast(d.error || 'could not clear the history'); return; }
      toast(`Cleared ${d.removed} session${d.removed === 1 ? '' : 's'}, freed ${mb(d.freed)}`);
      sessionsCache = null; homeKey = ''; await refresh(true);
    } catch (e) { dlg.close(); toast(e.message || String(e)); }
  };
  const keep = h('button', null, 'Keep it'); keep.type = 'button'; keep.onclick = () => dlg.close();
  add(btns, go, keep); dlg.appendChild(btns);
  dlg.showModal(); keep.focus();
}

function finishedSessions(list) {
  return (list || []).filter(x => x.finished && !!x.simulate === !!S.simulate);
}

function sessionLabel(x) {
  const sm = x.summary || {};
  const m = /^(\d{4})-(\d{2})-(\d{2}) (\d{2}:\d{2})/.exec(x.created || '');
  const when = m ? `${['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][+m[2] - 1]} ${+m[3]}, ${m[1]} ${m[4]}` : x.folder;
  const what = x.recipe === 'rollback' ? 'rolled back' : sm.total ? `${sm.fixed} of ${sm.total} pixels normal` : 'pixel mapping';
  return `${when} · ${what}`;
}

// a date drop-down on a finished session: jump to any other finished session, newest first
async function renderHistoryPicker() {
  const list = finishedSessions(await loadSessions(true));
  if (list.length < 2 || !S || !S.folder) return;
  const wrap = h('div', 'histpick');
  const sel = document.createElement('select'); sel.setAttribute('aria-label', 'Session date');
  list.forEach(x => { const o = document.createElement('option'); o.value = x.folder; o.textContent = sessionLabel(x); o.selected = x.folder === S.folder; sel.appendChild(o); });
  sel.onchange = () => { if (sel.value !== S.folder) openHistory(sel.value); };
  add(wrap, h('span', 'hint', 'Session'), sel);
  const anchor = els.card.querySelector('.btns');
  if (anchor && anchor.parentNode) anchor.parentNode.insertBefore(wrap, anchor.nextSibling); else els.card.appendChild(wrap);
}

function itemsDone(step) {
  return (step.items || []).every(it => it.status === 'done');
}

function instructReady(step) {
  if (!itemsDone(step)) return false;
  return !step.wait_for || S.camera === step.wait_for;
}

function canGoBack(step) {
  if (S.index <= 0 || step.status === 'running') return false;
  const prev = S.steps[S.index - 1];
  return !!prev && (prev.kind === 'instruct' || prev.kind === 'review');
}

function actionsFor(step) {
  const a = [];

  if (step.kind === 'instruct') {
    const ready = instructReady(step);
    let title = '';
    if (!itemsDone(step)) title = 'Tick every item first';
    else if (!ready) title = `Waiting for the camera in ${step.wait_for} mode on USB`;
    a.push({ id: 'continue', label: 'Continue', primary: true, disabled: !ready, title });
  }

  if ((step.kind === 'run' || step.kind === 'result') &&
      (step.status === 'pending' || step.status === 'failed')) {
    a.push({
      id: step.status === 'failed' ? 'retry' : 'start',
      label: step.status === 'failed' ? 'Retry' : 'Start',
      primary: true
    });
  }

  if (step.kind === 'review') {
    a.push({
      id: 'review',
      label: `Continue with ${selected.size} pixel${selected.size === 1 ? '' : 's'}`,
      primary: true
    });
  }

  if (canGoBack(step)) a.push({ id: 'back', label: 'Back' });

  return a;
}

function statusHint(step) {
  if (step.status === 'running') return 'The software is working.';
  if (step.status === 'failed') return 'This step failed.';
  if (step.kind === 'approve') return 'Approve below to write.';
  if (step.kind === 'result') return '';
  return '';
}

// ---- step list ----------------------------------------------------------------------------------------

function phases() {
  const groups = [];
  S.steps.forEach((step, i) => {
    let g = groups[groups.length - 1];
    if (!g || g.name !== step.phase) {
      g = { name: step.phase, items: [] };
      groups.push(g);
    }
    g.items.push({ step, i });
  });
  return groups;
}

function stepWord(step, i) {
  if (i === S.index) return 'now';
  if (step.status === 'running') return 'working';
  if (step.status === 'done') return 'done';
  if (step.status === 'failed') return 'failed';
  if (step.status === 'skipped') return 'skipped';
  return '';
}

function renderSteps() {
  if (!S || !els.steps) return;
  els.steps.innerHTML = '';

  const metro = h('div', 'metro');
  const here = current() ? current().phase : null;
  let currentEl = null;

  phases().forEach(group => {
    const done = group.items.filter(x => x.step.status === 'done').length;
    const total = group.items.length;
    const isHere = group.name === here;

    const station = h('div', 'mstation');
    if (done === total) station.classList.add('done');
    if (isHere) station.classList.add('current');
    add(station,
      h('span', 'dot'),
      h('span', 'name', group.name),
      h('span', 'count', `${done}/${total}`)
    );
    metro.appendChild(station);

    if (!isHere) return;

    group.items.forEach(({ step, i }) => {
      const row = h('div', 'mstep ' + step.status);
      if (i === S.index) row.classList.add('current');
      add(row,
        h('span', 'mdot'),
        h('span', 'title', step.title),
        h('span', 'st', stepWord(step, i))
      );
      if (step.note) row.title = step.note;
      metro.appendChild(row);
      if (i === S.index) currentEl = row;
    });
  });

  els.steps.appendChild(metro);

  if (S.index !== lastIndex && currentEl) {
    lastIndex = S.index;
    currentEl.scrollIntoView({ block: 'nearest' });
  }
}

// ---- camera guide (drawn from the X-T1 manual, pages 2-4) ---------------------------------------------

function fig(src, cap) {
  return { src, cap };
}

const SCAN = { dark_4th_iso200: 'ISO 200 · 1/4 s', dark_1s_iso250: 'ISO 250 · 1 s', dark_4th_iso320: 'ISO 320 · 1/4 s',
               dark_4th_iso400: 'ISO 400 · 1/4 s', dark_short_iso800: 'ISO 800 · 1/200 s', dark_4s_iso1250: 'ISO 1250 · 4 s' };

const GUIDE = {
  connect: { note: 'Plug in the USB cable, then set PC SHOOT AUTO on the camera.',
             images: [fig('usb.svg', 'Micro USB, left side, under the cover'), fig('back-menu-up.svg', 'MENU, Left, then Up · USB mode: PC SHOOT AUTO')] },
  settings: { note: 'No camera action: the software is reading your menu settings over USB.' },
  first_service: { note: 'Camera off first. Keep the USB cable connected.', images: [fig('service-combo.svg', 'Hold Shutter + Up, then switch on')] },
  first_read: { note: 'No camera action: the software reads the pixel map from the camera. It writes nothing.' },
  first_normal: { note: 'Leave service mode, then set PC SHOOT AUTO again.',
                  images: [fig('battery.svg', 'Off · wait · battery out · wait · battery in'), fig('back-menu-up.svg', 'MENU, Left, then Up · USB mode: PC SHOOT AUTO'),
                           fig('usb.svg', 'Plug the USB cable back in')] },
  first_restore: { note: 'No camera action: the software restores your menu settings and clock.' },
  dark_setup: { note: 'Lens cap on (the lens stays on the camera), dark cloth over the camera, dim room. Shutter type: Mechanical, or Mechanical + Electronic.',
                images: [fig('top-setup.svg', 'Shutter dial A · ISO dial A'), fig('front-focus-m.svg', 'Focus switch on M')] },
  white_setup: { note: 'Lens on, f/5.6, focus at infinity, phone screen all white.',
                 images: [fig('top-iso-a.svg', 'Shutter dial and ISO dial stay on A'), fig('front-focus-m.svg', 'Focus switch on M')] },
  shoot_white: { note: 'Keep the white screen in front of the lens.' },
  sort: { note: 'No camera action: the software is sorting what the frames found.' },
  review: { note: 'No camera action: tick the pixels you want in the new pixel map.' },
  build: { note: 'No camera action: the software builds and checks the new pixel map.' },
  service: { note: 'Camera off first. Keep the USB cable connected.', images: [fig('service-combo.svg', 'Hold Shutter + Up, then switch on')] },
  save: { note: 'The software writes the pixel map. Do not touch the camera or the cable.' },
  cycle: { note: 'Power cycle, then straight back into service mode.',
           images: [fig('battery.svg', 'Off · wait · battery out · wait · battery in'), fig('service-combo.svg', 'Hold Shutter + Up while switching on')] },
  readback: { note: 'No camera action: the software reads the pixel map back from the camera.' },
  normal: { note: 'Leave service mode, then set PC SHOOT AUTO again.',
            images: [fig('battery.svg', 'Off · wait · battery out · wait · battery in'), fig('back-menu-up.svg', 'MENU, Left, then Up · USB mode: PC SHOOT AUTO'),
                     fig('usb.svg', 'Plug the USB cable back in')] },
  restore: { note: 'No camera action: the software restores your menu settings and clock.' },
  check_setup: { note: 'Same dark setup as before, still on the mechanical shutter. Both dials should still be on A.', images: [fig('top-setup.svg', 'Shutter dial A · ISO dial A'), fig('front-focus-m.svg', 'Focus switch on M')] },
  end_200: { note: 'No more shots. Leave the camera ready to use.', images: [fig('top-setup.svg', 'Set your dials back the way you like them')] },
  report: { note: 'No camera action: the before/after report.' },
  choose: { note: 'No camera action: pick which saved pixel map to put back.' },
  restore_img: { note: 'The software writes the chosen image. Do not touch the camera or the cable.' }
};
Object.entries(SCAN).forEach(([name, label], i) => {
  const images = [fig('top-iso-a.svg', `Dials stay on A · computer sets ${label}`)];
  GUIDE['shoot_' + name] = i ? { images } : { note: 'Leave the camera alone while it shoots.', images };
  GUIDE['after_' + name] = { images };
});

const MODELS_WITH_DRAWINGS = ['x-t1'];
function modelSlug() {
  const m = (S && S.device && S.device.model) || '';
  return m.toLowerCase().replace(/\s+/g, '-');
}

let lastGuideKey = '';

function renderGuide() {
  if (!els.guide) return;
  const step = current();
  const key = (step ? step.id + '|' + step.status : 'finished') + '|' + modelSlug();
  if (key === lastGuideKey) return;
  lastGuideKey = key;
  els.guide.innerHTML = '';

  const guide = step ? GUIDE[step.id] : null;
  if (!step) {
    els.guide.appendChild(h('p', 'nonote', 'Session finished — nothing else to do on the camera.'));
    return;
  }
  if (!guide) {
    els.guide.appendChild(h('p', 'nonote', 'No camera action for this step.'));
    return;
  }

  if (guide.note) els.guide.appendChild(h('p', 'guide-note', guide.note));
  if (guide.images && guide.images.length && !MODELS_WITH_DRAWINGS.includes(modelSlug())) {
    els.guide.appendChild(h('p', 'hint', `No drawings for ${(S.device && S.device.model) || 'this camera'} yet.`));
    return;
  }

  (guide.images || []).forEach(item => {
    const figure = h('figure');
    const img = document.createElement('img');
    img.src = `/static/models/${modelSlug()}/${item.src}`;
    img.alt = item.cap;
    img.loading = 'lazy';
    add(figure, img, h('figcaption', null, item.cap));
    els.guide.appendChild(figure);
  });

  if (guide.images && guide.images.length) {
    els.guide.appendChild(h('p', 'hint', `Drawn for the ${S.device.model}.`));
  }
}

// ---- card ---------------------------------------------------------------------------------------------

function cardKey(step) {
  if (!step) return 'finished';
  const a = S.activity || {};
  let k = [S.index, step.status, step.note || ''].join('|');

  if (step.kind === 'instruct') {
    k += '|' + (step.items || []).map(it =>
      it.status + ':' + (it.end ? Math.ceil(it.end - (a.now || 0)) : '')).join(',');
    k += '|' + (S.camera || '');
  } else if (step.kind === 'run' || step.kind === 'approve') {
    k += '|' + (a.text || '') + '|' + (a.frac === undefined ? '' : a.frac);
    k += '|' + (a.countdown_end ? Math.ceil(a.countdown_end - (a.now || 0)) : '');
    k += '|' + (a.image || '');
  } else if (step.kind === 'review') {
    k += '|' + ((S.ctx.found || []).length) + '|' + reviewFilter + '|' + showOther;
  } else if (step.kind === 'choose') {
    k += '|' + (step.choices || []).map(c => c.sha12).join(',');
  } else if (step.kind === 'result') {
    const r = S.ctx.report;
    k += '|' + (r ? r.fixed + '/' + r.total : '');
  }
  return k;
}

function renderCard(force) {
  if (!S || !els.card) return;
  const step = current();
  const key = cardKey(step);
  if (!force && key === lastCardKey) return;
  lastCardKey = key;

  els.card.innerHTML = '';

  if (!step) {
    add(els.card,
      h('div', 'phase-l', 'Done'),
      h('div', 'hero ok', 'Session complete'),
      h('p', 'hint', 'Every step is finished. Activity below has the full record.')
    );
    const btns = h('div', 'btns end');
    const stop = h('button', 'primary', 'Stop server and close');
    stop.type = 'button'; stop.title = 'Stop the local server; this session stays saved';
    stop.onclick = () => exitApp();
    const home = h('button', null, 'Back to home');
    home.type = 'button';
    home.onclick = () => goHome();
    add(btns, stop, home);
    add(els.card, btns);
    renderHistoryPicker();
    const last = S.steps[S.steps.length - 1];
    if (last && last.kind === 'result' && S.ctx && S.ctx.report) renderResult(last);
    return;
  }

  add(els.card,
    h('div', 'phase-l', step.phase),
    h('h1', null, step.title)
  );
  if (step.why) add(els.card, h('p', 'why', step.why));

  const who = step.kind === 'approve' ? ['Your approval', 'ok']
    : step.kind === 'run' || step.kind === 'result' ? ['Software', 'sw']
    : ['You', 'you'];
  add(els.card, h('span', 'who ' + who[1], who[0]));

  if (step.kind === 'instruct') renderInstruct(step);
  else if (step.kind === 'run') renderRun(step);
  else if (step.kind === 'approve') renderApprove(step);
  else if (step.kind === 'review') renderReview(step);
  else if (step.kind === 'result') renderResult(step);
  else if (step.kind === 'choose') renderChoose(step);

  if (step.note && step.status === 'failed') {
    add(els.card, h('div', 'banner bad', step.note));
  }

  if (step.kind !== 'approve' && step.kind !== 'review' && step.kind !== 'choose') appendCardActions(step);
}

function appendCardActions(step) {
  const actions = actionsFor(step).filter(a => a.id !== 'back');
  if (!actions.length) return;
  const btns = h('div', 'btns');
  actions.forEach(a => {
    const b = h('button', a.primary ? 'primary' : '');
    b.type = 'button';
    b.textContent = a.label;
    b.disabled = !!a.disabled;
    if (a.title) b.title = a.title;
    b.onclick = () => act(a.id);
    btns.appendChild(b);
  });
  els.card.appendChild(btns);
}

// ---- instruct -----------------------------------------------------------------------------------------

function renderInstruct(step) {
  const list = h('div', 'items');

  (step.items || []).forEach((it, i) => {
    const row = h('div', 'item');
    if (it.status === 'done') row.classList.add('done');

    add(row, h('span', 'box'), h('span', 'n', String(i + 1)), h('span', 't', it.text));

    if (it.status === 'timing' && it.end) {
      const now = (S.activity && S.activity.now) || 0;
      const total = it.wait || 1;
      const left = Math.max(0, it.end - now);
      const timer = h('div', 'timer');
      timer.innerHTML = ringHTML('ring', 1 - left / total);
      timer.appendChild(h('span', null, Math.ceil(left) + 's'));
      row.appendChild(timer);
    } else if (it.status === 'todo' && step.status !== 'done') {
      row.title = 'Click when done';
      row.onclick = () => act('tick', { item: i });
    }

    list.appendChild(row);
  });

  els.card.appendChild(list);

  if (step.wait_for) {
    const ok = S.camera === step.wait_for;
    els.card.appendChild(h(
      'div', 'waitcam' + (ok ? ' ok' : ''),
      ok ? `Camera is in ${step.wait_for} mode on USB.`
         : `Waiting for the camera in ${step.wait_for} mode on USB (now: ${S.camera}).`
    ));
  }

  if (step.status !== 'done') {
    els.card.appendChild(h('p', 'hint',
      instructReady(step) ? 'Everything is ticked. Press Continue.'
                          : 'Tick each item as you do it on the camera.'));
  }
}

// ---- run / progress -----------------------------------------------------------------------------------

function ringHTML(cls, frac) {
  const r = 26;
  const c = 2 * Math.PI * r;
  const off = c * (1 - clamp(frac));
  return `<svg class="${cls}" viewBox="0 0 60 60" aria-hidden="true">` +
    `<circle class="bg" cx="30" cy="30" r="${r}"></circle>` +
    `<circle class="fg" cx="30" cy="30" r="${r}" stroke-dasharray="${c.toFixed(2)}" ` +
    `stroke-dashoffset="${off.toFixed(2)}"></circle></svg>`;
}

function renderProgress() {
  const a = S.activity || {};
  const work = h('div', 'work');
  work.innerHTML = ringHTML('big-ring', a.frac || 0);

  const box = h('div');
  add(box,
    h('div', 'now', a.text || 'Working…'),
    a.detail ? h('div', 'detail', a.detail) : null
  );

  if (a.countdown_end) {
    const left = Math.max(0, Math.ceil(a.countdown_end - (a.now || 0)));
    add(box, h('div', 'cd', left + 's'));
  }

  if (typeof a.frac === 'number') {
    const bar = h('div', 'bar');
    const fill = h('i');
    fill.style.width = (clamp(a.frac) * 100).toFixed(1) + '%';
    bar.appendChild(fill);
    box.appendChild(bar);
  }

  work.appendChild(box);
  els.card.appendChild(work);

  if (a.image) els.card.appendChild(shot(a.image, a.text || ''));
}

function shot(path, caption) {
  const box = h('div', 'shot');
  const img = document.createElement('img');
  img.src = API.file + '?p=' + encodeURIComponent(path);
  img.alt = caption || '';
  img.loading = 'lazy';
  add(box, img, h('div', 'cap', caption || ''));
  return box;
}

function renderRun(step) {
  const a = S.activity || {};
  if (step.status === 'running' || (a.text && step.status === 'pending')) renderProgress();
  else if (a.image) els.card.appendChild(shot(a.image, a.text || ''));

  if (step.status === 'done' && step.result) els.card.appendChild(resultBox(step.result));
  if (step.status === 'pending') els.card.appendChild(h('p', 'hint', 'Ready to start.'));
}

// ---- approve ------------------------------------------------------------------------------------------

function renderApprove(step) {
  const a = S.activity || {};

  if (step.status === 'running') {
    renderProgress();
    els.card.appendChild(h('p', 'hint', 'Do not touch the camera or the cable until this finishes.'));
    return;
  }

  if (step.status === 'done') {
    if (step.result) els.card.appendChild(resultBox(step.result));
    return;
  }

  if (step.status === 'failed') {
    els.card.appendChild(h('p', 'hint', 'Nothing was written. Fix the cause, then use Back or Retry where offered.'));
  }

  const s = step.summary;
  if (!s) {
    els.card.appendChild(h('p', 'hint', 'Preparing this step…'));
    return;
  }

  const box = h('div', 'approve');
  add(box, h('h3', null, s.title));
  const ul = h('ul');
  (s.lines || []).forEach(line => ul.appendChild(h('li', null, line)));
  box.appendChild(ul);

  const confirm = s.confirm;
  if (confirm) add(box, h('div', 'hint', 'New pixel map'), h('div', 'sha', confirm));
  const btn = h('button', 'danger', confirm ? `Approve and write ${confirm}` : 'Approve');
  btn.type = 'button';
  btn.onclick = () => { btn.disabled = true; act('approve', confirm ? { confirm } : {}); };
  const btns = h('div', 'btns');
  btns.appendChild(btn);
  if (canGoBack(step)) {
    const back = h('button', null, 'Not now, go back'); back.type = 'button'; back.onclick = () => act('back'); btns.appendChild(back);
  }
  add(box, btns, h('p', 'hint', 'Nothing is written until you press the button. One press writes this one pixel map.'));
  els.card.appendChild(box);
}

// ---- choose (roll back) ---------------------------------------------------------------------------------

let chosen = null;
function renderChoose(step) {
  const now = S.holds && S.holds.now;
  const list = h('div', 'choices');
  (step.choices || []).forEach(c => {
    const isNow = now && c.sha12 === now.sha12;
    if (chosen === null && S.holds && S.holds.rollback && c.sha12 === S.holds.rollback.sha12) chosen = c.sha12;
    const row = h('label', 'choice' + (chosen === c.sha12 ? ' on' : '') + (isNow ? ' now' : ''));
    const r = document.createElement('input'); r.type = 'radio'; r.name = 'img'; r.checked = chosen === c.sha12; r.disabled = isNow;
    r.onchange = () => { chosen = c.sha12; renderCard(true); };
    add(row, r, add(h('div', 'cinfo'), h('div', 'cname', c.name + (isNow ? ' · in the camera now' : '')),
                    h('div', 'hint', `ID ${c.sha12}`)));
    list.appendChild(row);
  });
  els.card.appendChild(list);
  const go = h('button', 'primary', 'Use this pixel map'); go.type = 'button'; go.disabled = !chosen;
  go.onclick = () => act('choose', { value: chosen });
  els.card.appendChild(add(h('div', 'btns'), go, h('span', 'hint', 'Nothing is written yet. You approve the write two steps later.')));
}

function resetSessionUi() {
  chosen = null; lastCardKey = ''; lastGuideKey = ''; lastIndex = -1;
  selected = new Set(); reviewKey = null; reviewFilter = 'all'; showOther = false;
}

async function startSession(url, payload) {
  if (homeBusy) return;
  homeBusy = true;
  try {
    const res = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    const d = await res.json().catch(() => ({}));
    if (!d.ok) { toast(d.error || 'could not start'); return; }
    resetSessionUi();
    homeKey = '';
    await refresh(true);
  } catch (e) {
    toast(e.message || String(e));
  } finally {
    homeBusy = false;
  }
}

function newSession(recipe) {
  return startSession(API.newSession, { recipe });
}

function resumeSession(folder) {
  return startSession(API.resume, { folder });
}

function openHistory(folder) {
  return startSession(API.resume, { folder, view: true });      // a finished session opens read-only: its report, before and after
}

async function goHome() {
  try {
    const res = await fetch('/api/home', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
    const d = await res.json().catch(() => ({}));
    if (!d.ok) { toast(d.error || 'could not go home'); return; }
    resetSessionUi();
    homeKey = ''; sessionsCache = null;
    await refresh(true);
  } catch (e) {
    toast(e.message || String(e));
  }
}

// keyboard: Enter = the card's main button (never an approval), Space = tick the next item
document.addEventListener('keydown', e => {
  if (e.target && /INPUT|TEXTAREA|SELECT/.test(e.target.tagName)) return;
  const dlg = $('exitdlg'); if (dlg && dlg.open) return;     // the exit dialog owns the keyboard
  if (e.key === 'Escape' && page === 'about') { e.preventDefault(); closeAbout(); return; }
  if (page === 'about') return;
  const step = current(); if (!step) return;
  if (e.key === ' ' && step.kind === 'instruct') {
    const i = (step.items || []).findIndex(it => it.status === 'todo');
    if (i >= 0) { e.preventDefault(); act('tick', { item: i }); }
  } else if (e.key === 'Enter' && step.kind !== 'approve') {
    const b = els.card.querySelector('.btns button.primary:not(:disabled)');
    if (b) { e.preventDefault(); b.click(); }
  }
});

// ---- review -------------------------------------------------------------------------------------------

function reviewCounts(found) {
  const counts = {};
  found.forEach(f => { counts[f.label] = (counts[f.label] || 0) + 1; });
  return counts;
}

function renderReview(step) {
  const all = S.ctx.found || [];
  const shown = f => PRETICKED.includes(f.cls) || f.cls === 'focus';          // what the owner should see
  const found = showOther ? all : all.filter(shown);
  const others = all.filter(f => !shown(f));
  const cant = others.filter(f => f.cls === 'margin' || f.cls === 'listed');   // real but not fixable here: say so
  const counts = reviewCounts(found);
  const sets = Object.entries(S.ctx.sets || {}).filter(([n]) => !n.startsWith('after_')).map(([n, i]) => ({ name: n, label: setLabel(n, i) }));
  const v = viewer({ sets, dots: () => found.map(f => Object.assign({ sel: selected.has(f.x + ',' + f.y) }, f)),
                     onPick: f => togglePixel(f, gridTile(f)) });
  els.card.appendChild(v.el);

  const chips = h('div', 'chips');
  const chip = (key, label) => {
    const c = h('button', 'chip' + (reviewFilter === key ? ' on' : ''), label);
    c.type = 'button';
    c.onclick = () => { reviewFilter = key; renderCard(true); };
    chips.appendChild(c);
  };
  chip('all', `All (${found.length})`);
  Object.keys(counts).sort().forEach(label => chip(label, `${label} (${counts[label]})`));
  els.card.appendChild(chips);

  if (cant.length && !showOther) {
    els.card.appendChild(h('div', 'hint', `${cant.length} more hot pixel${cant.length === 1 ? '' : 's'} cannot be fixed by this tool ` +
      '(at the very edge of the picture, or already in the camera\'s list but still showing).'));
  }
  if (others.length) {
    const t = h('button', 'chip', showOther ? 'Hide technical details' : 'Show technical details'); t.type = 'button';
    t.onclick = () => { showOther = !showOther; reviewFilter = 'all'; renderCard(true); };
    const row = h('div', 'hint'); row.appendChild(t); els.card.appendChild(row);
  }

  const grid = h('div', 'grid');
  found.forEach(f => {
    if (reviewFilter !== 'all' && f.label !== reviewFilter) return;
    const key = f.x + ',' + f.y;
    const px = h('div', 'px' + (selected.has(key) || f.cls === 'focus' ? ' sel' : ''));
    px.dataset.key = key;
    px.title = f.why || '';
    px.onclick = () => { togglePixel(f, px); v.focus(f); };
    px.onmouseenter = () => v.highlight(f);
    px.onmouseleave = () => v.highlight(null);

    const img = document.createElement('img');
    img.loading = 'lazy';
    img.alt = `(${f.x}, ${f.y})`;
    if (f.tile_set) img.src = `${API.tile}?set=${encodeURIComponent(f.tile_set)}&x=${f.x}&y=${f.y}`;
    add(px, img);

    const b = h('div', 'b');
    add(b,
      add(h('div', 'xy', `(${f.x}, ${f.y})`), h('span', 'check')),
      h('div', 'lab ' + (f.cls || ''), f.label || f.cls || ''),
      h('div', 'w', f.why || '')
    );
    px.appendChild(b);
    grid.appendChild(px);
  });
  els.card.appendChild(grid);

  const btns = h('div', 'btns');
  const go = h('button', 'primary', `Continue with ${selected.size} pixel${selected.size === 1 ? '' : 's'}`);
  go.type = 'button';
  go.onclick = submitReview;
  add(btns, go, h('span', 'hint', 'Click a circle or a tile to tick or untick it. Blue pixels sit on a focus-sensor spot: they are fixed automatically and cannot be ticked.'));
  els.card.appendChild(btns);
}

function gridTile(f) {
  return els.card.querySelector(`.px[data-key="${f.x},${f.y}"]`);
}

const FIXABLE = ['ordinary', 'spike', 'pair', 'light', 'dead', 'bright-iso', 'faint-iso'];
const PRETICKED = ['ordinary', 'spike', 'pair', 'light', 'dead', 'bright-iso'];

function togglePixel(f, px) {
  const key = f.x + ',' + f.y;
  if (!FIXABLE.includes(f.cls)) { toast(`(${f.x}, ${f.y}) ${f.label}: ${f.why}`); return; }
  if (selected.has(key)) { selected.delete(key); if (px) px.classList.remove('sel'); }
  else { selected.add(key); if (px) px.classList.add('sel'); }
  if (activeViewer) activeViewer.redraw();

  const go = els.card.querySelector('.btns button.primary');
  if (go) go.textContent = `Continue with ${selected.size} pixel${selected.size === 1 ? '' : 's'}`;
  renderHeader();
}

function submitReview() {
  act('review', { selected: Array.from(selected).sort() });
}

function syncReview() {
  const step = current();
  if (!step || step.kind !== 'review') { reviewKey = null; return; }

  const found = S.ctx.found || [];
  const key = S.index + ':' + found.length;
  if (reviewKey === key) return;

  reviewKey = key;
  reviewFilter = 'all'; showOther = false;
  const saved = S.ctx.selected;
  selected = new Set(Array.isArray(saved) && saved.length
    ? saved
    : found.filter(f => f.selected).map(f => `${f.x},${f.y}`));
}

// ---- result -------------------------------------------------------------------------------------------

function setLabel(name) {
  const after = name.startsWith('after_'); name = name.replace(/^after_/, '');
  let t;
  if (SCAN[name]) t = SCAN[name];
  else if (name === 'flat_iso200') t = 'White · ISO 200';
  else { const m = /^dark_1s_iso(\d+)$/.exec(name); t = m ? `ISO ${m[1]} · 1 s` : name.replace(/_/g, ' '); }
  return (after ? 'After · ' : '') + t;
}

function renderResult(step) {
  const rep = S.ctx.report;
  if (!rep) {
    if (step.result) els.card.appendChild(resultBox(step.result));
    else els.card.appendChild(h('p', 'hint', 'The session report is not ready yet.'));
    return;
  }

  if (!rep.total) {
    add(els.card, h('div', 'hero ok', 'All clear'), h('p', 'hint', 'Nothing needed changing: the camera already holds everything this scan would list.'));
    return;
  }
  add(els.card,
    h('div', 'hero' + (rep.fixed === rep.total ? ' ok' : ''), `${rep.fixed} / ${rep.total}`),
    h('p', 'hint', 'pixels now read at a normal level' +
      (rep.untestable ? ` · ${rep.untestable} more can only be checked on the camera (grey)` : ''))
  );
  const order = [...Object.keys(SCAN), 'dark_1s_iso200', 'dark_1s_iso800', 'dark_1s_iso1600'];     // older sessions used 1 s sets
  const rsets = [];
  order.forEach(n => { if ((S.ctx.sets || {})[n]) rsets.push({ name: n, label: 'Before · ' + setLabel(n) });
                       if ((S.ctx.sets || {})['after_' + n]) rsets.push({ name: 'after_' + n, label: setLabel('after_' + n) }); });
  if (rsets.length) {
    const dots = () => (rep.rows || []).map(r => {
      const vals = Object.keys(r).filter(k => k.startsWith('dark_')).map(k => r[k]);
      const ok = vals.length && vals.every(v => v[2]);
      const ex = {}; Object.keys(r).filter(k => k.startsWith('dark_')).forEach(k => { ex[k] = r[k][1]; });
      if (r.sim_untestable) return { x: r.x, y: r.y, label: r.label + ': not checkable in a simulation', state: 'info', ex: {},
        why: 'The replayed after-frames come from the real v12 session, which never listed this pixel. On the camera it would be fixed like the others.' };
      return { x: r.x, y: r.y, label: r.label + (ok ? ': fixed' : ': still above threshold'), state: ok ? 'fixed' : 'still', ex,
               why: Object.keys(r).filter(k => k.startsWith('dark_')).map(k => `${setLabel(k)} ${r[k][0]} → ${r[k][1]}`).join(' · ') };
    });
    els.card.appendChild(viewer({ sets: rsets, dots }).el);
  }

  const table = h('table', 'r');
  const head = h('tr');
  add(head, h('th', null, 'Pixel'), h('th', null, 'Kind'));
  const setNames = new Set();
  (rep.rows || []).forEach(r => Object.keys(r).forEach(k => {
    if (k.startsWith('dark_')) setNames.add(k);
  }));
  const cols = [...Object.keys(SCAN).filter(n => setNames.has(n)), ...Array.from(setNames).filter(n => !SCAN[n]).sort()];
  cols.forEach(n => head.appendChild(h('th', null, setLabel(n))));
  table.appendChild(head);

  (rep.rows || []).forEach(r => {
    const tr = h('tr');
    add(tr, h('td', null, `(${r.x}, ${r.y})`), h('td', null, r.label || ''));
    cols.forEach(n => {
      const v = r[n];
      if (!v) { tr.appendChild(h('td', null, '—')); return; }
      tr.appendChild(h('td', 'okc', `${v[0]} → ${v[1]}`));
      if (r.sim_untestable) tr.lastChild.className = 'hint';
      else if (!v[2]) tr.lastChild.className = 'badc';
    });
    table.appendChild(tr);
  });
  els.card.appendChild(table);

  const own = rep.own || {};
  const keys = Object.keys(own);
  if (keys.length) {
    const det = h('details', 'techdet');
    add(det, h('summary', null, 'Focus-pixel details (technical)'),
        h('p', 'hint', 'How much of a focus pixel\'s value comes from the pixel itself rather than from its neighbours. Your camera\'s factory setting is about 0.22 to 0.25; the fix leaves this alone.'));
    els.card.appendChild(det);
    const t2 = h('table', 'r');
    const hr = h('tr');
    add(hr, h('th', null, 'Set'), h('th', null, 'Own share'));
    t2.appendChild(hr);
    keys.sort().forEach(k => {
      const tr = h('tr');
      add(tr, h('td', null, setLabel(k.replace(/^after_/, 'after '))),
             h('td', null, own[k] === null || own[k] === undefined ? '—' : String(own[k])));
      t2.appendChild(tr);
    });
    det.appendChild(t2);
  }
}

const RESULT_LABELS = {
  frames: 'Frames', hot: 'Hot pixels', sha12: 'Pixel map', found: 'Pixels found',
  counts: 'By kind', changes: 'Changes', skipped: 'Skipped', saved: 'Saved',
  match: 'Read-back match', backup: 'Backup', copy: 'Safe copy', restored: 'Restored',
  clock: 'Clock', selected: 'Selected'
};

function resultBox(r) {
  const box = h('div', 'result-list');
  Object.entries(r).forEach(([k, v]) => {
    if (v === null || v === undefined || v === '') return;
    const row = h('div');
    const val = typeof v === 'object' ? JSON.stringify(v) : String(v);
    add(row, h('span', 'k', (RESULT_LABELS[k] || k) + ':'), h('span', 'v', val));
    box.appendChild(row);
  });
  return box;
}


// ---- sensor viewer: whole-frame overview, zoom to 100 % and beyond, clickable circles ------------------

let activeViewer = null;

function viewer(opts) {
  const sets = opts.sets.filter(x => x.name);
  const el = h('div', 'viewer');
  const bar = h('div', 'vbar');
  const stage = h('div', 'vstage');
  const content = h('div', 'vcontent');
  const base = document.createElement('img'); base.className = 'vbase'; base.alt = 'sensor overview';
  const patch = document.createElement('img'); patch.className = 'vpatch'; patch.alt = '';
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg'); svg.classList.add('vdots');
  const info = h('div', 'vinfo'); info.hidden = true;
  const zoomLbl = h('span', 'vzoom');
  add(content, base, patch, svg); add(stage, content, info);

  let cur = sets[0] ? sets[0].name : null, W = 4992, H = 3294, s = 0.2, tx = 0, ty = 0, hl = null, patchTimer = null, drag = null;

  const chips = h('div', 'vsets');
  sets.forEach(x => {
    const c = h('button', 'chip' + (x.name === cur ? ' on' : ''), x.label); c.type = 'button';
    c.onclick = () => { cur = x.name; chips.querySelectorAll('.chip').forEach(b => b.classList.toggle('on', b === c)); load(); };
    chips.appendChild(c);
  });
  const fitB = h('button', 'mini', 'Fit'); fitB.type = 'button'; fitB.onclick = () => fit();
  const oneB = h('button', 'mini', '100 %'); oneB.type = 'button';
  oneB.onclick = () => zoomAt(stage.clientWidth / 2, stage.clientHeight / 2, 1 / s);
  add(bar, chips, add(h('div', 'vtools'), zoomLbl, fitB, oneB));
  add(el, bar, stage, h('div', 'vhint', 'Scroll to zoom, drag to move, double-click to look closer. ' +
    (opts.onPick ? 'Click a circle to tick or untick it.' : 'Green = now normal, red = still above the threshold. Hover a circle for its numbers.')));

  function load() {
    if (!cur) return;
    patch.removeAttribute('src'); patch.hidden = true;
    base.onload = () => { W = base.naturalWidth * 3; H = base.naturalHeight * 3; fit(); };
    base.src = `/api/overview?set=${encodeURIComponent(cur)}`;
  }
  function fit() {
    const cw = stage.clientWidth, ch = stage.clientHeight;
    s = Math.min(cw / W, ch / H); tx = (cw - W * s) / 2; ty = (ch - H * s) / 2; apply();
  }
  function apply() {
    content.style.width = W + 'px'; content.style.height = H + 'px';
    content.style.transform = `translate(${tx}px, ${ty}px) scale(${s})`;
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`); svg.setAttribute('width', W); svg.setAttribute('height', H);
    zoomLbl.textContent = s >= 1 ? `${s.toFixed(s >= 10 ? 0 : 1)}× (1 sensor pixel = ${s.toFixed(1)} screen px)` : `${Math.round(s * 100)} %`;
    draw(); schedulePatch();
  }
  function cls(d) {
    if (d.state) return d.state;
    if (FIXABLE.includes(d.cls)) return d.sel ? 'fix' : 'skip';
    return d.cls === 'focus' ? 'focus' : 'info';
  }
  function draw() {
    const r = Math.max(7 / s, 1.3), sw = Math.min(2 / s, 0.12 * Math.max(1, 7 / s));   // hollow ring; zoomed in it stays clear of the pixel itself
    svg.innerHTML = '';
    opts.dots().forEach(d => {
      const c = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
      const big = hl && hl.x === d.x && hl.y === d.y;
      c.setAttribute('cx', d.x + 0.5); c.setAttribute('cy', d.y + 0.5); c.setAttribute('r', big ? r * 1.8 : r);
      c.setAttribute('stroke-width', sw); c.setAttribute('class', 'dot ' + cls(d) + (big ? ' hl' : ''));
      c.addEventListener('pointerenter', e => showInfo(d, e)); c.addEventListener('pointerleave', () => { info.hidden = true; });
      c.addEventListener('click', e => { e.stopPropagation(); if (!drag || !drag.moved) { if (opts.onPick) opts.onPick(d); draw(); } });
      svg.appendChild(c);
    });
  }
  function showInfo(d, e) {
    info.innerHTML = '';
    add(info, h('div', 'xy', `(${d.x}, ${d.y})`), h('div', 'lab ' + (d.cls || d.state || ''), d.label || ''), h('div', 'w', d.why || ''));
    const ex = d.ex || {};
    const rows = Object.keys(ex).sort().map(k => `${setLabel(k)}: ${ex[k] > 0 ? '+' : ''}${ex[k]}`);
    if (rows.length) info.appendChild(h('div', 'w', rows.join(' · ')));
    const r = stage.getBoundingClientRect();
    info.style.left = Math.min(e.clientX - r.left + 14, r.width - 260) + 'px'; info.style.top = (e.clientY - r.top + 14) + 'px';
    info.hidden = false;
  }
  function schedulePatch() {
    clearTimeout(patchTimer);
    if (s < 0.5 || !cur) { patch.hidden = true; return; }
    patchTimer = setTimeout(() => {
      const x = Math.max(0, Math.floor(-tx / s)), y = Math.max(0, Math.floor(-ty / s));
      const w = Math.min(W - x, Math.ceil(stage.clientWidth / s) + 2), hh = Math.min(H - y, Math.ceil(stage.clientHeight / s) + 2);
      if (w > 1200 || hh > 1200) { patch.hidden = true; return; }
      const url = `/api/region?set=${encodeURIComponent(cur)}&x=${x}&y=${y}&w=${w}&h=${hh}`;
      const img = new Image();
      img.onload = () => { patch.src = url; Object.assign(patch.style, { left: x + 'px', top: y + 'px', width: w + 'px', height: hh + 'px' }); patch.hidden = false; };
      img.src = url;
    }, 180);
  }
  function zoomAt(px, py, f) {
    const ns = Math.max(Math.min(stage.clientWidth / W, stage.clientHeight / H), Math.min(40, s * f));
    tx = px - (px - tx) * ns / s; ty = py - (py - ty) * ns / s; s = ns; apply();
  }
  stage.addEventListener('wheel', e => {
    e.preventDefault(); const r = stage.getBoundingClientRect();
    zoomAt(e.clientX - r.left, e.clientY - r.top, Math.exp(-e.deltaY * 0.0015));
  }, { passive: false });
  stage.addEventListener('dblclick', e => { const r = stage.getBoundingClientRect(); zoomAt(e.clientX - r.left, e.clientY - r.top, 3); });
  stage.addEventListener('pointerdown', e => { drag = { x: e.clientX, y: e.clientY, tx, ty, moved: false }; stage.setPointerCapture(e.pointerId); });
  stage.addEventListener('pointermove', e => {
    if (!drag || !(e.buttons & 1)) return;
    const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
    if (Math.abs(dx) + Math.abs(dy) > 3) drag.moved = true;
    tx = drag.tx + dx; ty = drag.ty + dy; content.style.transform = `translate(${tx}px, ${ty}px) scale(${s})`;
  });
  stage.addEventListener('pointerup', () => { if (drag && drag.moved) schedulePatch(); setTimeout(() => { drag = null; }, 0); });

  const api = {
    el,
    redraw: draw,
    highlight(f) { hl = f; draw(); },
    focus(f) { const ns = Math.max(s, 3); s = ns; tx = stage.clientWidth / 2 - (f.x + 0.5) * s; ty = stage.clientHeight / 2 - (f.y + 0.5) * s; hl = f; apply(); }
  };
  activeViewer = api;
  requestAnimationFrame(load);
  return api;
}

// ---- log ----------------------------------------------------------------------------------------------

function renderLog() {
  if (!S || !els.log) return;
  const pane = els.log;
  const atBottom = pane.scrollTop + pane.clientHeight >= pane.scrollHeight - 24;

  els.log.innerHTML = '';
  (S.log || []).slice(-40).forEach(entry => {
    const li = h('li', entry.level || 'info');
    add(li, h('span', 't', entry.t || ''), h('b', null, entry.text || ''));
    els.log.appendChild(li);
  });

  if (atBottom) pane.scrollTop = pane.scrollHeight;

  const last = (S.log || []).slice(-1)[0];
  if (els.loglast) els.loglast.textContent = last ? `${last.t} · ${last.text}` : '';
}

// ---- actions ------------------------------------------------------------------------------------------

async function postAct(action, extra) {
  const res = await fetch(API.act, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(Object.assign({ action }, extra || {}))
  });
  return res.json().catch(() => ({ ok: false, error: 'bad response from server' }));
}

let tickQueue = Promise.resolve();
async function act(action, extra) {
  // ticks queue up (clicking several items quickly must not drop any); everything else ignores a second press while one is running
  if (action === 'tick') { tickQueue = tickQueue.then(() => postTick(extra)); return tickQueue; }
  if (busy) return;
  busy = true;
  try {
    const data = await postAct(action, extra);
    if (!data.ok) toast(data.error || `${action} is not possible at this step`);
    await refresh(true);
  } catch (e) {
    toast(e.message || String(e));
  } finally {
    busy = false;
  }
}

async function postTick(extra) {
  try {
    const data = await postAct('tick', extra);
    if (!data.ok) toast(data.error || 'tick is not possible at this step');
    await refresh(true);
  } catch (e) {
    toast(e.message || String(e));
  }
}

// ---- simulation demo ----------------------------------------------------------------------------------

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

async function startDemo() {
  if (demoRunning) return;
  demoRunning = true;
  demoStop = false;
  renderHeader();
  toast('Demo: running the recipe. Press Stop demo to pause.', 'ok');

  try {
    while (!demoStop) {
      await refresh(true);
      const step = current();
      if (!step) { toast('Demo finished.', 'ok'); break; }

      if (step.status === 'running') { await sleep(600); continue; }
      if (step.status === 'failed' && step.kind !== 'instruct') {
        toast('Demo stopped: ' + (step.note || 'the step failed'));
        break;
      }

      await demoAct(step);
      await refresh(true);
      await sleep(350);
    }
  } finally {
    demoRunning = false;
    renderHeader();
  }
}

async function demoAct(step) {
  if (step.kind === 'instruct') {
    for (let i = 0; i < (step.items || []).length; i++) {
      if (step.items[i].status === 'todo') await postAct('tick', { item: i });
    }
    // timed items (battery out / wait) still have to run out on the server
    for (let k = 0; k < 200 && !demoStop; k++) {
      await refresh(true);
      const now = current();
      if (!now) break;
      if (now.kind !== 'instruct' || itemsDone(now)) break;
      await sleep(400);
    }
    if (!demoStop) {
      const r = await postAct('continue');
      if (!r.ok) toast(r.error || 'Could not continue');
    }
    return;
  }

  if (step.kind === 'run' || step.kind === 'result') {
    if (step.status === 'pending' || step.status === 'failed') {
      await postAct(step.status === 'failed' ? 'retry' : 'start');
    }
    return;
  }

  if (step.kind === 'approve') {
    const confirm = step.summary && step.summary.confirm;
    await postAct('approve', confirm ? { confirm } : {});
    return;
  }

  if (step.kind === 'review') {
    const found = (S.ctx && S.ctx.found) || [];
    const chosen = selected.size
      ? Array.from(selected)
      : found.filter(f => f.selected).map(f => `${f.x},${f.y}`);
    await postAct('review', { selected: chosen });
  }
}

function toast(text, kind) {
  let t = $('toast');
  if (!t) {
    t = h('div', 'toast');
    t.id = 'toast';
    document.body.appendChild(t);
  }
  t.textContent = text;
  t.classList.toggle('ok', kind === 'ok');
  t.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove('show'), 6000);
}

// ---- start --------------------------------------------------------------------------------------------

document.addEventListener('DOMContentLoaded', init);

window.xpixelmap = { refresh, act, submitReview };
