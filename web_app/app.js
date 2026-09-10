/* The browser submits jobs to the real server-side AVAgent CLI. No demo results. */
"use strict";

const $ = (id) => document.getElementById(id);
const state = { base: "", connected: false, ready: false, job: null,
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
const terminal = new Set(["completed", "failed", "cancelled"]);
const configuredBase = window.AVAGENT_CONFIG?.apiBase || "";
const localHost = ["localhost", "127.0.0.1", "[::1]"].includes(location.hostname);
const defaultBase = configuredBase || (localHost && location.protocol !== "file:" ? location.origin : "");

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
  const active = state.job && !terminal.has(state.job.status);
  $("run").disabled = !state.connected || !state.ready || state.submitting || !!active;
  $("input-fields").disabled = state.submitting || !!active;
  $("refresh-job").disabled = !state.connected || !state.job;
  $("submit-hint").textContent = state.submitting ? "正在上传并创建任务…" :
    active ? "正在评测…" : state.ready ? "由 avagent 分析视频" : "服务暂不可用";
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
    // Free ngrok browser requests otherwise receive HTML instead of API JSON.
    const ngrok = /\.(?:ngrok-free|ngrok)\.(?:app|dev)$/.test(new URL(snapshot.base).hostname);
    const response = await fetch(snapshot.base + path, { ...options, signal: controller.signal,
      cache: "no-store", credentials: "omit", redirect: "error",
      headers: { ...(ngrok ? { "ngrok-skip-browser-warning": "1" } : {}), ...(options.headers || {}) } });
    if (!response.ok) {
      if ([401, 403, 503].includes(response.status)) throw new Error("评测服务暂不可用，请稍后重试。");
      let detail;
      try { detail = (await response.json()).detail; } catch { /* A proxy may return HTML. */ }
      throw new Error(typeof detail === "string" ? detail : `服务器返回 HTTP ${response.status}`);
    }
    return response;
  } catch (error) {
    if (error.name === "AbortError") throw new Error(options.body ? "上传响应超时，未能确认是否提交成功，请勿连续重复提交。" : "请求超时，请稍后重试。");
    if (error instanceof TypeError) throw new Error("连接中断，请检查网络后重试。");
    throw error;
  } finally { clearTimeout(timer); }
}

async function connectBackend() {
  stopEvents();
  const generation = ++state.generation;
  state.connected = false;
  state.ready = false;
  $("retry-connection").disabled = true;
  $("retry-connection").hidden = true;
  $("cancel").hidden = true;
  $("download").disabled = true;
  status("connection-status", "正在连接…", "busy");
  setControls();
  notify();
  try {
    state.base = safeBase(defaultBase);
    const snapshot = { base: state.base };
    const health = await (await api("/api/health", {}, snapshot)).json();
    if (generation !== state.generation) return;
    if (health.service !== "avagent-eval") throw new Error("评测服务暂不可用，请稍后重试。");
    state.connected = true;
    state.ready = health.configured;
    if (Number.isFinite(health.limits?.max_upload_bytes) && health.limits.max_upload_bytes > 0) {
      state.maxUploadBytes = health.limits.max_upload_bytes;
      $("upload-limit").textContent = Math.floor(state.maxUploadBytes / 1048576);
    }
    status("connection-status", state.ready ? "服务可用" : "暂不可用", state.ready ? "good" : "busy");
    $("retry-connection").hidden = state.ready;
    if (!state.ready) notify("评测服务尚未就绪，请稍后重试。");
    state.job = null;
    renderJob(null);
    setControls();
  } catch (error) {
    if (generation !== state.generation) return;
    status("connection-status", "连接失败", "bad");
    $("retry-connection").hidden = false;
    notify(error.message);
  } finally { if (generation === state.generation) { $("retry-connection").disabled = false; setControls(); } }
}
$("retry-connection").addEventListener("click", connectBackend);

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
  if (!state.ready || state.submitting || (state.job && !terminal.has(state.job.status))) return;
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
  $("retry-connection").disabled = true;
  setControls(); notify();
  try {
    const job = await (await api("/api/jobs", { method: "POST", body: form })).json();
    state.previewJobId = job.id;
    state.job = job;
    renderJob(job);
    beginEvents(job.id);
  } catch (error) { notify(error.message); }
  finally { state.submitting = false; $("retry-connection").disabled = false; setControls(); }
});

function renderJob(job) {
  $("refresh-job").disabled = !state.connected || !job;
  const report = job?.report;
  $("report").hidden = !report;
  $("empty-results").hidden = !!report;
  $("job-updates").hidden = !job;
  $("result-metrics").hidden = !report;
  const active = job && !terminal.has(job.status);
  status("job-status", job ? jobLabels[job.status] || job.status : "等待提交",
    active ? "busy" : job?.status === "completed" ? "good" : job?.status === "failed" ? "bad" : "");
  $("cancel").hidden = !active;
  $("download").disabled = !report;
  $("metric-issues").textContent = report ? report.metrics.issue_count : "—";
  const elapsed = report?.metrics.elapsed_sec ?? (job?.started_at ? (job.finished_at || Date.now() / 1000) - job.started_at : null);
  $("metric-time").replaceChildren(document.createTextNode(Number.isFinite(elapsed) ? Math.max(0, elapsed).toFixed(1) : "—"), node("small", " s"));
  const empty = $("empty-results");
  empty.querySelector("h3").textContent = active ? jobLabels[job.status] : job?.status === "failed" ? "评测未完成" : job?.status === "cancelled" ? "评测已停止" : "等待评测";
  empty.querySelector("p").textContent = active ? "正在分析视频，请保持页面打开。完成后将在这里显示结果。" : job?.status === "failed" ? "本次未能生成结果，请稍后重试。" : job?.status === "cancelled" ? "可重新提交视频开始评测。" : "填写文本描述并上传视频，点击“开始评测”。";
  setControls();
  if (!report) return;
  $("issue-list").replaceChildren();
  const incomplete = (report.checks || []).filter((check) => check.decision === "not_evaluable" || check.execution_status !== "ok");
  $("report-notice").hidden = incomplete.length === 0;
  $("report-notice").textContent = incomplete.map((check) => {
    const reason = check.execution_status === "failed" ? "检测未完成" : check.execution_status === "not_applicable" ? "当前输入不适用" : "现有信息不足以判断";
    return `${checkLabels[check.check_name] || "部分检测项目"}：${reason}。`;
  }).join("\n");
  $("no-issues").hidden = report.issues.length > 0;
  $("no-issues").textContent = incomplete.length ? "本次未返回问题记录，部分项目未给出检测结论。" : "本次未检测到问题。";
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
}

function stopEvents() {
  state.events?.close();
  state.events = null;
  $("event-status").textContent = "";
}

function beginEvents(jobId) {
  stopEvents();
  const generation = ++state.generation;
  const snapshot = { base: state.base };
  const labels = {
    connecting: "正在获取评测状态…", connected: "评测完成后自动显示结果。",
    reconnecting: "连接中断，正在重连；评测仍在继续。",
    disconnected: "连接暂未恢复，可点击“刷新结果”。",
    denied: "暂时无法获取结果，请稍后重试。",
    missing: "该评测已不可用，请重新提交。"
  };
  state.events = new window.AvagentJobEvents({ ...snapshot, jobId, publicAccess: true,
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
        $("event-status").textContent = "";
      } catch (error) {
        if (generation === state.generation) {
          $("event-status").textContent = "结果获取失败，可点击“刷新结果”重试。";
          notify(error.message);
        }
      }
    }
  });
}
$("refresh-job").addEventListener("click", async () => {
  if (!state.connected || !state.job) return;
  stopEvents();
  const generation = ++state.generation;
  const snapshot = { base: state.base };
  const jobId = state.job.id;
  $("refresh-job").disabled = true;
  try {
    const job = await (await api(`/api/jobs/${encodeURIComponent(jobId)}`, {}, snapshot)).json();
    if (generation !== state.generation) return;
    state.job = job; renderJob(job);
    if (!terminal.has(job.status)) beginEvents(job.id);
    else $("event-status").textContent = "";
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
  if (!state.job || !confirm("停止本次评测？")) return;
  $("cancel").disabled = true;
  try {
    state.job = await (await api(`/api/jobs/${state.job.id}/cancel`, { method: "POST" })).json();
    if (terminal.has(state.job.status)) {
      stopEvents(); ++state.generation;
      $("event-status").textContent = "";
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
setControls();
if (defaultBase) connectBackend();
else {
  status("connection-status", "服务未配置", "bad");
  notify("默认服务器尚未配置，请联系维护者。");
}
