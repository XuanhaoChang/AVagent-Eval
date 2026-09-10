/* The browser submits jobs to the real server-side AVAgent CLI. No demo results. */
"use strict";

const $ = (id) => document.getElementById(id);
const state = { base: "", token: "", connected: false, ready: false, job: null,
  events: null, generation: 0, previewJobId: null, videoURL: null, referenceURLs: [], submitting: false,
  maxUploadBytes: 128 * 1048576 };
const jobLabels = { queued: "队列等待中", running: "avagent 正在评测", completed: "评测完成", failed: "评测失败", cancelled: "已停止" };
const checkLabels = {
  reference_subject_identity: "参考主体一致性", entity_count_spatial_composition: "实体数量与空间构图",
  remaining_hard_instruction_compliance: "硬性指令遵循", motion_physics_continuity: "动作、物理与连续性",
  dialogue_speaker_binding: "台词与说话人绑定", voice_characteristics: "声音特征",
  subtitle_text_logo_watermark: "字幕、文字与水印", av_lip_sync: "音画与唇音同步",
  visual_quality_temporal_artifacts: "画面质量与时序伪影", audio_quality_noise_artifacts: "音频质量与噪声"
};
const decisionLabels = { detected: "检测到问题", not_detected: "未检测到", not_evaluable: "无法评估" };
const terminal = new Set(["completed", "failed", "cancelled"]);
const configuredBase = window.AVAGENT_CONFIG?.apiBase || "";
const localHost = ["localhost", "127.0.0.1", "[::1]"].includes(location.hostname);
$("api-base").value = configuredBase || (localHost && location.protocol !== "file:" ? location.origin : "");

function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}
function notify(message = "") {
  $("notice").textContent = message;
  $("notice").hidden = !message;
}
function status(id, text, kind = "") {
  $(id).textContent = text;
  $(id).className = `status ${kind}`;
}
function valueText(value) {
  if (value === null || value === undefined || value === "") return "—";
  return typeof value === "object" ? JSON.stringify(value) : String(value);
}
function setControls() {
  $("run").disabled = !state.connected || !state.ready || state.submitting;
  $("input-fields").disabled = state.submitting;
  $("refresh-history").disabled = !state.connected;
  $("refresh-job").disabled = !state.connected || !state.job;
  $("submit-hint").textContent = state.submitting ? "正在上传并创建任务…" :
    state.ready ? "由服务器运行 avagent" : state.connected ? "后端配置尚未完成" : "请先连接后端";
}
function safeBase(input) {
  const url = new URL(input);
  const loopback = ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname);
  if (url.username || url.password || url.search || url.hash) throw new Error("后端地址不能包含用户名、密码、查询参数或片段。");
  if (url.protocol !== "https:" && !(url.protocol === "http:" && loopback && location.protocol !== "https:")) {
    throw new Error("公网后端必须使用 HTTPS；HTTP 只允许用于本地预览。");
  }
  return url.href.replace(/\/+$/, "");
}
async function api(path, options = {}, snapshot = state) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), options.body ? 180000 : 20000);
  try {
    const response = await fetch(snapshot.base + path, { ...options, signal: controller.signal,
      cache: "no-store", credentials: "omit", redirect: "error",
      headers: { Authorization: `Bearer ${snapshot.token}`, ...(options.headers || {}) } });
    if (!response.ok) {
      let detail;
      try { detail = (await response.json()).detail; } catch { /* A proxy may return HTML. */ }
      throw new Error(typeof detail === "string" ? detail : `服务器返回 HTTP ${response.status}`);
    }
    return response;
  } catch (error) {
    if (error.name === "AbortError") throw new Error("请求超时。上传超时时任务可能已创建，请刷新运行记录确认后再提交。");
    if (error instanceof TypeError) throw new Error("无法连接后端，请检查 HTTPS 地址、网络和服务器的跨域配置。");
    throw error;
  } finally { clearTimeout(timer); }
}

$("connection-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  stopEvents();
  const generation = ++state.generation;
  state.connected = false;
  state.ready = false;
  $("connect").disabled = true;
  $("cancel").hidden = true;
  $("download").disabled = true;
  setControls();
  notify();
  try {
    state.base = safeBase($("api-base").value.trim());
    state.token = $("access-token").value.trim();
    if (!state.token) throw new Error("请输入网站访问令牌。");
    const snapshot = { base: state.base, token: state.token };
    const health = await (await api("/api/health", {}, snapshot)).json();
    if (generation !== state.generation) return;
    if (health.service !== "avagent-eval") throw new Error("该地址不是 avagent-eval 后端。");
    state.connected = true;
    state.ready = health.configured;
    if (Number.isFinite(health.limits?.max_upload_bytes) && health.limits.max_upload_bytes > 0) {
      state.maxUploadBytes = health.limits.max_upload_bytes;
      $("upload-limit").textContent = Math.floor(state.maxUploadBytes / 1048576);
    }
    status("connection-status", state.ready ? "后端已连接" : "后端待配置", state.ready ? "good" : "busy");
    $("connection-summary").textContent = state.ready ? new URL(state.base).host : "已连接，评测配置未完成";
    $("connection-panel").open = !state.ready;
    if (!state.ready) notify(`服务器仍缺少配置：${health.missing.join("、")}。请管理员配置后重新连接。`);
    state.job = null;
    renderJob(null);
    await refreshHistory();
  } catch (error) {
    if (generation !== state.generation) return;
    status("connection-status", "连接失败", "bad");
    notify(error.message);
  } finally { $("connect").disabled = false; setControls(); }
});

$("video-input").addEventListener("change", () => {
  if (state.videoURL) URL.revokeObjectURL(state.videoURL);
  state.previewJobId = null;
  const file = $("video-input").files[0];
  $("video-figure").hidden = !file;
  if (file) {
    state.videoURL = URL.createObjectURL(file);
    $("video-preview").src = state.videoURL;
    $("video-caption").textContent = `${file.name} · ${(file.size / 1048576).toFixed(1)} MiB · 本地预览`;
  } else { $("video-preview").removeAttribute("src"); $("video-preview").load(); }
});
$("references").addEventListener("change", () => {
  state.referenceURLs.forEach((url) => URL.revokeObjectURL(url));
  state.referenceURLs = [];
  $("reference-previews").replaceChildren();
  const files = Array.from($("references").files);
  if (files.length > 4) { notify("参考图最多 4 张，请重新选择。"); $("references").value = ""; return; }
  for (const file of files) {
    const url = URL.createObjectURL(file);
    state.referenceURLs.push(url);
    const preview = node("img"); preview.src = url; preview.alt = file.name;
    $("reference-previews").append(preview);
  }
});

$("evaluation-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!state.ready || state.submitting) return;
  const video = $("video-input").files[0];
  const refs = Array.from($("references").files);
  if (!video) { notify("请选择生成视频。"); return; }
  if (video.size + refs.reduce((sum, file) => sum + file.size, 0) >= state.maxUploadBytes) {
    notify(`上传总大小须小于 ${Math.floor(state.maxUploadBytes / 1048576)} MiB。`); return;
  }
  if (refs.some((file) => file.size > 10 * 1048576)) { notify("参考图每张不能超过 10 MiB。"); return; }
  if ($("prompt").value.trim().length === 0) { notify("请填写原始生成 prompt。"); return; }
  const form = new FormData();
  form.append("prompt", $("prompt").value.trim()); form.append("video", video);
  refs.forEach((file) => form.append("references", file));
  state.submitting = true;
  $("connect").disabled = true;
  setControls(); notify();
  try {
    const job = await (await api("/api/jobs", { method: "POST", body: form })).json();
    state.previewJobId = job.id;
    state.job = job;
    renderJob(job);
    beginEvents(job.id);
    await refreshHistory();
  } catch (error) { notify(error.message); }
  finally { state.submitting = false; $("connect").disabled = false; setControls(); }
});

function renderJob(job) {
  $("refresh-job").disabled = !state.connected || !job;
  const report = job?.report;
  $("report").hidden = !report;
  $("empty-results").hidden = !!report;
  $("job-id").textContent = job ? `RUN ${job.id.slice(0, 10)}` : "—";
  $("job-id").title = job?.id || "";
  const active = job && !terminal.has(job.status);
  status("job-status", job ? jobLabels[job.status] || job.status : "等待提交",
    active ? "busy" : job?.status === "completed" ? "good" : job?.status === "failed" ? "bad" : "");
  $("cancel").hidden = !active;
  $("download").disabled = !report;
  $("metric-issues").textContent = report ? report.metrics.issue_count : "—";
  $("metric-coverage").replaceChildren(document.createTextNode(report ? report.metrics.evaluable_checks : "—"), node("small", " / 10"));
  const elapsed = report?.metrics.elapsed_sec ?? (job?.started_at ? (job.finished_at || Date.now() / 1000) - job.started_at : null);
  $("metric-time").replaceChildren(document.createTextNode(Number.isFinite(elapsed) ? Math.max(0, elapsed).toFixed(1) : "—"), node("small", " s"));
  const empty = $("empty-results");
  empty.querySelector("h3").textContent = active ? jobLabels[job.status] : job?.error ? "这次评测没有产生有效报告" : job?.status === "cancelled" ? "任务已停止" : "等待一份真实的评测结果";
  empty.querySelector("p").textContent = job?.error || (active ? "任务在服务器上执行。页面会自动查询状态；关闭页面不会取消任务。" : job?.status === "cancelled" ? "未完成的检查不会被标记为通过。" : "提交文本、可选参考图和生成视频。结果会呈现问题类型、时间定位及各项检查状态。");
  if (!report) return;
  $("issue-list").replaceChildren();
  $("no-issues").hidden = report.issues.length > 0;
  report.issues.forEach((issue, index) => {
    const article = node("article", undefined, "issue");
    const top = node("div", undefined, "issue-top");
    top.append(node("h4", `${String(index + 1).padStart(2, "0")} · ${valueText(issue["问题类型"])}`),
      node("span", `置信度 ${valueText(issue["置信度"])}`, "issue-confidence"));
    article.append(top, node("p", valueText(issue["问题说明"])));
    const location = node("p", `时间区间 ${valueText(issue["时间区间"])} · 关键帧 ${valueText(issue["关键帧秒"])} s\nBBox ${valueText(issue.BBox)} · 可定位性 ${valueText(issue["可定位性"])}`, "issue-location");
    article.append(location);
    const keyframe = issue["关键帧秒"];
    const frame = typeof keyframe === "number" ? keyframe : typeof keyframe === "string" && /^\d+(\.\d+)?$/.test(keyframe) ? Number(keyframe) : NaN;
    if (state.previewJobId === job.id && Number.isFinite(frame)) {
      const seek = node("button", `查看 ${frame.toFixed(2)} s`, "text-button");
      seek.type = "button";
      seek.addEventListener("click", () => {
        if (state.previewJobId !== job.id) { notify("当前预览视频已改变，无法定位到该任务的原视频。"); return; }
        $("video-preview").currentTime = frame; $("video-preview").focus();
        $("video-preview").scrollIntoView({ block: "center", behavior: "auto" });
      }); article.append(seek);
    }
    $("issue-list").append(article);
  });
  $("check-list").replaceChildren();
  report.checks.forEach((check) => {
    const decision = check.decision in decisionLabels ? check.decision : "not_evaluable";
    const detail = node("details", undefined, "check-row");
    const summary = node("summary");
    summary.append(node("span", checkLabels[check.check_name] || check.check_name), node("span", decisionLabels[decision], `check-label ${decision}`));
    const limitations = (check.limitations || []).map(valueText).join("\n");
    detail.append(summary, node("p", `执行状态：${check.execution_status}\n证据级别：${check.evidence_level}\n工具：${(check.tool_refs || []).map(valueText).join("、") || "—"}\n${limitations || "未附加限制说明。"}`));
    $("check-list").append(detail);
  });
  $("coverage-note").textContent = `${(report.metrics.coverage * 100).toFixed(0)}% 可评估 · 非准确率`;
  $("model-info").textContent = JSON.stringify(report.models, null, 2);
}

function stopEvents() {
  state.events?.close();
  state.events = null;
  $("event-status").textContent = "任务结束后自动通知，无需定时查询。";
}

function beginEvents(jobId) {
  stopEvents();
  const generation = ++state.generation;
  const snapshot = { base: state.base, token: state.token };
  const labels = {
    connecting: "正在连接结果通知…", connected: "已订阅任务状态；完成后自动显示报告。",
    reconnecting: "通知连接中断，正在重连；服务器任务不受影响。",
    disconnected: "自动重连已暂停以节省额度；可点击“获取最新状态”。",
    denied: "通知连接未获授权，请检查访问令牌或重新连接服务器。",
    missing: "任务不存在，请刷新运行记录。"
  };
  state.events = new window.AvagentJobEvents({ ...snapshot, jobId,
    onStatus: (kind) => {
      if (generation === state.generation) $("event-status").textContent = labels[kind];
    },
    onJob: async (event) => {
      if (generation !== state.generation) return;
      if (!terminal.has(event.status)) {
        state.job = { ...state.job, ...event };
        renderJob(state.job);
        return;
      }
      $("event-status").textContent = "任务已结束，正在获取报告…";
      try {
        const job = await (await api(`/api/jobs/${encodeURIComponent(jobId)}`, {}, snapshot)).json();
        if (generation !== state.generation) return;
        state.job = job;
        renderJob(job);
        $("event-status").textContent = "已收到最终状态，通知连接已关闭。";
        await refreshHistory();
      } catch (error) {
        if (generation === state.generation) {
          $("event-status").textContent = "最终状态获取失败，可点击“获取最新状态”重试。";
          notify(error.message);
        }
      }
    }
  });
}
async function refreshHistory() {
  const snapshot = { base: state.base, token: state.token };
  const jobs = await (await api("/api/jobs", {}, snapshot)).json();
  if (snapshot.base !== state.base || snapshot.token !== state.token || !state.connected) return;
  $("history-list").replaceChildren();
  if (!jobs.length) { $("history-list").append(node("p", "还没有任务。提交一次评测后，记录会显示在这里。", "empty-note")); return; }
  jobs.forEach((job) => {
    const row = node("div", undefined, "history-row");
    const date = node("time", new Date(job.created_at * 1000).toLocaleString());
    date.dateTime = new Date(job.created_at * 1000).toISOString();
    const select = node("button", "查看 →", "text-button");
    select.type = "button";
    select.addEventListener("click", async () => {
      try {
        stopEvents();
        const generation = ++state.generation;
        const selected = await (await api(`/api/jobs/${encodeURIComponent(job.id)}`)).json();
        if (generation !== state.generation) return;
        state.job = selected; renderJob(selected);
        if (!terminal.has(selected.status)) beginEvents(selected.id);
        else $("event-status").textContent = "已载入最终状态，无需订阅通知。";
        $("output-title").scrollIntoView({ block: "start" });
      } catch (error) { notify(error.message); }
    });
    row.append(node("span", job.id.slice(0, 10), "mono"), node("span", job.input.video_name, "history-file"),
      node("span", jobLabels[job.status] || job.status), select, date);
    $("history-list").append(row);
  });
}
$("refresh-history").addEventListener("click", () => refreshHistory().catch((error) => notify(error.message)));
$("refresh-job").addEventListener("click", async () => {
  if (!state.connected || !state.job) return;
  stopEvents();
  const generation = ++state.generation;
  const snapshot = { base: state.base, token: state.token };
  const jobId = state.job.id;
  $("refresh-job").disabled = true;
  try {
    const job = await (await api(`/api/jobs/${encodeURIComponent(jobId)}`, {}, snapshot)).json();
    if (generation !== state.generation) return;
    state.job = job; renderJob(job);
    if (!terminal.has(job.status)) beginEvents(job.id);
    else $("event-status").textContent = "已载入最终状态，无需订阅通知。";
    notify();
  } catch (error) {
    if (generation === state.generation) notify(error.message);
  } finally {
    if (generation === state.generation) $("refresh-job").disabled = !state.connected || !state.job;
  }
});
window.addEventListener("pagehide", () => { stopEvents(); ++state.generation; });
window.addEventListener("pageshow", (event) => {
  if (event.persisted && state.connected && state.job && !terminal.has(state.job.status)) beginEvents(state.job.id);
});
$("cancel").addEventListener("click", async () => {
  if (!state.job || !confirm("停止当前任务？已完成的其他任务不会受影响。")) return;
  $("cancel").disabled = true;
  try {
    state.job = await (await api(`/api/jobs/${state.job.id}/cancel`, { method: "POST" })).json();
    if (terminal.has(state.job.status)) {
      stopEvents(); ++state.generation;
      $("event-status").textContent = "任务已结束，无需订阅通知。";
    }
    renderJob(state.job);
  }
  catch (error) { notify(error.message); }
  finally { $("cancel").disabled = false; }
});
$("download").addEventListener("click", async () => {
  if (!state.job?.report) return;
  const jobId = state.job.id;
  try {
    const blob = await (await api(`/api/jobs/${jobId}/report.jsonl`)).blob();
    const url = URL.createObjectURL(blob); const link = node("a");
    link.href = url; link.download = `avagent-eval-${jobId}.jsonl`; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (error) { notify(error.message); }
});
if (!$("api-base").value) $("connection-summary").textContent = "尚未配置公网后端地址";
if (window.AVAGENT_CONFIG?.deploymentMode === "temporary") {
  $("ingress-note").hidden = false;
  $("ingress-note").textContent = "当前通过 Cloudflare 临时 HTTPS 隧道连接服务器，仅供联调。隧道重启后地址可能变化，正式部署需固定域名入口。";
}
setControls();
