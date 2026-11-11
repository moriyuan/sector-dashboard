// 韭研公社异动数据采集：SESSION 登录态 + token/timestamp 签名。
// 签名机制移植自 https://github.com/Zessi-C/jiuyan-mcp （MIT）：
// 从 SSR 页面收割 window.__NUXT__ 内嵌 digest 与 Nuxt chunk，
// 在 node:vm 沙箱中定位混淆签名函数并调用，得到每次请求的鉴权头。
//
// 用法: node fetch.mjs '<json>'
//   {"session":"...","op":"fields","date":"2026-11-10"}      板块字段列表
//   {"session":"...","op":"list","field_id":"...","date":""} 板块下个股明细
// 成功: stdout 输出 {"ok":true,"data":...}；失败: {"ok":false,"error":"..."} 且 exit 1。
import os from 'node:os';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';

const PAGE_URL = 'https://www.jiuyangongshe.com/action';
const RES_BASE = 'https://res.jiuyangongshe.com/';
const API_BASE = 'https://web-api.jiuyangongshe.com/jystock-app';
// 长亭雷池对 Linux UA 直接 403，必须用桌面 Windows UA
const UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36';
const DIGEST_RE = /(\w+):\{digest:"([0-9a-f]{64,})",project:"([^"]+)",serverTime:(\d+)\}/;
const CHUNK_RE = /\/_nuxt\/([0-9a-f]+)\.js/g;
const KNOWN_MODULE_IDS = [144];
const KNOWN_GLOBAL_NAMES = ['nR4xKp7mQa'];
const SIGNER_MODULE_RE = /globalThis\.[A-Za-z0-9_$]{4,}\s*=/;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function fetchText(url, tries = 4) {
  for (let i = 0; ; i++) {
    const res = await fetch(url, { headers: { 'User-Agent': UA }, redirect: 'follow' });
    if (res.ok) return res.text();
    // 雷池按 IP 间歇限流：403/5xx 退避重试
    if (i >= tries - 1) throw new Error(`GET ${url} -> HTTP ${res.status}`);
    await sleep(4000 * (i + 1));
  }
}

function buildSandbox() {
  const registry = new Map();
  const noop = () => {};
  const el = () => ({ style: {}, setAttribute: noop, getAttribute: () => null,
    getElementsByTagName: () => [], addEventListener: noop, attachEvent: noop,
    appendChild: noop, insertBefore: noop });
  const sandbox = {
    document: { createElement: el, createElementNS: el, createTextNode: () => ({}),
      getElementsByTagName: () => [], getElementsByClassName: () => [],
      querySelector: () => null, querySelectorAll: () => [], head: el(), body: el(),
      documentElement: el(), cookie: '', addEventListener: noop, removeEventListener: noop,
      readyState: 'complete' },
    navigator: { userAgent: UA, platform: 'Win32',
      appVersion: '5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
      vendor: 'Google Inc.', language: 'zh-CN', languages: ['zh-CN', 'en'],
      cookieEnabled: true, hardwareConcurrency: 8, maxTouchPoints: 0 },
    location: { protocol: 'https:', host: 'www.jiuyangongshe.com', hostname: 'www.jiuyangongshe.com',
      origin: 'https://www.jiuyangongshe.com', href: PAGE_URL, pathname: '/action', search: '', hash: '' },
    localStorage: { getItem: () => null, setItem: noop, removeItem: noop },
    sessionStorage: { getItem: () => null, setItem: noop, removeItem: noop },
    screen: { width: 1920, height: 1080, availWidth: 1920, availHeight: 1040, colorDepth: 24 },
    history: { pushState: noop, replaceState: noop },
    addEventListener: noop, removeEventListener: noop,
    console: { log: noop, warn: noop, error: noop, info: noop, debug: noop },
    setTimeout, clearTimeout, setInterval, clearInterval,
  };
  sandbox.webpackJsonp = [];
  sandbox.webpackJsonp.push = (...args) => {
    for (const data of args) {
      const modules = data?.[1];
      if (modules && typeof modules === 'object')
        for (const [id, fn] of Object.entries(modules))
          if (typeof fn === 'function') registry.set(Number(id), fn);
      return data.length;
    }
  };
  vm.createContext(sandbox);
  sandbox.window = sandbox; sandbox.self = sandbox; sandbox.globalThis = sandbox;
  return { ctx: sandbox, registry };
}

const isSignature = (out) => !!out && typeof out.token === 'string'
  && /^[0-9a-f]{32,}$/.test(out.token) && typeof out.timestamp === 'string';

function initSignModule(ctx, registry, stateArg) {
  const cache = new Map();
  const req = (id) => {
    if (cache.has(id)) return cache.get(id);
    const fn = registry.get(id);
    if (!fn) throw new Error(`webpack 模块 ${id} 不存在`);
    const mod = { exports: {} };
    cache.set(id, mod.exports);
    const out = fn.call(mod.exports, mod, mod.exports, req);
    const value = out == null ? mod.exports : out;
    cache.set(id, value);
    return value;
  };
  const tried = new Set();
  const tryModule = (id) => {
    if (tried.has(id)) return null;
    tried.add(id);
    const before = new Set(Object.keys(ctx));
    let exports_ = null;
    try { exports_ = req(id); } catch { /* 导出常已完成 */ }
    const added = Object.keys(ctx).filter((k) => !before.has(k) && typeof ctx[k] === 'function');
    const cands = [
      ...added.map((n) => ({ known: KNOWN_GLOBAL_NAMES.includes(n), fn: ctx[n] })),
      ...(exports_ && typeof exports_ === 'object'
        ? Object.values(exports_).filter((v) => typeof v === 'function').map((fn) => ({ known: false, fn }))
        : []),
    ].sort((a, b) => Number(b.known) - Number(a.known));
    for (const c of cands) {
      try { if (isSignature(c.fn({ ...stateArg }, Date.now()))) return c.fn; } catch { /* 非签名函数 */ }
    }
    return null;
  };
  for (const id of KNOWN_MODULE_IDS) { const fn = tryModule(id); if (fn) return fn; }
  for (const [id, fn] of registry) {
    if (tried.has(id) || !SIGNER_MODULE_RE.test(String(fn))) continue;
    const sign = tryModule(id);
    if (sign) return sign;
  }
  throw new Error('未能定位签名函数，站点可能已升级');
}

async function buildSigner() {
  const html = await fetchText(PAGE_URL);
  const m = html.match(DIGEST_RE);
  if (!m) throw new Error('页面未内嵌签名原料 digest');
  const state = { digest: m[2], project: m[3], serverTime: Number(m[4]) };
  const offsetMs = state.serverTime * 1000 - Date.now();
  const names = [...new Set([...html.matchAll(CHUNK_RE)].map((x) => x[1]))];
  if (!names.length) throw new Error('页面未引用任何 Nuxt chunk');
  const cacheDir = path.join(os.tmpdir(), 'jygs-chunks');
  fs.mkdirSync(cacheDir, { recursive: true });
  const { ctx, registry } = buildSandbox();
  for (const name of names) {
    const file = path.join(cacheDir, `${name}.js`);
    let code = null;
    try {
      if (fs.existsSync(file) && Date.now() - fs.statSync(file).mtimeMs < 7 * 864e5 && fs.statSync(file).size > 1000)
        code = fs.readFileSync(file, 'utf8');
    } catch { /* 重新下载 */ }
    if (!code) {
      code = await fetchText(`${RES_BASE}_nuxt/${name}.js`);
      try { fs.writeFileSync(file, code); } catch { /* 缓存失败无碍 */ }
    }
    try { vm.runInContext(code, ctx, { filename: `${name}.js` }); } catch { /* 入口 chunk 噪音 */ }
    await sleep(150);
  }
  const sign = initSignModule(ctx, registry, state);
  return (nowMs) => sign({ ...state }, nowMs + offsetMs);
}

async function main() {
  const args = JSON.parse(process.argv[2] || '{}');
  if (!args.session) throw new Error('缺少 session');
  const sign = await buildSigner();
  const post = async (pathName, body) => {
    for (let attempt = 0; ; attempt++) {
      await sleep(1200);
      const s = sign(Date.now());
      const res = await fetch(API_BASE + pathName, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest',
          'User-Agent': UA, Referer: 'https://www.jiuyangongshe.com/',
          Origin: 'https://www.jiuyangongshe.com',
          platform: '3', timestamp: s.timestamp, token: s.token,
          Cookie: `SESSION=${args.session}` },
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error(`POST ${pathName} -> HTTP ${res.status}`);
      const json = await res.json();
      const code = String(json.errCode ?? '-1');
      if (code === '0') return json.data;
      if (code === '1') throw new Error('SESSION 登录态失效，需要更新 cookie');
      if (code === '110' && attempt === 0) { continue; } // 签名时间窗：重试一次
      throw new Error(`韭研接口 errCode=${code} ${json.msg || json.errMsg || ''}`);
    }
  };
  if (args.op === 'fields') {
    const data = await post('/api/v1/action/field', { date: String(args.date), pc: 1 });
    console.log(JSON.stringify({ ok: true, data }));
    return;
  }
  if (args.op === 'list') {
    const data = await post('/api/v1/action/list', {
      action_field_id: String(args.field_id), pc: 1,
      start: 1, limit: 50, sort_price: 0, sort_range: 0, sort_time: 0 });
    console.log(JSON.stringify({ ok: true, data }));
    return;
  }
  throw new Error(`未知操作 ${args.op}`);
}

main().catch((e) => { console.log(JSON.stringify({ ok: false, error: String(e.message || e) })); process.exit(1); });
