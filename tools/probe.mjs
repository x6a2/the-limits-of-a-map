// tools/probe.mjs <expr-file> — 在 Edge 无头里跑一段表达式并打印 JSON 结果
import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

const EDGE = "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";
const PORT = 9337;
const ROOT = path.resolve(process.cwd());
const URL = "file:///" + ROOT.replace(/\\/g, "/").replace(/ /g, "%20") + "/index.html";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const expr = fs.readFileSync(process.argv[2], "utf8");

const edge = spawn(EDGE, ["--headless=new", "--disable-gpu", "--hide-scrollbars", "--allow-file-access-from-files",
  `--remote-debugging-port=${PORT}`, "--user-data-dir=" + path.join(ROOT, "shots", "_profile2"), "about:blank"], { stdio: "ignore" });
for (let i = 0; i < 80; i++) { try { const r = await fetch(`http://127.0.0.1:${PORT}/json/version`); if (r.ok) break; } catch {} await sleep(200); }
const tab = await (await fetch(`http://127.0.0.1:${PORT}/json/new?about:blank`, { method: "PUT" })).json();
const ws = new WebSocket(tab.webSocketDebuggerUrl);
await new Promise((res) => (ws.onopen = res));
let id = 0; const waiters = new Map();
ws.onmessage = (m) => { const msg = JSON.parse(m.data); if (msg.id && waiters.has(msg.id)) { waiters.get(msg.id)(msg); waiters.delete(msg.id); } };
const send = (method, params = {}) => new Promise((res) => { const i = ++id; waiters.set(i, res); ws.send(JSON.stringify({ id: i, method, params })); });

await send("Emulation.setDeviceMetricsOverride", { width: 1280, height: 800, deviceScaleFactor: 1, mobile: false });
await send("Page.enable");
await send("Page.navigate", { url: URL });
await sleep(1500);
const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true });
console.log(JSON.stringify(r.result?.result?.value ?? r.result, null, 1));
edge.kill();
process.exit(0);
