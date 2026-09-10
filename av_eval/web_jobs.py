"""Private, single-worker job queue around the public AVAgent CLI.

No model configuration, filesystem paths or shell arguments come from clients.
The operator token is a shared research-workspace credential, not multi-user auth.
"""

from __future__ import annotations

import csv
import json
import os
from pathlib import Path
import queue
import re
import shutil
import signal
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable

from av_eval.project_env import load_project_env


CHECK_NAMES = (
    "reference_subject_identity", "entity_count_spatial_composition",
    "remaining_hard_instruction_compliance", "motion_physics_continuity",
    "dialogue_speaker_binding", "voice_characteristics",
    "subtitle_text_logo_watermark", "av_lip_sync",
    "visual_quality_temporal_artifacts", "audio_quality_noise_artifacts",
)
SOURCE_COLUMNS = (
    "序号", "user_prompt", "reference_image_urls", "generated_video_url",
    "用户反馈", "思考过程及标准答案",
)
ISSUE_KEYS = ("可定位性", "置信度", "问题说明", "问题类型", "时间区间", "关键帧秒", "BBox")
TERMINAL = {"completed", "failed", "cancelled"}


@dataclass(frozen=True)
class Settings:
    repo: Path
    data: Path
    python: Path
    token: str
    origins: tuple[str, ...] = ("https://xuanhaochang.github.io",)
    max_upload_bytes: int = 128 * 1024 * 1024
    max_duration_sec: int = 60
    max_pending: int = 3
    max_saved_jobs: int = 100
    max_storage_bytes: int = 5 * 1024 ** 3
    job_timeout_sec: int = 3600
    max_event_connections: int = 16
    event_auth_timeout_sec: float = 5.0
    event_heartbeat_sec: float = 25.0

    def runtime_env(self) -> dict[str, str]:
        env = dict(os.environ)
        load_project_env(self.repo / ".env.local", environ=env)
        env["PYTHONUNBUFFERED"] = "1"
        return env

    def readiness(self) -> list[str]:
        env = self.runtime_env()
        missing = []
        for name, fallback in (
            ("AVAGENT_API_KEY", "ARK_API_KEY"),
            ("AVAGENT_API_URL", "VIDEO_EVAL_API_URL"),
            ("AVAGENT_VISUAL_MODEL", "VIDEO_EVAL_MODEL"),
        ):
            if not (env.get(name) or env.get(fallback)):
                missing.append(name)
        if not self.python.is_file():
            missing.append("AVAGENT_WEB_RUNNER_PYTHON")
        if not (self.repo / "run_avagent.py").is_file():
            missing.append("run_avagent.py")
        if not shutil.which("ffprobe") or not shutil.which("ffmpeg"):
            missing.append("ffmpeg / ffprobe")
        return missing


def save_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def build_report(folder: Path) -> dict[str, Any]:
    """Keep missing/failed checks unknown; never manufacture a quality score."""
    with (folder / "predictions.csv").open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if len(rows) != 1:
        raise ValueError("Expected one prediction row")
    issues = json.loads(rows[0]["GPT预测结果"])
    if not isinstance(issues, list) or any(not isinstance(item, dict) for item in issues):
        raise ValueError("Invalid prediction schema")
    traces = [json.loads(line) for line in (folder / "run.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(traces) != 1 or traces[0].get("success") is not True:
        raise ValueError("Missing successful execution trace")
    trace = traces[0]
    reported = {item.get("check_name"): item for item in trace.get("classic_checks", []) if isinstance(item, dict)}
    checks = []
    for name in CHECK_NAMES:
        original = reported.get(name, {})
        decision = original.get("decision", "not_evaluable")
        execution = original.get("execution_status", "failed")
        if execution != "ok" or decision not in {"detected", "not_detected", "not_evaluable"}:
            decision = "not_evaluable"
        checks.append({
            "check_name": name, "decision": decision, "execution_status": execution,
            "evidence_level": original.get("evidence_level", "none"),
            "tool_refs": original.get("tool_refs", []),
            "limitations": original.get("limitations", ["No evidence record returned."]),
        })
    evaluable = sum(check["decision"] != "not_evaluable" for check in checks)
    # Export only the public report schema, not raw request payloads, paths or logs.
    return {
        "schema_version": 1,
        "issues": [{key: issue.get(key) for key in ISSUE_KEYS} for issue in issues],
        "checks": checks,
        "metrics": {"issue_count": len(issues), "evaluable_checks": evaluable,
                    "total_checks": len(CHECK_NAMES), "coverage": evaluable / len(CHECK_NAMES),
                    "elapsed_sec": trace.get("elapsed_sec")},
        "models": {key: trace.get(key) for key in ("gpt_a_model", "gemini_model", "seed_lite_model")},
        "note": "Model/tool findings, not human ground truth. Coverage is not accuracy. Unavailable evidence is not a pass.",
    }


def redact(value: Any, settings: Settings) -> Any:
    secrets = [v for k, v in settings.runtime_env().items()
               if v and len(v) >= 8 and any(part in k.upper() for part in ("KEY", "TOKEN", "PASSWORD", "SECRET"))]
    secrets.append(settings.token)

    def clean(item: Any) -> Any:
        if isinstance(item, str):
            for secret in secrets:
                if secret:
                    item = item.replace(secret, "[redacted]")
            item = item.replace(str(settings.repo), "[server]")
            item = item.replace(str(settings.data), "[jobs]")
            return item
        if isinstance(item, list):
            return [clean(part) for part in item]
        if isinstance(item, dict):
            return {key: clean(part) for key, part in item.items()}
        return item
    return clean(value)


def probe_video(path: Path, max_duration: int) -> dict[str, Any]:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe", "-show_format",
         "-show_streams", "-of", "json", str(path)],
        capture_output=True, timeout=20, check=False,
    )
    if result.returncode:
        raise ValueError("视频无法解码，请上传 MP4、MOV 或 WebM 文件。")
    media = json.loads(result.stdout)
    duration = float(media.get("format", {}).get("duration", 0))
    formats = set(media.get("format", {}).get("format_name", "").split(","))
    videos = [item for item in media.get("streams", []) if item.get("codec_type") == "video"]
    if not formats.intersection({"mp4", "mov", "webm", "matroska"}) or not videos:
        raise ValueError("需要包含视频轨的 MP4、MOV 或 WebM 文件。")
    if not 0 < duration <= max_duration:
        raise ValueError(f"视频时长须在 0–{max_duration} 秒之间。")
    first = videos[0]
    if int(first.get("width", 0)) * int(first.get("height", 0)) > 4096 * 2160:
        raise ValueError("视频分辨率不能超过 4096 × 2160。")
    return {"duration_sec": round(duration, 3), "width": first.get("width"),
            "height": first.get("height"), "has_audio": any(item.get("codec_type") == "audio" for item in media.get("streams", []))}


class JobManager:
    def __init__(self, settings: Settings):
        self.settings = settings
        settings.data.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = threading.RLock()
        self.jobs: dict[str, dict[str, Any]] = {}
        self.pending: queue.Queue[str | None] = queue.Queue()
        self.processes: dict[str, subprocess.Popen] = {}
        self.listeners: dict[str, set[Callable[[dict], None]]] = {}
        self.stopping = False
        for path in settings.data.glob("*/job.json"):
            if not re.fullmatch(r"[a-f0-9]{32}", path.parent.name):
                continue
            job = json.loads(path.read_text(encoding="utf-8"))
            if job["status"] not in TERMINAL:
                job.update(status="failed", error="服务重启中断了此任务；未自动重跑。", finished_at=time.time())
                save_json(path, job)
            self.jobs[job["id"]] = job
        self.worker = threading.Thread(target=self._work, name="avagent-eval-worker", daemon=True)
        self.worker.start()

    def available(self) -> None:
        with self.lock:
            if self.stopping or sum(job["status"] not in TERMINAL for job in self.jobs.values()) >= self.settings.max_pending:
                raise OverflowError("评测队列已满，请稍后提交。")
            if len(self.jobs) >= self.settings.max_saved_jobs:
                raise OverflowError("任务存储已满，请联系管理员归档。")
            used = sum(path.stat().st_size for path in self.settings.data.rglob("*") if path.is_file())
            if used + self.settings.max_upload_bytes > self.settings.max_storage_bytes:
                raise OverflowError("服务器存储额度不足，请联系管理员。")

    def submit(self, staging: Path, prompt: str, video_name: str, refs: list[str], metadata: dict) -> dict:
        with self.lock:
            self.available()
            job_id = uuid.uuid4().hex
            folder = self.settings.data / job_id
            staging.rename(folder)
            video = next(folder.glob("video.*"))
            references = sorted(folder.glob("reference_*"))
            with (folder / "input.csv").open("w", encoding="utf-8", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(SOURCE_COLUMNS)
                writer.writerow([job_id, prompt, json.dumps([str(path) for path in references]), str(video), "", ""])
            job = {"id": job_id, "status": "queued", "created_at": time.time(), "started_at": None,
                   "finished_at": None, "error": None, "report": None,
                   "input": {"prompt": prompt, "video_name": video_name, "references": refs, "media": metadata}}
            self.jobs[job_id] = job
            save_json(folder / "job.json", job)
            self.pending.put(job_id)
            return self.get(job_id)

    def get(self, job_id: str) -> dict:
        with self.lock:
            if not re.fullmatch(r"[a-f0-9]{32}", job_id) or job_id not in self.jobs:
                raise KeyError(job_id)
            return json.loads(json.dumps(self.jobs[job_id]))

    def list(self) -> list[dict]:
        with self.lock:
            return [{key: job[key] for key in ("id", "status", "created_at", "input")}
                    for job in sorted(self.jobs.values(), key=lambda item: item["created_at"], reverse=True)]

    @staticmethod
    def event_snapshot(job: dict) -> dict:
        # Reports, prompts, paths and credentials never enter event messages.
        return {key: job.get(key) for key in ("id", "status", "started_at", "finished_at")}

    def subscribe(self, job_id: str, listener: Callable[[dict], None]) -> dict:
        """Atomically register and snapshot, including completion while disconnected."""
        with self.lock:
            job = self.get(job_id)
            self.listeners.setdefault(job_id, set()).add(listener)
            return self.event_snapshot(job)

    def unsubscribe(self, job_id: str, listener: Callable[[dict], None]) -> None:
        with self.lock:
            listeners = self.listeners.get(job_id)
            if listeners is not None:
                listeners.discard(listener)
                if not listeners:
                    self.listeners.pop(job_id, None)

    def _update(self, job_id: str, **fields: Any) -> None:
        with self.lock:
            self.jobs[job_id].update(fields)
            save_json(self.settings.data / job_id / "job.json", self.jobs[job_id])
            for listener in tuple(self.listeners.get(job_id, ())):
                listener(self.event_snapshot(self.jobs[job_id]))

    @staticmethod
    def _kill(process: subprocess.Popen) -> None:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=10)

    def cancel(self, job_id: str) -> dict:
        with self.lock:
            job = self.get(job_id)
            if job["status"] not in TERMINAL:
                self._update(job_id, status="cancelled", finished_at=time.time())
                process = self.processes.get(job_id)
                if process is not None:
                    self._kill(process)
            return self.get(job_id)

    def close(self) -> None:
        with self.lock:
            self.stopping = True
            for job_id in list(self.jobs):
                if self.jobs[job_id]["status"] not in TERMINAL:
                    self.cancel(job_id)
        self.pending.put(None)
        self.worker.join(timeout=15)

    def _work(self) -> None:
        while (job_id := self.pending.get()) is not None:
            try:
                with self.lock:
                    if self.stopping or self.jobs[job_id]["status"] != "queued":
                        continue
                    folder = self.settings.data / job_id
                    self._update(job_id, status="running", started_at=time.time())
                    command = [str(self.settings.python), str(self.settings.repo / "run_avagent.py"),
                               "--input-csv", str(folder / "input.csv"), "--output-csv", str(folder / "predictions.csv"),
                               "--run-log", str(folder / "run.jsonl"), "--limit", "1"]
                    with (folder / "runner.log").open("wb") as log:
                        process = subprocess.Popen(command, cwd=self.settings.repo, env=self.settings.runtime_env(),
                                                   stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                    self.processes[job_id] = process
                try:
                    code = process.wait(timeout=self.settings.job_timeout_sec)
                except subprocess.TimeoutExpired:
                    self._kill(process)
                    raise RuntimeError("评测超时，任务已停止。") from None
                with self.lock:
                    if self.jobs[job_id]["status"] == "cancelled":
                        continue
                    if code != 0:
                        raise RuntimeError(f"评测程序退出（code {code}）；请管理员查看此任务的 runner.log。")
                    report = redact(build_report(folder), self.settings)
                    self._update(job_id, status="completed", report=report, finished_at=time.time())
            except Exception as exc:
                with self.lock:
                    if self.jobs[job_id]["status"] != "cancelled":
                        message = str(exc) if isinstance(exc, RuntimeError) else "未取得有效结果；请管理员检查任务日志。"
                        self._update(job_id, status="failed", error=message, finished_at=time.time())
            finally:
                with self.lock:
                    self.processes.pop(job_id, None)
