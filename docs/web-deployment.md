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

### Server-pushed task notifications

The console no longer polls the job API. After submitting or selecting an
unfinished job, it opens `WSS /api/jobs/{id}/events`. Browser WebSockets cannot
set an Authorization header, so the client sends
`{"type":"authenticate","token":"<website-access-token>"}` as its first frame.
The token is never placed in the URL, subprotocol, or browser storage. HTTP
middleware does not authenticate WebSockets: this endpoint independently
checks the Origin allowlist, authenticates within five seconds, and only then
looks up the job. Missing or untrusted Origins and query strings are rejected.

After authentication, the server immediately sends the latest persisted job
state, including a job that finished while disconnected. Worker state changes
trigger events; there is no background HTTP poll or periodic job-state scan.
Events contain only the job ID, status, and timestamps. The browser fetches the
existing authenticated report endpoint once after a terminal event. Failed
jobs remain failed, not a fabricated successful report.

Heartbeats run every 25 seconds inside the same WebSocket. They are not new
HTTP requests. Lost connections use up to five retries (2, 5, 15, 30, 60 seconds)
per selected job subscription, then require the **获取最新状态** button.
Refreshing that button fetches the latest job once and resubscribes if needed.
At most 16 event connections, including pending authentication, are admitted;
incoming frames are limited to 4 KiB by the supplied uvicorn launcher. Slow
consumers receive the latest state through a one-item queue. Finished jobs and
disconnected clients release subscriptions. Closing the page never cancels
the evaluator. Browser/OS notifications after closing the page are not included.

### Fixed free ngrok ingress

Register a Free ngrok account and find its assigned development domain and
authtoken in the ngrok dashboard: [Domains](https://dashboard.ngrok.com/domains)
and [Your Authtoken](https://dashboard.ngrok.com/get-started/your-authtoken).
No new domain purchase is required. `127.0.0.1:8766` is the server-internal
upstream, not the URL to open on a different computer. Visitors open GitHub
Pages; the console sends API requests through the assigned ngrok HTTPS domain.
On this
server run:

```bash
python scripts/configure_ngrok_access.py
```

Enter the assigned domain and the authtoken in the hidden prompt. This writes
`.local/ngrok/ngrok.yml` with mode `0600` and a separate public `endpoint.txt`.
It does not start a tunnel or claim remote authorization has succeeded. Never
share the config or commit it. The configuration disables the local inspection
UI and request-body inspection storage, and pins an explicit HTTPS domain;
it will not fall back to a new random URL if that domain is unavailable.

Run an installed official ngrok v3 binary with:

```bash
ngrok config check --config .local/ngrok/ngrok.yml
ngrok start avagent-eval --config .local/ngrok/ngrok.yml
```

For unattended operation use a dedicated systemd service with restart backoff,
`UMask=0077`, and the API service as a dependency. Enable the service at boot and
ask the administrator to enable user lingering or provide a system service.
Do not stop a working ingress until the replacement's authenticated HTTPS and
WSS paths have both been verified. Then set `web_app/config.js` to the verified
public URL with `deploymentMode: "fixed"` and deploy GitHub Pages. If serving
the console directly from that URL, add its HTTPS origin to the API allowlist.

The browser adds `ngrok-skip-browser-warning: 1` only for supported ngrok
hostnames; the API allows this header in CORS preflights. Without it, the Free
plan can return its HTML interstitial rather than JSON, breaking cross-origin
browser requests even when command-line health checks pass. This header does
not replace or bypass the application's access token or Origin checks. A direct
browser visit to the ngrok-hosted HTML may still show the provider's Visit Site
page; the GitHub Pages frontend does not require that manual step.
See [ngrok's supported header](https://ngrok.com/docs/pricing-limits/free-plan-limits#using-headers).

For the maintained server deployment, operate the services with:

```bash
systemctl --user status avagent-eval.service avagent-eval-ngrok.service
systemctl --user restart avagent-eval-ngrok.service
# Stop only public ingress (the API and any evaluation jobs keep running):
systemctl --user stop avagent-eval-ngrok.service
```

The domain stays the same across agent restarts. Check `loginctl show-user
changxuanhao -p Linger` and service enablement before relying on logout/boot
persistence. This is not a guarantee of uptime during host or network outages.

ngrok supports WebSockets on the same HTTP endpoint. Connection setup and
reconnects still use requests/connections, and frames still consume traffic;
push notifications do not remove the Free plan's monthly traffic quota.
The Free plan currently includes 20,000 HTTP requests and 1 GB data transfer
per month (including traffic forwarded to the agent). Verify limits in the
account dashboard before public release. See [ngrok WebSockets](https://ngrok.com/docs/using-ngrok-with/websockets)
and [Free limits](https://ngrok.com/docs/pricing-limits/free-plan-limits).

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

## GitHub publishing credentials

Run `python scripts/configure_github_access.py` in the server terminal. Reuse
the existing personal-repository token if it is still valid. The hidden prompt
passes it to Git through stdin and caches it in memory for **30 days**
(`2592000` seconds), never in source files or command-line arguments.

This cache timeout does not extend the token's GitHub expiration date. A server
restart or stopped credential-cache daemon also clears the cache early. If
creating a new token, choose an expiration of at least 30 days on GitHub and
restrict its repository access to the publishing repository.

Publishing commands that explicitly select a credential helper must use
`-c credential.helper= -c 'credential.helper=cache --timeout=2592000'`
and `-c credential.useHttpPath=true`. Using bare `credential.helper=cache`
can shorten the lifetime when Git saves a successfully used credential again.
Do not store the token in `.gitconfig`, a remote URL, or a plaintext credential
file. See [Git's credential cache documentation](https://git-scm.com/docs/git-credential-cache)
and [GitHub's token guidance](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens).

## Verification

```bash
.local/web-api-venv/bin/python -m unittest discover -s tests -p 'test_web_api.py' -v
python -m unittest discover -s tests -p 'test_web_app.py' -v
python -m unittest discover -s tests -p 'test_configure_github_access.py' -v
node --check web_app/app.js
node --test tests/test_web_events.mjs
python -m unittest discover -s tests -p 'test_configure_ngrok_access.py' -v
```

API tests use a clearly labeled subprocess fixture, not real model calls. A
real inference smoke test and external HTTPS verification must be reported
separately. Authentication succeeds only after both credential validation and
a push preflight; a cached token alone does not prove repository write access.

Official references: [GitHub Pages workflows](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages),
[FastAPI uploads](https://fastapi.tiangolo.com/tutorial/request-files/),
[FastAPI CORS](https://fastapi.tiangolo.com/tutorial/cors/).
