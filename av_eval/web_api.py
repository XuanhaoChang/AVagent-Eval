"""Upload API with explicit public/private modes for the research console."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import hmac
import json
from pathlib import Path
import subprocess
import tempfile

from av_eval.web_jobs import JobManager, Settings, TERMINAL, probe_video


def create_app(settings: Settings):
    from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import JSONResponse, Response
    from fastapi.staticfiles import StaticFiles
    from starlette.datastructures import UploadFile

    # FastAPI resolves annotations using module globals, including factory-local types.
    globals()["Request"] = Request
    globals()["WebSocket"] = WebSocket
    if not settings.public_access and len(settings.token) < 32:
        raise ValueError("The private web access token must have at least 32 characters.")

    @asynccontextmanager
    async def lifespan(app):
        app.state.jobs = JobManager(settings)
        app.state.event_connections = 0
        yield
        await asyncio.to_thread(app.state.jobs.close)

    app = FastAPI(title="avagent-eval", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    uploads = asyncio.Semaphore(2)

    @app.middleware("http")
    async def access_control(request: Request, call_next):
        if request.url.path.startswith("/api/") and request.method != "OPTIONS":
            expected = f"Bearer {settings.token}"
            if not settings.public_access and not hmac.compare_digest(
                    request.headers.get("authorization", "").encode(), expected.encode()):
                return JSONResponse({"detail": "请输入有效的网站访问令牌。"}, status_code=401)
            origin = request.headers.get("origin")
            if origin and origin not in settings.origins:
                return JSONResponse({"detail": "This origin is not allowed."}, status_code=403)
            if request.method == "POST" and request.url.path == "/api/jobs":
                try:
                    length = int(request.headers.get("content-length", "-1"))
                except ValueError:
                    length = -1
                if length < 0:
                    return JSONResponse({"detail": "Content-Length required."}, status_code=411)
                if length > settings.max_upload_bytes:
                    maximum = settings.max_upload_bytes // (1024 * 1024)
                    return JSONResponse({"detail": f"上传总大小不能超过 {maximum} MiB。"}, status_code=413)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    # CORS must wrap auth errors too, so add it after the authentication middleware.
    app.add_middleware(CORSMiddleware, allow_origins=list(settings.origins),
                       allow_methods=["GET", "POST", "OPTIONS"],
                       allow_headers=["Authorization", "Content-Type", "ngrok-skip-browser-warning"],
                       max_age=600)

    @app.get("/api/health")
    def health(request: Request):
        missing = settings.readiness()
        return {"service": "avagent-eval", "configured": not missing, "missing": missing,
                "runtime_note": "Configuration check only; tool availability is reported by each evaluation.",
                "notifications": "websocket",
                "access_mode": "public" if settings.public_access else "private",
                "limits": {"max_upload_bytes": settings.max_upload_bytes,
                           "max_duration_sec": settings.max_duration_sec, "max_references": 4}}

    @app.get("/api/jobs")
    def list_jobs(request: Request):
        if settings.public_access:
            raise HTTPException(404, "公开服务不提供任务列表。")
        return request.app.state.jobs.list()

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str, request: Request):
        try:
            return request.app.state.jobs.get(job_id)
        except KeyError:
            raise HTTPException(404, "任务不存在。") from None

    @app.websocket("/api/jobs/{job_id}/events")
    async def job_events(websocket: WebSocket, job_id: str):
        # HTTP middleware/CORS do not protect WebSockets. Check Origin explicitly.
        # Public clients subscribe without a secret; private clients authenticate.
        if websocket.headers.get("origin") not in settings.origins or websocket.url.query:
            await websocket.close(code=4403)
            return
        if app.state.event_connections >= settings.max_event_connections:
            await websocket.close(code=1013)
            return
        app.state.event_connections += 1
        manager = app.state.jobs
        loop = asyncio.get_running_loop()
        updates = asyncio.Queue(maxsize=1)
        tasks = []
        subscribed = False
        alive = True

        def deliver(snapshot):
            if alive:
                if updates.full():
                    updates.get_nowait()
                updates.put_nowait(snapshot)

        def changed(snapshot):
            try:
                loop.call_soon_threadsafe(deliver, snapshot)
            except RuntimeError:
                pass  # Event loop already stopped; persisted job remains authoritative.

        async def send(message):
            await asyncio.wait_for(websocket.send_json(message), timeout=10)

        async def receive_text():
            packet = await websocket.receive()
            if packet["type"] == "websocket.disconnect":
                raise WebSocketDisconnect(packet.get("code", 1000))
            if not isinstance(packet.get("text"), str):
                raise ValueError("Text frames required")
            return packet["text"]

        try:
            await websocket.accept()
            raw = await asyncio.wait_for(receive_text(), settings.event_auth_timeout_sec)
            if len(raw) > 4096:
                await websocket.close(code=4401)
                return
            auth = json.loads(raw)
            public_subscription = (settings.public_access and isinstance(auth, dict)
                                   and auth.get("type") in {"subscribe", "authenticate"})
            private_subscription = (isinstance(auth, dict) and auth.get("type") == "authenticate"
                    and isinstance(auth.get("token"), str)
                    and hmac.compare_digest(auth["token"].encode(), settings.token.encode()))
            if not (public_subscription or (not settings.public_access and private_subscription)):
                await websocket.close(code=4401)
                return
            snapshot = manager.subscribe(job_id, changed)
            subscribed = True
            await send({"type": "job", "job": snapshot})
            if snapshot["status"] in TERMINAL:
                await websocket.close(code=1000)
                return
            receiver = asyncio.create_task(receive_text())
            updater = asyncio.create_task(updates.get())
            tasks = [receiver, updater]
            last_pong = loop.time()
            while True:
                done, _ = await asyncio.wait(tasks, timeout=settings.event_heartbeat_sec,
                                             return_when=asyncio.FIRST_COMPLETED)
                if not done:
                    if loop.time() - last_pong > settings.event_heartbeat_sec * 3:
                        await websocket.close(code=1001)
                        return
                    await send({"type": "heartbeat"})
                if receiver in done:
                    raw = receiver.result()
                    if len(raw) > 128 or json.loads(raw) != {"type": "pong"}:
                        await websocket.close(code=4400)
                        return
                    last_pong = loop.time()
                    receiver = asyncio.create_task(receive_text())
                    tasks = [receiver, updater]
                if updater in done:
                    snapshot = updater.result()
                    await send({"type": "job", "job": snapshot})
                    if snapshot["status"] in TERMINAL:
                        await websocket.close(code=1000)
                        return
                    updater = asyncio.create_task(updates.get())
                tasks = [receiver, updater]
        except KeyError:
            await websocket.close(code=4404)
        except (ValueError, TypeError):
            await websocket.close(code=4400)
        except asyncio.TimeoutError:
            await websocket.close(code=4408)
        except WebSocketDisconnect:
            pass
        finally:
            alive = False
            if subscribed:
                manager.unsubscribe(job_id, changed)
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            app.state.event_connections -= 1

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str, request: Request):
        try:
            return request.app.state.jobs.cancel(job_id)
        except KeyError:
            raise HTTPException(404, "任务不存在。") from None

    @app.get("/api/jobs/{job_id}/report.jsonl")
    def export_job(job_id: str, request: Request):
        job = get_job(job_id, request)
        if not job["report"]:
            raise HTTPException(409, "任务尚未产生有效报告。")
        return Response(json.dumps(job, ensure_ascii=False) + "\n", media_type="application/x-ndjson",
                        headers={"Content-Disposition": f'attachment; filename="avagent-eval-{job_id}.jsonl"'})

    def persist_uploads(form, jobs):
        from PIL import Image, UnidentifiedImageError

        if set(form.keys()) - {"prompt", "video", "references"}:
            raise HTTPException(422, "Unknown upload fields.")
        prompt = form.get("prompt", "")
        video = form.get("video")
        refs = form.getlist("references")
        if not isinstance(prompt, str) or not 1 <= len(prompt.strip()) <= 10000:
            raise HTTPException(422, "请填写文本描述，最多 10000 字符。")
        if len(form.getlist("video")) != 1 or not isinstance(video, UploadFile):
            raise HTTPException(422, "请选择一个生成视频。")
        if len(refs) > 4 or any(not isinstance(item, UploadFile) for item in refs):
            raise HTTPException(422, "最多上传 4 张参考图。")
        with tempfile.TemporaryDirectory(prefix="upload-", dir=settings.data) as directory:
            folder = Path(directory)
            total = 0

            def copy_file(upload, path, allowed, limit):
                nonlocal total
                suffix = Path(upload.filename or "").suffix.lower()
                if suffix not in allowed:
                    raise HTTPException(422, "不支持的文件类型。")
                count = 0
                with path.open("wb") as stream:
                    while block := upload.file.read(1024 * 1024):
                        count += len(block)
                        total += len(block)
                        if count > limit or total > settings.max_upload_bytes:
                            raise HTTPException(413, "文件过大（参考图每张最多 10 MiB）。")
                        stream.write(block)
                if count == 0:
                    raise HTTPException(422, "不能上传空文件。")

            suffix = Path(video.filename or "").suffix.lower()
            video_path = folder / f"video{suffix}"
            copy_file(video, video_path, {".mp4", ".mov", ".webm"}, settings.max_upload_bytes)
            try:
                metadata = probe_video(video_path, settings.max_duration_sec)
            except subprocess.TimeoutExpired:
                raise HTTPException(422, "视频解析超时，请检查文件。") from None
            except (ValueError, TimeoutError) as exc:
                raise HTTPException(422, str(exc)) from None
            for index, reference in enumerate(refs):
                suffix = Path(reference.filename or "").suffix.lower()
                path = folder / f"reference_{index}{suffix}"
                copy_file(reference, path, {".jpg", ".jpeg", ".png", ".webp"}, 10 * 1024 * 1024)
                try:
                    with Image.open(path) as image:
                        if image.width * image.height > 20_000_000:
                            raise ValueError("Reference exceeds 20 megapixels")
                        if image.format not in {"JPEG", "PNG", "WEBP"}:
                            raise ValueError("Invalid image format")
                        image.verify()
                except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
                    raise HTTPException(422, "参考图无效或分辨率过高。") from None
            return jobs.submit(folder, prompt.strip(), Path(video.filename.replace("\\", "/")).name,
                               [Path(item.filename.replace("\\", "/")).name for item in refs], metadata)

    @app.post("/api/jobs", status_code=202)
    async def submit_job(request: Request):
        if missing := settings.readiness():
            raise HTTPException(503, "服务器尚未配置：" + ", ".join(missing))
        jobs = request.app.state.jobs
        try:
            jobs.available()
            async with uploads:
                async with request.form(max_files=5, max_fields=1, max_part_size=40000) as form:
                    return await asyncio.to_thread(persist_uploads, form, jobs)
        except OverflowError as exc:
            raise HTTPException(429, str(exc)) from None

    site = settings.repo / "web_app"
    if site.is_dir():
        app.mount("/", StaticFiles(directory=site, html=True), name="console")
    return app
