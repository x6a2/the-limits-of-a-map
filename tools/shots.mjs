// tools/shots.mjs — 本地校验：用 CDP 驱动 Edge 无头模式，截图 + 读取运行时状态
// 用法：node tools/shots.mjs
import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

const EDGE = "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";
const PORT = 9334;
const ROOT = path.resolve(process.cwd());
const OUT = path.join(ROOT, "shots");
const URL = "file:///" + ROOT.replace(/\\/g, "/").replace(/ /g, "%20") + "/index.html";
fs.mkdirSync(OUT, { recursive: true });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function jversion() {
  for (let i = 0; i < 60; i++) {
    try {
      const r = await fetch(`http://127.0.0.1:${PORT}/json/version`);
      if (r.ok) return await r.json();
    } catch {}
    await sleep(200);
  }
  throw new Error("edge did not start");
}

class CDP {
  constructor(ws) { this.ws = ws; this.id = 0; this.waiters = new Map(); this.events = []; }
  static async connect(url) {
    const ws = new WebSocket(url);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
    const c = new CDP(ws);
    ws.onmessage = (m) => {
      const msg = JSON.parse(m.data);
      if (msg.id && c.waiters.has(msg.id)) { c.waiters.get(msg.id)(msg); c.waiters.delete(msg.id); }
      else if (msg.method) c.events.push(msg);
    };
    return c;
  }
  send(method, params = {}) {
    const id = ++this.id;
    return new Promise((res) => {
      this.waiters.set(id, (m) => res(m.result ?? m.error));
      this.ws.send(JSON.stringify({ id, method, params }));
    });
  }
  async eval(expr) {
    const r = await this.send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true });
    if (r && r.exceptionDetails) throw new Error(r.exceptionDetails.text + " " + (r.exceptionDetails.exception?.description || ""));
    return r?.result?.value;
  }
}

const edge = spawn(EDGE, [
  "--headless=new", "--disable-gpu", "--hide-scrollbars", "--allow-file-access-from-files",
  "--no-first-run", "--no-default-browser-check",
  `--remote-debugging-port=${PORT}`, "--user-data-dir=" + path.join(ROOT, "shots", "_profile"),
  "about:blank",
], { stdio: "ignore" });

const info = await jversion();
const tab = await (await fetch(`http://127.0.0.1:${PORT}/json/new?about:blank`, { method: "PUT" })).json();
const cdp = await CDP.connect(tab.webSocketDebuggerUrl);
await cdp.send("Page.enable");
await cdp.send("Runtime.enable");

const SHOTS = [
  { name: "hero", sel: null },
  { name: "act1", sel: "#act1" },
  { name: "act2", sel: "#act2" },
  { name: "trans", sel: "#act2", dy: -240, wait: 380 },
  { name: "act3", sel: "#act3" },
  { name: "act4", sel: "#act4" },
  { name: "act4b", sel: "#act4", dy: 430 },
  { name: "act4c", sel: "#act4", dy: 980 },
  { name: "act4d", sel: "#act4", dy: 1360 },
  { name: "act5", sel: "#act5" },
  { name: "act5b", sel: "#act5", dy: 560 },
  { name: "act55", sel: "#act55" },
  { name: "footer", sel: "footer" },
];
const SIZES = [[1280, 800], [1440, 900]];
const report = { checks: {}, overflow: {}, shots: [] };

for (const [w, hgt] of SIZES) {
  for (const s of SHOTS) {
    await cdp.send("Emulation.setDeviceMetricsOverride", { width: w, height: hgt, deviceScaleFactor: 1, mobile: false });
    await cdp.send("Page.navigate", { url: URL });
    await sleep(900);
    if (s.sel) {
      await cdp.eval(`(()=>{const el=document.querySelector(${JSON.stringify(s.sel)});
        const y=el.getBoundingClientRect().top+window.scrollY;
        window.scrollTo(0, y + ${s.dy ?? 30}); return y;})()`);
    }
    await sleep(s.wait ?? 1400);
    const m = await cdp.eval(`(()=>{
      const de=document.documentElement;
      const over=[...document.querySelectorAll('body *')].filter(el=>{
        const r=el.getBoundingClientRect();
        return r.right>window.innerWidth+1.5 && r.width>0;
      }).slice(0,6).map(el=>el.tagName+'.'+(el.className||'').toString().slice(0,28)+' r='+Math.round(el.getBoundingClientRect().right));
      return {sw:de.scrollWidth, cw:de.clientWidth, sh:de.scrollHeight, over,
        check:(window.__CHECK__?window.__CHECK__():[]).slice(0,12), y:window.scrollY,
        vb:document.querySelector('#map').getAttribute('viewBox'),
        bb:(function(){try{return document.querySelector('#map g').getBBox();}catch(e){return null}})(),
        viz:(function(){const r=document.querySelector('#story .story__viz').getBoundingClientRect();return [r.width,r.height]})(),
        step:(function(){const r=document.querySelector('.step__inner').getBoundingClientRect();return [Math.round(r.left),Math.round(r.width)]})()};
    })()`);
    report.overflow[`${w}-${s.name}`] = m;
    if (w === 1280) report.checks = m.check;
    const png = await cdp.send("Page.captureScreenshot", { format: "png" });
    const f = path.join(OUT, `w${w}-${s.name}.png`);
    fs.writeFileSync(f, Buffer.from(png.data, "base64"));
    report.shots.push(f);
  }
}
report.font = await cdp.eval("getComputedStyle(document.querySelector('h1')).fontFamily");
fs.writeFileSync(path.join(OUT, "report.json"), JSON.stringify(report, null, 1));
console.log(JSON.stringify(report.overflow, null, 1));
console.log("checks:", JSON.stringify(report.checks));
console.log("font:", report.font);
edge.kill();
process.exit(0);
