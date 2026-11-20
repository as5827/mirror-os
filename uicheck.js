/* UI 自动验收：真浏览器打开、真登录、逐个 tab 截图、收集 JS 异常。 */
const { spawn } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

const BROWSER = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
const OUT = path.join(__dirname, '_shots');
const URL = 'http://127.0.0.1:8765/';
const sleep = ms => new Promise(r => setTimeout(r, ms));

const envTxt = fs.readFileSync(path.join(__dirname, '.env'), 'utf8');
const PIN = (envTxt.match(/^MIRROR_PIN=(.*)$/m) || [])[1].trim();

function waitPort(dir) {
  const f = path.join(dir, 'DevToolsActivePort');
  return (async () => {
    for (let i = 0; i < 200; i++) {
      if (fs.existsSync(f)) {
        const t = fs.readFileSync(f, 'utf8').split('\n')[0].trim();
        if (t) return Number(t);
      }
      await sleep(120);
    }
    throw new Error('devtools port timeout');
  })();
}

function client(ws) {
  let id = 0; const pend = new Map();
  ws.addEventListener('message', ev => {
    const m = JSON.parse(ev.data);
    if (m.id && pend.has(m.id)) {
      const { res, rej } = pend.get(m.id); pend.delete(m.id);
      m.error ? rej(new Error(m.error.message)) : res(m.result);
    }
  });
  return (method, params, sessionId) => new Promise((res, rej) => {
    const mid = ++id; pend.set(mid, { res, rej });
    const p = { id: mid, method, params: params || {} };
    if (sessionId) p.sessionId = sessionId;
    ws.send(JSON.stringify(p));
    setTimeout(() => { if (pend.has(mid)) { pend.delete(mid); rej(new Error(method + ' timeout')); } }, 40000);
  });
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const udd = fs.mkdtempSync(path.join(os.tmpdir(), 'mshot-'));
  const child = spawn(BROWSER, ['--headless=new', '--remote-debugging-port=0',
    '--user-data-dir=' + udd, '--no-first-run', '--no-default-browser-check',
    '--disable-gpu', '--hide-scrollbars', '--window-size=430,1000', 'about:blank'],
    { stdio: 'ignore' });

  const errs = [];
  const logs = [];
  try {
    const port = await waitPort(udd);
    let wsUrl;
    for (let i = 0; i < 60; i++) {
      try {
        const r = await fetch(`http://127.0.0.1:${port}/json/version`);
        const j = await r.json();
        if (j.webSocketDebuggerUrl) { wsUrl = j.webSocketDebuggerUrl; break; }
      } catch (_) { /* 还没起来 */ }
      await sleep(150);
    }
    const ws = new WebSocket(wsUrl);
    await new Promise((res, rej) => {
      ws.addEventListener('open', res, { once: true });
      ws.addEventListener('error', rej, { once: true });
    });
    const send = client(ws);
    const { targetId } = await send('Target.createTarget', { url: 'about:blank' });
    const { sessionId } = await send('Target.attachToTarget', { targetId, flatten: true });
    await send('Page.enable', {}, sessionId);
    await send('Runtime.enable', {}, sessionId);
    await send('Log.enable', {}, sessionId);
    await send('Emulation.setDeviceMetricsOverride',
      { width: 430, height: 932, deviceScaleFactor: 2, mobile: true }, sessionId);

    ws.addEventListener('message', ev => {
      const m = JSON.parse(ev.data);
      if (m.method === 'Runtime.exceptionThrown') {
        const d = m.params.exceptionDetails;
        errs.push((d.exception && (d.exception.description || d.exception.value)) || d.text);
      }
      if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error') {
        logs.push(m.params.args.map(a => a.value || a.description || '').join(' '));
      }
      if (m.method === 'Log.entryAdded' && m.params.entry.level === 'error') {
        logs.push('[net] ' + m.params.entry.text + ' ' + (m.params.entry.url || ''));
      }
    });

    const run = async expr => {
      const r = await send('Runtime.evaluate',
        { expression: expr, awaitPromise: true, returnByValue: true }, sessionId);
      if (r.exceptionDetails) throw new Error(r.exceptionDetails.text + ' ' +
        (r.exceptionDetails.exception && r.exceptionDetails.exception.description || ''));
      return r.result && r.result.value;
    };
    const shot = async name => {
      const { data } = await send('Page.captureScreenshot', { format: 'png' }, sessionId);
      fs.writeFileSync(path.join(OUT, name), Buffer.from(data, 'base64'));
      console.log('  shot', name);
    };
    const tap = async (label, file) => {
      await run(`(()=>{const b=[...document.querySelectorAll('#nav button')]
        .find(x=>x.textContent.includes(${JSON.stringify(label)})); if(!b) return 'no-btn'; b.click(); return 'ok';})()`);
      await sleep(1400);
      await shot(file);
    };

    console.log('-> 打开首页（应显示登录）');
    await send('Page.navigate', { url: URL }, sessionId);
    await sleep(1500);
    await shot('00-login.png');

    console.log('-> 登录');
    const st = await run(`fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({pin:${JSON.stringify(PIN)}})}).then(r=>r.status)`);
    console.log('   login status:', st);
    await send('Page.navigate', { url: URL }, sessionId);
    await sleep(2200);
    await shot('01-dashboard.png');

    const loginVisible = await run(`getComputedStyle(document.getElementById('login')).display`);
    console.log('   login overlay display:', loginVisible);

    await tap('事件', '02-events.png');
    await tap('方向', '03-topics.png');
    await tap('决策', '04-decisions.png');
    await tap('人际', '05-people.png');
    await tap('风控', '06-guard.png');
    await tap('看板', '07-dashboard-again.png');

    console.log('-> 展开一条决策看 MCDA 表');
    await run(`(()=>{const b=[...document.querySelectorAll('#nav button')]
      .find(x=>x.textContent.includes('决策')); b.click();})()`);
    await sleep(1200);
    const opened = await run(`(()=>{const d=document.querySelector('#decList details');
      if(!d) return 'none'; d.open=true; return 'opened';})()`);
    console.log('   decision detail:', opened);
    await sleep(600);
    await shot('08-decision-detail.png');

    console.log('-> 桌面视口');
    await send('Emulation.setDeviceMetricsOverride',
      { width: 1280, height: 900, deviceScaleFactor: 1, mobile: false }, sessionId);
    await run(`(()=>{const b=[...document.querySelectorAll('#nav button')]
      .find(x=>x.textContent.includes('看板')); b.click();})()`);
    await sleep(1500);
    await shot('09-desktop-dashboard.png');

    const bodyLen = await run('document.body.innerText.length');
    console.log('   页面文本长度:', bodyLen);

    console.log('\n--- JS 异常 ---');
    if (errs.length) errs.slice(0, 8).forEach(e => console.log('  ✗', String(e).split('\n')[0]));
    else console.log('  无');
    console.log('--- console.error / 网络错误 ---');
    if (logs.length) logs.slice(0, 8).forEach(e => console.log('  ✗', e));
    else console.log('  无');

    ws.close();
    if (errs.length || logs.length) process.exitCode = 2;
  } finally {
    try { child.kill(); } catch (_) {}
    try { fs.rmSync(udd, { recursive: true, force: true }); } catch (_) {}
  }
})().catch(e => { console.error('FAIL:', e.message); process.exit(1); });
