/* 镜 MirrorOS — 前端。零依赖，无构建步骤。 */
'use strict';

/* ------------------------------------------------------------------ 基础 */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
const todayStr = () => new Date().toLocaleDateString('sv-SE');
const fmt = n => (n == null || n === '' || isNaN(n)) ? '—' : Number(n).toLocaleString('zh-CN', { maximumFractionDigits: 2 });

let toastTimer = null;
function toast(msg, kind = '') {
  const t = $('#toast');
  t.textContent = msg;
  t.className = 'toast on ' + kind;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.className = 'toast ' + kind; }, kind === 'err' ? 5200 : 2600);
}

async function api(path, body, method) {
  const opt = { method: method || (body ? 'POST' : 'GET'), headers: {} };
  if (body) { opt.headers['Content-Type'] = 'application/json'; opt.body = JSON.stringify(body); }
  const r = await fetch(path, opt);
  let data = {};
  try { data = await r.json(); } catch (e) { data = { error: '服务器返回了无法解析的内容' }; }
  if (r.status === 401) { showLogin(); throw new Error('未登录'); }
  if (!r.ok || data.error) throw new Error(data.error || ('HTTP ' + r.status));
  return data;
}

/* ------------------------------------------------------------------ 弹层 */
function closeModal() { $('#modalBg').classList.remove('on'); $('#modalBox').innerHTML = ''; }
function openModal(html) {
  $('#modalBox').innerHTML = html;
  $('#modalBg').classList.add('on');
  $('#modalBox').scrollTop = 0;
}
$('#modalBg').addEventListener('click', e => { if (e.target.id === 'modalBg') closeModal(); });

/** 通用的「必须写理由」输入框。返回 Promise<string|null> */
function askText({ title, label, hint = '', minLength = 8, placeholder = '', danger = false, confirm = '确认' }) {
  return new Promise(resolve => {
    openModal(`
      <h2>${esc(title)}</h2>
      <div class="small muted" style="margin-bottom:10px">${hint}</div>
      <label class="fld"><span>${esc(label)}（至少 ${minLength} 字）</span>
        <textarea id="askTa" placeholder="${esc(placeholder)}" style="min-height:90px"></textarea>
      </label>
      <div class="tiny muted" id="askCount">0 字</div>
      <div class="row" style="margin-top:14px">
        <div class="sp"></div>
        <button class="btn" id="askNo">取消</button>
        <button class="btn ${danger ? 'danger' : 'primary'}" id="askYes" disabled>${esc(confirm)}</button>
      </div>`);
    const ta = $('#askTa'), btn = $('#askYes'), cnt = $('#askCount');
    ta.focus();
    ta.oninput = () => {
      const n = ta.value.trim().length;
      cnt.textContent = n + ' 字';
      cnt.style.color = n >= minLength ? 'var(--good)' : 'var(--mut)';
      btn.disabled = n < minLength;
    };
    $('#askNo').onclick = () => { closeModal(); resolve(null); };
    btn.onclick = () => { const v = ta.value.trim(); closeModal(); resolve(v); };
  });
}

/* ------------------------------------------------------------------ 登录 */
function showLogin() {
  $('#login').style.display = 'flex';
  $('#pinInput').value = '';
  setTimeout(() => $('#pinInput').focus(), 100);
}
async function doLogin() {
  const pin = $('#pinInput').value.trim();
  if (!pin) return;
  try {
    await api('/api/login', { pin });
    $('#login').style.display = 'none';
    boot();
  } catch (e) {
    $('#pinErr').textContent = e.message;
  }
}
$('#pinBtn').onclick = doLogin;
$('#pinInput').onkeydown = e => { if (e.key === 'Enter') doLogin(); };

/* ------------------------------------------------------------------ 导航 */
const VIEWS = ['chat', 'dash', 'events', 'topics', 'decisions', 'people', 'guard'];
const LOADERS = {};
function go(v) {
  VIEWS.forEach(x => $('#v-' + x).classList.toggle('on', x === v));
  $$('#nav button').forEach(b => b.classList.toggle('on', b.dataset.v === v));
  window.scrollTo({ top: 0, behavior: 'instant' });
  if (LOADERS[v]) LOADERS[v]();
  if (v === 'guard') { if (typeof loadHealth === 'function') loadHealth(); if (typeof loadTheory === 'function') loadTheory(''); }
}
$('#nav').addEventListener('click', e => {
  const b = e.target.closest('button');
  if (b) go(b.dataset.v);
});
window.go = go;

/* ------------------------------------------------------------------ 对话 */
const CHAT = { conv: 0, skill: '', sending: false, skills: [], web: false, webLang: 'both', comfort: 'auto', tone: 'warm' };

function refChips(refs, bad) {
  const cls = { '事件': 'acc', '决策': 'acc', '记忆': 'warn', '画像': 'warn',
                '理论': 'good', '文献': 'acc' };
  const good = (refs || []).map(r =>
    `<span class="chip ${cls[r.type] || ''}">${esc(r.type)}#${r.id}</span>`).join(' ');
  const b = (bad || []).map(r =>
    `<span class="chip bad">${esc(r.type)}#${r.id} 不存在</span>`).join(' ');
  return (good || b) ? `<div class="row wrap tiny" style="gap:4px;margin-top:6px">${good}${b}</div>` : '';
}

function md(s) {
  // 只有「先转义、再套一层很窄的 Markdown」才安全：不做行内 HTML 透传。
  let h = esc(s == null ? '' : s);
  h = h.replace(/`([^`\n]+)`/g, '<code>$1</code>');
  h = h.replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>');
  h = h.replace(/^#{1,6}\s+(.+)$/gm, '<div style="font-weight:600;margin:8px 0 2px">$1</div>');
  h = h.replace(/^\s*[-*]\s+/gm, '· ');
  h = h.replace(/^\s*-{3,}\s*$/gm, '<div style="height:1px;background:var(--line);margin:10px 0"></div>');
  return h.replace(/\n/g, '<br>');
}

function msgHtml(m) {
  const mine = m.role === 'user';
  // 必须严格判断。原来写的是 if (!m.ok)，而发消息时临时构造的对象没有 ok 字段
  // （undefined 也是 falsy），结果每条自己发的消息都被渲染成「(发送失败：未知错误)」，
  // 明明发送是成功的。只有从历史加载的消息带 ok=1，所以刷新后又显示正常。
  if (m.ok === 0 || m.ok === false) {
    return `<div style="margin:10px 0;text-align:right">
      <div class="chat-bubble bad">(发送失败：${esc(m.err || '未知错误')})</div></div>`;
  }
  return `<div style="margin:10px 0;${mine ? 'text-align:right' : ''}">
    <div class="chat-bubble ${mine ? 'mine' : ''}">${md(m.content)}</div>
    ${refChips(m.refs, m.bad_refs)}
    <div class="tiny muted" style="margin-top:3px">${m.created_at ? m.created_at.slice(5, 16) : ''}</div>
  </div>`;
}

async function loadSkills() {
  try {
    const d = await api('/api/skills');
    CHAT.skills = d.skills || [];
    $('#skillBar').innerHTML = `
      <span class="chip ${!CHAT.skill ? 'acc' : ''}" data-skill="">不指定 skill</span>
      ${CHAT.skills.map(s => `<span class="chip ${CHAT.skill === s.name ? 'acc' : ''}"
        data-skill="${esc(s.name)}" title="${esc(s.description)}">${esc(s.name)}</span>`).join('')}`;
    $$('#skillBar .chip').forEach(el => el.onclick = async () => {
      CHAT.skill = el.dataset.skill;
      await loadSkills();
      if (CHAT.conv) {
        try { await api('/api/conversations/' + CHAT.conv + '/skill', { skill: CHAT.skill }); } catch (e) { }
      }
      toast(CHAT.skill ? `已启用 ${CHAT.skill}` : '已取消 skill');
    });
  } catch (e) { toast(e.message, 'err'); }
}

async function loadConvs() {
  try {
    const d = await api('/api/conversations');
    const list = d.conversations || [];
    $('#convList').innerHTML = list.length ? list.map(c => `
      <span class="chip ${CHAT.conv === c.id ? 'acc' : ''}" data-cid="${c.id}"
        style="flex:0 0 auto;max-width:190px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">
        ${esc(c.title)} <span class="tiny">${c.msg_count}</span></span>`).join('')
      : '<span class="tiny muted">还没有对话，点「+ 新对话」</span>';
    $$('#convList .chip').forEach(el => el.onclick = () => openConv(+el.dataset.cid));
    return list;
  } catch (e) { toast(e.message, 'err'); return []; }
}

async function openConv(id) {
  CHAT.conv = id;
  try {
    const d = await api('/api/conversations/' + id);
    CHAT.skill = d.conversation.skill || '';
    CHAT.web = !!d.conversation.web;
    CHAT.webLang = d.conversation.web_lang || 'both';
    CHAT.comfort = d.conversation.comfort || 'auto';
    paintComfortBtn();
    paintWebBtn();
    await loadSkills();
    await loadConvs();
    const html = (d.messages || []).map(msgHtml).join('');
    const sumNote = (d.summaries || []).length
      ? `<div class="tiny muted" style="text-align:center;margin:8px 0">
          · 已压缩 ${d.summaries.length} 段历史（原文仍在库里） ·</div>` : '';
    $('#chatMsgs').innerHTML = sumNote + (html || '<div class="empty">开始说点什么。<br>这里的对话会记住：你确认过的长期记忆、采纳过的画像、以及相关旧事件。</div>');
    const box = $('#chatMsgs');
    box.scrollIntoView({ block: 'end' });
    window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' });
  } catch (e) { toast(e.message, 'err'); }
}

LOADERS.chat = async () => {
  await loadSkills();
  const list = await loadConvs();
  if (!CHAT.conv && list.length) await openConv(list[0].id);
  else if (!CHAT.conv) $('#chatMsgs').innerHTML =
    '<div class="empty">还没有对话。<br>对话会自动带上你确认过的记忆与画像。</div>';
};

async function newConv() {
  try {
    const d = await api('/api/conversations', { skill: CHAT.skill });
    CHAT.conv = d.id;
    $('#chatMsgs').innerHTML = '<div class="empty">新对话开始。</div>';
    await loadConvs();
    $('#chatInput').focus();
  } catch (e) { toast(e.message, 'err'); }
}

async function sendMsg() {
  if (CHAT.sending) return;
  const ta = $('#chatInput');
  const text = ta.value.trim();
  if (!text) return;
  if (!CHAT.conv) { await newConv(); if (!CHAT.conv) return; }
  CHAT.sending = true;
  ta.value = '';
  const btn = $('#chatSend');
  btn.disabled = true;
  $('#chatMsgs').insertAdjacentHTML('beforeend', msgHtml({ role: 'user', content: text }));
  const pend = document.createElement('div');
  pend.id = 'chatPending';
  pend.style.cssText = 'margin:10px 0';
  pend.innerHTML = `<div class="chat-bubble"><span class="spinner"></span>
    正在思考… <span id="chatSec">0</span>s</div>`;
  $('#chatMsgs').appendChild(pend);
  window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' });
  let sec = 0;
  const timer = setInterval(() => {
    sec++;
    const el = $('#chatSec');
    if (el) el.textContent = sec;
  }, 1000);
  try {
    const r = await api(`/api/chat/${CHAT.conv}/send`, { text, skill: CHAT.skill });
    $('#chatPending')?.remove();
    $('#chatMsgs').insertAdjacentHTML('beforeend', msgHtml({
      role: 'assistant', content: r.reply, refs: r.refs, bad_refs: r.bad_refs,
      created_at: new Date().toISOString(),
    }));
    const c = r.context || {};
    $('#chatHint').textContent =
      `上下文 ${c.chars} 字 / ${c.turns} 轮 · 记忆${c.memory} 画像${c.traits} 事件${c.events} 理论${c.theory}`
      + (c.summaries ? ` 摘要${c.summaries}` : '')
      + (c.refs ? ` · 联网文献${c.refs}「${c.web_query}」` : '')
      + (c.web_skipped ? ` · ${c.web_skipped}` : '')
      + (c.web_error ? ` · 联网失败(${c.web_error})` : '')
      + (c.comfort_applied && c.comfort_applied !== 'none' ? ` · 体例:${c.comfort_applied}${c.comfort_level ? c.comfort_level : ''}` : '')
      + ((c.background || []).length ? ` · 背景参照${c.background.length}则(${c.background.join('/')})` : '');
    if ((r.bad_refs || []).length) {
      toast(`AI 引用了 ${r.bad_refs.length} 个不存在的编号，已标红——它在编证据`, 'err');
    }
    await loadConvs();
    window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' });
  } catch (e) {
    $('#chatPending')?.remove();
    toast(e.message, 'err');
    ta.value = text;   // 失败不丢输入
  }
  clearInterval(timer);
  CHAT.sending = false;
  btn.disabled = false;
}

$('#chatNew').onclick = newConv;
$('#chatSend').onclick = sendMsg;

function paintToneBtn() {
  const b = $('#chatTone');
  if (!b) return;
  const warm = (CHAT.tone || 'warm') !== 'strict';
  b.textContent = '语气：' + (warm ? '温和' : '直接');
  b.className = 'btn' + (warm ? ' primary' : '');
}

$('#chatTone').onclick = async () => {
  if (!CHAT.conv) { toast('先开一个对话'); return; }
  const next = (CHAT.tone || 'warm') === 'strict' ? 'warm' : 'strict';
  try {
    const r = await api(`/api/conversations/${CHAT.conv}/tone`, { mode: next });
    CHAT.tone = r.mode;
    paintToneBtn();
    toast(r.mode === 'warm'
      ? '温和：判断落在事上，不落在人身上；术语说人话'
      : '直接：判词式短句，不铺垫', 'ok');
  } catch (e) { toast(e.message, 'err'); }
};

function paintComfortBtn() {
  const b = $('#chatComfort');
  if (!b) return;
  const label = { auto: '自动', on: '先共情', off: '直接分析' }[CHAT.comfort || 'auto'] || '自动';
  b.textContent = '共情：' + label;
  b.className = 'btn' + (CHAT.comfort === 'off' ? '' : ' primary');
}

$('#chatExtract').onclick = async () => {
  if (!CHAT.conv) { toast('先开一个对话'); return; }
  const b = $('#chatExtract');
  b.disabled = true; b.textContent = '提炼中…';
  try {
    const r = await api('/api/analyze/from-chat', { conversation_id: CHAT.conv });
if (r.skipped) { toast(r.skipped, 'ok'); b.disabled = false; b.textContent = '整理对话'; return; }
    const nm = (r.memory || []).length, nt = (r.traits || []).length;
    if (!nm && !nt) {
      toast(`扫了 ${r.scanned_messages} 条消息，没有值得长期沉淀的内容` +
            (r.dropped ? `（${r.dropped} 条因证据不足被丢弃）` : ''), 'ok');
    } else {
      toast(`提炼出 记忆 ${nm} 条 / 画像 ${nt} 条，都是待确认状态，去「风控」页逐条确认`, 'ok');
    }
    await loadConvs();
  } catch (e) { toast(e.message, 'err'); }
  b.disabled = false; b.textContent = '整理对话';
};

$('#chatComfort').onclick = async () => {
  if (!CHAT.conv) { toast('先开一个对话'); return; }
  const order = ['auto', 'on', 'off'];
  const next = order[(order.indexOf(CHAT.comfort || 'auto') + 1) % order.length];
  try {
    const r = await api(`/api/conversations/${CHAT.conv}/comfort`, { mode: next });
    CHAT.comfort = r.mode;
    paintComfortBtn();
    toast({
      auto: '自动：按这句的情绪信号决定要不要先安慰',
      on: '先共情：信号不明显也先给一句确认',
      off: '直接分析：不铺垫情绪，只要结论',
    }[r.mode], 'ok');
  } catch (e) { toast(e.message, 'err'); }
};

function paintWebBtn() {
  const b = $('#chatWeb');
  if (!b) return;
  const label = { zh: '中文', en: '英文', both: '中英' }[CHAT.webLang || 'both'] || '中英';
  b.textContent = CHAT.web ? ('联网：' + label) : '联网：关';
  b.className = 'btn' + (CHAT.web ? ' primary' : '');
}

$('#chatWeb').onclick = async () => {
  if (!CHAT.conv) { toast('先开一个对话'); return; }
  // 关 → 中英 → 中文 → 英文 → 关
  const order = ['off', 'both', 'zh', 'en'];
  const cur = CHAT.web ? (CHAT.webLang || 'both') : 'off';
  const next = order[(order.indexOf(cur) + 1) % order.length];
  if (next !== 'off' && cur === 'off') {
    const tip = next === 'zh' ? '只查中文文献'
      : next === 'en' ? '只查英文文献（经典高被引为主）' : '中英都查，中文排前';
    if (!confirm(`开启联网：${tip}\n\n只会把理论名当检索词发给 OpenAlex，不发送你的记录内容。继续？`)) return;
  }
  try {
    const on = next !== 'off';
    const lang = on ? next : (CHAT.webLang || 'both');
    const r = await api(`/api/conversations/${CHAT.conv}/web`, { on, lang });
    CHAT.web = !!r.web;
    CHAT.webLang = r.lang || 'both';
    paintWebBtn();
    const nm = { zh: '中文', en: '英文', both: '中英' }[CHAT.webLang] || '中英';
    toast(r.web ? `联网已开：${nm}` : '联网已关', 'ok');
  } catch (e) { toast(e.message, 'err'); }
};
$('#chatInput').onkeydown = e => {
  if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); sendMsg(); }
};
$('#chatSumm').onclick = async () => {
  if (!CHAT.conv) return toast('先选一个对话');
  try {
    const r = await api(`/api/conversations/${CHAT.conv}/summarize`, { keep_recent: 14 });
    toast(`已压缩 ${r.messages_compressed} 条为摘要`, 'ok');
    await openConv(CHAT.conv);
  } catch (e) { toast(e.message, 'err'); }
};
$('#chatSearchBtn').onclick = () => {
  openModal(`<h2>搜旧话</h2>
    <div class="row" style="gap:8px"><input id="msQ" placeholder="关键词（3 字以上更准）">
      <button class="btn primary" id="msGo">搜</button></div>
    <div id="msOut" style="margin-top:12px"></div>`);
  $('#msGo').onclick = async () => {
    try {
      const d = await api('/api/messages/search?q=' + encodeURIComponent($('#msQ').value));
      $('#msOut').innerHTML = (d.messages || []).length ? d.messages.map(m => `
        <div class="small" style="margin-bottom:8px;border-bottom:1px solid var(--line);padding-bottom:6px">
          <div class="tiny muted">#${m.id} · 会话${m.conversation_id} · ${m.created_at.slice(5, 16)}</div>
          <div>${esc(m.content).replace(/\n/g, ' ')}</div>
        </div>`).join('') : '<div class="tiny muted">没找到。</div>';
    } catch (e) { toast(e.message, 'err'); }
  };
};

async function loadTheory(q) {
  try {
    const d = await api('/api/theory' + (q ? '?q=' + encodeURIComponent(q) : ''));
    const rows = d.theory || [];
    $('#theoryCount').textContent = rows.length ? `${rows.length} 条` : '';
    $('#theoryList').innerHTML = rows.length ? rows.map(t => `
      <details class="acc" style="margin-bottom:6px">
        <summary style="padding:9px 12px;font-size:13px">
          <span class="chip">${esc(t.field)}</span>
          <span style="flex:1">${esc(t.name)}</span>
          <span class="tiny muted">#${t.id}</span>
        </summary>
        <div class="body small" style="padding:0 12px 12px">
          <div style="margin-bottom:6px">${esc(t.summary)}</div>
          ${t.applies ? `<div class="tiny muted">适用：${esc(t.applies)}</div>` : ''}
          ${t.signal ? `<div class="tiny muted">你的数据里对应信号：${esc(t.signal)}</div>` : ''}
          <div class="tiny" style="color:var(--warn);margin-top:4px">误用风险：${esc(t.misuse)}</div>
          ${t.source ? `<div class="tiny muted" style="margin-top:4px">出处：${esc(t.source)}</div>` : ''}
          <div class="row" style="gap:6px;margin-top:8px">
            <button class="btn sm" onclick="verifyTheory(${t.id}, this)">查文献支撑</button>
          </div>
          <div id="refs-${t.id}"></div>
        </div>
      </details>`).join('')
      : '<div class="tiny muted">没有匹配条目。</div>';
  } catch (e) { toast(e.message, 'err'); }
}

window.verifyTheory = async (id, btn) => {
  if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> 检索中…'; }
  try {
    const d = await api(`/api/theory/${id}/verify`, {});
    const refs = d.references || [];
    const box = $('#refs-' + id);
    if (box) {
      box.innerHTML = refs.length ? `
        <div class="tiny muted" style="margin-top:8px">检索词「${esc(d.query)}」· 来源 ${esc(d.source)}</div>
        ${refs.map(r => `<div class="small" style="margin-top:6px;border-left:2px solid var(--acc-dim);padding-left:8px">
          <div>[文献#${r.id}] ${esc(r.title)}</div>
          <div class="tiny muted">${r.year || '—'} · ${esc(r.venue || '')} ·
            <b style="color:${(r.cited_by || 0) > 100 ? 'var(--good)' : 'var(--mut)'}">被引 ${r.cited_by}</b></div>
          ${r.doi ? `<div class="tiny muted">DOI ${esc(r.doi)}</div>` : ''}
        </div>`).join('')}`
        : `<div class="tiny" style="color:var(--warn);margin-top:6px">没检索到文献：${esc(d.source)}</div>`;
    }
  } catch (e) { toast(e.message, 'err'); }
  if (btn) { btn.disabled = false; btn.textContent = '查文献支撑'; }
};

/* ------------------------------------------------------------------ 看板 */
function chart(series) {
  const W = 640, H = 130, pad = 8;
  const maxC = Math.max(3, ...series.map(s => s.count));
  const bw = (W - pad * 2) / series.length;
  let bars = '', pts = [];
  series.forEach((s, i) => {
    const h = (s.count / maxC) * (H - 26);
    bars += `<rect x="${(pad + i * bw + bw * .18).toFixed(1)}" y="${(H - 18 - h).toFixed(1)}"
      width="${(bw * .64).toFixed(1)}" height="${h.toFixed(1)}" rx="2" fill="#2a7d74" opacity=".55"/>`;
    if (s.emotion != null) {
      pts.push([pad + i * bw + bw / 2, H - 18 - (s.emotion / 5) * (H - 30)]);
    }
  });
  const path = pts.length
    ? `<polyline points="${pts.map(p => p[0].toFixed(1) + ',' + p[1].toFixed(1)).join(' ')}"
        fill="none" stroke="#3fb6a8" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>` +
      pts.map(p => `<circle cx="${p[0].toFixed(1)}" cy="${p[1].toFixed(1)}" r="2.6" fill="#3fb6a8"/>`).join('')
    : '';
  const labels = series.map((s, i) => i % 3 === 0
    ? `<text x="${(pad + i * bw + bw / 2).toFixed(1)}" y="${H - 4}" font-size="9" fill="#8b97a8"
        text-anchor="middle">${s.date.slice(5)}</text>` : '').join('');
  return `<svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto;display:block">
    <line x1="0" y1="${H - 18}" x2="${W}" y2="${H - 18}" stroke="#242c39"/>
    ${bars}${path}${labels}</svg>`;
}

/* ------------------------------------------------------- 方向草稿 / 自动整理 */
async function askDirection(btn) {
  if (btn) { btn.disabled = true; btn.textContent = '生成中…'; }
  try {
    const d = await api('/api/autopilot/direction', {});
    toast(d.pick === '材料不足' ? '材料还不够，它给了一份「要补什么」的清单' : '方向草稿已更新', 'ok');
    LOADERS.dash();
  } catch (e) { toast(e.message, 'err'); }
  if (btn) { btn.disabled = false; btn.textContent = '现在生成'; }
}

async function renderDirection() {
  const box = document.getElementById('dashDirection');
  if (!box) return;
  let d = {};
  try { d = await api('/api/autopilot'); } catch (e) { box.innerHTML = ''; return; }
  const st = d.status || {};
  const dir = d.direction;
  const prog = `<div class="tiny muted" style="margin-bottom:10px">
    自动整理：已攒 <b style="color:${(st.pending_messages || 0) >= (st.threshold || 30) ? 'var(--warn)' : 'var(--fg)'}">${st.pending_messages || 0}</b>
    / ${st.threshold || 30} 条${st.remaining ? ` · 再聊 ${st.remaining} 条自动整理` : ' · 已达阈值'}
  </div>`;
  if (!dir) {
    box.innerHTML = prog + '<div class="empty">还没有方向草稿。<br>聊够一轮，或点右上「现在生成」。</div>';
    return;
  }
  const dirs = (dir.directions || []).map(x => `
    <div style="padding:8px 0;border-top:1px solid var(--line)">
      <div class="row"><b>${esc(x.name || '')}</b><div class="sp"></div>
        <span class="chip ${x.name === dir.pick ? 'good' : ''}">${x.name === dir.pick ? '推荐' : '备选'}</span>
        <span class="tiny muted">可持续 ${x.sustainability == null ? '—' : Math.round(x.sustainability * 100) + '%'}</span></div>
      <div class="tiny muted" style="margin-top:3px">${esc(x.why || '')}</div>
      ${x.risk ? `<div class="tiny" style="margin-top:3px;color:var(--mut)">最可能放弃在：${esc(x.risk)}</div>` : ''}
    </div>`).join('');
  box.innerHTML = prog + `
    <div class="small" style="margin-bottom:8px">${esc(dir.diagnosis || '')}</div>
    ${dirs}
    ${dir.why_pick ? `<div class="small" style="margin-top:10px"><b>为什么押它：</b>${esc(dir.why_pick)}</div>` : ''}
    ${dir.missing ? `<div class="tiny" style="margin-top:8px;color:var(--warn)">还缺：${esc(dir.missing)}</div>` : ''}
    <div class="tiny muted" style="margin-top:8px">
      置信度 ${dir.confidence == null ? '—' : Math.round(dir.confidence * 100) + '%'}
      ${dir._created_at ? ' · 生成于 ' + esc(String(dir._created_at).slice(5, 16)) : ''}
      · 这只是草稿，采纳与否你定</div>`;
}
LOADERS.dash = async () => {
  try {
    const d = await api('/api/dashboard');
    const t = d.totals;
    $('#dashKpis').innerHTML = `
      <div class="kpi"><div class="v">${t.week_events}</div><div class="l">本周记录</div></div>
      <div class="kpi"><div class="v">${fmt(t.week_spend)}<span class="u">元</span></div><div class="l">本周支出</div></div>
      <div class="kpi ${t.week_emotion == null ? '' : t.week_emotion >= 3.5 ? 'ok' : t.week_emotion < 2.5 ? 'alert' : 'warn'}">
        <div class="v">${t.week_emotion == null ? '—' : t.week_emotion.toFixed(1)}</div><div class="l">本周情绪均值</div></div>
      <div class="kpi ${t.high_alerts ? 'alert' : t.open_alerts ? 'warn' : 'ok'}">
        <div class="v">${t.open_alerts}</div><div class="l">待处置预警${t.high_alerts ? ` · ${t.high_alerts} 高危` : ''}</div></div>`;

    // 对话与沉淀：聊天不直接进结构化表，但它是主要输入源，必须让它可见
    const pend = (t.draft_memory || 0) + (t.draft_traits || 0) + (t.proposals || 0);
    renderDirection();   // 方向草稿 + 自动整理进度（独立请求，失败不影响上面）
    $('#dashChat').innerHTML = `
      <div class="row wrap small" style="gap:14px">
        <span>对话 <b style="color:var(--fg)">${t.conversations || 0}</b> 段</span>
        <span>消息 <b style="color:var(--fg)">${t.messages || 0}</b> 条</span>
        <span>AI 分析 <b style="color:var(--fg)">${t.reports || 0}</b> 次</span>
        <span>待确认 <b style="color:${pend ? 'var(--warn)' : 'var(--fg)'}">${pend}</b>
          <span class="muted tiny">（记忆 ${t.draft_memory || 0} / 画像 ${t.draft_traits || 0}）</span></span>
      </div>
      <div class="tiny muted" style="margin-top:8px">
        ${t.last_chat ? `最近一次对话：${esc(String(t.last_chat).slice(5, 16))}` : '还没有对话记录'}
        ${pend ? ' · 待确认的条目不会进入上下文，需要你在记忆/画像里逐条确认' : ''}
      </div>
      ${pend ? `<div class="row" style="margin-top:10px;gap:8px">
        <button class="btn sm primary" onclick="go('guard')">去确认 ${pend} 条</button>
      </div>` : ''}`;

    $('#dashChart').innerHTML = t.events ? chart(d.series)
      : '<div class="empty">还没有任何事件记录。<br>对话不会自动变成事件——可以在聊天页点「从对话提炼」先把事实提取出来。</div>';
    $('#emoHint').textContent = t.events ? '柱=记录数 线=情绪' : '';

    const share = d.mainline_share;
    $('#dashFocus').innerHTML = `
      <div class="row small" style="margin-bottom:8px">
        <span class="muted">主线投入占比</span>
        <div class="sp"></div>
        <b class="${share == null ? '' : share >= .5 ? 'good' : 'warn'}"
           style="color:${share == null ? 'var(--mut)' : share >= .5 ? 'var(--good)' : 'var(--warn)'}">
          ${share == null ? '—' : Math.round(share * 100) + '%'}</b>
      </div>
      <div class="bar ${share == null ? '' : share >= .5 ? 'good' : 'warn'}" style="margin-bottom:14px">
        <i style="width:${share == null ? 0 : Math.round(share * 100)}%"></i></div>
      <div class="row wrap small muted" style="margin-bottom:10px;gap:12px">
        <span>活跃方向 <b style="color:var(--fg)">${d.active_count}</b></span>
        <span>主线 <b style="color:var(--fg)">${d.mainline_count}</b></span>
        <span>浅尝积压 <b style="color:${d.shallow_count ? 'var(--warn)' : 'var(--fg)'}">${d.shallow_count}</b></span>
      </div>
      ${d.topics.length ? d.topics.map(x => `
        <div style="margin-bottom:10px">
          <div class="row small">
            <span>${x.mainline ? '<span class="chip acc">主线</span> ' : ''}${esc(x.name)}</span>
            <div class="sp"></div>
            ${x.decay > .7 ? `<span class="chip bad">退潮 ${Math.round(x.decay * 100)}%</span>` : ''}
            <span class="muted tiny">${x.depth_label} · ${fmt(x.hours)}h · 候选分 ${x.focus}</span>
          </div>
          <div class="bar" style="margin-top:4px">
            <i style="width:${Math.round(x.focus * 100)}%"></i></div>
        </div>`).join('')
        : '<div class="empty">还没有方向记录。<a href="#" onclick="go(\'topics\');return false">去建一个</a></div>'}`;

    $('#alertCount').textContent = t.open_alerts ? `${t.open_alerts} 条` : '';
    $('#dashAlerts').innerHTML = d.alerts.length
      ? d.alerts.map(a => alertHtml(a, true)).join('')
      : '<div class="empty">没有待处置预警。要么你真的很稳，要么你记录得太少。</div>';
    $('#navBadge').textContent = t.open_alerts || '';
    $('#aiTag').textContent = t.draft_traits ? `${t.draft_traits} 条画像待审` : 'AI 就绪';
  } catch (e) { toast(e.message, 'err'); }
};

function alertHtml(a, compact) {
  return `<div class="alert-item ${a.severity}">
    <div class="msg">${esc(a.message)}</div>
    ${a.detail ? `<div class="det">${esc(a.detail)}</div>` : ''}
    ${compact && a.ack_status === 'open' ? `<div class="row" style="margin-top:8px;gap:6px">
      <button class="btn sm" onclick="ackAlert('${esc(a.fingerprint)}','handled')">处置</button>
      <button class="btn sm ghost" onclick="ackAlert('${esc(a.fingerprint)}','ignored')">我知道，先不管</button>
    </div>` : ''}
  </div>`;
}
window.ackAlert = async (fp, status) => {
  const v = await askText({
    title: status === 'handled' ? '记录处置动作' : '记录「暂时不管」的理由',
    hint: status === 'handled' ? '你打算具体做什么？' : '为什么现在不处理它？这也会留档。',
    label: status === 'handled' ? '处置动作' : '不管的理由',
    placeholder: status === 'handled' ? '例：今晚给她发一句「那 500 什么时候方便还」' : '例：这个月要交付 X，先压到月底',
  });
  if (!v) return;
  try {
    await api('/api/alerts/ack', { fingerprint: fp, status, note: v });
    toast('已留档', 'ok');
    LOADERS.dash();
  } catch (e) { toast(e.message, 'err'); }
};

/* ------------------------------------------------------------------ 事件 */
/* ---------------------------------------------------------- 对话整理出来的候选 */
const PEND_BOX = { event: 'pendEvents', topic: 'pendTopics', decision: 'pendDecs', person: 'pendPeople' };
const PEND_NAME = { event: '事件', topic: '方向', decision: '决策', person: '人际' };

function propTitle(kind, p) {
  if (kind === 'event') return p.title || '(无标题)';
  if (kind === 'topic') return p.name || '(无名称)';
  if (kind === 'person') return p.name || '(无姓名)';
  if (kind === 'decision') return p.title || '(无标题)';
  return '(未知)';
}

function propDetail(kind, p) {
  const bits = [];
  if (kind === 'event') {
    if (p.on_date) bits.push(`日期 ${esc(p.on_date)}`);
    if (p.domain) bits.push(esc(p.domain));
    if (p.my_actions) bits.push(`我做了：${esc(p.my_actions)}`);
    if (p.other_response) bits.push(`对方：${esc(p.other_response)}`);
  } else if (kind === 'topic') {
    if (p.domain) bits.push(esc(p.domain));
    if (p.why) bits.push(esc(p.why));
  } else if (kind === 'person') {
    if (p.relation) bits.push(esc(p.relation));
    if (p.note) bits.push(esc(p.note));
  } else if (kind === 'decision') {
    if (p.context) bits.push(esc(p.context));
    if (p.chosen) bits.push(`选了：${esc(p.chosen)}`);
    if (p._complete === false) bits.push('<span style="color:var(--bad)">缺否决理由/二阶效应，采纳会被拒绝</span>');
  }
  return bits.join(' · ');
}

async function renderProposals(kind) {
  const box = document.getElementById(PEND_BOX[kind]);
  if (!box) return;
  let list = [];
  try {
    const d = await api('/api/proposals?kind=' + kind);
    list = d.proposals || [];
  } catch (e) { box.innerHTML = ''; return; }
  if (!list.length) { box.innerHTML = ''; return; }
  box.innerHTML = `
    <div class="card" style="border-color:var(--warn)">
      <h3>待确认 · 来自对话 <span class="sp"></span>
        <span class="tiny muted">${list.length} 条 ${PEND_NAME[kind]}候选，采纳后才进正式表</span></h3>
      ${list.map(x => `
        <div class="row" style="gap:10px;align-items:flex-start;padding:8px 0;border-top:1px solid var(--line)">
          <div style="flex:1;min-width:0">
            <div><b>${esc(propTitle(kind, x.payload))}</b></div>
            <div class="tiny muted" style="margin-top:3px">${propDetail(kind, x.payload)}</div>
            ${x.note ? `<div class="tiny" style="margin-top:3px;color:var(--mut)">${esc(x.note)}</div>` : ''}
          </div>
          <button class="btn sm primary" onclick="acceptProp(${x.id},'${kind}')">采纳</button>
          <button class="btn sm ghost" onclick="rejectProp(${x.id},'${kind}')">丢弃</button>
        </div>`).join('')}
    </div>`;
}

async function acceptProp(id, kind) {
  try {
    await api(`/api/proposals/${id}/accept`, {});
    toast('已采纳，写入' + PEND_NAME[kind], 'ok');
    renderProposals(kind);
    if (LOADERS[kind === 'event' ? 'events' : kind === 'topic' ? 'topics'
        : kind === 'decision' ? 'decisions' : 'people']) {
      LOADERS[kind === 'event' ? 'events' : kind === 'topic' ? 'topics'
        : kind === 'decision' ? 'decisions' : 'people']();
    }
  } catch (e) { toast(e.message, 'err'); }
}

async function rejectProp(id, kind) {
  const why = await askText({
    title: '丢弃这条候选', label: '为什么丢弃（至少 8 字）', minLength: 8,
    placeholder: '例如：这不是事实，是对话里的假设', danger: true, confirm: '丢弃',
  });
  if (!why) return;
  try {
    await api(`/api/proposals/${id}/reject`, { reason: why });
    toast('已丢弃');
    renderProposals(kind);
  } catch (e) { toast(e.message, 'err'); }
}
LOADERS.events = async () => {
  renderProposals('event');
  const dom = $('#evFilter').value;
  try {
    const d = await api('/api/events' + (dom ? '?domain=' + encodeURIComponent(dom) : ''));
    $('#evList').innerHTML = d.events.length ? d.events.map(e => `
      <details class="acc">
        <summary>
          <span class="chip">${esc(e.domain)}</span>
          <span style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(e.title)}</span>
          <span class="tiny muted">${e.on_date.slice(5)}</span>
        </summary>
        <div class="body small">
          ${e.my_words ? `<div style="margin-bottom:6px"><span class="muted">我说：</span>${esc(e.my_words)}</div>` : ''}
          ${e.my_actions ? `<div style="margin-bottom:6px"><span class="muted">我做：</span>${esc(e.my_actions)}</div>` : ''}
          ${e.other_response ? `<div style="margin-bottom:6px"><span class="muted">反应：</span>${esc(e.other_response)}</div>` : ''}
          ${e.replay ? `<div style="margin-bottom:6px;color:var(--acc)">重来我会：${esc(e.replay)}</div>` : ''}
          <div class="row wrap tiny muted" style="gap:10px;margin-top:8px">
            <span>情绪 ${e.emotion}/5</span><span>结果 ${esc(e.outcome)}</span>
            <span>主动性 ${e.initiative}/4</span>
            ${e.hours ? `<span>${fmt(e.hours)}h</span>` : ''}
            ${e.money ? `<span>${fmt(e.money)}元</span>` : ''}
            ${e.tags ? `<span>#${esc(e.tags)}</span>` : ''}
          </div>
          <div class="row" style="margin-top:10px"><div class="sp"></div>
            <button class="btn sm danger" onclick="delEvent(${e.id})">删除</button></div>
        </div>
      </details>`).join('')
      : '<div class="empty">还没有记录。<br>记录的标准只有一个：<b>具体发生了什么，你说了什么、做了什么</b>。</div>';
  } catch (e) { toast(e.message, 'err'); }
};
$('#evFilter').onchange = LOADERS.events;

window.delEvent = async id => {
  if (!confirm('删除这条事件？画像里引用它的证据会失效。')) return;
  try { await api('/api/events/' + id, null, 'DELETE'); toast('已删除'); LOADERS.events(); }
  catch (e) { toast(e.message, 'err'); }
};

$('#evAdd').onclick = () => {
  openModal(`
    <h2>记一条事实</h2>
    <div class="small muted" style="margin-bottom:12px">只写发生的，不写「我觉得」。判断留给后面的分析。</div>
    <div class="grid2">
      <label class="fld"><span>日期</span><input type="date" id="eDate" value="${todayStr()}"></label>
      <label class="fld"><span>领域</span><select id="eDom"><option>人际</option><option>职业</option>
        <option>金钱</option><option>精力</option><option>学习</option><option>其他</option></select></label>
    </div>
    <label class="fld"><span>发生了什么（一句话）</span><input id="eTitle" placeholder="例：老同学第三次找我改 PPT"></label>
    <label class="fld"><span>我当时说了什么（尽量原话）</span><textarea id="eWords" placeholder="例：行吧，我帮你弄"></textarea></label>
    <label class="fld"><span>我当时做了什么</span><textarea id="eActs" placeholder="例：改了两版，对方说再改改"></textarea></label>
    <label class="fld"><span>对方/环境如何反应</span><textarea id="eResp" placeholder="例：谢谢啊，还有个小地方"></textarea></label>
    <div class="grid2">
      <label class="fld"><span>我的情绪 (1差–5好)</span><input type="number" id="eEmo" value="3" min="1" max="5"></label>
      <label class="fld"><span>结果</span><select id="eOut"><option value="neutral">中性</option>
        <option value="good">好</option><option value="bad">差</option></select></label>
    </div>
    <label class="fld"><span>主动性 (0回避 1被动 2被推着 3主动 4强推)</span>
      <input type="number" id="eIni" value="2" min="0" max="4"></label>
    <div class="grid2">
      <label class="fld"><span>耗时(小时)</span><input type="number" id="eHours" value="0" step="0.1" min="0"></label>
      <label class="fld"><span>花费(元)</span><input type="number" id="eMoney" value="0" step="1" min="0"></label>
    </div>
    <label class="fld"><span>如果重来一次，我会怎么做</span><textarea id="eReplay" placeholder="例：应该说「这次不行，我在赶自己的事」"></textarea></label>
    <label class="fld"><span>标签</span><input id="eTags" placeholder="例：边界,消费"></label>
    <div class="row" style="margin-top:8px"><div class="sp"></div>
      <button class="btn" onclick="closeModal()">取消</button>
      <button class="btn primary" id="eSave">保存</button></div>`);
  $('#eSave').onclick = async () => {
    const body = {
      on_date: $('#eDate').value, domain: $('#eDom').value, title: $('#eTitle').value,
      my_words: $('#eWords').value, my_actions: $('#eActs').value, other_response: $('#eResp').value,
      emotion: +$('#eEmo').value, outcome: $('#eOut').value, initiative: +$('#eIni').value,
      hours: +$('#eHours').value, money: +$('#eMoney').value,
      replay: $('#eReplay').value, tags: $('#eTags').value,
    };
    try { await api('/api/events', body); closeModal(); toast('已记录', 'ok'); LOADERS.events(); }
    catch (e) { toast(e.message, 'err'); }
  };
};

/* ------------------------------------------------------------------ 方向 */
const DEPTH = ['刚接触', '能聊两句', '能讲清楚', '能产出东西', '专业水平', '能靠它吃饭'];

LOADERS.topics = async () => {
  renderProposals('topic');
  try {
    const d = await api('/api/topics');
    const share = d.mainline_share;
    $('#topicStats').innerHTML = `
      <div class="grid3" style="margin-bottom:12px">
        <div><div class="tiny muted">活跃方向</div><div style="font-size:19px;font-weight:700">${d.active_count}</div></div>
        <div><div class="tiny muted">主线</div><div style="font-size:19px;font-weight:700;color:var(--acc)">${d.mainline_count}</div></div>
        <div><div class="tiny muted">浅尝积压</div><div style="font-size:19px;font-weight:700;color:${d.shallow_count ? 'var(--warn)' : 'var(--fg)'}">${d.shallow_count}</div></div>
      </div>
      <div class="row small"><span class="muted">本月主线投入占比</span><div class="sp"></div>
        <b style="color:${share == null ? 'var(--mut)' : share >= .5 ? 'var(--good)' : 'var(--warn)'}">
        ${share == null ? '—' : Math.round(share * 100) + '%'}</b></div>
      <div class="bar ${share == null ? '' : share >= .5 ? 'good' : 'warn'}" style="margin-top:5px">
        <i style="width:${share == null ? 0 : Math.round(share * 100)}%"></i></div>`;

    $('#topicsList').innerHTML = d.topics.length ? d.topics.map(t => `
      <div class="card">
        <div class="row" style="margin-bottom:6px">
          ${t.mainline ? '<span class="chip acc">主线</span>' : ''}
          <b style="font-size:15px">${esc(t.name)}</b>
          <div class="sp"></div>
          <span class="chip ${t.status === 'active' ? '' : 'bad'}">${t.status === 'active' ? '进行中' : t.status === 'parked' ? '封存' : '归档'}</span>
        </div>
        <div class="row wrap tiny muted" style="gap:10px;margin-bottom:8px">
          <span>${esc(t.domain || '—')}</span>
          <span>深度 <b style="color:var(--acc)">${t.depth_level}/5</b> ${esc(t.depth_label)}</span>
          <span>${fmt(t.hours)}h</span>
          <span>作品 ${t.artifacts}</span>
          <span>兴趣 ${t.interest}/5</span>
          <span>月均 ${t.monthly_avg}h · 近30天 ${t.hours_30d}h</span>
          ${t.idle_days != null ? `<span>${t.idle_days} 天未动</span>` : ''}
        </div>
        <div class="row tiny muted" style="gap:8px;margin-bottom:3px">
          <span style="width:62px;white-space:nowrap">候选分</span>
          <div class="bar " style="flex:1"><i style="width:${Math.round(t.focus * 100)}%"></i></div>
          <span class="mono" style="width:34px;text-align:right">${t.focus}</span>
        </div>
        <div class="row tiny muted" style="gap:8px;margin-bottom:3px">
          <span style="width:62px;white-space:nowrap">深度指数</span>
          <div class="bar" style="flex:1"><i style="width:${Math.round(t.depth_index * 100)}%"></i></div>
          <span class="mono" style="width:34px;text-align:right">${t.depth_index}</span>
        </div>
        <div class="row tiny muted" style="gap:8px;margin-bottom:8px">
          <span style="width:62px;white-space:nowrap">投入退潮</span>
          <div class="bar ${t.decay > .7 ? 'bad' : t.decay > .4 ? 'warn' : 'good'}" style="flex:1">
            <i style="width:${Math.round(t.decay * 100)}%"></i></div>
          <span class="mono" style="width:34px;text-align:right">${Math.round(t.decay * 100)}%</span>
        </div>
        ${t.verdict && t.verdict.length ? `<div class="small" style="color:var(--warn);margin-bottom:8px">
          ${t.verdict.map(v => '· ' + esc(v)).join('<br>')}</div>` : ''}
        ${t.archive_reason ? `<div class="tiny muted" style="margin-bottom:8px">封存理由：${esc(t.archive_reason)}</div>` : ''}
        <div class="row wrap" style="gap:6px">
          <button class="btn sm" onclick="logSession(${t.id},'${esc(t.name).replace(/'/g, '')}')">记投入</button>
          <button class="btn sm" onclick="levelUp(${t.id},${t.depth_level})">升一级</button>
          <button class="btn sm ${t.mainline ? 'ghost' : ''}" onclick="toggleMain(${t.id},${t.mainline ? 0 : 1})">
            ${t.mainline ? '取消主线' : '设为主线'}</button>
          ${t.status === 'active' ? `<button class="btn sm ghost danger" onclick="archiveTopic(${t.id})">封存</button>` : ''}
        </div>
      </div>`).join('')
      : '<div class="empty">还没有方向。先把你最近在学的 3–5 个东西写进来，<br>系统才能算出你到底在哪个上面真的在往下走。</div>';
  } catch (e) { toast(e.message, 'err'); }
};

window.logSession = (id, name) => {
  openModal(`
    <h2>记投入 · ${esc(name)}</h2>
    <label class="fld"><span>日期</span><input type="date" id="sDate" value="${todayStr()}"></label>
    <label class="fld"><span>投入小时</span><input type="number" id="sHours" step="0.1" min="0.1" value="1"></label>
    <label class="fld"><span>这次做了什么</span><textarea id="sNote" placeholder="例：把 region split 的触发流程画了一遍"></textarea></label>
    <label class="fld"><span>深度有没有实质提升？</span>
      <select id="sDelta"><option value="0">没有，只是练习</option><option value="1">有，升了一级</option></select></label>
    <div class="row"><div class="sp"></div>
      <button class="btn" onclick="closeModal()">取消</button>
      <button class="btn primary" id="sSave">记录</button></div>`);
  $('#sSave').onclick = async () => {
    try {
      await api(`/api/topics/${id}/session`, {
        on_date: $('#sDate').value, hours: +$('#sHours').value,
        note: $('#sNote').value, depth_delta: +$('#sDelta').value,
      });
      closeModal(); toast('已记投入', 'ok'); LOADERS.topics();
    } catch (e) { toast(e.message, 'err'); }
  };
};

window.levelUp = async (id, cur) => {
  if (cur >= 5) return toast('已经是最高级了');
  try { await api('/api/topics', { id, depth_level: cur + 1, last_touch_on: todayStr() }); toast('深度 +1', 'ok'); }
  catch (e) { toast(e.message, 'err'); }
  LOADERS.topics();
};

window.toggleMain = async (id, main) => {
  try { await api('/api/topics', { id, mainline: !!main }); toast(main ? '已设为主线' : '已取消主线'); }
  catch (e) { toast(e.message, 'err'); }
  LOADERS.topics();
};

window.archiveTopic = async id => {
  const v = await askText({
    title: '封存这个方向',
    hint: '封存不是删除。你以后要能回答「当初为什么放弃它」，所以这里必须写清楚。',
    label: '封存理由', placeholder: '例：做了 3 周发现只是想要「会一点」的感觉，没有产出欲望',
    confirm: '封存', danger: true,
  });
  if (!v) return;
  try { await api('/api/topics', { id, status: 'parked', archive_reason: v }); toast('已封存并留档', 'ok'); }
  catch (e) { toast(e.message, 'err'); }
  LOADERS.topics();
};

$('#topicAdd').onclick = () => {
  openModal(`
    <h2>新主题</h2>
    <label class="fld"><span>名称</span><input id="tName" placeholder="例：分布式存储 / HBase"></label>
    <div class="grid2">
      <label class="fld"><span>领域</span><input id="tDomain" placeholder="例：后端"></label>
      <label class="fld"><span>开始日期</span><input type="date" id="tStart" value="${todayStr()}"></label>
    </div>
    <div class="grid2">
      <label class="fld"><span>当前深度 0–5</span>
        <select id="tDepth">${DEPTH.map((n, i) => `<option value="${i}">${i} ${n}</option>`).join('')}</select></label>
      <label class="fld"><span>兴趣 1–5</span><input type="number" id="tInterest" value="3" min="1" max="5"></label>
    </div>
    <div class="grid2">
      <label class="fld"><span>累计投入(h)</span><input type="number" id="tHours" value="0" step="0.1"></label>
      <label class="fld"><span>作品数</span><input type="number" id="tArt" value="0"></label>
    </div>
    <label class="fld"><span>为什么开始它</span><textarea id="tWhy" placeholder="例：觉得写底层代码很酷"></textarea></label>
    <div class="row"><div class="sp"></div>
      <button class="btn" onclick="closeModal()">取消</button>
      <button class="btn primary" id="tSave">创建</button></div>`);
  $('#tSave').onclick = async () => {
    try {
      await api('/api/topics', {
        name: $('#tName').value, domain: $('#tDomain').value, started_on: $('#tStart').value,
        depth_level: +$('#tDepth').value, interest: +$('#tInterest').value,
        hours: +$('#tHours').value, artifacts: +$('#tArt').value, why: $('#tWhy').value,
        last_touch_on: $('#tStart').value,
      });
      closeModal(); toast('已创建', 'ok'); LOADERS.topics();
    } catch (e) { toast(e.message, 'err'); }
  };
};

$('#dirAI').onclick = async e => {
  const btn = e.target; btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> 正在读你的方向数据…';
  try {
    const d = await api('/api/analyze/direction', {});
    openModal(`<h2>方向诊断</h2><div class="ai-out">
      <h4>总体判断</h4><div class="quote">${esc(d.diagnosis || '')}</div>
      <h4>该押注的方向</h4>
      <div style="font-size:17px;font-weight:700;color:var(--acc);margin-bottom:4px">${esc(d.pick || '—')}</div>
      <div class="small">${esc(d.pick_reason || '')}</div>
      ${(d.candidates || []).map(c => `
        <h4>${esc(c.topic)} · 可持续性 ${c.sustainability != null ? Math.round(c.sustainability * 100) + '%' : '—'}</h4>
        <div class="row wrap" style="gap:6px;margin-bottom:6px">
          <span class="chip ${c.go_deep === '值得深挖' ? 'acc' : c.go_deep === '不值得' ? 'bad' : 'warn'}">${esc(c.go_deep)}</span>
        </div>
        <div class="small"><span class="muted">你会停手的真实原因：</span>${esc(c.why_you_stop || '')}</div>
        <div class="small" style="margin-top:4px"><span class="muted">什么信号说明你是真喜欢：</span>${esc(c.proof_needed || '')}</div>
        <div class="small" style="margin-top:6px"><span class="muted">90 天动作：</span></div>
        <ul class="small">${(c.next_90_days || []).map(x => `<li>${esc(x)}</li>`).join('')}</ul>
      `).join('')}
      ${(d.park || []).length ? `<h4>建议封存</h4><div class="small">${d.park.map(esc).join('、')}</div>` : ''}
      <h4>你需要的硬规则</h4><div class="quote">${esc(d.one_rule || '')}</div>
      <div class="row" style="margin-top:16px"><div class="sp"></div>
        <button class="btn" onclick="closeModal()">关闭</button></div>
    </div>`);
  } catch (e2) { toast(e2.message, 'err'); }
  btn.disabled = false; btn.textContent = 'AI 诊断：我该深挖哪个';
};

/* ------------------------------------------------------------------ 决策 */
const decState = { criteria: [], options: [], chosen: '', rejected: {}, chain: ['', '', ''] };

function resetDecState(d) {
  decState.criteria = (d && d.criteria && d.criteria.length) ? d.criteria.map(c => ({ ...c }))
    : [{ name: '长期回报', weight: 5 }, { name: '兴趣强度', weight: 4 }];
  const names = decState.criteria.map(c => c.name);
  decState.options = (d && d.options && d.options.length)
    ? d.options.map(o => ({ name: o.name, note: o.note || '', scores: names.map(n => (o.scores || {})[n] || 0) }))
    : [{ name: '', note: '', scores: names.map(() => 5) }, { name: '', note: '', scores: names.map(() => 5) }];
  decState.chosen = (d && d.chosen) || '';
  decState.rejected = {};
  ((d && d.rejected) || []).forEach(r => { decState.rejected[r.option] = r.reason; });
  const so = ((d && d.second_order) || []).find(s => s.option === decState.chosen);
  decState.chain = (so && so.chain) ? so.chain.slice(0, 3).concat(['', '', '']).slice(0, 3) : ['', '', ''];
}

function weightedOf(o) {
  const ws = decState.criteria.map(c => Math.max(0, +c.weight || 0));
  const sum = ws.reduce((a, b) => a + b, 0) || 1;
  return o.scores.reduce((acc, s, i) => acc + (+s || 0) * ws[i], 0) / sum;
}

function renderDec(state, d) {
  const names = state.criteria.map(c => c.name || '未命名准则');
  const best = state.options.reduce((m, o) => Math.max(m, weightedOf(o)), 0);

  $('#modalBox').innerHTML = `
    <h2>${d && d.id ? '编辑' : '新建'}决策</h2>
    <div class="grid2">
      <label class="fld"><span>标题</span><input id="dcTitle" value="${esc((d && d.title) || '')}" placeholder="例：要不要接这个外包"></label>
      <label class="fld"><span>类型</span><select id="dcKind">
        ${['进入', '止损', '人际降级', '支出', '方向选择', '其他'].map(k => `<option ${d && d.kind === k ? 'selected' : ''}>${k}</option>`).join('')}
      </select></label>
    </div>
    <label class="fld"><span>背景与约束（钱、时间、可逆性）</span><textarea id="dcCtx" placeholder="把约束写清，否则后面所有推算都是在空气里做">${esc((d && d.context) || '')}</textarea></label>

    <div class="card" style="background:var(--panel2)">
      <h3>准则与权重</h3>
      <div id="critBox"></div>
      <button class="btn sm" id="addCrit">+ 加准则</button>
    </div>

    <div class="card" style="background:var(--panel2)">
      <h3>备选方案 × 打分 (0–10)</h3>
      <div id="optBox"></div>
      <button class="btn sm" id="addOpt">+ 加方案</button>
    </div>

    <div class="card" style="background:var(--panel2)">
      <h3>你选哪个</h3>
      <select id="dcChosen">
        <option value="">（还没定，先存草稿）</option>
        ${state.options.filter(o => o.name).map(o => `<option ${state.chosen === o.name ? 'selected' : ''}>${esc(o.name)}</option>`).join('')}
      </select>
      <div class="tiny muted" style="margin-top:6px">选了它之后，其他每个分支都必须写否决理由才能保存。</div>
    </div>

    <div class="card" style="background:var(--panel2)">
      <h3>二阶效应（选中方案，必须推三层）</h3>
      <label class="fld"><span>① 直接结果</span><input id="dcL1" value="${esc(state.chain[0])}" placeholder="例：半年内能写出可复用的组件"></label>
      <label class="fld"><span>② 然后呢</span><input id="dcL2" value="${esc(state.chain[1])}" placeholder="例：团队里成为这块的默认人选"></label>
      <label class="fld"><span>③ 再然后呢</span><input id="dcL3" value="${esc(state.chain[2])}" placeholder="例：议价权来自稀缺性，标签从「都会一点」变成「这块最懂」"></label>
    </div>

    <label class="fld"><span>止损条件（可验证）</span><textarea id="dcStop" placeholder="例：3 个月内没有可展示产出，视为押错，立即重开决策">${esc((d && d.stop_loss) || '')}</textarea></label>

    <div class="grid2">
      <label class="fld"><span>可逆性</span><select id="dcRev">
        ${['unknown', '可逆', '半可逆', '不可逆'].map(r => `<option value="${r}" ${d && d.reversibility === r ? 'selected' : ''}>${r === 'unknown' ? '未评估' : r}</option>`).join('')}
      </select></label>
      <label class="fld"><span>复盘日期</span><input type="date" id="dcReview" value="${(d && d.review_due) || ''}"></label>
    </div>

    <div class="row" style="margin-top:6px"><div class="sp"></div>
      <button class="btn" onclick="closeModal()">取消</button>
      <button class="btn primary" id="dcSave">保存</button></div>`;

  const syncFromDom = () => {
    decState.chain = [$('#dcL1').value, $('#dcL2').value, $('#dcL3').value];
  };

  const paintCrit = () => {
    $('#critBox').innerHTML = state.criteria.map((c, i) => `
      <div class="row" style="margin-bottom:6px;gap:6px">
        <input value="${esc(c.name)}" placeholder="准则名" data-ci="${i}" class="critName" style="flex:2">
        <input type="number" value="${+c.weight || 0}" min="0" step="1" data-ci="${i}" class="critW" style="flex:1" title="权重">
        <button class="btn sm danger" data-delc="${i}">×</button>
      </div>`).join('');
    $$('.critName').forEach(el => el.oninput = () => { state.criteria[+el.dataset.ci].name = el.value; });
    $$('.critW').forEach(el => el.oninput = () => { state.criteria[+el.dataset.ci].weight = +el.value; paintOpt(); });
    $$('[data-delc]').forEach(el => el.onclick = () => {
      const i = +el.dataset.delc;
      state.criteria.splice(i, 1);
      state.options.forEach(o => o.scores.splice(i, 1));
      paintCrit(); paintOpt();
    });
  };

  const paintOpt = () => {
    $('#optBox').innerHTML = state.options.map((o, oi) => `
      <div class="card" style="background:var(--bg);margin-bottom:8px">
        <div class="row" style="gap:6px;margin-bottom:6px">
          <input value="${esc(o.name)}" placeholder="方案 ${oi + 1}" data-oi="${oi}" class="optName" style="flex:1">
          <span class="chip" id="chip-${oi}">${weightedOf(o).toFixed(2)}</span>
          <button class="btn sm danger" data-delo="${oi}">×</button>
        </div>
        <div class="grid2">
          ${names.map((n, ci) => `
            <label class="fld" style="margin-bottom:4px"><span>${esc(n)}</span>
              <input type="number" value="${+o.scores[ci] || 0}" min="0" max="10" data-oi="${oi}" data-ci="${ci}" class="optScore">
            </label>`).join('')}
        </div>
        <input value="${esc(o.note || '')}" placeholder="备注（依据是什么）" data-oi="${oi}" class="optNote" style="margin-top:2px">
        ${state.chosen && state.chosen !== o.name && o.name ? `
          <label class="fld" style="margin-top:8px"><span style="color:var(--bad)">否决这个分支的理由（必填）</span>
            <textarea data-rej="${esc(o.name)}" style="min-height:52px"
              placeholder="为什么不要它？写清依据">${esc(state.rejected[o.name] || '')}</textarea></label>` : ''}
      </div>`).join('');
    refreshChips();
    $$('.optName').forEach(el => {
      el.oninput = () => { state.options[+el.dataset.oi].name = el.value; };
      el.onblur = () => paintOpt();          // 失焦后再重建下拉，避免打字被吞
    });
    $$('.optScore').forEach(el => el.oninput = () => {
      state.options[+el.dataset.oi].scores[+el.dataset.ci] = +el.value;
      refreshChips();                        // 只刷新分数，不重建 DOM，保住输入焦点
    });
    $$('.optNote').forEach(el => el.oninput = () => { state.options[+el.dataset.oi].note = el.value; });
    $$('[data-rej]').forEach(el => el.oninput = () => { state.rejected[el.dataset.rej] = el.value; });
    $$('[data-delo]').forEach(el => el.onclick = () => {
      const i = +el.dataset.delo;
      if (state.options.length <= 2) return toast('至少保留两个方案', 'err');
      state.options.splice(i, 1); paintOpt();
    });
    const sel = $('#dcChosen');
    sel.innerHTML = `<option value="">（还没定，先存草稿）</option>` +
      state.options.filter(o => o.name).map(o =>
        `<option ${state.chosen === o.name ? 'selected' : ''}>${esc(o.name)}</option>`).join('');
  };

  const refreshChips = () => {
    const top = Math.max(...state.options.map(o => weightedOf(o)));
    state.options.forEach((o, i) => {
      const c = $('#chip-' + i);
      if (!c) return;
      c.textContent = weightedOf(o).toFixed(2);
      c.className = 'chip' + (o.name && weightedOf(o) >= top && top > 0 ? ' acc' : '');
    });
  };

  paintCrit(); paintOpt();

  $('#addCrit').onclick = () => {
    state.criteria.push({ name: '', weight: 1 });
    state.options.forEach(o => o.scores.push(5));
    paintCrit(); paintOpt();
  };
  $('#addOpt').onclick = () => {
    state.options.push({ name: '', note: '', scores: state.criteria.map(() => 5) });
    paintOpt();
  };
  $('#dcChosen').onchange = e => {
    syncFromDom();
    state.chosen = e.target.value;
    paintOpt();
  };
  $('#dcSave').onclick = async () => {
    syncFromDom();
    const chosen = $('#dcChosen').value;
    const critNames = state.criteria.map(c => c.name).filter(Boolean);
    const body = {
      id: (d && d.id) || undefined,
      title: $('#dcTitle').value, kind: $('#dcKind').value, context: $('#dcCtx').value,
      criteria: state.criteria.filter(c => c.name).map(c => ({ name: c.name, weight: +c.weight || 0 })),
      options: state.options.filter(o => o.name).map(o => ({
        name: o.name, note: o.note,
        scores: Object.fromEntries(critNames.map((n, i) => [n, +o.scores[state.criteria.findIndex(c => c.name === n)] || 0])),
      })),
      chosen, stop_loss: $('#dcStop').value, reversibility: $('#dcRev').value,
      review_due: $('#dcReview').value,
      rejected: Object.entries(state.rejected).filter(([k]) => k && k !== chosen)
        .map(([option, reason]) => ({ option, reason })),
      second_order: chosen && state.chain.some(x => x)
        ? [{ option: chosen, chain: state.chain }] : [],
      status: chosen ? 'decided' : 'open',
    };
    try {
      await api('/api/decisions', body);
      closeModal(); toast('已保存', 'ok'); LOADERS.decisions();
    } catch (e) { toast(e.message, 'err'); }
  };
}

LOADERS.decisions = async () => {
  renderProposals('decision');
  try {
    const d = await api('/api/decisions');
    $('#decList').innerHTML = d.decisions.length ? d.decisions.map(x => {
      const m = x.mcda;
      return `<details class="acc">
        <summary>
          <span class="chip ${x.status === 'closed' ? 'good' : x.status === 'decided' ? 'acc' : 'warn'}">
            ${x.status === 'closed' ? '已复盘' : x.status === 'decided' ? '已决定' : '悬置中'}</span>
          <span style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(x.title)}</span>
          <span class="tiny muted">${x.on_date.slice(5)}</span>
        </summary>
        <div class="body">
          ${x.context ? `<div class="small" style="margin-bottom:10px">${esc(x.context)}</div>` : ''}
          ${m && m.ok ? `
            <div class="scroll-x"><table>
              <tr><th>方案</th><th class="num">加权</th><th class="num">最差</th><th class="num">后悔</th></tr>
              ${m.ranking.map(r => `<tr class="${r.option === x.chosen ? 'win' : ''}">
                <td>${esc(r.option)}${r.option === x.chosen ? ' ✓' : ''}</td>
                <td class="num">${r.weighted}</td><td class="num">${r.maximin}</td><td class="num">${r.regret}</td></tr>`).join('')}
            </table></div>
            <div class="row wrap" style="gap:6px;margin:8px 0">
              <span class="chip ${m.robust === '稳健' ? 'good' : m.robust === '脆弱' ? 'bad' : 'warn'}">${m.robust}</span>
              <span class="chip">加权 ${esc(m.weighted_winner)}</span>
              <span class="chip">悲观 ${esc(m.maximin_winner)}</span>
              <span class="chip">后悔最小 ${esc(m.minimax_regret_winner)}</span>
            </div>
            <div class="small" style="color:${m.robust === '稳健' ? 'var(--good)' : 'var(--warn)'}">${esc(m.verdict)}</div>
            ${m.fragile && m.fragile.length ? `<div class="tiny muted" style="margin-top:4px">
              敏感点：${m.fragile.map(f => esc(f.criterion) + (f.factor > 1 ? '↑' : '↓') + ' → ' + esc(f.winner)).join('；')}</div>` : ''}
          ` : '<div class="tiny muted">数据不足以做加权分析</div>'}

          ${(x.second_order || []).filter(s => s.option === x.chosen).map(s => `
            <h4 style="color:var(--acc);font-size:12px;margin:12px 0 4px">二阶效应</h4>
            <ol class="small" style="padding-left:18px;margin:0">
              ${(s.chain || []).map(c => `<li>${esc(c)}</li>`).join('')}</ol>`).join('')}

          ${(x.rejected || []).length ? `<h4 style="color:var(--bad);font-size:12px;margin:12px 0 4px">被否决的分支</h4>
            ${x.rejected.map(r => `<div class="small" style="margin-bottom:6px">
              <span class="chip bad">${esc(r.option)}</span>
              <span class="muted">${esc(r.reason)}</span></div>`).join('')}` : ''}

          ${x.stop_loss ? `<div class="small" style="margin-top:10px;color:var(--warn)">止损线：${esc(x.stop_loss)}</div>` : ''}
          ${x.review_due ? `<div class="tiny muted" style="margin-top:4px">复盘日：${esc(x.review_due)}</div>` : ''}
          ${x.review_lesson ? `<div class="small" style="margin-top:8px;color:var(--good)">
            复盘（${x.review_score > 0 ? '判断正确 +' : '判断偏差 '}${x.review_score}）：${esc(x.review_lesson)}</div>` : ''}

          <div class="row wrap" style="gap:6px;margin-top:12px">
            <button class="btn sm" onclick="stressIt(${x.id})">AI 压力测试</button>
            ${x.status !== 'closed' ? `<button class="btn sm" onclick="reviewDec(${x.id})">复盘</button>` : ''}
            <button class="btn sm" onclick="editDec(${x.id})">编辑</button>
          </div>
        </div>
      </details>`;
    }).join('')
      : '<div class="empty">还没有决策记录。<br>真正的决策都值得留档：<b>选项、打分、否决理由、止损线</b>。</div>';
  } catch (e) { toast(e.message, 'err'); }
};

$('#decAdd').onclick = () => { resetDecState(null); renderDec(decState, null); $('#modalBg').classList.add('on'); };

window.editDec = async id => {
  const d = await api('/api/decisions');
  const x = d.decisions.find(y => y.id === id);
  resetDecState(x); renderDec(decState, x); $('#modalBg').classList.add('on');
};

window.reviewDec = id => {
  openModal(`
    <h2>复盘这个决策</h2>
    <div class="small muted" style="margin-bottom:10px">不写复盘的决策等于没做——下次你还会犯同一个错。</div>
    <label class="fld"><span>实际发生了什么</span><textarea id="rvAct" placeholder="事实，不要评价"></textarea></label>
    <label class="fld"><span>现在看，当初的判断</span><select id="rvScore">
      <option value="-2">严重错判</option><option value="-1">有偏差</option>
      <option value="0" selected>中性</option><option value="1">基本正确</option><option value="2">判断到位</option>
    </select></label>
    <label class="fld"><span>教训（至少 8 字）</span><textarea id="rvLesson" placeholder="下次遇到同类情况，我该改哪一步"></textarea></label>
    <div class="row"><div class="sp"></div>
      <button class="btn" onclick="closeModal()">取消</button>
      <button class="btn primary" id="rvSave">保存复盘</button></div>`);
  $('#rvSave').onclick = async () => {
    try {
      await api(`/api/decisions/${id}/review`, {
        actual: $('#rvAct').value, score: +$('#rvScore').value, lesson: $('#rvLesson').value,
      });
      closeModal(); toast('已复盘', 'ok'); LOADERS.decisions();
    } catch (e) { toast(e.message, 'err'); }
  };
};

window.stressIt = async id => {
  toast('正在做压力测试，通常 20–40 秒…');
  try {
    const d = await api(`/api/decisions/${id}/stress`, {});
    const rep = d.repeating_mistakes || [];
    openModal(`<h2>压力测试</h2><div class="ai-out">
      ${rep.length ? `<h4 style="color:var(--bad)">⚠ 你正在重犯的错误</h4>
        ${rep.map(r => `<div style="margin-bottom:10px;border-left:2px solid var(--bad);padding-left:10px">
          <div class="small"><b>${esc(r.pattern)}</b>${r.past_id ? ` <span class="tiny muted">（对照 #${esc(String(r.past_id))}）</span>` : ''}</div>
          <div class="tiny muted">本次依据：${esc(r.evidence || '')}</div></div>`).join('')}` : ''}
      <h4>你隐含的假设</h4>
      ${(d.hidden_assumptions || []).map(a => `<div style="margin-bottom:10px">
        <div>· ${esc(a.assumption)}</div>
        <div class="small muted">若不成立：${esc(a.if_false)}</div>
        <div class="small" style="color:var(--acc)">验证方法：${esc(a.test)}</div></div>`).join('')}
      <h4>四种立场轮流攻击</h4>
      ${(d.personas || []).map(p => `<div style="margin-bottom:10px">
        <div><b style="color:var(--warn)">${esc(p.role)}</b></div>
        <div class="small">${esc(p.strongest_objection)}</div>
        <div class="small muted">它会怎么做：${esc(p.what_i_would_do)}</div></div>`).join('')}
      <h4>二阶效应</h4>
      <ol style="padding-left:18px">${(d.second_order || []).map(s => `<li>${esc(s.effect)}</li>`).join('')}</ol>
      <h4>事前验尸</h4><div class="quote">${esc(d.premortem || '')}</div>
      <h4>必须立刻退出的信号</h4>
      <ul>${(d.kill_signals || []).map(s => `<li>${esc(s)}</li>`).join('')}</ul>
      <h4>结论</h4>
      <div class="row wrap" style="gap:8px;margin-bottom:8px">
        <span class="chip ${d.verdict === '支持执行' ? 'good' : d.verdict === '应当否决' ? 'bad' : 'warn'}">${esc(d.verdict)}</span>
      </div>
      <div style="color:var(--acc)">${esc(d.one_question || '')}</div>
      <div class="row" style="margin-top:16px"><div class="sp"></div>
        <button class="btn" onclick="closeModal()">关闭</button></div>
    </div>`);
  } catch (e) { toast(e.message, 'err'); }
};

/* ------------------------------------------------------------------ 人际 */
LOADERS.people = async () => {
  renderProposals('person');
  try {
    const d = await api('/api/people');
    $('#peopleList').innerHTML = d.people.length ? d.people.map(p => {
      const ratio = p.ratio;
      const bad = ratio != null && ratio > 0.6;
      return `<div class="card">
        <div class="row" style="margin-bottom:6px">
          <b style="font-size:15px">${esc(p.name)}</b>
          <span class="chip ${p.status === 'normal' ? '' : p.status === 'cooling' ? 'warn' : 'bad'}">
            ${p.status === 'normal' ? '正常' : p.status === 'cooling' ? '冷却' : p.status === 'downgraded' ? '已降级' : '已切断'}</span>
          ${bad ? '<span class="chip bad">单向消耗</span>' : ''}
          ${p.idle_days != null && p.idle_days > 14 ? `<span class="chip warn">悬置 ${p.idle_days} 天</span>` : ''}
          <div class="sp"></div><span class="tiny muted">${esc(p.relation || '')}</span>
        </div>
        <div class="row tiny muted" style="gap:10px;margin-bottom:6px">
          <span>我付出 <b style="color:var(--fg)">${p.give}</b></span>
          <span>我得到 <b style="color:var(--fg)">${p.take}</b></span>
          <span>净 ${p.net > 0 ? '+' : ''}${p.net}</span>
          <span>失衡度 <b style="color:${bad ? 'var(--bad)' : 'var(--fg)'}">${ratio == null ? '—' : Math.round(ratio * 100) + '%'}</b></span>
        </div>
        <div class="bar ${bad ? 'bad' : 'good'}"><i style="width:${ratio == null ? 0 : Math.min(100, Math.round(ratio * 100))}%"></i></div>
        ${p.interactions && p.interactions.length ? `<div class="tiny muted" style="margin-top:8px">
          ${p.interactions.slice(0, 4).map(i => `<div>${i.on_date.slice(5)} ·
            <span style="color:${i.direction === 'give' ? 'var(--warn)' : i.direction === 'take' ? 'var(--good)' : 'var(--mut)'}">
            ${i.direction === 'give' ? '我付出' : i.direction === 'take' ? '我得到' : '中性'}×${i.weight}</span>
            ${esc(i.what)}</div>`).join('')}</div>` : ''}
        ${p.status_reason ? `<div class="tiny muted" style="margin-top:6px">状态理由：${esc(p.status_reason)}</div>` : ''}
        <div class="row wrap" style="gap:6px;margin-top:10px">
          <button class="btn sm" onclick="addInt(${p.id},'give')">+ 我付出</button>
          <button class="btn sm" onclick="addInt(${p.id},'take')">+ 我得到</button>
          <button class="btn sm" onclick="reviewPerson(${p.id})">AI 复核边界</button>
          <button class="btn sm ghost danger" onclick="setPerson(${p.id},'${esc(p.name).replace(/'/g, '')}','${p.status}')">改状态</button>
        </div>
      </div>`;
    }).join('')
      : '<div class="empty">还没有记录。<br>只记录<b>谁付出、谁得到</b>，不要记录情绪。</div>';
  } catch (e) { toast(e.message, 'err'); }
};

window.addInt = (pid, dir) => {
  openModal(`
    <h2>${dir === 'give' ? '我付出了什么' : '我得到了什么'}</h2>
    <label class="fld"><span>日期</span><input type="date" id="iDate" value="${todayStr()}"></label>
    <label class="fld"><span>权重（1=小事 3=明确帮忙 5=重大投入）</span><input type="number" id="iW" value="2" min="1" step="1"></label>
    <label class="fld"><span>具体是什么</span><textarea id="iWhat" placeholder="例：帮他改了两版 PPT"></textarea></label>
    <div class="row"><div class="sp"></div>
      <button class="btn" onclick="closeModal()">取消</button>
      <button class="btn primary" id="iSave">记录</button></div>`);
  $('#iSave').onclick = async () => {
    try {
      await api(`/api/people/${pid}/interaction`, {
        direction: dir, on_date: $('#iDate').value, weight: +$('#iW').value, what: $('#iWhat').value,
      });
      closeModal(); toast('已记录', 'ok'); LOADERS.people();
    } catch (e) { toast(e.message, 'err'); }
  };
};

window.reviewPerson = async pid => {
  toast('正在复核，通常 15–30 秒…');
  try {
    const d = await api(`/api/people/${pid}/review`, {});
    openModal(`<h2>边界复核</h2><div class="ai-out">
      <div class="row wrap" style="gap:6px;margin-bottom:8px">
        <span class="chip ${d.risk_level === '高' ? 'bad' : d.risk_level === '中' ? 'warn' : 'good'}">风险 ${esc(d.risk_level)}</span>
        <span class="chip">${esc(d.structure)}</span>
        <span class="chip ${d.keep_or_cut === '切断' ? 'bad' : d.keep_or_cut === '降级' ? 'warn' : 'good'}">${esc(d.keep_or_cut)}</span>
      </div>
      <h4>诊断</h4><div class="quote">${esc(d.diagnosis || '')}</div>
      <h4>悬置判断</h4><div>${esc(d.suspension || '')}</div>
      <h4>红线</h4><div class="quote">${esc(d.boundary_line || '')}</div>
      <h4>可以照着说的话</h4>
      <div style="background:var(--panel2);border:1px solid var(--line);border-radius:9px;padding:10px;margin-top:4px">
        ${esc(d.script || '')}</div>
      ${(d.evidence || []).length ? `<h4>依据</h4><div class="small muted">${d.evidence.map(esc).join('；')}</div>` : ''}
      <div class="row" style="margin-top:16px"><div class="sp"></div>
        <button class="btn" onclick="closeModal()">关闭</button></div>
    </div>`);
  } catch (e) { toast(e.message, 'err'); }
};

window.setPerson = async (pid, name, cur) => {
  openModal(`
    <h2>${esc(name)} 的状态</h2>
    <label class="fld"><span>新状态</span><select id="pStatus">
      ${[['normal', '正常'], ['cooling', '冷却'], ['downgraded', '降级'], ['cut', '切断']]
      .map(([v, l]) => `<option value="${v}" ${cur === v ? 'selected' : ''}>${l}</option>`).join('')}
    </select></label>
    <label class="fld"><span>理由（降级/切断必填，至少 8 字）</span>
      <textarea id="pReason" placeholder="例：连续三次单向索取，我的付出回报比 0.93"></textarea></label>
    <div class="row"><div class="sp"></div>
      <button class="btn" onclick="closeModal()">取消</button>
      <button class="btn primary" id="pSave">保存</button></div>`);
  $('#pSave').onclick = async () => {
    try {
      await api('/api/people', { id: pid, name, status: $('#pStatus').value, status_reason: $('#pReason').value });
      closeModal(); toast('已更新', 'ok'); LOADERS.people();
    } catch (e) { toast(e.message, 'err'); }
  };
};

$('#personAdd').onclick = () => {
  openModal(`
    <h2>加一个人</h2>
    <label class="fld"><span>称呼</span><input id="pName" placeholder="例：老同学 A"></label>
    <label class="fld"><span>关系</span><input id="pRel" placeholder="例：大学室友 / 同事 / 导师"></label>
    <label class="fld"><span>备注</span><textarea id="pNotes" placeholder="他通常找我做什么"></textarea></label>
    <div class="row"><div class="sp"></div>
      <button class="btn" onclick="closeModal()">取消</button>
      <button class="btn primary" id="pNew">添加</button></div>`);
  $('#pNew').onclick = async () => {
    try {
      await api('/api/people', { name: $('#pName').value, relation: $('#pRel').value, notes: $('#pNotes').value });
      closeModal(); toast('已添加', 'ok'); LOADERS.people();
    } catch (e) { toast(e.message, 'err'); }
  };
};

/* ------------------------------------------------------------------ 风控 */
LOADERS.guard = async () => {
  try {
    const [tr, ru, al, mm] = await Promise.all([
      api('/api/traits'), api('/api/rules'), api('/api/alerts'), api('/api/memory')]);
    ALL_ALERTS = al.alerts || [];
    $('#traitsList').innerHTML = tr.traits.length ? tr.traits.map(t => `
      <div class="card" style="background:var(--panel2);margin-bottom:10px">
        <div class="row" style="margin-bottom:6px">
          <b>${esc(t.name)}</b>
          <span class="chip ${t.direction === '有害' ? 'bad' : t.direction === '有益' ? 'good' : ''}">${esc(t.direction)}</span>
          <span class="chip ${t.status === 'accepted' ? 'good' : t.status === 'rejected' ? 'bad' : 'warn'}">
            ${t.status === 'accepted' ? '已采纳' : t.status === 'rejected' ? '已驳回' : '待审'}</span>
          ${t.dup_name ? `<span class="chip warn">疑似 #${t.dup_of} 换措辞</span>` : ''}
          <div class="sp"></div>
          <span class="tiny muted">置信 ${Math.round((t.confidence || 0) * 100)}%</span>
        </div>
        ${t.dup_name ? `<div class="tiny" style="color:var(--warn);margin-bottom:4px">
          这条可能只是「${esc(t.dup_name)}」换了个说法。若无新机制，建议驳回。</div>` : ''}
        <div style="margin-bottom:6px">${esc(t.statement)}</div>
        ${t.blind_spot ? `<div class="small muted">盲区：${esc(t.blind_spot)}</div>` : ''}
        ${t.cost ? `<div class="small" style="color:var(--warn)">代价：${esc(t.cost)}</div>` : ''}
        ${t.counter ? `<div class="small" style="color:var(--acc)">反制：${esc(t.counter)}</div>` : ''}
        ${(t.evidence || []).length ? `<div class="tiny" style="margin-top:6px">
          <span class="muted">证据：</span>${t.evidence.map(e =>
            `<span class="ev chip" title="${esc(e.title)}">#${e.id} ${e.on_date.slice(5)}</span>`).join(' ')}</div>` : ''}
        ${t.reject_reason ? `<div class="tiny muted" style="margin-top:4px">驳回理由：${esc(t.reject_reason)}</div>` : ''}
        ${t.status === 'draft' ? `<div class="row" style="gap:6px;margin-top:10px">
          <button class="btn sm primary" onclick="decideTrait(${t.id},'accepted')">采纳</button>
          <button class="btn sm ghost danger" onclick="decideTrait(${t.id},'rejected')">驳回</button></div>` : ''}
      </div>`).join('')
      : '<div class="empty">还没有画像。<br>先记 5 条以上真实事件，再点「重新分析」。</div>';

    $('#rulesList').innerHTML = ru.rules.map(r => `
      <div class="row" style="gap:8px;margin-bottom:10px;align-items:flex-start">
        <input type="checkbox" ${r.enabled ? 'checked' : ''} data-rk="${esc(r.key)}" class="rkEn"
          style="width:auto;margin-top:9px">
        <div style="flex:1">
          <div class="small">${esc(r.name)}</div>
          <div class="tiny muted">${esc(r.descr)}</div>
        </div>
        <input type="number" value="${r.value}" step="any" data-rk="${esc(r.key)}" class="rkV"
          style="width:82px;text-align:right">
        <span class="tiny muted" style="width:26px;padding-top:9px">${esc(r.unit)}</span>
      </div>`).join('');

    const acks = ALL_ALERTS.filter(a => a.ack_status !== 'open');
    $('#acksList').innerHTML = acks.length ? acks.map(a => `
      <div class="small" style="margin-bottom:8px;border-bottom:1px solid var(--line);padding-bottom:8px">
        <div>${esc(a.message)}</div>
        <div class="tiny muted">${a.ack_status === 'handled' ? '已处置' : '暂不处理'} · ${esc(a.ack_note)}</div>
      </div>`).join('') : '<div class="tiny muted">还没有处置记录。</div>';

    const live = (mm.memory || []).filter(m => m.status === 'active');
    const pending = (mm.memory || []).filter(m => m.status === 'draft');
    const dead = (mm.memory || []).filter(m => m.status === 'retired');
    $('#memoryList').innerHTML = `
      ${pending.length ? `<div class="row" style="margin-bottom:8px">
        <span class="chip warn">${pending.length} 条待确认</span>
        <div class="sp"></div>
        <span class="tiny muted">由旧事件提炼，确认后才进入 AI 上下文</span></div>` : ''}
      ${pending.map(m => `
        <div style="margin-bottom:8px;border-left:2px solid var(--warn);padding-left:10px">
          <div class="row"><span class="chip warn">${esc(m.kind)}</span>
            <div class="sp"></div></div>
          <div class="small" style="margin-top:2px">${esc(m.statement)}</div>
          <div class="tiny muted">证据 ${(m.evidence || []).map(i => '#' + i).join(' ')}</div>
          <div class="row" style="gap:6px;margin-top:6px">
            <button class="btn sm primary" onclick="approveMemory(${m.id})">确认</button>
            <button class="btn sm ghost danger" onclick="retireMemory(${m.id})">作废</button>
          </div>
        </div>`).join('')}
      ${live.map(m => `
        <div style="margin-bottom:8px;border-left:2px solid var(--acc);padding-left:10px">
          <div class="row"><span class="chip acc">${esc(m.kind)}</span>
            ${m.source === 'consolidated' ? '<span class="chip">由旧事件提炼</span>' : ''}
            <div class="sp"></div>
            <button class="btn sm ghost danger" onclick="retireMemory(${m.id})">作废</button></div>
          <div class="small" style="margin-top:2px">${esc(m.statement)}</div>
        </div>`).join('')}
      ${dead.map(m => `
        <div style="margin-bottom:6px;opacity:.5;border-left:2px solid var(--line);padding-left:10px">
          <div class="small"><span class="chip">已作废</span> ${esc(m.statement)}</div>
          <div class="tiny muted">作废理由：${esc(m.retired_reason)}</div>
        </div>`).join('')}
      ${(!live.length && !pending.length && !dead.length) ? `<div class="empty">还没有长期记忆。<br>
        写第一条建议：你反复观察到自己的一件事，例如<br>
        「每次某个方向进入平台期，我就会去找新工具。」</div>` : ''}
      <button class="btn" id="consolidateBtn" style="width:100%;margin-top:10px">
        从旧事件提炼记忆（30 天前、且未提炼过的）</button>`;

    const cb = $('#consolidateBtn');
    if (cb) cb.onclick = async () => {
      cb.disabled = true; cb.innerHTML = '<span class="spinner"></span> 正在提炼…';
      try {
        const r = await api('/api/analyze/consolidate', { older_than_days: 30 });
        toast(`提炼出 ${r.created.length} 条记忆草稿，覆盖 ${r.covered_events} 条事件`, 'ok');
        LOADERS.guard();
      } catch (e) {
        toast(e.message, 'err');
        cb.disabled = false; cb.textContent = '从旧事件提炼记忆（30 天前、且未提炼过的）';
      }
    };
  } catch (e) { toast(e.message, 'err'); }
};

let ALL_ALERTS = [];

window.approveMemory = async id => {
  try { await api(`/api/memory/${id}/approve`, {}); toast('已确认，进入上下文', 'ok'); LOADERS.guard(); }
  catch (e) { toast(e.message, 'err'); }
};

window.retireMemory = async id => {
  const v = await askText({
    title: '作废这条记忆',
    hint: '它不再成立了吗？作废也留档——以后回看，你会知道自己什么时候纠正过对自己的判断。',
    label: '作废理由', danger: true, confirm: '作废',
  });
  if (!v) return;
  try { await api(`/api/memory/${id}/retire`, { reason: v }); toast('已作废并留档', 'ok'); LOADERS.guard(); }
  catch (e) { toast(e.message, 'err'); }
};

document.getElementById('memAdd').onclick = () => {
  openModal(`
    <h2>加一条长期记忆</h2>
    <div class="small muted" style="margin-bottom:12px">
      写「你已经确认的关于自己的一件事」，不要写愿望。AI 会把它当作前提，不再争论。
    </div>
    <label class="fld"><span>类别</span><select id="mKind">
      <option>自我认知</option><option>边界规则</option><option>偏好</option><option>禁忌</option>
    </select></label>
    <label class="fld"><span>内容（至少 6 字）</span>
      <textarea id="mStmt" placeholder="例：我在平台期会想换方向，这不是发现了更好的东西，是想逃离枯燥。"></textarea></label>
    <div class="row"><div class="sp"></div>
      <button class="btn" onclick="closeModal()">取消</button>
      <button class="btn primary" id="mSave">保存</button></div>`);
  $('#mSave').onclick = async () => {
    try {
      await api('/api/memory', { statement: $('#mStmt').value, kind: $('#mKind').value });
      closeModal(); toast('已加入长期记忆', 'ok'); LOADERS.guard();
    } catch (e2) { toast(e2.message, 'err'); }
  };
};

window.decideTrait = async (id, status) => {
  if (status === 'accepted') {
    try { await api(`/api/traits/${id}/decide`, { status }); toast('已采纳', 'ok'); LOADERS.guard(); }
    catch (e) { toast(e.message, 'err'); }
    return;
  }
  const v = await askText({
    title: '驳回这条画像',
    hint: '为什么它不成立？是你觉得证据不够，还是它说错了？驳回理由会留档——以后你会想回来看自己当时为什么不信。',
    label: '驳回理由', danger: true, confirm: '驳回',
  });
  if (!v) return;
  try { await api(`/api/traits/${id}/decide`, { status, reason: v }); toast('已驳回并留档', 'ok'); LOADERS.guard(); }
  catch (e) { toast(e.message, 'err'); }
};

$('#rulesSave').onclick = async () => {
  const rules = [];
  $$('.rkV').forEach(el => {
    const key = el.dataset.rk;
    const en = $$(`.rkEn[data-rk="${key}"]`)[0];
    rules.push({ key, value: +el.value, enabled: en ? en.checked : true });
  });
  try { await api('/api/rules', { rules }); toast('阈值已保存', 'ok'); }
  catch (e) { toast(e.message, 'err'); }
};

$('#traitAI').onclick = async e => {
  const btn = e.target; btn.disabled = true; btn.innerHTML = '<span class="spinner"></span>';
  try {
    const d = await api('/api/analyze/personality', { limit: 80 });
    // 把 AI 结果里可验证的模式写入 traits（草稿态，等用户采纳/驳回）
    for (const p of d.patterns || []) {
      await api('/api/traits', {
        name: p.name, direction: p.direction, statement: p.statement, evidence: p.evidence,
        blind_spot: p.blind_spot, cost: p.cost, counter: p.counter,
        confidence: p.confidence, source: 'ai', dup_of: p.duplicate_of_id || 0,
      }).catch(err => toast(err.message, 'err'));
    }
    toast(`新增 ${(d.patterns || []).length} 条画像待审` + (d.dropped_unverifiable ? `，丢弃 ${d.dropped_unverifiable} 条无证据结论` : ''), 'ok');
    LOADERS.guard();

    const inj = d.injected || {};
    const ups = d.updates || [];
    openModal(`<h2>本次分析</h2><div class="ai-out">
      <h4>喂给 AI 的记忆</h4>
      <div class="small muted">已采纳结论 ${inj.traits || 0} 条 · 已驳回 ${inj.rejected || 0} 条 ·
        长期记忆 ${inj.memory || 0} 条。未采纳的草稿不会进入上下文。</div>

      <h4>新增画像 ${(d.patterns || []).length} 条</h4>
      ${(d.patterns || []).length ? `<ul>${d.patterns.map(p =>
        `<li>${esc(p.name)} <span class="tiny muted">（证据 ${p.evidence.map(i => '#' + i).join(' ')}）</span></li>`).join('')}</ul>`
        : '<div class="small muted">没有新东西。可能是既有结论已经覆盖了现有证据——这本身是个好信号。</div>'}

      <h4>对既有结论的更新 ${ups.length} 条</h4>
      ${ups.length ? ups.map(u => `<div style="margin-bottom:8px">
        <span class="chip ${u.change === '推翻' ? 'bad' : u.change === '修正' ? 'warn' : 'good'}">${esc(u.change)}</span>
        <b>${esc(u.target || ('#' + u.target_id))}</b>
        <div class="small">${esc(u.why)}</div>
        <div class="tiny muted">证据 ${(u.evidence || []).map(i => '#' + i).join(' ')}</div>
      </div>`).join('') : '<div class="small muted">无。既有结论在新证据下依然成立。</div>'}

      ${d.dropped_unverifiable ? `<h4>被丢弃</h4><div class="small" style="color:var(--bad)">
        ${d.dropped_unverifiable} 条结论引用了不存在的事件编号——AI 在编证据，已拦下。</div>` : ''}

      <div class="row" style="margin-top:16px"><div class="sp"></div>
        <button class="btn" onclick="closeModal()">知道了</button></div>
    </div>`);
  } catch (e2) { toast(e2.message, 'err'); }
  btn.disabled = false; btn.textContent = '重新分析';
};

$('#exportBtn').onclick = async () => {
  try {
    const d = await api('/api/export');
    const blob = new Blob([JSON.stringify(d, null, 2)], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'mirror-' + todayStr() + '.json';
    a.click();
    URL.revokeObjectURL(a.href);
  } catch (e) { toast(e.message, 'err'); }
};

$('#aiPing').onclick = async () => {
  try { const d = await api('/api/ai/ping', {}); toast('AI 连接正常', 'ok'); }
  catch (e) { toast(e.message, 'err'); }
};

async function loadHealth() {
  try {
    const h = await api('/api/health');
    const c = h.counts || {};
    $('#healthBox').innerHTML = `
      <div class="row wrap tiny muted" style="gap:12px;margin-bottom:8px">
        <span>自检 <b style="color:${h.integrity === 'ok' ? 'var(--good)' : 'var(--bad)'}">${esc(h.integrity)}</b></span>
        <span>库 ${(h.db_size / 1024).toFixed(0)} KB</span>
        <span>skill ${h.skills}</span>
      </div>
      <div class="row wrap tiny muted" style="gap:10px;margin-bottom:10px">
        <span>事件 ${c.events}</span><span>对话 ${c.conversations}</span><span>消息 ${c.messages}</span>
        <span>记忆 ${c.memory}</span><span>画像 ${c.traits}</span>
        <span>决策 ${c.decisions}</span><span>方向 ${c.topics}</span>
      </div>
      <div class="tiny muted">最近备份（自动保留 10 份）：</div>
      ${(h.backups || []).length ? h.backups.map(b => `<div class="tiny muted">
        · ${esc(b.mtime)} · ${(b.size / 1024).toFixed(0)} KB · ${esc(b.name)}</div>`).join('')
        : '<div class="tiny muted">还没有备份</div>'}`;
  } catch (e) { toast(e.message, 'err'); }
}

$('#theorySearch').onclick = () => loadTheory($('#theoryQ').value.trim());
$('#theoryQ').onkeydown = e => { if (e.key === 'Enter') loadTheory($('#theoryQ').value.trim()); };

$('#backupBtn').onclick = async () => {
  try {
    const r = await api('/api/backup', {});
    toast('已备份 · 自检 ' + r.check, 'ok');
    loadHealth();
  } catch (e) { toast(e.message, 'err'); }
};

/* ------------------------------------------------------------------ 启动 */
async function boot() {
  try {
    const me = await api('/api/me');
    $('#login').style.display = 'none';
    $('#navBadge').textContent = '';
    // 预热：拿一次全量预警用于「已处置」列表
    api('/api/alerts').then(d => { ALL_ALERTS = d.alerts || []; }).catch(() => { });
    go('chat');
  } catch (e) {
    if (String(e.message).includes('未登录')) showLogin();
  }
}

(async function start() {
  try {
    const r = await fetch('/api/me');
    if (r.status === 401) { showLogin(); return; }
    boot();
  } catch (e) {
    showLogin();
  }
})();
