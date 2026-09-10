import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import { test } from "node:test";

function setup() {
  let serial = 0;
  const timers = new Map(), sockets = [], jobs = [], statuses = [];
  class Socket {
    constructor(url) { this.url = url; this.sent = []; sockets.push(this); }
    send(message) { this.sent.push(JSON.parse(message)); }
    close(code = 1000) { this.closed = true; this.onclose?.({ code }); }
    open() { this.onopen?.(); }
    message(event) { this.onmessage?.({ data: JSON.stringify(event) }); }
  }
  const context = { window: {}, URL, WebSocket: Socket,
    setTimeout: (callback, delay) => { timers.set(++serial, { callback, delay }); return serial; },
    clearTimeout: (id) => timers.delete(id) };
  vm.runInNewContext(fs.readFileSync(new URL("../web_app/job-events.js", import.meta.url), "utf8"), context);
  const events = new context.window.AvagentJobEvents({ base: "https://api.example", token: "private-test-value",
    jobId: "a".repeat(32), onJob: (job) => jobs.push(job), onStatus: (status) => statuses.push(status) });
  function tick(delay) {
    const item = [...timers].find(([, timer]) => timer.delay === delay);
    assert.ok(item, `No timer at ${delay}`);
    timers.delete(item[0]); item[1].callback();
  }
  const job = (status) => ({ type: "job", job: { id: "a".repeat(32), status } });
  return { events, timers, sockets, jobs, statuses, tick, job };
}

test("auth frame only, one connection for updates and heartbeat, finish closes", () => {
  const s = setup(), ws = s.sockets[0];
  assert.equal(ws.url, `wss://api.example/api/jobs/${"a".repeat(32)}/events`);
  assert.ok(!ws.url.includes("private-test-value"));
  ws.open();
  assert.deepEqual(ws.sent[0], { type: "authenticate", token: "private-test-value" });
  ws.message(s.job("running"));
  ws.message({ type: "heartbeat" });
  assert.deepEqual(ws.sent[1], { type: "pong" });
  ws.message(s.job("completed"));
  assert.equal(s.jobs.at(-1).status, "completed");
  assert.equal(s.sockets.length, 1);
  assert.equal(s.timers.size, 0);
  assert.equal(s.events.token, "");
});

test("bounded backoff does not start HTTP polling or reconnect forever", () => {
  const s = setup();
  for (const delay of [2000, 5000, 15000, 30000, 60000]) {
    s.sockets.at(-1).close(1006); s.tick(delay);
  }
  s.sockets.at(-1).close(1006);
  assert.equal(s.sockets.length, 6);
  assert.equal(s.statuses.at(-1), "disconnected");
  assert.equal(s.timers.size, 0);
});

test("reconnect accepts terminal snapshot, ignores stale connection", () => {
  const s = setup(), old = s.sockets[0];
  old.close(1006); s.tick(2000);
  old.message(s.job("running"));
  assert.equal(s.jobs.length, 0);
  s.sockets[1].open(); s.sockets[1].message(s.job("failed"));
  assert.equal(s.jobs.at(-1).status, "failed");
  assert.equal(s.timers.size, 0);
});

test("bad credentials do not trigger repeated authentication requests", () => {
  const s = setup();
  s.sockets[0].close(4401);
  assert.equal(s.statuses.at(-1), "denied");
  assert.equal(s.timers.size, 0);
});

test("silent/broken connections time out, manual close cancels pending recovery", () => {
  const s = setup();
  s.tick(15000);
  assert.equal(s.statuses.at(-1), "reconnecting");
  s.events.close();
  assert.equal(s.timers.size, 0);
});

test("events from a different task never update the selected job", () => {
  const s = setup();
  s.sockets[0].message({ type: "job", job: { id: "other", status: "completed" } });
  assert.equal(s.jobs.length, 0);
});
