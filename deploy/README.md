# Deploy on one Linux server

This setup runs Nginx → one Gunicorn application worker with four request threads → private temporary storage. Report processing runs in a background thread inside that worker. Colleagues share one active-report slot, but each page has its own uploads and reports. There is no login: restrict access to your company network or VPN using the server/network firewall before enabling the site.

The examples use Ubuntu/Debian, `/opt/ai-feedback` for code, `/var/lib/ai-feedback` for data, and `feedback.example.com` for the hostname. Replace the hostname with yours. Keep this app's service, storage, secret, hostname and port separate from your translation app. Port 5050 must be free; if you change it, update both `gunicorn.conf.py` and `deploy/nginx.conf`.

## 1. Install the application

Run these commands from a checkout of this repository on the server. Create the service account once:

```sh
sudo apt-get update
sudo apt-get install python3-venv nginx rsync curl
sudo useradd --system --user-group --home-dir /var/lib/ai-feedback --no-create-home --shell /usr/sbin/nologin ai-feedback
sudo install -d -m 0755 /opt/ai-feedback
sudo rsync -a --exclude=.git --exclude=.venv --exclude=.env --exclude=storage --exclude=backup --exclude=artifacts ./ /opt/ai-feedback/
sudo python3 -m venv /opt/ai-feedback/.venv
sudo /opt/ai-feedback/.venv/bin/python -m pip install -r /opt/ai-feedback/requirements.txt
```

The service needs read access to the code and configuration; keep them owned by the deployment administrator. systemd creates the writable private data directory for the service account. Python 3.11 or newer is supported; Python 3.12 is a suitable server choice.

Before the first report, review `config/settings.json` on the server. Its current `concurrency` is 200 simultaneous classification requests; start with a modest value such as 5 or 10 and raise it after observing your provider limits and server memory. This is independent of Gunicorn's four request threads. Keep the proxy's `client_max_body_size` aligned with `max_upload_mb`.

## 2. Configure the environment

```sh
sudo install -m 0600 /opt/ai-feedback/.env.example /etc/ai-feedback.env
python3 -c 'import secrets; print(secrets.token_hex(32))'
sudoedit /etc/ai-feedback.env
```

Paste the generated value into `FLASK_SECRET_KEY`, set `OPENROUTER_API_KEY`, and replace `FEEDBACK_ALLOWED_HOSTS` with your domain. Use comma-separated hostnames if needed, without schemes or paths. Retain the same session secret across restarts. Set Feishu credentials only if you intend to use the integration, and verify its destination in `config/baloot/settings.json`.

| Variable | Production value/purpose |
| --- | --- |
| `FEEDBACK_ENV` | `production`; enables deployment validation and exclusive storage ownership |
| `FEEDBACK_STORAGE` | Absolute private data directory, here `/var/lib/ai-feedback`; holds temporary uploads and the persistent prompt database |
| `FEEDBACK_PROMPT_PASSWORD_BALOOT`, `FEEDBACK_PROMPT_PASSWORD_XINJIANG` | Optional, separate random passwords for editing each game's prompts |
| `FLASK_SECRET_KEY` | Random secret, at least 32 characters |
| `FEEDBACK_ALLOWED_HOSTS` | Your hostname, for example `feedback.example.com` |
| `FEEDBACK_SECURE_COOKIES` | `true` for HTTPS-only browser cookies |
| `FEEDBACK_BEHIND_PROXY` | `true` for the single Nginx proxy in this example |

systemd loads this file into the service environment. Avoid keeping a second production `.env` in the code directory. The app also supports the project `.env` for local development; environment variables take precedence. Never put credentials or uploaded reports in Git or an Nginx static directory. If reusing an existing storage directory, assign it to `ai-feedback` and set its permissions to `0700`; production startup refuses a group/world-accessible storage root.

## 3. Start the service

```sh
sudo install -m 0644 /opt/ai-feedback/deploy/ai-feedback.service /etc/systemd/system/ai-feedback.service
sudo systemctl daemon-reload
sudo systemctl enable --now ai-feedback
sudo systemctl status ai-feedback
curl --fail -H 'Host: feedback.example.com' http://127.0.0.1:5050/healthz
```

`/healthz` checks that the application answers requests; it makes no paid model calls and does not check OpenRouter or Feishu availability. It returns no uploaded data or credentials.

Use the supplied Gunicorn configuration: exactly **one** worker, no preload, automatic worker recycling or rolling reload. A second production process against the same storage is rejected before it can alter task status. Do not run `python app.py` alongside the service, send Gunicorn HUP/USR2, or increase `--workers` to scale it. The service intentionally has no `ExecReload` action.

## 4. Enable HTTPS

Configure DNS for your hostname and obtain its TLS certificate using your existing server certificate process. Edit the Nginx example's hostname, redirect hostname, `ssl_certificate` and `ssl_certificate_key` to point to that certificate. The placeholder certificate paths are not created by this repository.

```sh
sudo install -m 0644 /opt/ai-feedback/deploy/nginx.conf /etc/nginx/sites-available/ai-feedback
sudoedit /etc/nginx/sites-available/ai-feedback
sudo ln -s /etc/nginx/sites-available/ai-feedback /etc/nginx/sites-enabled/ai-feedback
sudo nginx -t
sudo systemctl reload nginx
```

Add this as a separate site alongside your other app. Gunicorn remains bound to loopback; expose only the HTTPS proxy to your authorized network. The supplied proxy overwrites the forwarded client IP and scheme headers, matching the app's trust of exactly one proxy. If your server has additional proxy layers, adapt both configurations before using them.

Open `https://feedback.example.com`, upload `examples/feedback_sample.csv`, and check preview and date selection. These operations do not use the LLM. Refresh and verify a blank workspace and deletion of the previous upload folder. A generated sample report makes paid model calls; sending it to Feishu is a separate manual action. Verify HTTPS cookies and downloads before inviting colleagues.

## Updating and recovery

Wait for the active report and any Feishu push to finish before deployment. Alternatively use **Stop** and wait until report processing has stopped. Then stop the service, install the new code/dependencies, and start it again:

```sh
sudo systemctl stop ai-feedback
# Update /opt/ai-feedback from the desired revision, excluding .env and private data.
sudo /opt/ai-feedback/.venv/bin/python -m pip install -r /opt/ai-feedback/requirements.txt
sudo systemctl start ai-feedback
curl --fail -H 'Host: feedback.example.com' http://127.0.0.1:5050/healthz
```

This uses a brief maintenance window. Ask colleagues to download reports before shutdown: restarting deletes marked temporary workspaces and requires a fresh upload. The app does not automatically issue new paid requests after restart. For an interrupted Feishu send with unknown delivery status, inspect the destination before sending a newly generated report; previous delivery checkpoints have been deleted.

## Storage, backups and monitoring

The storage directory holds temporary files while a page is in use. Refresh/close requests cancel that page's worker and delete its files after active operations finish. A cleanup thread checks every 15 seconds; 30 minutes without heartbeat expires abandoned pages. Startup removes remaining folders marked with `workspace.json`. Downloaded copies and previously sent Feishu content are unaffected. The app creates `prompts.sqlite3` in this directory on startup. Keep it across deployments and include it in backups (copy it while the service is stopped). Temporary uploads and reports do not need backups.

Back up code/configuration and protect `/etc/ai-feedback.env` separately. Existing folders from before temporary workspaces were introduced are not automatically removed and are no longer accessible through the app; archive or remove them separately after checking whether you need their contents. Do not delete files underneath a running report or push. Duplicate-send protection exists only until the workspace is deleted.

```sh
sudo journalctl -u ai-feedback -n 100 --no-pager
sudo journalctl -u ai-feedback -f
sudo du -sh /var/lib/ai-feedback
df -h /var/lib/ai-feedback
```

Gunicorn and application logs go to the system journal; use your server's journal retention limits. Nginx logs use its existing log rotation. Monitor service failures, disk capacity, provider 429 responses and processing latency. Never treat a successful health check as proof that a report or external push succeeded; use its saved status.
