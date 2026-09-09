# avagent-eval website

The single-page console in `web_app/` submits uploads to the API in
`av_eval/web_api.py`. The server runs **this repository's `run_avagent.py`** in
its existing evaluation environment; this is not a direct-model baseline.

The intended public site is `https://xuanhaochang.github.io/AVagent-Eval/`.
GitHub Pages hosts only the frontend. A separate **administrator-approved HTTPS
endpoint** must route requests to the server's local API. A public frontend
alone does not make the backend reachable.

## Start the backend

Install the lightweight API separately from the existing model environment:

```bash
python -m venv .local/web-api-venv
.local/web-api-venv/bin/python -m pip install -r requirements-web.txt
.local/web-api-venv/bin/python scripts/run_web_api.py \
  --runner-python /absolute/path/to/avagent/python \
  --port 8766 \
  --origin http://127.0.0.1:8766
```

The server also needs `ffmpeg`/`ffprobe`. The evaluator still uses `.env.local`
and its normal local OCR/ASR/SyncNet assets. No model assets are bundled or
installed by the web setup. Set `AVAGENT_VISUAL_MODEL` and any specialist
overrides in the server environment, as for normal CLI evaluations. These are
internal AVAgent branch settings, not alternative evaluators.

The first startup generates `.local/web-jobs/access-token` with mode `0600`.
Read that file privately in your server terminal and enter its value in the
website's **website access token** field. It is not a GitHub token or a model
API key. The browser keeps it only in page memory, so refresh requires
re-entering it. The server binds to `127.0.0.1` by default.

Use one API process and one worker. For persistent deployment, run the command
under a user systemd service with `WorkingDirectory` set to the repository,
`UMask=0077`, `Restart=on-failure` and `KillMode=control-group`. The latter also
stops runner subprocesses when the service is stopped. User services may stop
after the final logout unless the administrator has enabled user lingering.

## Public ingress and frontend

The current development configuration uses a Cloudflare Quick Tunnel. It is
temporary: the hostname changes when its connector process restarts, and
Cloudflare provides no uptime guarantee. For release, replace it with a
named tunnel and a domain in your own Cloudflare account, or an administrator's
HTTPS reverse proxy. See [Quick Tunnel limitations](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/trycloudflare/).
Traffic through this endpoint is forwarded by Cloudflare. The bearer token
remains mandatory; knowing the URL alone does not grant access to jobs.

The development API uses `--max-upload-mib 90` to leave room below the proxy's
upload ceiling. The console reads the actual limit from `/api/health`.
For a fixed ingress, replace `apiBase` and `deploymentMode` in `config.js` and
add its origin with `--origin` if also serving the console from that hostname.

Ask the server administrator to provide an HTTPS reverse proxy to
`127.0.0.1:8766`, preserving `/api/*`, `Authorization`, `Origin` and upload
bodies. Set an upload limit of 128 MiB, a request timeout of at least 180 seconds,
and suitable connection/rate limits. Evaluation itself is asynchronous and does
not keep the upload request open for the entire evaluation.

Do not expose an unencrypted public HTTP endpoint: browsers cannot use it from
an HTTPS Pages site. Do not change an administrator-managed SSH proxy or bypass
a disabled SSH forwarding policy. An outbound address or an SSH port mapping
does not establish an HTTPS ingress.

Set `web_app/config.js`'s public `apiBase` to the approved HTTPS URL, or enter it
in the console. Never put credentials in this file. The default CORS origin is
`https://xuanhaochang.github.io` (origins do not include `/AVagent-Eval/`).
This allowlist permits requests from that origin; bearer authentication is
still required and is the actual access control.

In the personal repository, choose **Settings → Pages → Source → GitHub
Actions**. `.github/workflows/pages.yml` validates the web code and publishes
only `web_app/`, not backend files, uploads or experiment results.

## API and evidence semantics

All `/api/*` requests require `Authorization: Bearer <website-access-token>`.

| Endpoint | Purpose |
| --- | --- |
| `GET /api/health` | Configuration readiness, not a full inference health check |
| `POST /api/jobs` | Multipart `prompt`, one `video`, optional repeated `references` |
| `GET /api/jobs` | Shared-workspace task list |
| `GET /api/jobs/{id}` | Status, input metadata and completed report |
| `POST /api/jobs/{id}/cancel` | Cancel queued/running task and stop its process group |
| `GET /api/jobs/{id}/report.jsonl` | Sanitized structured report, not the raw run log |

The console shows reported issue count, evaluable-check coverage and elapsed
time. A missing check or failed tool remains `not_evaluable`; zero issues is not
proof that the video has no defects. Confidence is not calibrated accuracy.
No Precision/Recall/F1 is calculated without human ground truth.

Uploads are capped at 128 MiB total, four reference images (10 MiB each),
60 seconds of video and 4096 × 2160 video pixels. The initial deployment is a
**private research workspace**, not an anonymous public GPU endpoint: everyone
holding the shared token can see its jobs. Uploaded media may be sent to the
configured model providers. Do not distribute this token publicly.

Jobs, uploads and raw runner logs stay in the ignored `.local/web-jobs/`
directory. No raw-log/file-serving endpoint exists. Finished jobs are retained;
there is no automatic deletion. New submissions stop at 100 saved jobs or the
5 GiB storage admission limit until an administrator archives data. These are
admission limits, not OS-enforced disk/CPU quotas; use a bounded volume and a
dedicated service account/sandbox before offering access to untrusted users.
On service restart, unfinished jobs are marked failed and never billed again
through an automatic retry.

## Verification

```bash
.local/web-api-venv/bin/python -m unittest discover -s tests -p 'test_web_api.py' -v
python -m unittest discover -s tests -p 'test_web_app.py' -v
python -m unittest discover -s tests -p 'test_configure_github_access.py' -v
node --check web_app/app.js
```

API tests use a clearly labeled subprocess fixture, not real model calls. A
real inference smoke test and external HTTPS verification must be reported
separately. Authentication succeeds only after both credential validation and
a push preflight; a cached token alone does not prove repository write access.

Official references: [GitHub Pages workflows](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages),
[FastAPI uploads](https://fastapi.tiangolo.com/tutorial/request-files/),
[FastAPI CORS](https://fastapi.tiangolo.com/tutorial/cors/).
