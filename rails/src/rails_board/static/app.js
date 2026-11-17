'use strict';
/* The board viewer. State lives on the server; this file renders it and posts changes.
   Server events (`change`, `presence`) trigger a refetch, so every open tab stays current. */

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

const STATUSES = [['todo', 'To review'], ['discussing', 'Discussing'], ['settled', 'Settled']];
/* A list board: the same cards and columns for a plain to-do list. Nothing on it goes to Claude. */
const LIST_STATUSES = [['todo', 'To do'], ['discussing', 'In progress'], ['settled', 'Done']];
const OUTCOMES = [
  ['fix', 'Fix', 'Claude makes the change'],
  ['defer', 'Defer', 'Real, but not in this round'],
  ['wontfix', "Won't fix", 'Real, and accepted as it is'],
  ['invalid', 'Not an issue', 'The finding does not hold'],
];
const OUTCOME_LABEL = Object.fromEntries(OUTCOMES.map(o => [o[0], o[1]]));
const VERDICT = { valid: 'agrees', partial: 'partly agrees', invalid: 'disagrees', unsure: 'is unsure' };
const SEVERITY = { critical: 'Critical', high: 'High', medium: 'Medium', low: 'Low' };

const S = {
  listening: [], board: null, boardId: null, open: null, drafts: {},
  press: null, drag: null, stale: false, offline: false, stamp: null, peekSig: '',
};

// ---------- helpers ----------
async function api(path, body) {
  const res = await fetch(path, body === undefined ? {} : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `The server answered ${res.status}`);
  return data;
}

function toast(text, bad) {
  const t = $('#toast');
  t.textContent = text;
  t.className = 'show' + (bad ? ' bad' : '');
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { t.className = ''; }, bad ? 5000 : 2200);
}

function when(ts) {
  const d = new Date(ts * 1000);
  const time = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
  return d.toDateString() === new Date().toDateString()
    ? time : `${d.toLocaleDateString([], { day: 'numeric', month: 'short' })}, ${time}`;
}

const listed = () => S.board?.kind === 'list';
const statuses = () => listed() ? LIST_STATUSES : STATUSES;
const statusName = status => statuses().find(s => s[0] === status)[1];
const sourceName = b => (b.source || 'reviewer').replace(/^./, c => c.toUpperCase());
const issue = key => S.board?.issues.find(i => i.key === key);
const column = status => S.board.issues.filter(i => i.status === status).sort((a, b) => a.order - b.order);
const place = i => !i.file ? '' : i.file + (i.line_start ? ':' + i.line_start + (i.line_end && i.line_end !== i.line_start ? '-' + i.line_end : '') : '');
const last = i => i.messages[i.messages.length - 1];

/* Where a thread stands: Claude owes a reply ('working' once it has the message, 'queued' before). */
function owed(i) {
  if (listed()) return null;  // notes on a list card are owed nothing
  const m = last(i);
  return m && m.author === 'user' ? (m.delivered ? 'working' : 'queued') : null;
}

/* What Claude said it would do for the outcome this issue was settled as: the note on its latest
   suggestion, when that suggestion is for the same outcome. Empty if you chose a different one. */
const instruction = i => i.status === 'settled' && i.proposal?.outcome === i.outcome ? i.proposal.note || '' : '';

function suggestionHtml(p) {
  return `<div class="suggest"><span>Suggests settling as ${OUTCOME_LABEL[p.outcome]}</span>${p.note ? `<div class="md">${md(p.note)}</div>` : ''}</div>`;
}

/* The last entry of a settled issue: what happens to it at hand-off. */
function decisionHtml(i) {
  const fix = i.outcome === 'fix';
  const mine = instruction(i);
  const parts = [];
  if (mine) parts.push(`<div class="suggest"><span>${fix ? 'Claude will' : 'Claude’s reason'}</span><div class="md">${md(mine)}</div></div>`);
  if (i.note && fix) parts.push(`<p class="none">${mine ? 'Your instruction below goes to Claude as well, and takes priority where the two differ.' : 'Claude will follow your instruction below.'}</p>`);
  if (fix && !mine && !i.note) parts.push(`<p class="none">Claude gave no instruction for this outcome, so it will follow ${esc(sourceName(S.board))}’s recommendation. Write your own below to say otherwise.</p>`);
  if (fix && owed(i)) parts.push('<p class="msg-state">Claude has not answered your last message yet, so its instruction may still change.</p>');
  return `<div class="entry entry-decision"><div class="who"><b>Settled as ${OUTCOME_LABEL[i.outcome]}</b><time>${i.settled_at ? when(i.settled_at) : ''}</time>${
    i.handed ? '<span class="mid">handed to Claude</span>' : ''}</div>${parts.join('')}</div>`;
}

const seen = JSON.parse(localStorage.getItem('rails-seen') || '{}');
/* Unread: Claude has the last word on the issue and you have not had it open, in this window, since. */
function unread(i) {
  const m = last(i);
  return !!m && m.author === 'claude' && (seen[S.boardId] || {})[i.key] !== m.id;
}
function markSeen(i) {
  const m = last(i);
  if (!m || (seen[S.boardId] || {})[i.key] === m.id) return false;
  (seen[S.boardId] ||= {})[i.key] = m.id;
  localStorage.setItem('rails-seen', JSON.stringify(seen));
  return true;
}

// ---------- a small markdown renderer (input is escaped first; nothing is trusted) ----------
function inline(s) {
  return s.split(/(`[^`\n]+`)/).map(part => /^`[^`\n]+`$/.test(part)
    ? `<code>${esc(part.slice(1, -1))}</code>`
    : esc(part)
      .replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>')
      .replace(/(^|[\s(])\*([^*\s][^*\n]*)\*(?=[\s).,;:!?]|$)/g, '$1<em>$2</em>')
      .replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer">$1</a>')
  ).join('');
}

function md(src) {
  const lines = String(src || '').replace(/\r/g, '').split('\n');
  const out = [];
  let para = [];
  const flush = () => { if (para.length) { out.push(`<p>${para.map(inline).join('<br>')}</p>`); para = []; } };
  const item = /^\s*([-*+]|\d+[.)])\s+(.*)$/;
  for (let n = 0; n < lines.length; n++) {
    const line = lines[n];
    if (/^\s*```/.test(line)) {
      flush();
      const code = [];
      for (n++; n < lines.length && !/^\s*```/.test(lines[n]); n++) code.push(lines[n]);
      out.push(`<pre><code>${esc(code.join('\n'))}</code></pre>`);
    } else if (/^#{1,6}\s/.test(line)) {
      flush();
      out.push(`<h4>${inline(line.replace(/^#+\s+/, ''))}</h4>`);
    } else if (item.test(line)) {
      flush();
      const ordered = /^\s*\d/.test(line);
      const items = [];
      for (; n < lines.length; n++) {
        const m = lines[n].match(item);
        if (m) items.push(m[2]);
        else if (/^\s+\S/.test(lines[n]) && items.length) items[items.length - 1] += '\n' + lines[n].trim();
        else break;
      }
      n--;
      const tag = ordered ? 'ol' : 'ul';
      out.push(`<${tag}>${items.map(t => `<li>${t.split('\n').map(inline).join('<br>')}</li>`).join('')}</${tag}>`);
    } else if (/^>\s?/.test(line)) {
      flush();
      const quote = [];
      for (; n < lines.length && /^>\s?/.test(lines[n]); n++) quote.push(lines[n].replace(/^>\s?/, ''));
      n--;
      out.push(`<blockquote>${md(quote.join('\n'))}</blockquote>`);
    } else if (!line.trim()) {
      flush();
    } else {
      para.push(line);
    }
  }
  flush();
  return out.join('');
}

// ---------- icons ----------
function statusIcon(status, outcome) {
  if (status === 'todo') return '<svg class="st st-todo" viewBox="0 0 14 14" aria-hidden="true"><circle cx="7" cy="7" r="5.5" fill="none" stroke="currentColor" stroke-width="1.5" stroke-dasharray="2.2 2.1"/></svg>';
  if (status === 'discussing') return '<svg class="st st-discussing" viewBox="0 0 14 14" aria-hidden="true"><circle cx="7" cy="7" r="5.5" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M7 3.6a3.4 3.4 0 0 1 0 6.8z" fill="currentColor"/></svg>';
  return `<svg class="st st-settled o-${outcome || 'any'}" viewBox="0 0 14 14" aria-hidden="true"><circle cx="7" cy="7" r="6.2" fill="currentColor"/><path d="M4.2 7.2l1.9 1.9 3.7-4" fill="none" stroke="var(--bg)" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
}

function severityIcon(sev) {
  const label = `<span class="sr">${SEVERITY[sev]} severity</span>`;
  if (sev === 'critical') return `<svg class="sev sev-critical" viewBox="0 0 14 14"><title>Critical severity</title><rect x="1" y="1" width="12" height="12" rx="3" fill="currentColor"/><path d="M7 3.6v4.2M7 9.9v.4" stroke="var(--card)" stroke-width="1.7" stroke-linecap="round"/></svg>${label}`;
  const bars = { high: 3, medium: 2, low: 1 }[sev];
  return `<svg class="sev sev-${sev}" viewBox="0 0 14 14"><title>${SEVERITY[sev]} severity</title>${[0, 1, 2].map(n =>
    `<rect x="${1.5 + n * 4}" y="${9 - n * 3}" width="3" height="${4 + n * 3}" rx="1" fill="currentColor" opacity="${n < bars ? 1 : 0.28}"/>`).join('')}</svg>${label}`;
}

// ---------- rendering ----------
function renderHead() {
  const b = S.board;
  if (!b) {
    $('#head').innerHTML = '';
    return;
  }
  const offline = S.offline ? '<div class="banner">The board server stopped. Any <code>rails-board</code> command in Claude Code starts it again.</div>' : '';
  const settled = b.issues.filter(i => i.status === 'settled');
  if (listed()) {
    $('#head').innerHTML = `${offline}
    <div class="head-row">
      <div class="head-title">
        <h1>${esc(b.title)}</h1>
        <p>${settled.length} of ${b.issues.length} done<span class="gap"></span>${when(b.created)}</p>
      </div>
    </div>`;
    return;
  }
  const fresh = settled.filter(i => !i.handed).length;
  const working = b.issues.some(i => owed(i) === 'working');
  const listening = S.listening.includes(b.id);
  const presence = working ? ['working', 'Claude is replying']
    : listening ? ['on', 'Claude is listening']
    : ['off', 'Claude isn’t listening'];
  $('#head').innerHTML = `
    ${offline}
    <div class="head-row">
      <div class="head-title">
        <h1>${esc(b.title)}</h1>
        <p>${esc(sourceName(b))} review${b.target ? ` of <code>${esc(b.target)}</code>` : ''}<span class="gap"></span>${when(b.created)}</p>
      </div>
      <span class="presence p-${presence[0]}" title="${listening || working ? '' : 'Messages wait here until Claude runs /rails for this board.'}"><i></i>${presence[1]}</span>
      <button class="btn${fresh ? ' primary' : ''}" data-act="handoff" ${fresh ? '' : 'disabled'}
        title="Sends the settled outcomes to Claude so it can make the agreed changes">${
        fresh ? `Hand ${fresh} decision${fresh > 1 ? 's' : ''} to Claude` : settled.length ? 'Decisions handed to Claude' : 'Hand decisions to Claude'}</button>
    </div>`;
}

const countHtml = (n, what) => `<span class="count" title="${n} ${what}"><svg viewBox="0 0 14 14" aria-hidden="true"><path d="M2.5 3h9v6.2h-5L4 11.4V9.2H2.5z" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/></svg>${n}</span>`;

function cardHtml(i) {
  if (listed()) {
    return `<article class="card${i.key === S.open ? ' open' : ''}" data-key="${i.key}">
    <div class="card-top"><span class="key">${i.key}</span><span class="grow"></span>${i.messages.length ? countHtml(i.messages.length, i.messages.length > 1 ? 'notes' : 'note') : ''}</div>
    <div class="card-title">${esc(i.title)}</div>
  </article>`;
  }
  const state = owed(i);
  const activity = unread(i) ? '<span class="chip chip-new">Claude replied</span>'
    : state === 'working' ? '<span class="chip chip-wait"><i class="pulse"></i>Claude is replying</span>'
    : state === 'queued' ? '<span class="chip chip-wait">Waiting for Claude</span>'
    : i.messages.length ? countHtml(i.messages.length, 'messages') : '';
  const verdict = i.verdict ? `<span class="verdict v-${i.verdict}">Claude ${VERDICT[i.verdict]}</span>` : '<span class="verdict v-none">Not triaged yet</span>';
  return `<article class="card${i.key === S.open ? ' open' : ''}" data-key="${i.key}">
    <div class="card-top"><span class="key">${i.key}</span>${severityIcon(i.severity)}<span class="grow"></span>${activity}</div>
    <div class="card-title">${esc(i.title)}</div>
    <div class="card-foot">${i.status === 'settled' ? `<span class="outcome o-${i.outcome}">${OUTCOME_LABEL[i.outcome]}</span>` : verdict}
      ${place(i) ? `<span class="file" title="${esc(place(i))}">${esc(place(i).split('/').pop())}</span>` : ''}</div>
  </article>`;
}

function renderCols() {
  const b = S.board;
  const cols = $('#cols');
  if (!b) {
    cols.innerHTML = S.missing ? `<div class="empty"><h2>This board is no longer here</h2>
      <p><code>${esc(S.missing)}</code> was removed, or deleted because nobody had touched it for a week. Run <code>rails-board boards</code> in Claude Code to see the boards that exist.</p></div>`
      : `<div class="empty"><h2>No review is open here</h2>
      <p>After a review comes back in Claude Code, run <code>/rails:rails</code>. Its findings open on this page as cards, each with Claude’s triage, and you can question any of them from here.</p></div>`;
    return;
  }
  const scroll = Object.fromEntries($$('.cards', cols).map(el => [el.parentElement.dataset.status, el.scrollTop]));
  const hint = listed()
    ? { todo: 'Nothing left to do.', discussing: 'Drag a card here when you start on it.', settled: 'Drag a card here when it is done.' }
    : { todo: b.issues.length ? 'Everything has been looked at.' : 'This review has no findings.',
        discussing: 'Ask Claude about a card and it moves here.', settled: 'Drag a card here once you and Claude agree.' };
  cols.innerHTML = statuses().map(([status, label]) => {
    const items = column(status);
    return `<section class="col" data-status="${status}" aria-label="${label}">
      <header>${statusIcon(status)}<span>${label}</span><span class="n">${items.length}</span></header>
      <div class="cards">${items.map(cardHtml).join('') || `<p class="col-empty">${hint[status]}</p>`}</div>
      ${status === 'settled' && !listed() ? `<div class="zones">${OUTCOMES.map(([o, name, hint]) =>
        `<div class="zone" data-outcome="${o}"><b>${name}</b><span>${hint}</span></div>`).join('')}</div>` : ''}
    </section>`;
  }).join('');
  for (const el of $$('.cards', cols)) el.scrollTop = scroll[el.parentElement.dataset.status] || 0;
}

function messageHtml(m, i) {
  const mine = m.author === 'user';
  const isLast = m === last(i);
  const status = mine && isLast && listed() ? '<p class="msg-state"><span><kbd>esc</kbd> to take it back</span></p>'
    : mine && isLast
    ? `<p class="msg-state">${m.delivered ? '<i class="pulse"></i>Claude has this and is working on a reply.'
      : S.listening.includes(S.boardId) ? 'Sent. Claude picks it up in a moment.'
      : 'Saved. Claude isn’t listening on this board, so it gets this when it next runs /rails.'}
      <span><kbd>esc</kbd> to take it back</span></p>` : '';
  const extras = [
    m.verdict ? `Verdict is now: Claude ${VERDICT[m.verdict]}` : '',
  ].filter(Boolean).map(t => `<p class="msg-extra">${esc(t)}</p>`).join('') + (m.propose ? suggestionHtml(m.propose) : '');
  return `<div class="entry msg-${m.author}"><div class="who"><b>${mine ? 'You' : 'Claude'}</b><time>${when(m.ts)}</time><span class="mid">${i.key}#${m.n}</span></div>
    <div class="md">${md(m.text)}</div>${extras}${status}</div>`;
}

function renderPeek() {
  const i = issue(S.open);
  $('#scrim').hidden = !i;
  if (!i) {
    S.peekSig = '';
    return;
  }
  const order = statuses().flatMap(([s]) => column(s));
  const at = order.indexOf(i);
  $('#peek-bar').innerHTML = `
    <span class="key">${i.key}</span>
    <span class="state">${statusIcon(i.status, i.outcome)}${i.status === 'settled' && !listed() ? 'Settled as ' + OUTCOME_LABEL[i.outcome] : statusName(i.status)}</span>
    <span class="grow"></span>
    <button class="icon" data-act="prev" ${at <= 0 ? 'disabled' : ''} title="Previous issue" aria-label="Previous issue">↑</button>
    <button class="icon" data-act="next" ${at >= order.length - 1 ? 'disabled' : ''} title="Next issue" aria-label="Next issue">↓</button>
    <button class="icon" data-act="close" title="Close" aria-label="Close">✕</button>`;

  const src = esc(sourceName(S.board));
  const scroller = $('#peek-scroll');
  const sig = [i.key, i.messages.length, i.status, i.outcome, i.note, instruction(i)].join(':');  // what, when it changes, belongs in view
  const switched = !S.peekSig.startsWith(i.key + ':');
  const grew = !switched && sig !== S.peekSig;
  const keepTop = scroller.scrollTop;
  const wasAtEnd = scroller.scrollHeight - scroller.clientHeight - keepTop < 4;
  $('#draft').setAttribute('aria-label', listed()
    ? 'A note on this card. Enter saves it, Shift and Enter adds a line, Escape takes back the last note'
    : 'Message to Claude. Enter sends, Shift and Enter adds a line, Escape takes back the last message');
  scroller.innerHTML = listed() ? `
    <h2>${esc(i.title)}</h2>
    ${i.body ? `<div class="entry entry-source"><div class="md source">${md(i.body)}</div></div>` : ''}
    ${i.messages.map(m => messageHtml(m, i)).join('')}` : `
    <h2>${esc(i.title)}</h2>
    <p class="meta">${severityIcon(i.severity)}<span>${SEVERITY[i.severity]} severity</span>
      ${place(i) ? `<button class="file" data-copy="${esc(place(i))}" title="Copy path">${esc(place(i))}</button>` : ''}
      ${typeof i.confidence === 'number' ? `<span>${src} is ${Math.round(i.confidence * 100)}% confident</span>` : ''}</p>
    <div class="entry entry-source"><div class="who"><b>${src}</b><span>reported this</span></div>
      <div class="md source">${md(i.body) || '<p class="none">No description was given.</p>'}</div>
      ${i.recommendation ? `<div class="recommend"><span>${src} recommends</span><div class="md source">${md(i.recommendation)}</div></div>` : ''}
    </div>
    <div class="entry entry-triage"><div class="who"><b>Claude</b><span>${i.verdict ? VERDICT[i.verdict] : 'hasn’t triaged this yet'}</span></div>
      ${i.triage ? `<div class="md">${md(i.triage)}</div>` : i.verdict ? '' : '<p class="none">Its verdict shows up here when it has checked the finding against the code.</p>'}
      ${i.proposal && !i.messages.some(m => m.propose) ? suggestionHtml(i.proposal) : ''}
    </div>
    ${i.messages.map(m => messageHtml(m, i)).join('')}
    ${i.status === 'settled' ? decisionHtml(i) : ''}`;
  S.peekSig = sig;

  // Settling is done by dragging the card on the board. A settled issue keeps one field here: the note.
  const note = $('#note');
  const typing = note && document.activeElement === note && note.dataset.key === i.key;
  if (!typing) {
    $('#settle').innerHTML = i.status === 'settled' && !listed()
      ? `<input id="note" data-key="${i.key}" value="${esc(i.note)}" placeholder="Your own instruction for this, if Claude’s is not what you want (optional)" aria-label="Your instruction">` : '';
  }
  const draft = $('#draft');
  if (draft.dataset.key !== i.key) {
    if (draft.dataset.key) S.drafts[draft.dataset.key] = draft.value;
    draft.dataset.key = i.key;
    draft.value = S.drafts[i.key] || '';
    grow(draft);
  }
  // Last, once the footer has its final height: an issue opens at the bottom, on its newest entry.
  // The same when something new arrives, or when you were already at the bottom; otherwise stay put.
  scroller.scrollTop = switched || grew || wasAtEnd ? scroller.scrollHeight : keepTop;
}

function render() {
  if (S.drag) { S.stale = true; return; }
  if (S.board && S.open && !issue(S.open)) S.open = null;
  // The open issue counts as read only while you are actually in this window looking at it.
  if (S.open && document.hasFocus()) markSeen(issue(S.open));
  document.body.classList.toggle('list', listed());
  renderHead();
  renderCols();
  land();
  renderPeek();
  document.title = S.board ? `${S.board.issues.filter(i => i.status === 'settled').length}/${S.board.issues.length} ${S.board.title}` : 'Review board';
}

// ---------- loading ----------
async function loadBoard() {
  if (!S.boardId) { S.board = null; return; }
  try {
    S.board = await api('/api/boards/' + encodeURIComponent(S.boardId));
  } catch {
    S.board = null;
  }
}

/* The page shows one review: the one named in the URL, else the newest. */
async function route() {
  const [id, key] = decodeURIComponent(location.hash.slice(1)).split('/');
  const { boards, listening } = await api('/api/boards');
  S.listening = listening;
  // The newest board is shown only when the address names none. An address naming a board that is
  // gone shows that it is gone, never some other review in its place.
  const pick = id ? boards.find(b => b.id === id) : boards[0];
  S.missing = id && !pick ? id : null;
  const changed = pick?.id !== S.boardId;
  S.boardId = pick?.id || null;
  if (changed || !S.board) await loadBoard();
  S.open = key && issue(key.toUpperCase()) ? key.toUpperCase() : null;
  render();
}

function setHash() {
  const want = S.boardId ? '#' + S.boardId + (S.open ? '/' + S.open : '') : '';
  if (location.hash !== want) history.replaceState(null, '', want || location.pathname);
}

let refreshing = null;
function refresh() {
  refreshing ||= Promise.resolve().then(async () => {
    refreshing = null;
    if (!S.boardId) return route();
    await loadBoard();
    render();
  }).catch(() => {});
}

function listen() {
  const es = new EventSource('/api/events');
  es.addEventListener('presence', e => {
    const d = JSON.parse(e.data);
    if (d.stamp) {
      if (S.stamp && S.stamp !== d.stamp) return location.reload();  // the server restarted on new code
      S.stamp = d.stamp;
    }
    S.listening = d.listening;
    if (S.offline) { S.offline = false; refresh(); } else render();
  });
  es.addEventListener('change', e => { if (!S.boardId || JSON.parse(e.data).board === S.boardId) refresh(); });
  es.onerror = () => {
    clearTimeout(listen.timer);
    listen.timer = setTimeout(() => {
      if (es.readyState !== EventSource.OPEN && !S.offline) { S.offline = true; S.listening = []; render(); }
    }, 4000);
  };
}

// ---------- actions ----------
async function patch(key, body, okText) {
  try {
    putIssue(await api(`/api/boards/${encodeURIComponent(S.boardId)}/issues/${key}`, body));
    if (okText) toast(okText);
  } catch (e) {
    toast(e.message, true);
    refresh();
  }
}

function settle(key, outcome, before) {
  const i = issue(key);
  if (!i) return;
  const body = { status: 'settled', outcome };
  if (before !== undefined) body.before = before;
  patch(key, body, `${key} settled as ${OUTCOME_LABEL[outcome]}`);
}

function move(key, status, before) {
  const i = issue(key);
  if (!i) return;
  patch(key, { status, before: before ?? null }, i.status === status ? '' : `${key} moved to ${statusName(status)}`);
}

function openIssue(key) {
  S.open = key;
  setHash();
  render();
}

function closeIssue() {
  const draft = $('#draft');
  if (draft.dataset.key) S.drafts[draft.dataset.key] = draft.value;
  S.open = null;
  setHash();
  render();
}

function putIssue(updated) {
  const at = S.board.issues.findIndex(i => i.key === updated.key);
  if (at >= 0) S.board.issues[at] = updated;
  render();
}

async function send() {
  const draft = $('#draft');
  const key = S.open;
  const text = draft.value.trim();
  if (!key || !text || S.sending) return;
  S.sending = true;
  try {
    const updated = await api(`/api/boards/${encodeURIComponent(S.boardId)}/issues/${key}/messages`, { author: 'user', text });
    S.drafts[key] = '';
    // Empty the box only if it still shows this card and still holds what was sent: by the time the
    // answer is back you may have opened another card, or typed more.
    if (S.open === key && draft.value.trim() === text) {
      draft.value = '';
      grow(draft);
    }
    putIssue(updated);
  } catch (e) {
    toast(e.message, true);
  }
  S.sending = false;
}

/* Esc, as in Claude Code: the last message you sent that Claude has not answered leaves the thread
   and comes back into the box to edit. If Claude already picked it up, its reply to it is refused. */
async function takeBack() {
  const i = issue(S.open);
  const m = i && last(i);
  if (!m || m.author !== 'user' || S.sending) return;
  S.sending = true;
  try {
    const res = await api(`/api/boards/${encodeURIComponent(S.boardId)}/issues/${i.key}/withdraw`, {});
    const draft = $('#draft');
    const ahead = (S.open === i.key ? draft.value : S.drafts[i.key]) || '';
    const text = ahead.trim() ? res.text + '\n' + ahead : res.text;
    if (S.open === i.key) {
      draft.value = text;
      putIssue(res.issue);
      grow(draft);
      draft.focus();
      draft.setSelectionRange(draft.value.length, draft.value.length);
    } else {  // another card is open by now: the text goes back to its own card's draft, not into this box
      S.drafts[i.key] = text;
      putIssue(res.issue);
    }
    toast(res.delivered ? `${i.key}#${res.n} taken back. Claude had started on it; that reply will be dropped.` : `${i.key}#${res.n} taken back`);
  } catch (e) {
    toast(e.message, true);
    refresh();
  }
  S.sending = false;
}

function grow(el) {
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 220) + 'px';
}

function step(delta) {
  const order = statuses().flatMap(([s]) => column(s));
  const next = order[order.findIndex(i => i.key === S.open) + delta];
  if (next) openIssue(next.key);
}

// ---------- drag and drop ----------
function startDrag(e) {
  const { card, key } = S.press;
  const r = card.getBoundingClientRect();
  const ghost = card.cloneNode(true);
  ghost.classList.add('ghost');
  ghost.style.width = r.width + 'px';
  const line = Object.assign(document.createElement('div'), { className: 'drop-line', hidden: true });
  document.body.append(ghost, line);
  requestAnimationFrame(() => ghost.classList.add('up'));  // a frame later, so the tilt eases in
  card.classList.add('lifted');
  document.body.classList.add('dragging');
  S.drag = { key, ghost, line, dx: e.clientX - r.left, dy: e.clientY - r.top, x: r.left, y: r.top, target: null, col: null };
}

/* Each step only moves two things that sit above the page (the lifted card and the drop line), so
   nothing in the columns is laid out again while you drag. */
function moveDrag(e) {
  const d = S.drag;
  d.x = e.clientX - d.dx;
  d.y = e.clientY - d.dy;
  d.ghost.style.translate = `${d.x}px ${d.y}px`;
  const col = document.elementFromPoint(e.clientX, e.clientY)?.closest('.col');
  $$('.col.over, .zone.over').forEach(el => el.classList.remove('over'));
  d.line.hidden = true;
  d.target = null;
  if (!col) return;
  col.classList.add('over');
  const status = col.dataset.status;
  if (status === 'settled' && !listed()) {
    const zones = $$('.zone', col);
    const mid = z => { const r = z.getBoundingClientRect(); return r.top + r.height / 2; };
    const zone = zones.reduce((a, z) => Math.abs(mid(z) - e.clientY) < Math.abs(mid(a) - e.clientY) ? z : a);
    zone.classList.add('over');
    d.target = { status, outcome: zone.dataset.outcome };
    return;
  }
  const cards = $$('.card:not(.lifted)', col);
  const before = cards.find(c => { const r = c.getBoundingClientRect(); return e.clientY < r.top + r.height / 2; });
  // The line sits in the gap above the card it would go before, or under the last card.
  const box = $('.cards', col).getBoundingClientRect();
  const y = before ? before.getBoundingClientRect().top - 5
    : cards.length ? cards[cards.length - 1].getBoundingClientRect().bottom + 3 : box.top + 2;
  d.line.style.transition = d.col === col ? '' : 'none';  // glide within a column, jump between columns
  d.col = col;
  d.line.style.width = box.width - 4 + 'px';
  d.line.style.translate = `${box.left + 2}px ${y}px`;
  d.line.hidden = false;
  d.target = { status, before: before?.dataset.key || null };
}

function endDrag(apply) {
  const d = S.drag;
  d.line.remove();
  document.body.classList.remove('dragging');
  $$('.col.over, .zone.over').forEach(el => el.classList.remove('over'));
  S.drag = null;
  S.landing = { key: d.key, ghost: d.ghost, flying: false };
  const t = apply && d.target;
  let changing = false;
  if (t) {
    const i = issue(d.key);
    if (t.status === 'settled' && !listed()) {
      changing = !(i.status === 'settled' && i.outcome === t.outcome);
      if (changing) settle(d.key, t.outcome, null);
    } else {
      const here = column(t.status);
      const next = here[here.indexOf(i) + 1]?.key || null;  // dropped where it already was?
      changing = !(i.status === t.status && next === t.before);
      if (changing) move(d.key, t.status, t.before);
    }
  }
  // A change redraws the columns, and the card lands then. With no change it goes back where it was.
  if (!changing) land();
  if (S.stale) { S.stale = false; refresh(); }
}

/* The let-go card glides from where it was dropped into its place, instead of jumping there. The real
   card stays hidden under it until it arrives, through any redraw that happens meanwhile. */
function land() {
  const l = S.landing;
  if (!l) return;
  const card = $$('#cols .card').find(c => c.dataset.key === l.key);
  const done = () => {
    l.ghost.remove();
    if (S.landing === l) S.landing = null;
    for (const c of $$('#cols .card')) { c.style.visibility = ''; c.classList.remove('lifted'); }
  };
  if (!card || matchMedia('(prefers-reduced-motion: reduce)').matches) return done();
  card.style.visibility = 'hidden';
  if (l.flying) return;
  l.flying = true;
  const r = card.getBoundingClientRect();
  const flight = l.ghost.animate({ translate: `${r.left}px ${r.top}px`, rotate: '0deg' }, { duration: 170, easing: 'cubic-bezier(0.2, 0.8, 0.2, 1)', fill: 'forwards' });
  flight.onfinish = flight.oncancel = done;
}

document.addEventListener('pointerdown', e => {
  S.downOnScrim = e.target.id === 'scrim';
  const card = e.target.closest('#cols .card');
  if (card && e.button === 0) S.press = { card, key: card.dataset.key, x: e.clientX, y: e.clientY };
});
document.addEventListener('pointermove', e => {
  if (!S.press) return;
  if (!S.drag) {
    if (Math.hypot(e.clientX - S.press.x, e.clientY - S.press.y) < 5) return;
    startDrag(e);
  }
  moveDrag(e);
});
document.addEventListener('pointerup', () => {
  if (S.drag) endDrag(true);
  else if (S.press) openIssue(S.press.key);
  S.press = null;
});
document.addEventListener('pointercancel', () => {
  if (S.drag) endDrag(false);
  S.press = null;
});

// ---------- clicks and typing ----------
document.addEventListener('click', async e => {
  if (e.target.id === 'scrim') {  // a click outside the popup closes it; a text selection dragged out of it does not
    if (S.downOnScrim) closeIssue();
    return;
  }
  const t = e.target.closest('[data-open], [data-act], [data-copy]');
  if (!t) return;
  if (t.dataset.open) return openIssue(t.dataset.open);
  if (t.dataset.copy) {
    await navigator.clipboard.writeText(t.dataset.copy).catch(() => {});
    return toast('Copied ' + t.dataset.copy);
  }
  const act = t.dataset.act;
  if (act === 'close') closeIssue();
  if (act === 'prev') step(-1);
  if (act === 'next') step(1);
  if (act === 'handoff') {
    try {
      const h = await api(`/api/boards/${encodeURIComponent(S.boardId)}/handoff`, {});
      toast(S.listening.includes(S.boardId) ? `Handed ${h.keys.length} decision${h.keys.length > 1 ? 's' : ''} to Claude`
        : 'Saved. Claude gets them when it next runs /rails');
    } catch (err) {
      toast(err.message, true);
    }
  }
});

$('#draft').addEventListener('input', e => grow(e.target));
document.addEventListener('change', e => {
  if (e.target.id === 'note') patch(e.target.dataset.key, { note: e.target.value }, 'Instruction saved');
});

/* The only keys: Enter sends (Shift+Enter adds a line), Esc takes the last message back. */
document.addEventListener('keydown', e => {
  if (e.isComposing) return;  // an input method is still composing; its Enter and Esc are not ours
  if (e.key === 'Enter' && e.target.id === 'note') e.target.blur();  // commits the note, as any one-line field does
  if (e.key === 'Enter' && e.target.id === 'draft' && !e.shiftKey && !e.altKey && !e.metaKey && !e.ctrlKey) {
    e.preventDefault();
    send();
  }
  if (e.key === 'Escape' && S.open) {
    e.preventDefault();
    takeBack();
  }
});

window.addEventListener('focus', render);  // coming back to the window reads what is open
window.addEventListener('hashchange', route);
route().then(listen);
