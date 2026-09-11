from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.reactive import reactive
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, Footer, Header, Label, ListItem, ListView, RichLog, Static

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
        marker = "●" if self.status.loaded else "○"
        return f"{marker} {self.job_name}"

    def update_status(self, status: JobStatus) -> None:
        self.status = status
        self.query_one(Label).update(self.label_text)


class JobDetails(Static):
    def show_job(
        self,
        name: Optional[str],
        data: Optional[Mapping[str, Any]],
        status: Optional[JobStatus],
        error: Optional[str] = None,
    ) -> None:
        if error:
            self.update(f"[bold red]Error[/bold red]\n\n{error}")
            return
        if not name or data is None or status is None:
            self.update("Select a job to view its details.")
            return
        command = data.get("ProgramArguments", [])
        lines = [
            f"[bold]{name}[/bold]",
            "",
            f"Status       {format_status(status)}",
            f"PID          {status.pid if status.pid is not None else '-'}",
            f"Last exit    {status.last_exit_code if status.last_exit_code is not None else '-'}",
            f"Command      {display_command(command) if isinstance(command, list) else 'invalid plist'}",
            f"Directory    {data.get('WorkingDirectory', '-')}",
            f"Plist        {data.get('_path', '-')}",
            "",
            "s Start/Stop  R Restart  x Run now  l Logs  d Delete",
        ]
        self.update("\n".join(lines))


class ConfirmDeleteScreen(ModalScreen[bool]):
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
        with Horizontal(id="confirm-buttons"):
            yield Button("Delete", variant="error", id="confirm-delete")
            yield Button("Cancel", id="cancel-delete")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm-delete")

    def action_cancel(self) -> None:
        self.dismiss(False)


class LogsScreen(Screen[None]):
    BINDINGS = [
        ("escape", "app.pop_screen", "Back"),
        ("q", "app.pop_screen", "Back"),
        ("o", "show_stdout", "Stdout"),
        ("e", "show_stderr", "Stderr"),
        ("a", "show_all", "All"),
    ]

    def __init__(self, manager: AgentManager, name: str) -> None:
        super().__init__()
        self.manager = manager
        self.job_name = name
        self.stream = "all"
        self.refresh_timer = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="logs-screen"):
            yield Label(f"Logs: {self.job_name}", id="logs-title")
            yield Label("o stdout  e stderr  a all  q back", id="logs-help")
            yield RichLog(id="logs-output", highlight=False, markup=False, wrap=False)
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_logs()
        self.refresh_timer = self.set_interval(2, self.refresh_logs)

    def on_unmount(self) -> None:
        if self.refresh_timer is not None:
            self.refresh_timer.stop()

    def action_show_stdout(self) -> None:
        self.stream = "stdout"
        self.refresh_logs()

    def action_show_stderr(self) -> None:
        self.stream = "stderr"
        self.refresh_logs()

    def action_show_all(self) -> None:
        self.stream = "all"
        self.refresh_logs()

    def refresh_logs(self) -> None:
        try:
            data = self.manager.read_plist(self.job_name)
        except LagctlError as exc:
            try:
                output = self.query_one("#logs-output", RichLog)
            except NoMatches:
                return
            output.clear()
            output.write(str(exc))
            return

        paths = []
        if self.stream in ("stdout", "all"):
            paths.append(("stdout", Path(str(data["StandardOutPath"]))))
        if self.stream in ("stderr", "all"):
            paths.append(("stderr", Path(str(data["StandardErrorPath"]))))

        chunks = []
        for label, path in paths:
            chunks.append(f"--- {label} ---")
            if not path.exists():
                chunks.append("(log file does not exist)")
                continue
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
                chunks.extend(content.splitlines()[-200:])
            except OSError as exc:
                chunks.append(f"(cannot read log: {exc})")

        try:
            output = self.query_one("#logs-output", RichLog)
        except NoMatches:
            return
        output.clear()
        output.write("\n".join(chunks) or "(no log output)")


class LagctlApp(App[None]):
    TITLE = "lagctl"
    CSS_PATH = "styles.tcss"
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("r", "refresh", "Refresh"),
        ("enter", "show_selected", "Details"),
        ("s", "toggle_selected", "Start/Stop"),
        ("R", "restart_selected", "Restart"),
        ("x", "run_selected", "Run now"),
        ("l", "logs_selected", "Logs"),
        ("d", "delete_selected", "Delete"),
    ]

    selected_name = reactive(None)

    def __init__(self, manager: AgentManager) -> None:
        super().__init__()
        self.manager = manager
        self.jobs: dict[str, tuple[Mapping[str, Any], JobStatus]] = {}
        self.refresh_timer = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="dashboard"):
            with Vertical(id="jobs-pane"):
                yield Label("Jobs", id="jobs-title")
                yield Label("Auto-refresh: 5s", id="refresh-status")
                yield ListView(id="job-list")
            with Vertical(id="details-pane"):
                yield JobDetails("Select a job to view its details.", id="details")
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_jobs()
        self.refresh_timer = self.set_interval(5, self.refresh_jobs)

    def on_unmount(self) -> None:
        if self.refresh_timer is not None:
            self.refresh_timer.stop()

    def action_refresh(self) -> None:
        self.refresh_jobs()

    def action_show_selected(self) -> None:
        self.refresh_selected_details()

    def action_toggle_selected(self) -> None:
        if not self.selected_name:
            return
        try:
            if self.jobs[self.selected_name][1].loaded:
                self.manager.stop(self.selected_name)
                self.notify(f"Stopped {self.selected_name}", severity="information")
            else:
                self.manager.start(self.selected_name)
                self.notify(f"Started {self.selected_name}", severity="information")
            self.refresh_jobs()
        except LagctlError as exc:
            self.notify(str(exc), severity="error", timeout=5)

    def action_restart_selected(self) -> None:
        self._run_job_action("restart", self.manager.restart, "Restarted")

    def action_run_selected(self) -> None:
        self._run_job_action("run", self.manager.run_now, "Triggered")

    def action_logs_selected(self) -> None:
        if self.selected_name:
            self.push_screen(LogsScreen(self.manager, self.selected_name))

    def action_delete_selected(self) -> None:
        if self.selected_name:
            self.push_screen(
                ConfirmDeleteScreen(self.selected_name),
                self._delete_confirmed,
            )

    def _run_job_action(self, action: str, operation: Any, verb: str) -> None:
        if not self.selected_name:
            return
        try:
            operation(self.selected_name)
            self.notify(f"{verb} {self.selected_name}", severity="information")
            self.refresh_jobs()
        except LagctlError as exc:
            self.notify(f"{action}: {exc}", severity="error", timeout=5)

    def _delete_confirmed(self, confirmed: bool) -> None:
        if not confirmed or not self.selected_name:
            return
        name = self.selected_name
        try:
            self.manager.remove(name)
            self.selected_name = None
            self.notify(f"Removed {name}", severity="information")
            self.refresh_jobs()
        except LagctlError as exc:
            self.notify(f"delete: {exc}", severity="error", timeout=5)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        item = event.item
        if isinstance(item, JobListItem):
            self.selected_name = item.job_name
            self.refresh_selected_details()

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        item = event.item
        if isinstance(item, JobListItem):
            self.selected_name = item.job_name
            self.refresh_selected_details()

    def refresh_jobs(self) -> None:
        try:
            jobs: dict[str, tuple[Mapping[str, Any], JobStatus]] = {}
            for name, data in self.manager.iter_jobs():
                if data.get("Invalid"):
                    jobs[name] = (data, JobStatus(loaded=False))
                    continue
                jobs[name] = (data, self.manager.status(name))
        except LagctlError as exc:
            self._show_details_error(str(exc))
            self._update_refresh_status("Refresh failed")
            return

        self.jobs = jobs
        if self.selected_name not in jobs:
            self.selected_name = next(iter(jobs), None)
        try:
            job_list = self.query_one("#job-list", ListView)
        except NoMatches:
            return
        list_had_focus = job_list.has_focus
        selected_index = list(jobs).index(self.selected_name) if self.selected_name in jobs else 0
        existing_items = {
            item.job_name: item
            for item in job_list.children
            if isinstance(item, JobListItem)
        }
        for name, item in existing_items.items():
            if name not in jobs:
                item.remove()
        for name, (_, status) in jobs.items():
            item = existing_items.get(name)
            if item is None:
                job_list.append(JobListItem(name, status))
            else:
                item.update_status(status)
        if jobs:
            job_list.index = selected_index
            if list_had_focus:
                job_list.focus()
        self.refresh_selected_details()
        refreshed_at = datetime.now().strftime("%H:%M:%S")
        self._update_refresh_status(f"Auto-refresh: 5s | Updated {refreshed_at}")

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
