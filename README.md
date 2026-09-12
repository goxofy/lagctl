# lagctl

`lagctl` is a lightweight macOS LaunchAgent manager with both a script-friendly CLI and an interactive terminal UI. It creates and manages user-level jobs through the system `launchd` service, without `sudo` and without running its own background daemon.

The project is designed for Python, Node.js, shell scripts, local web services, tunnels, workers, scheduled jobs, and other processes that should survive terminal sessions or start automatically after login.

## Highlights

- Create, edit, start, stop, restart, trigger, inspect, and remove user LaunchAgents.
- Interactive Textual dashboard with keyboard and mouse support.
- Keep-alive, restart-on-failure, one-shot, and fixed-interval modes.
- Separate stdout and stderr logs with live viewing, filtering, pause, and clear operations.
- PID, last exit code, process tree, TCP listening ports, and network traffic statistics.
- Automatic interpreter detection for Python, Node.js, sh, bash, and zsh scripts.
- Captures the current `PATH` so jobs can find Homebrew, virtualenv, Node.js, and other tools.
- Atomic plist updates with rollback when replacement fails to load.
- zsh and bash completion, including managed job names.
- No third-party runtime dependency for the base CLI. Textual is optional and only required for the TUI.

## Scope

`lagctl` manages only the current user's LaunchAgents with labels under:

```text
local.launch-agent-manager.*
```

It does not manage system LaunchDaemons, arbitrary third-party plist files, Docker services, or calendar-style launchd schedules.

## Requirements

- macOS 14 Sonoma or newer.
- Python 3.9 or newer.
- System tools:
  - `/bin/launchctl`
  - `/usr/bin/tail`
  - `/usr/sbin/lsof` for listening-port discovery
  - `/usr/bin/nettop` for network traffic sampling
- Textual `0.70` to `<1.0` for the optional TUI.

Check the local environment with:

```bash
./bin/lagctl doctor
```

## Installation

### Source Checkout

```bash
git clone https://github.com/goxofy/lagctl.git
cd lagctl
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e '.[tui]'
make test
```

Run directly from the repository:

```bash
./bin/lagctl tui
./bin/lagctl list
```

### Install To `~/.local`

```bash
make install
```

Make sure `~/.local/bin` is in `PATH`:

```bash
export PATH="$HOME/.local/bin:$PATH"
lagctl doctor
```

`make install` copies the program and completion files, but it does not install Textual. The `python3` used by the installed launcher must be able to import Textual for `lagctl tui` to work. When using a project virtual environment, activate it before running the installed command.

Install to another prefix:

```bash
make install PREFIX="$HOME/bin-tools"
```

Uninstall program files:

```bash
make uninstall
```

Uninstalling does not remove existing LaunchAgents, logs, or managed scripts.

## Quick Start

Create a continuously running Python worker:

```bash
lagctl add worker -- /absolute/path/to/worker.py
```

Create a Node.js service with a working directory and environment variables:

```bash
lagctl add api \
  --cwd /Users/me/projects/api \
  --env NODE_ENV=production \
  --env PORT=3000 \
  -- /opt/homebrew/bin/node server.js
```

Inspect it:

```bash
lagctl list
lagctl status api
lagctl network api
lagctl logs -f api
```

Open the interactive dashboard:

```bash
lagctl tui
```

`--` is required for `add`. Everything to its right is stored as the managed command and arguments.

## Terminal UI

The TUI supports:

- Search by job name.
- Filter by all, running, stopped, or error state.
- Create and edit jobs with the complete configuration form.
- Start, stop, restart, and trigger jobs in background workers.
- Independent details, logs, delete confirmation, and activity-history screens.
- Responsive vertical layout in terminals narrower than 100 columns.
- Mouse-selectable, read-only job details with `Ctrl+C` clipboard copy.

### Main Screen

| Key | Action |
|---|---|
| `Up` / `Down` | Select a job |
| `Enter` | Open job details |
| `a` | Add a job |
| `e` | Edit the selected job |
| `s` | Start or stop the selected job |
| `R` | Restart the selected job |
| `x` | Trigger the selected job immediately |
| `l` | Open logs |
| `d` | Remove the selected job |
| `h` | Open recent activity history |
| `r` | Refresh |
| `q` | Quit |

### Log Screen

The log screen starts in command mode, so letters are interpreted as shortcuts instead of search text.

| Key | Action |
|---|---|
| `o` | Show stdout |
| `e` | Show stderr |
| `a` | Show both streams |
| `/` | Enter log search mode |
| `Enter` / `Esc` | Leave search mode |
| `p` | Pause or resume refresh |
| `f` | Toggle automatic scrolling |
| `c` | Clear the selected stream |
| `+` / `-` | Increase or decrease the line limit |
| `q` / `Esc` | Return to the previous screen |

The default line limit is 200, adjustable from 50 to 2000. Log reads and clear operations run in background workers.

## Job Modes

### Keep Alive

The default mode restarts the process whenever it exits:

```bash
lagctl add collector -- /Users/me/apps/collector.py
```

Set the launchd restart throttle:

```bash
lagctl add collector --throttle 30 -- /Users/me/apps/collector.py
```

### Restart On Failure

```bash
lagctl add importer --mode on-failure -- /Users/me/apps/importer.py
```

Exit code `0` stops the job. A non-zero exit code causes launchd to restart it.

### Fixed Interval

```bash
lagctl add sync --interval 900 -- /Users/me/apps/sync.py
```

Disable the initial run when the job is loaded:

```bash
lagctl add sync --interval 900 --no-run-at-load -- /Users/me/apps/sync.py
```

When `--interval` is used without `--mode`, one-shot semantics are selected automatically. If `--mode` is explicit, it must be `once`.

### Create Without Loading

```bash
lagctl add worker --no-start -- /Users/me/apps/worker.py
lagctl start worker
```

### Legacy Background Launchers

For a legacy script that starts a child with `nohup ... &` and exits:

```bash
lagctl add legacy-service \
  --mode once \
  --allow-background-children \
  --cwd /Users/me/apps/legacy-service \
  -- /bin/bash /Users/me/apps/legacy-service/run.sh start
```

This writes `AbandonProcessGroup=true`. It can only be combined with `--mode once` and cannot be combined with `--interval`.

This mode supervises only the launcher. A detached process may not appear in `lagctl status`, may not stop with `lagctl stop`, and cannot be reliably included in process-tree, port, or traffic statistics. Prefer a foreground process that replaces the launcher with `exec` whenever possible.

## Updating Jobs

Replace a managed job with `--force`:

```bash
lagctl add worker --force --env APP_ENV=production -- /Users/me/apps/worker.py
```

The old loaded job is stopped, the plist is atomically replaced, and the new configuration is loaded. If the new configuration fails to load, `lagctl` attempts to restore both the previous plist and its loaded state.

The TUI edit form uses the same update path.

## Lifecycle Commands

```bash
lagctl start worker
lagctl stop worker
lagctl restart worker
lagctl run worker
```

- `stop` uses `launchctl bootout`, so keep-alive jobs remain stopped.
- `start` enables and bootstraps the job.
- `restart` and `run` use `kickstart -k`.
- Restarting an unloaded job bootstraps it first, then triggers it even when `RunAtLoad=false`.

## Logs

```bash
lagctl logs worker
lagctl logs -f worker
lagctl logs --stream stderr -n 200 worker
```

Default paths:

```text
~/Library/Logs/LaunchAgentManager/<name>.stdout.log
~/Library/Logs/LaunchAgentManager/<name>.stderr.log
```

Logs are appended by launchd. `lagctl` does not currently rotate, compress, or limit log files, so high-volume jobs should use an external rotation policy.

Remove a job while keeping logs:

```bash
lagctl remove worker
```

Remove a job and its logs:

```bash
lagctl remove --purge-logs worker
```

## Process And Network Metrics

For running jobs, the TUI shows:

- Main PID and descendant PIDs.
- TCP listening addresses from `lsof`.
- Cumulative received and transmitted bytes from `nettop`.
- Approximate receive and transmit rates from a one-second `nettop` delta sample.

The same snapshot is available from the CLI:

```bash
lagctl network NAME
```

All running jobs are sampled together during a background refresh. Jobs without a PID do not invoke `nettop`. Collection failures are shown as `Network error` and do not block lifecycle operations.

Limitations:

- Only TCP LISTEN sockets are shown; UDP sockets and outbound remote endpoints are not listed.
- Traffic is attributed to the launchd process tree.
- Detached legacy processes cannot be reliably attributed to their original job.
- Short-lived processes may exit before the sample completes.
- Rates are samples, not long-term averages.

## Files And launchd Operations

Generated plist files:

```text
~/Library/LaunchAgents/local.launch-agent-manager.<name>.plist
```

Modern launchctl operations used by the tool:

```text
load     launchctl bootstrap gui/$UID <plist>
stop     launchctl bootout gui/$UID/<label>
restart  launchctl kickstart -k gui/$UID/<label>
status   launchctl print gui/$UID/<label>
```

Commands are stored directly in `ProgramArguments`; `lagctl` does not use `sh -c`. Put pipelines, redirection, or shell expansion in a script file instead of passing shell syntax as arguments.

## Shell Completion

Temporary zsh setup:

```bash
source <(lagctl completion zsh)
```

Temporary bash setup:

```bash
source <(lagctl completion bash)
```

`make install` also copies completion files to:

```text
~/.local/share/zsh/site-functions/_lagctl
~/.local/share/bash-completion/completions/lagctl
```

When an Oh My Zsh custom directory is available, `_lagctl` is also installed there.

## Security Notes

- Values passed with `--env` are stored as plain text in the plist.
- `--env KEY=VALUE` may also remain in shell history or process arguments.
- Do not store passwords, API tokens, or other sensitive credentials this way.
- Prefer macOS Keychain or a permission-restricted file read by the managed process.
- Managed plist files are written with mode `0600`.
- The TUI displays only environment variables explicitly provided by the user; an automatically inherited `PATH` is hidden from the details view.

## Troubleshooting

### Works In A Terminal But Fails Under launchd

```bash
lagctl status NAME
lagctl logs --stream stderr NAME
```

`launchd` does not read `.zshrc`, shell aliases, functions, or interactive shell initialization. Use absolute paths, set required environment variables explicitly, and select the correct working directory.

### Inspect The Raw Configuration

```bash
lagctl show NAME
launchctl print "gui/$UID/local.launch-agent-manager.NAME"
plutil -lint "$HOME/Library/LaunchAgents/local.launch-agent-manager.NAME.plist"
```

### Script Changes

You do not need to recreate a job after changing the script contents. Run:

```bash
lagctl restart NAME
```

Use `add --force` or the TUI edit form only when the command, arguments, environment, working directory, or launch policy changes.

## Development

Run the base test suite:

```bash
make test
```

Run the complete suite, including Textual tests:

```bash
source .venv/bin/activate
python -m unittest discover -s tests -v
```

Tests use temporary directories, a fake `launchctl`, and an injected network collector. They do not modify real LaunchAgents. A separate read-only smoke test can be used to validate `lsof` and `nettop` on a local macOS host.

Isolate a development instance:

```bash
LAGCTL_AGENT_DIR=/tmp/lagctl-agents \
LAGCTL_LOG_DIR=/tmp/lagctl-logs \
./bin/lagctl list
```

Project layout:

```text
lagctl.py              Core CLI and LaunchAgent service layer
lagctl_tui/            Optional Textual interface
bin/lagctl             Source and installed launcher
completions/           zsh and bash completion
tests/                 CLI, service, and TUI tests
Makefile               Test, install, and uninstall commands
pyproject.toml         Python metadata and optional TUI dependency
```

## Acknowledgements

The original workflow was inspired by [LaunchManager](https://github.com/Sean10000/LaunchManager).
