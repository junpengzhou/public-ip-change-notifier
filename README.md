# Public IP Change Notifier

This service polls one or more public-IP endpoints for each configured WAN and
sends one Microsoft Teams Incoming Webhook message when a known WAN changes.
The first successful cycle creates the local baseline and does not notify.

## Install

```powershell
D:\Environment\Python\uv\bin\uv.exe sync --extra dev
```

The project uses a local `.venv` managed by uv. From a new terminal where the
user PATH has been refreshed, the same command can be shortened to `uv sync
--extra dev`.

The shared uv installation uses these user-level settings on Windows:

- Executable directory: `D:\Environment\Python\uv\bin`
- Download cache: `D:\Environment\Python\uv\cache`
- Managed Python versions: `D:\Environment\Python\uv\python`

Each project still gets its own `.venv`; only uv's executable, cache, and
optional managed interpreters are shared. For another project, run:

```powershell
uv init my-project
Set-Location my-project
uv python pin 3.11
uv venv
uv add httpx
uv run python -c "import httpx; print(httpx.__version__)"
```

## Configuration

```yaml
interval_seconds: 60
state_file: "./data/public-ip-state.json"

teams:
  webhook_url: "https://outlook.office.com/webhook/..."

servers:
  wan1:
    - "https://ifconfig.me/ip"
    - "https://ipinfo.io/ip"
  wan2:
    - "https://api.ipify.org"
```

URLs for one WAN are tried in order. The first response that is a valid IP is
used. If every URL fails, the previous value is retained and the next URL list
is retried on the next cycle.

The following user-level environment variables override YAML values:

- `PUBLIC_IP_NOTIFIER_TEAMS_WEBHOOK_URL`
- `PUBLIC_IP_NOTIFIER_INTERVAL_SECONDS`
- `PUBLIC_IP_NOTIFIER_STATE_FILE`

Keep webhook secrets out of committed files. A missing webhook disables
notifications but still allows the state file to be maintained.

## Run

```powershell
uv run public-ip-notifier --config config.yaml
```

Use `--once` to collect one cycle and exit, which is useful for diagnostics:

```powershell
uv run public-ip-notifier --config config.yaml --once
```

The state file contains a JSON mapping from WAN names to their latest known
IPs. It is written atomically. If a Teams delivery fails during a detected
change, the old state is retained so the same change is retried on the next
cycle.

## Service operation

For systemd, run the long-lived command from the project directory and provide
the webhook through an environment file rather than putting it in the unit:

```ini
[Service]
WorkingDirectory=/opt/public-ip-change-notifier
ExecStart=/opt/public-ip-change-notifier/.venv/bin/public-ip-notifier --config /etc/public-ip-notifier/config.yaml
EnvironmentFile=/etc/public-ip-notifier/notifier.env
Restart=always
```

On Windows Task Scheduler, use `uv run public-ip-notifier --config
C:\path\to\config.yaml` as the action and configure the task to run whether or
not a user is logged on. The normal mode is intentionally long-running;
`--once` is for diagnostics or an external scheduler.
