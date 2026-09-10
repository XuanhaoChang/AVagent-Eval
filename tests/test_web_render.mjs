import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import { test } from "node:test";

// A small DOM double keeps result-state regressions runnable without a browser.
function setup() {
  class Element {
    constructor() { this.textContent = ""; this.children = []; this.hidden = false; this.listeners = {}; }
    append(...children) { this.children.push(...children); }
    replaceChildren(...children) { this.children = children; }
    addEventListener(name, callback) { this.listeners[name] = callback; }
    querySelector(selector) { return this.parts[selector]; }
  }
  const html = fs.readFileSync(new URL("../web_app/index.html", import.meta.url), "utf8");
  const elements = Object.fromEntries([...html.matchAll(/id="([^"]+)"/g)].map((match) => [match[1], new Element()]));
  elements["empty-results"].parts = { h3: new Element(), p: new Element() };
  const requests = [];
  const context = vm.createContext({
    document: { getElementById: (id) => elements[id], createElement: () => new Element(), createTextNode: (text) => text },
    window: { addEventListener() {} }, location: { hostname: "preview.example", protocol: "https:" },
    URL, AbortController, setTimeout, clearTimeout,
    fetch: async (url, options) => {
      requests.push({ url, options });
      return { ok: true, json: async () => ({ service: "avagent-eval", configured: true }) };
    },
  });
  vm.runInContext(fs.readFileSync(new URL("../web_app/app.js", import.meta.url), "utf8"), context);
  function render(job) {
    context.fixtureJob = job;
    vm.runInContext("state.connected = state.ready = true; state.job = fixtureJob; renderJob(fixtureJob);", context);
  }
  return { elements, requests, context, render };
}

function result(checks = [], issues = []) {
  return { id: "a".repeat(32), status: "completed", report: { checks, issues, metrics: { issue_count: issues.length, elapsed_sec: 12 } } };
}

test("idle screen has no history, diagnostic dashboard or placeholder metrics", () => {
  const s = setup(); s.render(null);
  assert.equal(s.elements["result-metrics"].hidden, true);
  assert.equal(s.elements["job-updates"].hidden, true);
  assert.equal(s.elements["empty-results"].parts.h3.textContent, "等待评测");
  for (const id of ["history-list", "check-list", "model-info", "metric-coverage", "job-id"]) assert.equal(s.elements[id], undefined);
});

test("healthy result shows findings and download without internal-state labels", () => {
  const s = setup();
  s.render(result([{ check_name: "av_lip_sync", decision: "not_detected", execution_status: "ok" }]));
  assert.equal(s.elements["report-notice"].hidden, true);
  assert.equal(s.elements["no-issues"].textContent, "本次未检测到问题。");
  assert.equal(s.elements["result-metrics"].hidden, false);
  assert.equal(s.elements.download.disabled, false);
});

test("incomplete checks produce contextual reasons, not a clean result", () => {
  const s = setup();
  s.render(result([
    { check_name: "av_lip_sync", decision: "not_evaluable", execution_status: "failed" },
    { check_name: "reference_subject_identity", decision: "not_evaluable", execution_status: "not_applicable" },
    { check_name: "voice_characteristics", decision: "not_evaluable", execution_status: "ok" },
  ]));
  assert.equal(s.elements["report-notice"].hidden, false);
  const note = s.elements["report-notice"].textContent;
  assert.match(note, /音画与唇音同步：检测未完成/);
  assert.match(note, /参考主体一致性：当前输入不适用/);
  assert.match(note, /声音特征：现有信息不足以判断/);
  assert.doesNotMatch(note, /not_evaluable|无法评估|执行状态|证据级别/);
  assert.match(s.elements["no-issues"].textContent, /部分项目未给出检测结论/);
});

test("findings survive partial checks and stale warnings clear on the next result", () => {
  const s = setup();
  s.render(result([{ decision: "not_evaluable", execution_status: "failed" }], [{ 问题类型: "文字质量问题", 问题说明: "字幕拼写错误" }]));
  assert.equal(s.elements["issue-list"].children.length, 1);
  assert.equal(s.elements["no-issues"].hidden, true);
  s.render(result([{ decision: "not_detected", execution_status: "ok" }]));
  assert.equal(s.elements["report-notice"].hidden, true);
  assert.equal(s.elements["report-notice"].textContent, "");
  assert.equal(s.elements["issue-list"].children.length, 0);
});

test("running evaluation prevents replacing the current task; failure enables retry", () => {
  const s = setup();
  s.render({ id: "a".repeat(32), status: "running" });
  assert.equal(s.elements.run.disabled, true);
  assert.equal(s.elements["input-fields"].disabled, true);
  assert.equal(s.elements.cancel.hidden, false);
  s.render({ id: "a".repeat(32), status: "failed", error: "Internal stack trace: /server/private/path" });
  assert.equal(s.elements.run.disabled, false);
  assert.equal(s.elements["input-fields"].disabled, false);
  assert.equal(s.elements.download.disabled, true);
  assert.equal(s.elements["report"].hidden, true);
  assert.equal(s.elements["empty-results"].parts.h3.textContent, "评测未完成");
  assert.doesNotMatch(s.elements["empty-results"].parts.p.textContent, /Internal|private/);
});

test("automatic connection fetches health only, never the shared job list", async () => {
  const s = setup();
  // In the preview fixture there is no configured URL; replace safeBase only for this request double.
  vm.runInContext('safeBase = () => "https://api.example";', s.context);
  await vm.runInContext("connectBackend()", s.context);
  assert.deepEqual(s.requests.map((request) => request.url), ["https://api.example/api/health"]);
  assert.equal(s.elements.run.disabled, false);
  assert.equal(s.elements["connection-status"].textContent, "服务可用");
});

test("submission and completion fetch only the current job; export still works", async () => {
  const s = setup(), calls = [];
  let subscription;
  s.context.window.AvagentJobEvents = class {
    constructor(options) { subscription = options; }
    close() {}
  };
  s.context.FormData = class { append() {} };
  const completed = result([{ check_name: "av_lip_sync", decision: "not_detected", execution_status: "ok" }]);
  const queued = { id: completed.id, status: "queued" };
  s.context.fetch = async (url, options) => {
    calls.push([options.method || "GET", url]);
    return { ok: true, json: async () => options.method === "POST" ? queued : completed, blob: async () => new Blob(["fixture report"]) };
  };
  s.elements["video-input"].files = [{ size: 32 }];
  s.elements.references.files = [];
  s.elements.prompt.value = "synthetic fixture";
  vm.runInContext('state.connected = state.ready = true; state.base = "https://api.example";', s.context);
  await s.elements["evaluation-form"].listeners.submit({ preventDefault() {} });
  assert.equal(s.elements.run.disabled, true);
  assert.equal(subscription.jobId, completed.id);
  await subscription.onJob({ id: completed.id, status: "completed" });
  assert.equal(s.elements.run.disabled, false);
  assert.equal(s.elements.download.disabled, false);
  assert.deepEqual(calls, [["POST", "https://api.example/api/jobs"], ["GET", `https://api.example/api/jobs/${completed.id}`]]);
  // Check the download handler with a minimal anchor double.
  s.context.document.createElement = () => ({ click() {}, set href(value) {}, set download(value) {} });
  await s.elements.download.listeners.click();
  assert.deepEqual(calls.at(-1), ["GET", `https://api.example/api/jobs/${completed.id}/report.jsonl`]);
});
