/* One authenticated subscription; no HTTP polling or persistent credentials. */
"use strict";

window.AvagentJobEvents = class {
  constructor({ base, token = "", publicAccess = false, jobId, onJob, onStatus }) {
    const url = new URL(base + `/api/jobs/${encodeURIComponent(jobId)}/events`);
    url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
    this.url = url.href;
    this.token = token;
    this.publicAccess = publicAccess;
    this.jobId = jobId;
    this.onJob = onJob;
    this.onStatus = onStatus;
    this.retries = 0;
    this.stopped = false;
    this.socket = null;
    this.timer = null;
    this.watchdog = null;
    this.connect();
  }

  connect() {
    if (this.stopped) return;
    this.onStatus("connecting");
    let socket;
    try { socket = new WebSocket(this.url); }
    catch { this.retry(); return; }
    this.socket = socket;
    const current = () => !this.stopped && this.socket === socket;
    const armWatchdog = (milliseconds) => {
      clearTimeout(this.watchdog);
      this.watchdog = setTimeout(() => {
        if (!current()) return;
        this.socket = null;
        socket.close();
        this.retry();
      }, milliseconds);
    };
    armWatchdog(15000);
    socket.onopen = () => {
      if (current()) socket.send(JSON.stringify(this.publicAccess ? { type: "subscribe" } :
        { type: "authenticate", token: this.token }));
    };
    socket.onmessage = ({ data }) => {
      if (!current()) return;
      let event;
      try { event = JSON.parse(data); } catch { socket.close(1002); return; }
      if (!event || typeof event !== "object") { socket.close(1002); return; }
      armWatchdog(75000);
      if (event.type === "heartbeat") {
        socket.send(JSON.stringify({ type: "pong" }));
      } else if (event.type === "job" && event.job?.id === this.jobId &&
                 ["queued", "running", "completed", "failed", "cancelled"].includes(event.job.status)) {
        this.onStatus("connected");
        const finished = ["completed", "failed", "cancelled"].includes(event.job.status);
        if (finished) this.close();
        this.onJob(event.job);
      } else { socket.close(1002); }
    };
    socket.onerror = () => { /* onclose or watchdog handles a bounded retry. */ };
    socket.onclose = ({ code }) => {
      if (!current()) return;
      this.socket = null;
      clearTimeout(this.watchdog);
      if ([4400, 4401, 4403, 4404].includes(code)) {
        this.close();
        this.onStatus(code === 4404 ? "missing" : "denied");
      } else { this.retry(); }
    };
  }

  retry() {
    if (this.stopped) return;
    const delays = [2000, 5000, 15000, 30000, 60000];
    if (this.retries >= delays.length) {
      this.close();
      this.onStatus("disconnected");
      return;
    }
    this.onStatus("reconnecting");
    this.timer = setTimeout(() => this.connect(), delays[this.retries++]);
  }

  close() {
    this.stopped = true;
    this.token = "";
    clearTimeout(this.timer);
    clearTimeout(this.watchdog);
    if (this.socket) this.socket.close();
    this.socket = null;
  }
};
