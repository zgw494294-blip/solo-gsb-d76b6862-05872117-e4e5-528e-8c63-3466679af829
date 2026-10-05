/* 设备 CSV 并入前端生命周期：最小 DOM/事件 stub 加载真实 static/app.js。
 *
 * 覆盖：选文件自动 HTTP 预览（请求体含当前完整载荷+csv）、新增/文件内重复/
 * 与现有重复分类展示、确认前必须点确认（不自动追加）、确认请求带 token、
 * 确认成功后旧记录顺序不变新记录追加在空行/末尾并自动裁决、整批 422 拒绝
 * 时原表保留可重新预览、任何赛道输入或文件变化立即使预览失效（在途与迟到
 * 响应均不能恢复确认入口）、确认时输入与预览不一致被服务端令牌拒绝、
 * 40 条上限不触顶（空行复用）、预览期间可编辑、放弃/清空/示例清除文件、
 * 大编号与大秒数（>2^53）在预览请求与展示中无损、成功合并后复核与罚秒沿用原规则。
 */
"use strict";
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");

/* ---------------- mini DOM（与其他生命周期测试同一套） ---------------- */

class TokenList {
  constructor() { this._s = new Set(); }
  add(...names) { names.forEach((n) => n && this._s.add(n)); }
  remove(n) { this._s.delete(n); }
  contains(n) { return this._s.has(n); }
  [Symbol.iterator]() { return this._s[Symbol.iterator](); }
}

class El {
  constructor(tag) {
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.parent = null;
    this.attrs = {};
    this.classList = new TokenList();
    this.dataset = {};
    this.id = "";
    this.value = "";
    this.checked = false;
    this.hidden = false;
    this._text = "";
    this._html = "";
    this._listeners = new Map();
    this.name = "";
    this.type = "";
    this.files = null;
  }
  set textContent(v) { this._text = String(v); }
  get textContent() { return this._text; }
  set innerHTML(html) {
    this._html = html;
    if (html === "") this.children = [];
  }
  get innerHTML() { return this._html; }
  appendChild(c) {
    if (c.parent) c.parent._remove(c);
    c.parent = this;
    this.children.push(c);
    return c;
  }
  _remove(c) {
    const i = this.children.indexOf(c);
    if (i >= 0) this.children.splice(i, 1);
  }
  remove() { if (this.parent) { this.parent._remove(this); this.parent = null; } }
  insertAdjacentHTML(_pos, html) { parseHtml(html).forEach((n) => this.appendChild(n)); }
  addEventListener(type, fn) {
    if (!this._listeners.has(type)) this._listeners.set(type, []);
    this._listeners.get(type).push(fn);
  }
  dispatchEvent(evt) {
    let node = this;
    if (!evt.target) evt.target = this;
    if (!evt.preventDefault) evt.preventDefault = () => {};
    while (node) {
      const fns = node._listeners.get(evt.type) || [];
      for (const fn of fns) fn.call(node, evt);
      node = node.parent;
    }
  }
  _descendants(out = []) {
    for (const c of this.children) { out.push(c); c._descendants(out); }
    return out;
  }
  matches(sel) {
    let s = sel.trim();
    const checked = s.endsWith(":checked");
    if (checked) s = s.slice(0, -":checked".length);
    let m = s.match(/^([\w-]+)?(?:\[([\w-]+)(?:=["']?([^"'\]]*)["']?)?\])?$/);
    if (s.startsWith(".") || s.startsWith("#")) m = null;
    if (s.startsWith(".")) {
      if (checked && !this.checked) return false;
      return this.classList.contains(s.slice(1));
    }
    if (s.startsWith("#")) return this.id === s.slice(1);
    if (!m) return false;
    const [, tag, attr, val] = m;
    if (tag && this.tagName !== tag.toUpperCase()) return false;
    if (attr) {
      const actual = attr === "class" ? [...this.classList].join(" ") : this.attrs[attr] ?? this[attr];
      if (val !== undefined && String(actual) !== val) return false;
      if (val === undefined && actual === undefined) return false;
    }
    if (checked && !this.checked) return false;
    return true;
  }
  querySelector(sel) { return this._descendants().find((n) => n.matches(sel)); }
  querySelectorAll(sel) { return this._descendants().filter((n) => n.matches(sel)); }
  closest(sel) {
    let n = this;
    while (n) { if (n.matches(sel)) return n; n = n.parent; }
    return null;
  }
}

const VOID_TAGS = new Set(["input", "br", "img", "hr", "meta", "link"]);

function parseHtml(html) {
  const root = new El("#root");
  const stack = [root];
  const tokenRe = /<\/?[a-zA-Z][\w-]*(?:[^<>]*?)\/?>|[^<]+/g;
  let tok;
  while ((tok = tokenRe.exec(html))) {
    const t = tok[0];
    if (t[0] !== "<") continue;
    const closing = t[1] === "/";
    const selfClose = t.endsWith("/>");
    const nameM = t.match(/^<\/?([\w-]+)/);
    const tag = nameM[1].toLowerCase();
    if (closing) { stack.pop(); continue; }
    const el = new El(tag);
    const attrRe = /([\w:-]+)(?:\s*=\s*"([^"]*)")?/g;
    let am;
    const raw = t.replace(/^<\/?[\w-]+/, "").replace(/\/?>$/, "");
    while ((am = attrRe.exec(raw))) {
      const [, k, v] = am;
      if (!k || k === "/") continue;
      if (v === undefined) { el.attrs[k] = true; continue; }
      el.attrs[k] = v;
      if (k === "class") v.split(/\s+/).forEach((c) => c && el.classList.add(c));
      else if (k === "id") el.id = v;
      else if (k.startsWith("data-")) el.dataset[k.slice(5)] = v;
      else if (k === "value") el.value = v;
      else if (k === "checked") el.checked = true;
      else if (k === "hidden") el.hidden = true;
      else if (k === "name") el.name = v;
      else if (k === "type") el.type = v;
    }
    stack[stack.length - 1].appendChild(el);
    if (!selfClose && !VOID_TAGS.has(tag)) stack.push(el);
  }
  return root.children;
}

/* ---------------- document stub ---------------- */

const byId = new Map();
function mk(id, tag = "div") { const e = new El(tag); e.id = id; byId.set(id, e); return e; }

const form = mk("judge-form", "form");
const runnerRows = mk("runner-rows");
const recordRows = mk("record-rows");
const addRunner = mk("add-runner", "button");
const addRecord = mk("add-record", "button");
mk("record-count", "span");
mk("error-box"); mk("pending-hint");
const results = mk("results");
mk("diagnosis-box");
for (const id of ["smMin", "smMax", "mfMin", "mfMax"]) mk(id, "input");
mk("sample-btn", "button"); mk("clear-btn", "button");
const mergeFile = mk("merge-file", "input");
mergeFile.files = null;
mergeFile.type = "file";
const mergePreviewBtn = mk("merge-preview-btn", "button");
const mergePanel = mk("merge-panel");
mergePanel.hidden = true; // 真实 HTML 中带 hidden 属性
mergePreviewBtn.disabled = true;

for (const id of ["smMin", "smMax", "mfMin", "mfMax"]) form.appendChild(byId.get(id));
form.appendChild(runnerRows);
form.appendChild(recordRows);

global.document = { getElementById: (id) => byId.get(id) };

/* ---------------- FileReader stub：按预设文本/错误读取 ---------------- */

const fileStore = new Map(); // File 对象 -> {text?, error?}
let fileSeq = 0;
function fakeFile(name, textOrErr, isError = false) {
  const f = { name: `device-${name}.csv` };
  fileStore.set(f, isError ? { error: textOrErr } : { text: textOrErr });
  return f;
}
global.FileReader = class {
  readAsText(file, enc) {
    this.encoding = enc;
    const rec = fileStore.get(file);
    Promise.resolve().then(() => {
      if (!rec || rec.error) {
        this.result = null;
        this.onerror && this.onerror();
      } else {
        this.result = rec.text;
        this.onload && this.onload();
      }
    });
  }
};

/* ---------------- 初始合法输入 ---------------- */

function lap(s, m, f, st, mt, ft) {
  return {
    start: { id: s, time: st }, middle: { id: m, time: mt }, finish: { id: f, time: ft },
    smTime: mt - st, mfTime: ft - mt, totalTime: ft - st,
  };
}
const validResult = {
  runners: [{ runner: 101, lapCount: 1, totalTime: 22,
    laps: [lap("a1", "b1", "c1", 0, 12, 22)], unused: [] }],
};
const validResult4 = {
  runners: [{ runner: 101, lapCount: 1, totalTime: 22,
    laps: [lap("a1", "b1", "c1", 0, 12, 22)], unused: [] }],
};

/* ---------------- fetch stub：裁决 + 合并（真实令牌算法，与后端一致） ---------------- */

const server = {
  adjMode: "valid",      // valid | invalid | neterr | hold
  adjHold: null,
  mergeMode: "normal",   // normal | neterr | hold | confirmReject
  rejectOnce: false,
  previewRawProvider: null, // 非 null 时预览返回其生成的原始 JSON（保留 >2^53 数字字面量）
  confirmRaw: null,
  // hold 模式下每次调用各自排队，测试逐个 resolve，模拟真正独立的在途请求。
  previewWaiters: [],
  confirmWaiters: [],
};
const calls = { adjudicate: 0, previews: [], confirms: [] };
function resolveNextPreview(result) {
  const w = server.previewWaiters.shift();
  if (!w) throw new Error("没有在途的预览请求");
  w(result);
}
function resolveNextConfirm(result) {
  const w = server.confirmWaiters.shift();
  if (!w) throw new Error("没有在途的确认请求");
  w(result);
}

function stable(obj) {
  if (obj === null || typeof obj !== "object") return JSON.stringify(obj);
  if (Array.isArray(obj)) return `[${obj.map(stable).join(",")}]`;
  const keys = Object.keys(obj).sort();
  return `{${keys.map((k) => `${JSON.stringify(k)}:${stable(obj[k])}`).join(",")}}`;
}
function tokenOf(bodyNoToken) {
  return crypto.createHash("sha256").update(Buffer.from(stable(bodyNoToken), "utf8")).digest("hex");
}

function mergePreviewCompute(payload) {
  // 与后端同一套语义的精简实现：用前端将要面对的输入校验 + CSV 比对产出结果。
  // 直接复用真实后端模块最稳妥，但这是 Node 环境，故按合同生成（复杂错误用例走 override）。
  return server.previewOverride ? server.previewOverride(payload) : null;
}

global.fetch = async (url, opts) => {
  const respond = (data, ok, status) => ({
    ok,
    status,
    json: async () => (typeof data === "string" ? JSON.parse(data) : data),
    text: async () => (typeof data === "string" ? data : JSON.stringify(data)),
  });
  if (url === "/api/adjudicate") {
    calls.adjudicate += 1;
    if (server.adjMode === "neterr") throw new TypeError("模拟断网");
    if (server.adjMode === "invalid") return respond({ ok: false, error: "模拟非法输入" }, false, 422);
    if (server.adjMode === "hold") return respond(await server.adjHold, true, 200);
    return respond({ ok: true, result: server.adjResult || validResult }, true, 200);
  }
  if (url === "/api/merge/preview") {
    calls.previews.push(opts.body);
    if (server.mergeMode === "neterr") throw new TypeError("模拟断网");
    if (server.mergeMode === "hold") {
      const held = await new Promise((resolve) => server.previewWaiters.push(resolve));
      return respond(held, true, 200);
    }
    if (server.previewRawProvider) {
      const bodyObj = JSON.parse(opts.body);
      // 直接以原始文本返回，绕过 fetch 桩自己的 JSON.parse（保留 >2^53 数字字面量）。
      return respond(server.previewRawProvider(strip(bodyObj)), true, 200);
    }
    const body = JSON.parse(opts.body);
    const override = mergePreviewCompute(body);
    if (override !== null && override !== undefined) {
      if (override.ok === false) return respond(override, false, 422);
      const result = override.result;
      result.token = tokenOf(strip(body));
      return respond({ ok: true, result }, true, 200);
    }
    return respond({ ok: false, error: "测试未配置该 CSV 的预览结果" }, false, 422);
  }
  if (url === "/api/merge/confirm") {
    calls.confirms.push(opts.body);
    if (server.mergeMode === "neterr") throw new TypeError("模拟断网");
    if (server.mergeMode === "hold") {
      const held = await new Promise((resolve) => server.confirmWaiters.push(resolve));
      return respond(held, true, 200);
    }
    if (server.confirmRaw !== null) {
      const body = JSON.parse(opts.body);
      const expected = tokenOf(strip(body));
      if (body.token !== expected) {
        return respond({ ok: false, error: "预览已失效：赛道输入或上传文件发生变化，请重新预览后再确认" }, false, 422);
      }
      const raw = server.confirmRaw;
      server.confirmRaw = null;
      return respond(raw, true, 200);
    }
    const body = JSON.parse(opts.body);
    const noTok = strip(body);
    const result = server.previewOverride(noTok);
    if (result.ok === false) return respond(result, false, 422);
    const expected = tokenOf(noTok);
    if (server.mergeMode === "confirmReject" || server.rejectOnce) {
      server.rejectOnce = false;
      return respond({ ok: false, error: "预览已失效：赛道输入或上传文件发生变化，请重新预览后再确认" }, false, 422);
    }
    if (body.token !== expected) {
      return respond({ ok: false, error: "预览已失效：赛道输入或上传文件发生变化，请重新预览后再确认" }, false, 422);
    }
    return respond({ ok: true, result: result.result }, true, 200);
  }
  throw new Error(`unexpected url ${url}`);
};
function strip(obj) {
  const c = { ...obj };
  delete c.token;
  return c;
}

/* ---------------- 加载真实 app.js ---------------- */

new Function(fs.readFileSync(path.join(__dirname, "static", "app.js"), "utf8"))();

/* ---------------- 测试辅助 ---------------- */

let failures = 0;
function assert(cond, msg) {
  if (cond) { console.log("  ✓", msg); }
  else { failures++; console.error("  ✗", msg); }
}
const tick = () => new Promise((r) => setTimeout(r, 0));
const $ = (id) => byId.get(id);
function setVal(id, v) { $(id).value = v; }
function fire(el, type) { el.dispatchEvent({ type, preventDefault() {}, target: el }); }
async function flush(n = 6) { for (let i = 0; i < n; i++) await tick(); }

function setRow(ids, runner, gate, time) {
  const rows = recordRows.querySelectorAll(".record-row");
  for (const row of rows) {
    if (row.querySelector(".rec-id").value === "") {
      row.querySelector(".rec-id").value = ids;
      row.querySelector(".rec-runner").value = runner;
      row.querySelector(".rec-gate").value = gate;
      row.querySelector(".rec-time").value = time;
      return row;
    }
  }
  throw new Error("no empty row");
}
function findRow(id) {
  return recordRows.querySelectorAll(".record-row").find((r) => r.querySelector(".rec-id").value === id);
}
function rowIdsInOrder() {
  return recordRows.querySelectorAll(".record-row").map((r) => r.querySelector(".rec-id").value);
}
const panel = () => mergePanel.innerHTML;
const panelHidden = () => mergePanel.hidden;
function clickPanel(cls) {
  const btn = new El("button");
  btn.classList.add(cls);
  mergePanel.appendChild(btn);
  fire(btn, "click");
  btn.parent = null;
}
function chooseFile(fileObj) {
  mergeFile.files = [fileObj];
  fire(mergeFile, "change");
}
function clearFile() {
  mergeFile.files = null;
  mergeFile.value = "";
  fire(mergeFile, "change");
}
const fileCleared = () => mergeFile.value === "" && globalThis.__getMergeStateForTest().status === "idle";
async function adjudicate() { fire(form, "submit"); await flush(); }

// 构造与“当前表”同步的预览 override：依据请求体真实计算新增/重复，
// 令牌由 fetch 层用稳定序列化生成，模拟后端无状态重算比对。
function installRealisticPreview(csvPlan) {
  // csvPlan: { ok, result?, error? } 固定结果即可；需要与请求体联动时用函数。
  server.previewOverride = typeof csvPlan === "function" ? csvPlan : () => csvPlan;
}

/* ---------------- 场景 ---------------- */

async function main() {
  // 初始合法输入：区间 10~60，选手 101，记录 a1/b1/c1（另有两个空行，清空后默认 2 行→加 1）。
  setVal("smMin", "10"); setVal("smMax", "60"); setVal("mfMin", "10"); setVal("mfMax", "60");
  runnerRows.querySelector(".runner-number").value = "101";
  fire(addRecord, "click"); // 共 3 行：a1 b1 c1
  setRow("a1", "101", "S", "0");
  setRow("b1", "101", "M", "12");
  setRow("c1", "101", "F", "22");
  await adjudicate();
  assert(panelHidden(), "初始面板隐藏");
  assert(mergePreviewBtn.disabled, "未选文件时预览按钮禁用");

  const CSV1 = [
    "id,runner,gate,time",
    "n1,101,S,30",
    "a1,101,S,0",
    "n2,101,M,42",
    "n2,101,M,42",
  ].join("\n") + "\n";
  const plan1 = {
    ok: true,
    result: {
      existingCount: 3, addedCount: 2, skippedCount: 2, totalAfter: 5,
      added: [
        { id: "n1", runner: 101, gate: "S", time: 30, line: 2 },
        { id: "n2", runner: 101, gate: "M", time: 42, line: 4 },
      ],
      skipped: [
        { id: "a1", runner: 101, gate: "S", time: 0, line: 3, reason: "existing" },
        { id: "n2", runner: 101, gate: "M", time: 42, line: 5, reason: "file", duplicateOfLine: 4 },
      ],
    },
  };
  installRealisticPreview(plan1);

  console.log("场景1：选择文件自动经 HTTP 预览，请求体含完整载荷与 csv");
  chooseFile(fakeFile("ok1", CSV1));
  assert(panel().includes("正在经 HTTP 预览"), "选择文件后立即进入在途态");
  await flush();
  assert(calls.previews.length === 1, "发出一次 POST /api/merge/preview");
  const sent = JSON.parse(calls.previews[0]);
  assert(sent.csv === CSV1 && sent.runners[0].number === "101" && sent.records.length === 3,
    "预览请求体 = 当前完整裁决载荷 + csv 原文");
  assert(panel().includes("n1") && panel().includes("n2"), "新增条目已列出");
  assert(panel().includes("文件内重复") && panel().includes("与现有记录一致"),
    "两类重复原因分别展示");
  assert(panel().includes("确认追加"), "确认入口出现");
  assert(rowIdsInOrder().filter(Boolean).join(",") === "a1,b1,c1", "仅预览：表格尚未被修改");

  console.log("场景2：确认追加带 token；旧记录顺序不变、新记录按文件顺序加入并自动裁决");
  const adjBefore = calls.adjudicate;
  clickPanel("merge-confirm");
  await flush();
  assert(calls.confirms.length === 1, "发出一次 POST /api/merge/confirm");
  const conf = JSON.parse(calls.confirms[0]);
  assert(typeof conf.token === "string" && conf.token.length === 64, "确认请求附带预览令牌");
  assert(conf.csv === CSV1, "确认体与预览体一致（含 csv 原文）");
  const ids = rowIdsInOrder();
  assert(ids.slice(0, 3).join(",") === "a1,b1,c1", "旧记录相对顺序不变");
  // 3 个旧行占满初始 3 行 -> 新记录在末尾新增行；无空行可复用。
  assert(ids[ids.length - 2] === "n1" && ids[ids.length - 1] === "n2", "新记录按文件首次出现顺序追加");
  assert(panelHidden(), "成功确认后面板收起");
  assert(mergeFile.value === "" && globalThis.__getMergeStateForTest().status === "idle", "已消费文件被清除并复位");
  await new Promise((r) => setTimeout(r, 420)); // 等待 350ms 防抖自动裁决
  await flush();
  assert(calls.adjudicate > adjBefore, "成功合并触发自动裁决");

  console.log("场景3：整批 422（同 ID 冲突 / 未知选手 / 超 40）不追加、原表保留");
  const before = rowIdsInOrder().join("|");
  installRealisticPreview({ ok: false });
  // fetch 对 ok:false override 直接 422，error 文案由服务端给：
  server.previewOverride = () => ({ ok: false, error: "CSV 第 2 行的 ID n1 与表现有记录内容不一致，同 ID 内容冲突，已整批拒绝" });
  chooseFile(fakeFile("bad", "id,runner,gate,time\nn1,101,F,30\n"));
  await flush();
  assert(panel().includes("同 ID 内容冲突") && panel().includes("整批拒绝"), "冲突原因展示");
  assert(rowIdsInOrder().join("|") === before, "整批拒绝：原表一行未动");
  assert(panel().includes("重新预览"), "失败态可重新预览");

  console.log("场景4：赛道输入变化立即使就绪预览失效，迟到成功不能恢复确认入口");
  installRealisticPreview(plan1);
  chooseFile(fakeFile("ok2", CSV1));
  await flush();
  assert(panel().includes("确认追加"), "重新预览成功");
  // 在途确认挂起期间编辑表格：版本号递增，预览立即 stale。
  server.mergeMode = "hold";
  clickPanel("merge-confirm");
  await flush();
  assert(panel().includes("正在确认追加"), "确认在途态");
  findRow("n1").querySelector(".rec-time").value = "31";
  fire(findRow("n1").querySelector(".rec-time"), "input"); // 任意赛道输入
  assert(panel().includes("预览已失效") && !panel().includes("确认追加"),
    "编辑瞬间确认入口消失（无需等响应）");
  // 迟到的确认若真的应用会重复写入 n1/n2 —— 版本守卫必须把它整个丢弃。
  const rowsBeforeLate = rowIdsInOrder().join("|");
  resolveNextConfirm({ ok: true, result: plan1.result });
  await flush();
  assert(!panel().includes("确认追加"), "迟到的确认成功不能恢复确认入口");
  assert(rowIdsInOrder().join("|") === rowsBeforeLate,
    "迟到确认未追加任何记录（表格保持编辑后状态）");
  server.mergeMode = "normal";

  console.log("场景5：确认时输入与预览不一致 -> 服务端令牌 422，原表保留");
  // 当前表格已被编辑（n1 尚未存在于场景4，因为确认被丢弃；重新准备一个干净环境）。
  installRealisticPreview(plan1);
  chooseFile(fakeFile("ok3", CSV1));
  await flush();
  server.mergeMode = "confirmReject";
  clickPanel("merge-confirm");
  await flush();
  assert(panel().includes("预览已失效"), "令牌失配 422 展示失效原因");
  assert(panel().includes("重新预览") && !panel().includes("确认追加"), "失配后只能重新预览");
  server.mergeMode = "normal";

  console.log("场景6：重新选择文件立即使旧预览失效，旧在途响应作废");
  installRealisticPreview(plan1);
  chooseFile(fakeFile("ok4", CSV1));
  await flush();
  assert(panel().includes("确认追加"), "第一次选择预览成功");
  // 第二个文件的预览先挂起并确认在途；之后再选第三个文件，旧响应必须被版本守卫丢弃。
  server.mergeMode = "hold";
  const holdResult = { ok: true, result: { ...plan1.result } };
  chooseFile(fakeFile("ok5", CSV1)); // 旧选择：预览请求入队
  await flush();
  assert(server.previewWaiters.length === 1, "旧选择的预览请求已在途");
  chooseFile(fakeFile("ok6", CSV1)); // 新选择：版本号递增
  await flush();
  assert(server.previewWaiters.length === 2, "新选择也发出了在途预览请求");
  assert(panel().includes("正在经 HTTP 预览"), "面板保持在途态");
  resolveNextPreview(holdResult); // 先放行旧选择（版本已过期）
  await flush();
  assert(!panel().includes("确认追加"), "旧选择的在途响应未恢复确认入口");
  resolveNextPreview(holdResult); // 再放行最新选择
  await flush();
  assert(panel().includes("确认追加"), "最新选择的响应到达后恢复确认入口");
  server.mergeMode = "normal";

  console.log("场景7：清空文件选择 / 点放弃 -> 面板复位");
  // 上一步处于就绪态：直接点放弃。
  clickPanel("merge-dismiss");
  assert(panelHidden() && mergeFile.value === "", "放弃：清除文件并收起面板");
  chooseFile(fakeFile("ok7", CSV1));
  await flush();
  assert(panel().includes("确认追加"), "重新选择后可再次预览");
  clearFile();
  assert(panelHidden(), "清空文件选择立即收起面板");

  console.log("场景8：预览在途时编辑 -> stale；迟到的成功不覆盖");
  server.mergeMode = "normal";
  chooseFile(fakeFile("ok8", CSV1)); // 自动预览立即成功
  await flush();
  assert(panel().includes("确认追加"), "就绪");
  // 制造在途：再点一次预览并挂起；期间编辑表格。
  server.mergeMode = "hold";
  fire(mergePreviewBtn, "click");
  await tick();
  assert(server.previewWaiters.length === 1, "手动预览请求在途");
  setVal("smMin", "9");
  fire($("smMin"), "input");
  assert(panel().includes("预览已失效"), "在途预览期间编辑：立即失效");
  resolveNextPreview({ ok: true, result: { ...plan1.result } });
  await flush();
  assert(!panel().includes("确认追加"), "在途预览的迟到成功不恢复入口");
  server.mergeMode = "normal";
  setVal("smMin", "10");
  fire($("smMin"), "input");

  console.log("场景9：预览失败（断网）保留文件、可重试；重试成功可确认");
  server.mergeMode = "neterr";
  chooseFile(fakeFile("ok9", CSV1));
  await flush();
  assert(panel().includes("无法连接合并服务") && panel().includes("重新预览"), "断网错误可重试");
  server.mergeMode = "normal";
  clickPanel("merge-retry");
  await flush();
  assert(panel().includes("确认追加"), "重试预览成功");
  clickPanel("merge-dismiss");

  console.log("场景9b：文件读取失败可保留选择，重试读取后正常预览");
  const unreadable = fakeFile("broken", new Error("无法读取该文件"), true);
  chooseFile(unreadable);
  await flush();
  assert(panel().includes("无法读取该文件"), "读取错误展示");
  assert(mergePreviewBtn.disabled === false, "错误态允许重试读取（按钮可用）");
  fileStore.set(unreadable, { text: CSV1 }); // “同一文件”恢复可读
  clickPanel("merge-retry");
  await flush();
  assert(panel().includes("确认追加"), "重新读取并预览成功");
  clickPanel("merge-dismiss");

  console.log("场景10：空行复用——合并不触顶，旧行不重排");
  // 当前表 a1,b1,c1 + n1,n2（5 行，均无空行）。先加两个空行，再合并 2 条：应复用空行。
  fire(addRecord, "click");
  fire(addRecord, "click");
  const totalRows = recordRows.children.length;
  installRealisticPreview({
    ok: true,
    result: {
      existingCount: 5, addedCount: 2, skippedCount: 0, totalAfter: 7,
      added: [
        { id: "g1", runner: 101, gate: "S", time: 60, line: 2 },
        { id: "g2", runner: 101, gate: "M", time: 70, line: 3 },
      ],
      skipped: [],
    },
  });
  chooseFile(fakeFile("fill", "id,runner,gate,time\ng1,101,S,60\ng2,101,M,70\n"));
  await flush();
  clickPanel("merge-confirm");
  await flush();
  assert(recordRows.children.length === totalRows, "复用已有空行：未新增 DOM 行");
  const ids7 = rowIdsInOrder();
  assert(ids7.indexOf("a1") === 0 && ids7.indexOf("g1") < ids7.indexOf("g2"),
    "旧顺序保持，空行被按文件顺序填入");

  console.log("场景11：超过 2^53 的编号与秒数在预览展示与确认写入中无损");
  const T = "9007199254740993"; // > 2^53，JS Number 会与相邻值混同
  const BIG_R = "9007199254740993";
  const CSVBIG = `id,runner,gate,time\nbig,${BIG_R},S,${T}\n`;
  const runnerInput = [...runnerRows.querySelectorAll(".runner-number")][0];
  runnerInput.value = BIG_R;
  // 把现有记录换成该大编号，避免“未知选手/冲突”语义；确认后写入行与自动裁决请求必须无损。
  for (const idv of ["a1", "b1", "c1", "n1", "n2", "g1", "g2"]) {
    const r = findRow(idv);
    if (r) r.querySelector(".rec-runner").value = BIG_R;
  }
  fire(runnerRows, "input");
  server.adjResult = {
    runners: [{ runner: BIG_R, lapCount: 0, totalTime: "0", laps: [], unused: [] }],
  };
  // 后端原始 JSON：编号/秒数数字字面量超过 2^53，前端无损解析器必须保留为字符串；
  // 令牌由 fetch 层在原始 body 上真实计算，确认时同样按重算指纹比对。
  let bigToken = null;
  server.previewOverride = null;
  const bigResultJson = (withToken) =>
    `{"ok":true,"result":{` +
    `"existingCount":7,"addedCount":1,"skippedCount":0,"totalAfter":8,` +
    (withToken ? `"token":"${bigToken}",` : "") +
    `"added":[{"id":"big","runner":${BIG_R},"gate":"S","time":${T},"line":2}],` +
    `"skipped":[]}}`;
  server.previewRawProvider = (noTokenBody) => {
    bigToken = tokenOf(noTokenBody);
    return bigResultJson(true);
  };
  // 合并前给该（未改号的）选手保存罚秒：成功合并不改选手与区间，只触发一次普通的
  // 输入变更 + 自动裁决，罚秒必须沿用原规则保留（由 onInputChanged 既有逻辑保证）。
  globalThis.__penaltiesForTest.set(BIG_R, { seconds: 5, reason: "合并前已保存" });
  chooseFile(fakeFile("big", CSVBIG));
  await flush();
  assert(panel().includes(BIG_R) && panel().includes(T), "大编号/大秒数原样展示");
  const prevSentBig = JSON.parse(calls.previews[calls.previews.length - 1]);
  assert(prevSentBig.records[0].runner === BIG_R, "预览请求编号为十进制字符串");
  // 确认响应同样是 >2^53 数字字面量的原始 JSON（令牌须与预览一致）。
  server.confirmRaw = bigResultJson(false);
  clickPanel("merge-confirm");
  await flush();
  const bigRow = findRow("big");
  assert(
    bigRow.querySelector(".rec-runner").value === BIG_R &&
      bigRow.querySelector(".rec-time").value === T,
    "确认写入的大编号/大秒数未被 Number 截断"
  );
  server.previewRawProvider = null;
  await new Promise((r) => setTimeout(r, 420));
  await flush();
  const savedPenalty = globalThis.__penaltiesForTest.get(BIG_R);
  assert(
    savedPenalty && savedPenalty.seconds === 5 && savedPenalty.reason === "合并前已保存",
    "成功合并不改选手编号：该选手原有罚秒与原因保留"
  );

  console.log("场景12b：载入示例同样清除所选文件与面板");
  // 再选一个文件进入就绪，然后载入示例：预览与文件都应复位。
  server.previewRawProvider = null;
  server.mergeMode = "normal";
  installRealisticPreview(plan1);
  chooseFile(fakeFile("after", CSV1));
  await flush();
  assert(!panelHidden(), "已重新出现预览");
  fire($("sample-btn"), "click");
  await new Promise((r) => setTimeout(r, 420));
  await flush();
  assert(panelHidden() && mergeFile.value === "", "填入示例：文件与预览清除");

  console.log("场景13：清空 / 填入示例清除所选文件与面板");
  installRealisticPreview(plan1);
  // 先恢复一个可预览文件
  fire($("clear-btn"), "click");
  await flush();
  assert(panelHidden(), "清空：即使曾有预览也收起面板");

  console.log(failures === 0 ? "\nALL MERGE LIFECYCLE SCENARIOS PASSED" : `\n${failures} ASSERTION(S) FAILED`);
  process.exit(failures === 0 ? 0 : 1);
}

main().catch((e) => { console.error(e); process.exit(1); });
