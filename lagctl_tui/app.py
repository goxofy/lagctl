from __future__ import annotations

from datetime import datetime
from pathlib import Path
import shlex
from typing import Any, Mapping, Optional, Sequence

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.events import Resize
from textual.reactive import reactive
from textual.screen import ModalScreen, Screen
from textual import work
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    RichLog,
    Select,
    Static,
    Switch,
    TextArea,
)
from rich.text import Text

from lagctl import AgentManager, JobStatus, LagctlError, display_command, format_status


class JobListItem(ListItem):
    def __init__(self, name: str, status: JobStatus, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.job_name = name
        self.status = status

    def compose(self) -> ComposeResult:
        yield Label(self.label_text)

    @property
    def label_text(self) -> str:
        marker = "!" if self.status.state == "error" else ("●" if self.status.loaded else "○")
        return f"{marker} {self.job_name}"

    def update_status(self, status: JobStatus) -> None:
        self.status = status
        self.query_one(Label).update(self.label_text)


def explicit_environment(data: Mapping[str, Any]) -> list[tuple[str, str]]:
    environment = data.get("EnvironmentVariables", {})
    if not isinstance(environment, dict):
        return [("invalid environment", "")]
    explicit_keys = data.get("LagctlExplicitEnvironmentKeys")
    if isinstance(explicit_keys, list):
        keys = {str(key) for key in explicit_keys}
    else:
        # Older plists do not record metadata; PATH was the only value
        # automatically added by lagctl in those files.
        keys = set(environment) - {"PATH"}
    return [(key, str(environment[key])) for key in sorted(keys) if key in environment]


def aligned_lines(rows: Sequence[tuple[str, object]], width: Optional[int] = None) -> list[str]:
    width = width if width is not None else max((len(label) for label, _ in rows), default=0)
    result = []
    for label, value in rows:
        value_lines = str(value).splitlines() or [""]
        prefix = f"{label.ljust(width)}  "
        result.append(prefix + value_lines[0])
        result.extend(" " * len(prefix) + line for line in value_lines[1:])
    return result


def format_bytes(value: Optional[int], per_second: bool = False) -> str:
    if value is None:
        return "-"
    amount = float(value)
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    unit = units[0]
    for unit in units:
        if abs(amount) < 1024 or unit == units[-1]:
            break
        amount /= 1024
    rendered = f"{amount:.1f}" if amount < 10 and unit != "B" else f"{amount:.0f}"
    return f"{rendered} {unit}{'/s' if per_second else ''}"


def format_job_details(name: str, data: Mapping[str, Any], status: JobStatus, plist_path: object) -> str:
    if data.get("_error"):
        return f"Name    {name}\nStatus  error\nError   {data['_error']}"
    command = data.get("ProgramArguments", [])
    network = data.get("_network")
    network_pids = getattr(network, "pids", ())
    listening = getattr(network, "listening", ())
    network_error = getattr(network, "error", None) or "-"
    rows = [
        ("Name", name),
        ("Status", format_status(status)),
        ("PID", status.pid if status.pid is not None else "-"),
        ("Last exit", status.last_exit_code if status.last_exit_code is not None else "-"),
        ("Command", display_command(command) if isinstance(command, list) else "invalid plist"),
        ("Directory", data.get("WorkingDirectory", "-")),
        ("Run at load", data.get("RunAtLoad", "-")),
        ("Keep alive", data.get("KeepAlive", "no")),
        ("Interval", data.get("StartInterval", "no")),
        ("Stdout", data.get("StandardOutPath", "-")),
        ("Stderr", data.get("StandardErrorPath", "-")),
        ("Plist", plist_path),
        ("Process PIDs", ", ".join(str(pid) for pid in network_pids) or "-"),
        ("Listening", ", ".join(listening) or "-"),
        ("RX total", format_bytes(getattr(network, "bytes_in", None))),
        ("TX total", format_bytes(getattr(network, "bytes_out", None))),
        ("RX rate", format_bytes(getattr(network, "rate_in", None), per_second=True)),
        ("TX rate", format_bytes(getattr(network, "rate_out", None), per_second=True)),
        ("Network error", network_error),
    ]
    main_label_width = max((len(label) for label, _ in rows), default=0)
    lines = aligned_lines(rows, main_label_width) + ["", "Environment"]
    environment = explicit_environment(data)
    lines.extend(
        ["  " + line for line in aligned_lines(environment, max(main_label_width - 2, 0))]
        if environment
        else ["  -"]
    )
    return "\n".join(lines)


class CopyableTextArea(TextArea):
    BINDINGS = [
        Binding("ctrl+c", "copy_selection", "Copy", show=False, priority=True),
    ]

    def action_copy_selection(self) -> None:
        if self.selected_text:
            self.app.copy_to_clipboard(self.selected_text)
            self.app.notify("Copied selection", severity="information", timeout=2)

    def on_key(self, event: Any) -> None:
        if not isinstance(self, JobDetails) or len(self.app.screen_stack) != 1:
            return
        if event.key == "a" and hasattr(self.app, "action_add_selected"):
            event.stop()
            self.app.action_add_selected()
        elif event.key == "e" and hasattr(self.app, "action_edit_selected"):
            event.stop()
            self.app.action_edit_selected()

class JobDetails(CopyableTextArea):
    def show_job(
        self,
        name: Optional[str],
        data: Optional[Mapping[str, Any]],
        status: Optional[JobStatus],
        error: Optional[str] = None,
    ) -> None:
        if error:
            self.load_text(f"Error\n\n{error}")
            return
        if not name or data is None or status is None:
            self.load_text("Select a job to view its details.")
            return
        self.load_text(format_job_details(name, data, status, data.get("_path", "-")))


class DetailsScreen(Screen[None]):
    BINDINGS = [
        ("escape", "app.pop_screen", "Back"),
        ("q", "app.pop_screen", "Back"),
        ("r", "refresh_details", "Refresh"),
        ("l", "show_logs", "Logs"),
        ("s", "toggle_job", "Start/Stop"),
        ("R", "restart_job", "Restart"),
        ("x", "run_job", "Run now"),
    ]

    def __init__(
        self,
        manager: AgentManager,
        name: str,
        data: Mapping[str, Any],
        status: JobStatus,
    ) -> None:
        super().__init__()
        self.manager = manager
        self.job_name = name
        self.data = data
        self.status = status
        self.operation_running = False

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="details-screen"):
            yield Label(f"Details: {self.job_name}", id="details-title")
            yield CopyableTextArea("", read_only=True, soft_wrap=False, id="details-output")
            with Horizontal(id="details-actions"):
                yield Button("Start/Stop", id="details-toggle")
                yield Button("Restart", id="details-restart")
                yield Button("Run now", id="details-run")
                yield Button("Logs", id="details-logs")
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_details()

    def action_refresh_details(self) -> None:
        self.refresh_details()

    def action_show_logs(self) -> None:
        self.app.push_screen(LogsScreen(self.manager, self.job_name))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        actions = {
            "details-toggle": self.action_toggle_job,
            "details-restart": self.action_restart_job,
            "details-run": self.action_run_job,
            "details-logs": self.action_show_logs,
        }
        action = actions.get(event.button.id)
        if action is not None:
            action()

    def action_toggle_job(self) -> None:
        self._run_operation("stop" if self.status.loaded else "start")

    def action_restart_job(self) -> None:
        self._run_operation("restart")

    def action_run_job(self) -> None:
        self._run_operation("run")

    @work(thread=True, exclusive=True)
    def _run_operation(self, action: str) -> None:
        if self.operation_running:
            return
        self.operation_running = True
        self.app.call_from_thread(self._set_action_buttons, True)
        try:
            {"start": self.manager.start, "stop": self.manager.stop, "restart": self.manager.restart, "run": self.manager.run_now}[action](self.job_name)
        except Exception as exc:
            self.app.call_from_thread(self._operation_finished, action, str(exc))
        else:
            self.app.call_from_thread(self._operation_finished, action, None)

    def _operation_finished(self, action: str, error: Optional[str]) -> None:
        self.operation_running = False
        self._set_action_buttons(False)
        if error:
            self.app.record_event(f"{action} {self.job_name}: {error}", "error")
        else:
            self.app.record_event(f"{action} {self.job_name} completed", "information")
            self.refresh_details()

    def _set_action_buttons(self, disabled: bool) -> None:
        for button in self.query("#details-actions Button"):
            button.disabled = disabled

    def refresh_details(self) -> None:
        self._load_details()

    @work(thread=True, exclusive=True)
    def _load_details(self) -> None:
        try:
            data = self.manager.read_plist(self.job_name)
            status = self.manager.status(self.job_name)
            if status.pid is not None:
                data = {
                    **data,
                    "_network": self.manager.network_collector({self.job_name: status.pid}).get(self.job_name),
                }
        except LagctlError as exc:
            self.app.call_from_thread(self._show_details_error, str(exc))
        else:
            self.app.call_from_thread(self._show_details, data, status)

    def _show_details(self, data: Mapping[str, Any], status: JobStatus) -> None:
        self.data = data
        self.status = status
        try:
            self.query_one("#details-output", TextArea).load_text(
                format_job_details(self.job_name, data, status, self.manager.plist_path(self.job_name))
            )
        except NoMatches:
            pass

    def _show_details_error(self, message: str) -> None:
        try:
            self.query_one("#details-output", TextArea).load_text(f"Error\n\n{message}")
        except NoMatches:
            pass


class ConfirmDeleteScreen(ModalScreen[Optional[bool]]):
    BINDINGS = [
        ("escape", "cancel", "Cancel"),
        ("q", "cancel", "Cancel"),
    ]

    def __init__(self, name: str) -> None:
        super().__init__()
        self.job_name = name

    def compose(self) -> ComposeResult:
        yield Static(
            f"Delete [bold]{self.job_name}[/bold]?\n\n"
            "The job will be stopped and its plist removed. Logs are kept.",
            id="confirm-copy",
        )
        yield Switch(value=False, id="purge-logs")
        yield Label("Also delete logs", id="purge-logs-label")
        with Horizontal(id="confirm-buttons"):
            yield Button("Delete", variant="error", id="confirm-delete")
            yield Button("Cancel", id="cancel-delete")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "confirm-delete":
            self.dismiss(self.query_one("#purge-logs", Switch).value)
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class HistoryScreen(Screen[None]):
    BINDINGS = [("escape", "app.pop_screen", "Back"), ("q", "app.pop_screen", "Back")]

    def __init__(self, entries: Sequence[str]) -> None:
        super().__init__()
        self.entries = list(entries)

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="history-screen"):
            yield Label("Activity history", id="history-title")
            yield CopyableTextArea(
                "\n".join(self.entries) if self.entries else "No activity recorded.",
                read_only=True,
                soft_wrap=False,
                id="history-output",
            )
        yield Footer()


class AddJobScreen(ModalScreen[Optional[dict[str, Any]]]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, job: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__()
        self.job = dict(job or {})
        self.editing = bool(self.job)

    def compose(self) -> ComposeResult:
        command = self.job.get("ProgramArguments", [])
        environment = self.job.get("EnvironmentVariables", {})
        explicit_keys = self.job.get("LagctlExplicitEnvironmentKeys", list(environment))
        keep_alive = self.job.get("KeepAlive")
        if self.job.get("StartInterval") is not None:
            mode = "once"
        elif keep_alive is True:
            mode = "keep-alive"
        elif isinstance(keep_alive, dict) and keep_alive.get("SuccessfulExit") is False:
            mode = "on-failure"
        else:
            mode = "once"
        env_text = "\n".join(
            f"{key}={environment[key]}" for key in explicit_keys
            if key in environment and (key != "PATH" or key in explicit_keys)
        )
        yield Static("Edit LaunchAgent" if self.editing else "Add LaunchAgent", id="add-title")
        yield Input(value=self.job.get("name", ""), placeholder="Job name", id="add-name")
        yield Input(value=str(command[0]) if command else "", placeholder="Command path or executable", id="add-command")
        yield Input(value=shlex.join(command[1:]) if command else "", placeholder="Arguments, separated by spaces (optional)", id="add-arguments")
        yield Input(value=str(self.job.get("WorkingDirectory", "")), placeholder="Working directory (optional)", id="add-cwd")
        yield Select(
            [("Keep alive", "keep-alive"), ("On failure", "on-failure"), ("Once", "once")],
            value=mode,
            id="add-mode",
        )
        yield Input(value=str(self.job.get("StartInterval", "")), placeholder="Interval in seconds (optional)", id="add-interval")
        yield Input(value=str(self.job.get("ThrottleInterval", 10)), placeholder="Throttle seconds", id="add-throttle")
        yield Label("Environment variables, one KEY=VALUE per line (optional)", id="add-env-help")
        yield TextArea(env_text, id="add-env")
        with Horizontal(classes="add-switch-row"):
            with Horizontal(classes="add-switch-option"):
                yield Switch(value=self.job.get("RunAtLoad", True), id="add-run-at-load")
                yield Label("Run at load", id="add-run-label")
            with Horizontal(classes="add-switch-option"):
                yield Switch(value=self.job.get("_start_after_edit", not self.editing), id="add-start")
                yield Label("Start after create", id="add-start-label")
        with Horizontal(classes="add-switch-row"):
            with Horizontal(classes="add-switch-option"):
                yield Switch(value="PATH" not in explicit_keys, id="add-inherit-path")
                yield Label("Inherit current PATH", id="add-inherit-path-label")
            with Horizontal(classes="add-switch-option"):
                yield Switch(value=bool(self.job.get("AbandonProcessGroup", False)), id="add-background")
                yield Label("Allow background children", id="add-background-label")
        with Horizontal(id="add-buttons"):
            yield Button("Save" if self.editing else "Create", variant="primary", id="add-submit")
            yield Button("Cancel", id="add-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "add-cancel":
            self.dismiss(None)
        else:
            payload = self.submit()
            if payload is not None:
                self.dismiss(payload)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def submit(self) -> Optional[dict[str, Any]]:
        name = self.query_one("#add-name", Input).value.strip()
        command = self.query_one("#add-command", Input).value.strip()
        arguments = self.query_one("#add-arguments", Input).value
        cwd = self.query_one("#add-cwd", Input).value.strip() or None
        mode = self.query_one("#add-mode", Select).value
        interval_text = self.query_one("#add-interval", Input).value.strip()
        env_text = self.query_one("#add-env", TextArea).text
        if not name or not command:
            self.notify("Job name and command are required", severity="error", timeout=4)
            return None
        try:
            raw_command = [command, *shlex.split(arguments)]
        except ValueError as exc:
            self.notify(f"Invalid arguments: {exc}", severity="error", timeout=4)
            return None
        interval = None
        if interval_text:
            try:
                interval = int(interval_text)
            except ValueError:
                self.notify("Interval must be an integer", severity="error", timeout=4)
                return None
        try:
            throttle = int(self.query_one("#add-throttle", Input).value.strip() or "10")
        except ValueError:
            self.notify("Throttle must be an integer", severity="error", timeout=4)
            return None
        env_items = [line.strip() for line in env_text.splitlines() if line.strip()]
        return {
            "name": name,
            "raw_command": raw_command,
            "cwd": cwd,
            "env_items": env_items,
            "mode": None if mode is Select.BLANK else str(mode),
            "interval": interval,
            "throttle_interval": throttle,
            "run_at_load": self.query_one("#add-run-at-load", Switch).value,
            "start": self.query_one("#add-start", Switch).value,
            "force": self.editing,
            "inherit_path": self.query_one("#add-inherit-path", Switch).value,
            "allow_background_children": self.query_one("#add-background", Switch).value,
        }


class LogOutput(RichLog):
    can_focus = False


class LogsScreen(Screen[None]):
    BINDINGS = [
        Binding("escape", "back", "Back", priority=True),
        Binding("q", "back", "Back", priority=True),
        ("o", "show_stdout", "Stdout"),
        ("e", "show_stderr", "Stderr"),
        ("a", "show_all", "All"),
        ("p", "toggle_pause", "Pause"),
        ("+", "increase_lines", "More lines"),
        ("-", "decrease_lines", "Fewer lines"),
        ("f", "toggle_auto_scroll", "Auto scroll"),
        ("c", "clear_logs", "Clear"),
        ("slash", "focus_search", "Search"),
    ]

    def __init__(self, manager: AgentManager, name: str) -> None:
        super().__init__()
        self.manager = manager
        self.job_name = name
        self.stream = "all"
        self.refresh_timer = None
        self.paused = False
        self.lines = 200
        self.search = ""
        self.auto_scroll = True

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="logs-screen"):
            yield Label(f"Logs: {self.job_name}", id="logs-title")
            yield Input(placeholder="Filter log text", id="logs-search", disabled=True)
            yield LogOutput(id="logs-output", highlight=False, markup=False, wrap=False)
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_logs()
        self.set_focus(None)
        self.refresh_timer = self.set_interval(2, self.refresh_logs)

    def key_escape(self, event: Any) -> None:
        event.stop()
        search = self.query_one("#logs-search", Input)
        if search.has_focus:
            search.disabled = True
            self.set_focus(None)
        else:
            self.app.pop_screen()
            self.app.call_after_refresh(self.app.focus_job_list)

    def key_q(self, event: Any) -> None:
        event.stop()
        if not self.query_one("#logs-search", Input).has_focus:
            self.app.pop_screen()
            self.app.call_after_refresh(self.app.focus_job_list)

    def on_unmount(self) -> None:
        if self.refresh_timer is not None:
            self.refresh_timer.stop()

    def action_show_stdout(self) -> None:
        self.stream = "stdout"
        self.refresh_logs()

    def action_focus_search(self) -> None:
        search = self.query_one("#logs-search", Input)
        search.disabled = False
        search.focus()

    def action_back(self) -> None:
        search = self.query_one("#logs-search", Input)
        if search.has_focus:
            search.disabled = True
            self.focus()
        else:
            self.app.pop_screen()
            self.app.call_after_refresh(self.app.focus_job_list)

    def key_o(self, event: Any) -> None:
        if not self.query_one("#logs-search", Input).has_focus:
            event.stop()
            self.action_show_stdout()

    def key_e(self, event: Any) -> None:
        if not self.query_one("#logs-search", Input).has_focus:
            event.stop()
            self.action_show_stderr()

    def key_a(self, event: Any) -> None:
        if not self.query_one("#logs-search", Input).has_focus:
            event.stop()
            self.action_show_all()

    def on_key(self, event: Any) -> None:
        search = self.query_one("#logs-search", Input)
        if search.has_focus and event.key in ("escape", "enter"):
            event.stop()
            search.disabled = True
            self.focus()

    def action_show_stderr(self) -> None:
        self.stream = "stderr"
        self.refresh_logs()

    def action_show_all(self) -> None:
        self.stream = "all"
        self.refresh_logs()

    def action_toggle_pause(self) -> None:
        self.paused = not self.paused

    def action_toggle_auto_scroll(self) -> None:
        self.auto_scroll = not self.auto_scroll
        self.query_one("#logs-output", RichLog).auto_scroll = self.auto_scroll

    def action_clear_logs(self) -> None:
        self._clear_log_files(self.stream)

    @work(thread=True, exclusive=True)
    def _clear_log_files(self, stream: str) -> None:
        try:
            data = self.manager.read_plist(self.job_name)
            keys = []
            if stream in ("stdout", "all"):
                keys.append("StandardOutPath")
            if stream in ("stderr", "all"):
                keys.append("StandardErrorPath")
            for key in keys:
                Path(str(data[key])).write_text("", encoding="utf-8")
        except Exception as exc:
            self.app.call_from_thread(self.app.record_event, f"clear logs {self.job_name}: {exc}", "error")
        else:
            self.app.call_from_thread(self.app.record_event, f"cleared {stream} logs for {self.job_name}", "information")
            self.app.call_from_thread(self.refresh_logs)

    def action_increase_lines(self) -> None:
        self.lines = min(self.lines + 100, 2000)
        self.refresh_logs()

    def action_decrease_lines(self) -> None:
        self.lines = max(self.lines - 100, 50)
        self.refresh_logs()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "logs-search":
            self.search = event.value
            self.refresh_logs()

    def refresh_logs(self) -> None:
        if self.paused:
            return
        self._load_logs(self.stream, self.lines, self.search)

    @work(thread=True, exclusive=True)
    def _load_logs(self, stream: str, lines: int, search: str) -> None:
        try:
            data = self.manager.read_plist(self.job_name)
        except LagctlError as exc:
            self.app.call_from_thread(self._show_log_content, str(exc))
            return

        paths = []
        if stream in ("stdout", "all"):
            paths.append(("stdout", Path(str(data["StandardOutPath"]))))
        if stream in ("stderr", "all"):
            paths.append(("stderr", Path(str(data["StandardErrorPath"]))))

        chunks = []
        for label, path in paths:
            chunks.append(f"--- {label} ---")
            if not path.exists():
                chunks.append("(log file does not exist)")
                continue
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
                log_lines = content.splitlines()[-lines:]
                if search:
                    log_lines = [line for line in log_lines if search.lower() in line.lower()]
                chunks.extend(log_lines)
            except OSError as exc:
                chunks.append(f"(cannot read log: {exc})")

        self.app.call_from_thread(self._show_log_content, "\n".join(chunks) or "(no log output)")

    def _show_log_content(self, content: str) -> None:
        try:
            output = self.query_one("#logs-output", RichLog)
        except NoMatches:
            return
        output.clear()
        if self.search:
            needle = self.search.lower()
            for line in content.splitlines():
                text = Text(line)
                lower = line.lower()
                start = 0
                while True:
                    index = lower.find(needle, start)
                    if index < 0:
                        break
                    text.stylize("reverse", index, index + len(needle))
                    start = index + len(needle)
                output.write(text)
        else:
            output.write(content)
        if self.auto_scroll:
            output.scroll_end(animate=False)


class LagctlApp(App[None]):
    TITLE = "lagctl"
    CSS_PATH = "styles.tcss"
    BINDINGS = [
        ("q", "quit", "Quit"),
        Binding("ctrl+c", "copy_selection", "Copy", show=False, priority=True),
        ("r", "refresh", "Refresh"),
        ("enter", "show_selected", "Details"),
        ("s", "toggle_selected", "Start/Stop"),
        ("R", "restart_selected", "Restart"),
        ("x", "run_selected", "Run now"),
        ("l", "logs_selected", "Logs"),
        ("a", "add_selected", "Add"),
        ("e", "edit_selected", "Edit"),
        ("d", "delete_selected", "Delete"),
        ("h", "show_history", "History"),
    ]

    selected_name = reactive(None)

    def __init__(self, manager: AgentManager) -> None:
        super().__init__()
        self.manager = manager
        self.jobs: dict[str, tuple[Mapping[str, Any], JobStatus]] = {}
        self.refresh_timer = None
        self.search = ""
        self.status_filter = "all"
        self.operation_running = False
        self.events: list[str] = []

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="dashboard"):
            with Vertical(id="jobs-pane"):
                yield Label("Jobs", id="jobs-title")
                yield Label("Auto-refresh: 5s", id="refresh-status")
                yield Input(placeholder="Search jobs", id="job-search")
                yield Select(
                    [("All", "all"), ("Running", "running"), ("Stopped", "stopped"), ("Errors", "error")],
                    value="all",
                    id="status-filter",
                )
                yield ListView(id="job-list")
            with Vertical(id="details-pane"):
                yield JobDetails("Select a job to view its details.", read_only=True, soft_wrap=False, id="details")
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_jobs()
        self.query_one("#job-list", ListView).focus()
        self.refresh_timer = self.set_interval(5, self.refresh_jobs)

    def on_unmount(self) -> None:
        if self.refresh_timer is not None:
            self.refresh_timer.stop()

    def action_refresh(self) -> None:
        self.refresh_jobs()

    def focus_job_list(self) -> None:
        try:
            self.query_one("#job-list", ListView).focus()
        except NoMatches:
            pass

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "job-search":
            self.search = event.value.lower()
            self.render_job_list()

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "status-filter":
            self.status_filter = str(event.value)
            self.render_job_list()

    def on_resize(self, event: Resize) -> None:
        dashboard = self.query_one("#dashboard")
        jobs_pane = self.query_one("#jobs-pane")
        details_pane = self.query_one("#details-pane")
        if event.size.width < 100:
            dashboard.styles.layout = "vertical"
            jobs_pane.styles.width = "1fr"
            jobs_pane.styles.height = "40%"
            details_pane.styles.width = "1fr"
            details_pane.styles.height = "60%"
        else:
            dashboard.styles.layout = "horizontal"
            jobs_pane.styles.width = "27%"
            jobs_pane.styles.height = "1fr"
            details_pane.styles.width = "73%"
            details_pane.styles.height = "1fr"

    def action_copy_selection(self) -> None:
        focused = self.focused
        if isinstance(focused, CopyableTextArea) and focused.selected_text:
            self.copy_to_clipboard(focused.selected_text)
            self.notify("Copied selection", severity="information", timeout=2)

    def action_show_selected(self) -> None:
        self.show_selected_details()

    def action_toggle_selected(self) -> None:
        if not self.selected_name:
            return
        loaded = self.jobs[self.selected_name][1].loaded
        self._start_operation("stop" if loaded else "start")

    def action_restart_selected(self) -> None:
        self._start_operation("restart")

    def action_run_selected(self) -> None:
        self._start_operation("run")

    def action_logs_selected(self) -> None:
        if self.selected_name:
            self.push_screen(LogsScreen(self.manager, self.selected_name))

    def action_add_selected(self) -> None:
        self.push_screen(AddJobScreen(), self._add_submitted)

    def action_edit_selected(self) -> None:
        if not self.selected_name or self.selected_name not in self.jobs:
            return
        data = dict(self.jobs[self.selected_name][0])
        data["name"] = self.selected_name
        data["_start_after_edit"] = self.jobs[self.selected_name][1].loaded
        self.push_screen(AddJobScreen(data), self._add_submitted)

    def action_show_history(self) -> None:
        self.push_screen(HistoryScreen(self.events))

    def record_event(self, message: str, severity: str = "information") -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.events.append(f"{timestamp}  {message}")
        self.events = self.events[-100:]
        self.notify(message, severity=severity, timeout=5 if severity == "error" else 2)

    def _add_submitted(self, payload: Optional[dict[str, Any]]) -> None:
        if payload is None:
            return
        if getattr(self, "operation_running", False):
            self.notify("Another task operation is still running", severity="warning")
            return
        self.operation_running = True
        self.notify(f"Creating {payload['name']}...", severity="information", timeout=2)
        self._create_job(payload)

    @work(thread=True, exclusive=True)
    def _create_job(self, payload: dict[str, Any]) -> None:
        try:
            self.manager.add(**payload)
        except Exception as exc:
            self.call_from_thread(self._create_failed, payload["name"], str(exc))
        else:
            self.call_from_thread(self._create_succeeded, payload["name"])

    def _create_succeeded(self, name: str) -> None:
        self.operation_running = False
        self.selected_name = name
        self.record_event(f"Created {name}")
        self.refresh_jobs()

    def _create_failed(self, name: str, message: str) -> None:
        self.operation_running = False
        self.record_event(f"create {name}: {message}", "error")

    def action_delete_selected(self) -> None:
        if self.selected_name:
            self.push_screen(
                ConfirmDeleteScreen(self.selected_name),
                self._delete_confirmed,
            )

    def show_selected_details(self) -> None:
        if not self.selected_name or self.selected_name not in self.jobs:
            return
        data, status = self.jobs[self.selected_name]
        self.push_screen(DetailsScreen(self.manager, self.selected_name, data, status))

    def _start_operation(self, action: str) -> None:
        if not self.selected_name:
            return
        name = self.selected_name
        if getattr(self, "operation_running", False):
            self.notify("Another task operation is still running", severity="warning")
            return
        self.operation_running = True
        self.notify(f"{action.capitalize()}ing {name}...", severity="information", timeout=2)
        self._execute_operation(action, name)

    @work(thread=True, exclusive=True)
    def _execute_operation(self, action: str, name: str) -> None:
        try:
            operations = {
                "start": self.manager.start,
                "stop": self.manager.stop,
                "restart": self.manager.restart,
                "run": self.manager.run_now,
            }
            operations[action](name)
        except LagctlError as exc:
            self.call_from_thread(self._operation_failed, action, str(exc))
        except Exception as exc:
            self.call_from_thread(self._operation_failed, action, str(exc))
        else:
            self.call_from_thread(self._operation_succeeded, action, name)

    def _operation_succeeded(self, action: str, name: str) -> None:
        self.operation_running = False
        verbs = {"start": "Started", "stop": "Stopped", "restart": "Restarted", "run": "Triggered"}
        self.record_event(f"{verbs[action]} {name}")
        self.refresh_jobs()

    def _operation_failed(self, action: str, message: str) -> None:
        self.operation_running = False
        self.record_event(f"{action}: {message}", "error")

    def _delete_confirmed(self, confirmed: Optional[bool]) -> None:
        if confirmed is None or not self.selected_name:
            return
        name = self.selected_name
        if getattr(self, "operation_running", False):
            self.notify("Another task operation is still running", severity="warning")
            return
        self.operation_running = True
        self._delete_job(name, confirmed)

    @work(thread=True, exclusive=True)
    def _delete_job(self, name: str, purge_logs: bool) -> None:
        try:
            self.manager.remove(name, purge_logs=purge_logs)
        except Exception as exc:
            self.call_from_thread(self._delete_failed, name, str(exc))
        else:
            self.call_from_thread(self._delete_succeeded, name)

    def _delete_succeeded(self, name: str) -> None:
        self.operation_running = False
        self.selected_name = None
        self.record_event(f"Removed {name}")
        self.refresh_jobs()

    def _delete_failed(self, name: str, message: str) -> None:
        self.operation_running = False
        self.record_event(f"delete {name}: {message}", "error")

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        item = event.item
        if isinstance(item, JobListItem):
            self.selected_name = item.job_name
            self.show_selected_details()

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        item = event.item
        if isinstance(item, JobListItem):
            self.selected_name = item.job_name
            self.refresh_selected_details()

    def refresh_jobs(self) -> None:
        self._load_jobs()

    @work(thread=True, exclusive=True)
    def _load_jobs(self) -> None:
        try:
            jobs: dict[str, tuple[Mapping[str, Any], JobStatus]] = {}
            for name, data in self.manager.iter_jobs():
                if data.get("Invalid"):
                    jobs[name] = ({**data, "_error": "Invalid plist"}, JobStatus(loaded=False, state="error"))
                    continue
                try:
                    jobs[name] = (data, self.manager.status(name))
                except LagctlError as exc:
                    jobs[name] = ({**data, "_error": str(exc)}, JobStatus(loaded=False, state="error"))
            root_pids = {name: status.pid for name, (_, status) in jobs.items() if status.pid is not None}
            network_stats = self.manager.network_collector(root_pids)
            jobs = {
                name: ({**data, "_network": network_stats.get(name)}, status)
                for name, (data, status) in jobs.items()
            }
        except Exception as exc:
            self.call_from_thread(self._jobs_load_failed, str(exc))
            return
        self.call_from_thread(self._jobs_loaded, jobs)

    def _jobs_load_failed(self, message: str) -> None:
        self._show_details_error(message)
        self._update_refresh_status("Refresh failed")

    def _jobs_loaded(self, jobs: dict[str, tuple[Mapping[str, Any], JobStatus]]) -> None:
        self.jobs = jobs
        if self.selected_name not in jobs:
            self.selected_name = next(iter(jobs), None)
        self.render_job_list()

    def render_job_list(self) -> None:
        try:
            job_list = self.query_one("#job-list", ListView)
        except NoMatches:
            return
        visible_jobs = {
            name: value for name, value in self.jobs.items()
            if (not self.search or self.search in name.lower()) and self._matches_status(value[1])
        }
        if self.selected_name not in visible_jobs:
            self.selected_name = next(iter(visible_jobs), None)
        list_had_focus = job_list.has_focus
        selected_index = list(visible_jobs).index(self.selected_name) if self.selected_name in visible_jobs else 0
        existing_items = {
            item.job_name: item
            for item in job_list.children
            if isinstance(item, JobListItem)
        }
        for name, item in existing_items.items():
            if name not in visible_jobs:
                item.remove()
        for name, (_, status) in visible_jobs.items():
            item = existing_items.get(name)
            if item is None:
                job_list.append(JobListItem(name, status))
            else:
                item.update_status(status)
        if visible_jobs:
            job_list.index = selected_index
            if list_had_focus:
                job_list.focus()
        self.refresh_selected_details()
        refreshed_at = datetime.now().strftime("%H:%M:%S")
        self._update_refresh_status(f"Auto-refresh: 5s | Updated {refreshed_at}")

    def _matches_status(self, status: JobStatus) -> bool:
        if self.status_filter == "all":
            return True
        if self.status_filter == "running":
            return status.pid is not None
        if self.status_filter == "stopped":
            return not status.loaded and status.state != "error"
        return status.state == "error"

    def refresh_selected_details(self) -> None:
        try:
            details = self.query_one(JobDetails)
        except NoMatches:
            return
        if self.selected_name is None or self.selected_name not in self.jobs:
            details.show_job(None, None, None)
            return
        data, status = self.jobs[self.selected_name]
        enriched = dict(data)
        enriched["_path"] = str(self.manager.plist_path(self.selected_name))
        details.show_job(self.selected_name, enriched, status)

    def _show_details_error(self, message: str) -> None:
        try:
            self.query_one(JobDetails).show_job(None, None, None, message)
        except NoMatches:
            pass

    def _update_refresh_status(self, message: str) -> None:
        try:
            self.query_one("#refresh-status", Label).update(message)
        except NoMatches:
            pass
