#!/usr/bin/env python3
"""A small macOS LaunchAgent manager for long-running scripts."""

from __future__ import annotations

import argparse
import csv
import io
import os
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import platform
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple


VERSION = "0.3.0"
LABEL_PREFIX = "local.launch-agent-manager."
NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
ENV_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class LagctlError(RuntimeError):
    pass


@dataclass(frozen=True)
class JobStatus:
    loaded: bool
    state: str = "unloaded"
    pid: Optional[int] = None
    last_exit_code: Optional[int] = None


@dataclass(frozen=True)
class NetworkStats:
    pids: Tuple[int, ...] = ()
    listening: Tuple[str, ...] = ()
    bytes_in: Optional[int] = None
    bytes_out: Optional[int] = None
    rate_in: Optional[int] = None
    rate_out: Optional[int] = None
    error: Optional[str] = None


Runner = Callable[..., subprocess.CompletedProcess[str]]


def default_runner(args: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(args), text=True, capture_output=True, **kwargs)


def collect_network_stats(root_pids: Mapping[str, int]) -> Dict[str, NetworkStats]:
    """Collect process-tree ports and network counters for running jobs."""
    if not root_pids:
        return {}
    try:
        process_result = default_runner(["/bin/ps", "-axo", "pid=,ppid="], timeout=5)
        process_result.check_returncode()
        children: Dict[int, List[int]] = {}
        for line in process_result.stdout.splitlines():
            values = line.split()
            if len(values) != 2:
                continue
            pid, parent = (int(value) for value in values)
            children.setdefault(parent, []).append(pid)

        job_pids: Dict[str, set[int]] = {}
        all_pids: set[int] = set()
        for name, root_pid in root_pids.items():
            pending = [root_pid]
            related: set[int] = set()
            while pending:
                pid = pending.pop()
                if pid in related:
                    continue
                related.add(pid)
                pending.extend(children.get(pid, ()))
            job_pids[name] = related
            all_pids.update(related)

        ports_by_pid = _listening_ports_by_pid()
        totals = _nettop_counters(all_pids, delta=False)
        rates = _nettop_counters(all_pids, delta=True)
        stats: Dict[str, NetworkStats] = {}
        for name, pids in job_pids.items():
            listening = sorted({port for pid in pids for port in ports_by_pid.get(pid, ())})
            total_in = sum(totals.get(pid, (0, 0))[0] for pid in pids)
            total_out = sum(totals.get(pid, (0, 0))[1] for pid in pids)
            rate_in = sum(rates.get(pid, (0, 0))[0] for pid in pids)
            rate_out = sum(rates.get(pid, (0, 0))[1] for pid in pids)
            stats[name] = NetworkStats(
                pids=tuple(sorted(pids)),
                listening=tuple(listening),
                bytes_in=total_in,
                bytes_out=total_out,
                rate_in=rate_in,
                rate_out=rate_out,
            )
        return stats
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        return {name: NetworkStats(pids=(pid,), error=str(exc)) for name, pid in root_pids.items()}


def _listening_ports_by_pid() -> Dict[int, List[str]]:
    result = default_runner(
        ["/usr/sbin/lsof", "-nP", "-iTCP", "-sTCP:LISTEN", "-Fpn"],
        timeout=5,
    )
    if result.returncode not in (0, 1):
        result.check_returncode()
    ports: Dict[int, List[str]] = {}
    current_pid: Optional[int] = None
    for line in result.stdout.splitlines():
        if line.startswith("p"):
            current_pid = int(line[1:])
        elif line.startswith("n") and current_pid is not None:
            ports.setdefault(current_pid, []).append(line[1:])
    return ports


def _nettop_counters(pids: set[int], delta: bool) -> Dict[int, Tuple[int, int]]:
    if not pids:
        return {}
    command = ["/usr/bin/nettop", "-n", "-P", "-L", "2" if delta else "1", "-x"]
    if delta:
        command.extend(["-d", "-s", "1"])
    command.extend(["-J", "bytes_in,bytes_out"])
    for pid in sorted(pids):
        command.extend(["-p", str(pid)])
    result = default_runner(command, timeout=8)
    result.check_returncode()
    counters: Dict[int, Tuple[int, int]] = {}
    for row in csv.reader(io.StringIO(result.stdout)):
        if len(row) < 3 or not row[0] or row[0] == "interface":
            continue
        try:
            pid = int(row[0].rsplit(".", 1)[1])
            counters[pid] = (int(row[1] or 0), int(row[2] or 0))
        except (IndexError, ValueError):
            continue
    return counters


def expand_path(value: str, base: Optional[Path] = None) -> Path:
    expanded = Path(os.path.expandvars(os.path.expanduser(value)))
    if not expanded.is_absolute():
        expanded = (base or Path.cwd()) / expanded
    # Do not resolve symlinks here. Python virtual environments depend on the
    # invoked .venv/bin/python path to discover their pyvenv.cfg.
    return Path(os.path.abspath(str(expanded)))


def parse_env(items: Sequence[str]) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise LagctlError(f"Invalid environment variable {item!r}; expected KEY=VALUE")
        key, value = item.split("=", 1)
        if not ENV_NAME_PATTERN.fullmatch(key) or "\x00" in value:
            raise LagctlError(f"Invalid environment variable {item!r}")
        result[key] = value
    return result


def validate_name(name: str) -> None:
    if not NAME_PATTERN.fullmatch(name):
        raise LagctlError(
            "Name must start with a letter or digit and contain only "
            "letters, digits, dots, underscores, or hyphens (max 128 characters)"
        )


def resolve_executable(command: str) -> str:
    if "/" in command or command.startswith("~"):
        path = expand_path(command)
        if not path.exists():
            raise LagctlError(f"Command does not exist: {path}")
        if path.is_dir():
            raise LagctlError(f"Command is a directory: {path}")
        if not os.access(path, os.X_OK):
            raise LagctlError(f"Command is not executable: {path}")
        return str(path)

    resolved = shutil.which(command)
    if not resolved:
        raise LagctlError(f"Command not found in PATH: {command}")
    return str(expand_path(resolved))


def prepare_command(command: Sequence[str]) -> Tuple[List[str], Optional[Path]]:
    if not command:
        raise LagctlError("A command is required after '--'")

    values = list(command)
    if values[0] == "--":
        values = values[1:]
    if not values:
        raise LagctlError("A command is required after '--'")

    candidate = expand_path(values[0])
    suffix = candidate.suffix.lower()
    interpreters = {
        ".py": "python3",
        ".js": "node",
        ".mjs": "node",
        ".cjs": "node",
        ".sh": "/bin/sh",
        ".zsh": "/bin/zsh",
        ".bash": "/bin/bash",
    }

    if candidate.is_file() and suffix in interpreters:
        executable = resolve_executable(interpreters[suffix])
        return [executable, str(candidate), *values[1:]], candidate.parent

    values[0] = resolve_executable(values[0])
    return values, None


class AgentManager:
    def __init__(
        self,
        agent_dir: Optional[Path] = None,
        log_dir: Optional[Path] = None,
        runner: Runner = default_runner,
        uid: Optional[int] = None,
        network_collector: Callable[[Mapping[str, int]], Dict[str, NetworkStats]] = collect_network_stats,
    ) -> None:
        home = Path.home()
        configured_agent_dir = os.environ.get("LAGCTL_AGENT_DIR")
        configured_log_dir = os.environ.get("LAGCTL_LOG_DIR")
        self.agent_dir = agent_dir or (
            expand_path(configured_agent_dir) if configured_agent_dir else home / "Library/LaunchAgents"
        )
        self.log_dir = log_dir or (
            expand_path(configured_log_dir) if configured_log_dir else home / "Library/Logs/LaunchAgentManager"
        )
        self.runner = runner
        self.uid = os.getuid() if uid is None else uid
        self.network_collector = network_collector

    @property
    def domain(self) -> str:
        return f"gui/{self.uid}"

    def label(self, name: str) -> str:
        validate_name(name)
        return LABEL_PREFIX + name

    def plist_path(self, name: str) -> Path:
        return self.agent_dir / f"{self.label(name)}.plist"

    def target(self, name: str) -> str:
        return f"{self.domain}/{self.label(name)}"

    def _launchctl(self, *arguments: str, check: bool = False) -> subprocess.CompletedProcess[str]:
        result = self.runner(["/bin/launchctl", *arguments])
        if check and result.returncode != 0:
            detail = (result.stderr or result.stdout or "unknown launchctl error").strip()
            raise LagctlError(f"launchctl {' '.join(arguments)} failed: {detail}")
        return result

    def read_plist(self, name: str) -> Mapping[str, Any]:
        path = self.plist_path(name)
        if not path.exists():
            raise LagctlError(f"Job does not exist: {name}")
        try:
            with path.open("rb") as handle:
                data = plistlib.load(handle)
        except (OSError, plistlib.InvalidFileException) as exc:
            raise LagctlError(f"Cannot read plist {path}: {exc}") from exc
        if not isinstance(data, dict):
            raise LagctlError(f"Invalid plist root in {path}")
        expected_label = self.label(name)
        if data.get("Label") != expected_label:
            raise LagctlError(f"Invalid plist label in {path}; expected {expected_label}")
        arguments = data.get("ProgramArguments")
        if not isinstance(arguments, list) or not arguments or not all(isinstance(value, str) for value in arguments):
            raise LagctlError(f"Invalid ProgramArguments in {path}")
        if not isinstance(data.get("WorkingDirectory"), str):
            raise LagctlError(f"Invalid WorkingDirectory in {path}")
        expected_logs = {
            "StandardOutPath": str(self.log_dir / f"{name}.stdout.log"),
            "StandardErrorPath": str(self.log_dir / f"{name}.stderr.log"),
        }
        for key, expected_path in expected_logs.items():
            if data.get(key) != expected_path:
                raise LagctlError(f"Invalid {key} in {path}; expected {expected_path}")
        return data

    def write_plist(self, name: str, data: Mapping[str, Any]) -> Path:
        self.agent_dir.mkdir(parents=True, exist_ok=True)
        destination = self.plist_path(name)
        temporary_name: Optional[str] = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", prefix=f".{destination.name}.", dir=self.agent_dir, delete=False
            ) as handle:
                temporary_name = handle.name
                plistlib.dump(dict(data), handle, fmt=plistlib.FMT_XML, sort_keys=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary_name, 0o600)
            os.replace(temporary_name, destination)
        finally:
            if temporary_name and os.path.exists(temporary_name):
                os.unlink(temporary_name)
        return destination

    def build_plist(
        self,
        name: str,
        command: Sequence[str],
        cwd: Path,
        environment: Mapping[str, str],
        mode: str,
        interval: Optional[int],
        run_at_load: bool,
        throttle_interval: int,
        allow_background_children: bool,
        explicit_environment_keys: Sequence[str] = (),
    ) -> Dict[str, Any]:
        label = self.label(name)
        stdout_path = self.log_dir / f"{name}.stdout.log"
        stderr_path = self.log_dir / f"{name}.stderr.log"
        data: Dict[str, Any] = {
            "Label": label,
            "ProgramArguments": list(command),
            "WorkingDirectory": str(cwd),
            "RunAtLoad": run_at_load,
            "StandardOutPath": str(stdout_path),
            "StandardErrorPath": str(stderr_path),
            "ProcessType": "Background",
            "ThrottleInterval": throttle_interval,
        }

        if interval is not None:
            data["StartInterval"] = interval
        elif mode == "keep-alive":
            data["KeepAlive"] = True
        elif mode == "on-failure":
            data["KeepAlive"] = {"SuccessfulExit": False}

        if environment:
            data["EnvironmentVariables"] = dict(environment)
            if explicit_environment_keys:
                data["LagctlExplicitEnvironmentKeys"] = sorted(explicit_environment_keys)
        if allow_background_children:
            data["AbandonProcessGroup"] = True
        return data

    def add(
        self,
        name: str,
        raw_command: Sequence[str],
        cwd: Optional[str] = None,
        env_items: Sequence[str] = (),
        mode: Optional[str] = None,
        interval: Optional[int] = None,
        run_at_load: bool = True,
        throttle_interval: int = 10,
        start: bool = True,
        force: bool = False,
        inherit_path: bool = True,
        allow_background_children: bool = False,
    ) -> Path:
        validate_name(name)
        if interval is not None and interval < 1:
            raise LagctlError("Interval must be at least 1 second")
        if throttle_interval < 1:
            raise LagctlError("Throttle interval must be at least 1 second")
        effective_mode = mode or ("once" if interval is not None else "keep-alive")
        if interval is not None and mode is not None and mode != "once":
            raise LagctlError("--interval requires --mode once when --mode is specified")
        if allow_background_children and (effective_mode != "once" or interval is not None):
            raise LagctlError(
                "--allow-background-children requires --mode once and cannot be used with --interval"
            )

        path = self.plist_path(name)
        if path.exists() and not force:
            raise LagctlError(f"Job already exists: {name} (use --force to replace it)")

        command, script_dir = prepare_command(raw_command)
        working_directory = expand_path(cwd) if cwd else (script_dir or Path.cwd().resolve())
        if not working_directory.is_dir():
            raise LagctlError(f"Working directory does not exist: {working_directory}")

        environment = parse_env(env_items)
        explicit_environment_keys = list(environment)
        if inherit_path and "PATH" not in environment:
            environment["PATH"] = os.environ.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")

        data = self.build_plist(
            name=name,
            command=command,
            cwd=working_directory,
            environment=environment,
            mode=effective_mode,
            interval=interval,
            run_at_load=run_at_load,
            throttle_interval=throttle_interval,
            allow_background_children=allow_background_children,
            explicit_environment_keys=explicit_environment_keys,
        )
        self.log_dir.mkdir(parents=True, exist_ok=True)

        old_data: Optional[Mapping[str, Any]] = None
        if path.exists():
            old_data = self.read_plist(name)
        was_loaded = self.status(name).loaded
        if was_loaded:
            self.stop(name, missing_ok=True)
        self.write_plist(name, data)

        if start:
            try:
                self.start(name)
            except LagctlError as exc:
                if old_data is not None:
                    try:
                        self.write_plist(name, old_data)
                        if was_loaded:
                            self.start(name)
                    except LagctlError as restore_exc:
                        raise LagctlError(f"Failed to load new job: {exc}; rollback failed: {restore_exc}") from exc
                raise
        return path

    def iter_jobs(self) -> List[Tuple[str, Mapping[str, Any]]]:
        if not self.agent_dir.exists():
            return []
        jobs: List[Tuple[str, Mapping[str, Any]]] = []
        pattern = f"{LABEL_PREFIX}*.plist"
        for path in sorted(self.agent_dir.glob(pattern)):
            name = path.name[len(LABEL_PREFIX) : -len(".plist")]
            try:
                data = self.read_plist(name)
            except LagctlError:
                data = {"Label": LABEL_PREFIX + name, "Invalid": True}
            jobs.append((name, data))
        return jobs

    def status(self, name: str) -> JobStatus:
        result = self._launchctl("print", self.target(name))
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            if result.returncode == 113 or "Could not find service" in detail:
                return JobStatus(loaded=False)
            raise LagctlError(f"launchctl print failed: {detail or 'unknown launchctl error'}")

        output = result.stdout
        state_match = re.search(r"^\s*state\s*=\s*([^\s]+)\s*$", output, re.MULTILINE)
        pid_match = re.search(r"^\s*pid\s*=\s*(\d+)\s*$", output, re.MULTILINE)
        exit_match = re.search(r"^\s*last exit code\s*=\s*(-?\d+)\s*$", output, re.MULTILINE)
        return JobStatus(
            loaded=True,
            state=state_match.group(1) if state_match else "loaded",
            pid=int(pid_match.group(1)) if pid_match else None,
            last_exit_code=int(exit_match.group(1)) if exit_match else None,
        )

    def start(self, name: str) -> None:
        path = self.plist_path(name)
        if not path.exists():
            raise LagctlError(f"Job does not exist: {name}")
        self._launchctl("enable", self.target(name), check=True)
        if not self.status(name).loaded:
            self._launchctl("bootstrap", self.domain, str(path), check=True)

    def stop(self, name: str, missing_ok: bool = False) -> None:
        if not self.plist_path(name).exists() and not missing_ok:
            raise LagctlError(f"Job does not exist: {name}")
        if not self.status(name).loaded:
            return
        self._launchctl("bootout", self.target(name), check=True)

    def restart(self, name: str) -> None:
        if not self.plist_path(name).exists():
            raise LagctlError(f"Job does not exist: {name}")
        loaded = self.status(name).loaded
        if loaded:
            self._launchctl("kickstart", "-k", self.target(name), check=True)
        else:
            self.start(name)
            self._launchctl("kickstart", "-k", self.target(name), check=True)

    def run_now(self, name: str) -> None:
        if not self.plist_path(name).exists():
            raise LagctlError(f"Job does not exist: {name}")
        if not self.status(name).loaded:
            self.start(name)
        self._launchctl("kickstart", "-k", self.target(name), check=True)

    def remove(self, name: str, purge_logs: bool = False) -> None:
        path = self.plist_path(name)
        if not path.exists():
            raise LagctlError(f"Job does not exist: {name}")
        self.stop(name, missing_ok=True)
        path.unlink()
        if purge_logs:
            for stream in ("stdout", "stderr"):
                log_path = self.log_dir / f"{name}.{stream}.log"
                try:
                    log_path.unlink()
                except FileNotFoundError:
                    pass


def display_command(values: Sequence[str]) -> str:
    return " ".join(shlex_quote(value) for value in values)


def shlex_quote(value: str) -> str:
    if not value:
        return "''"
    if re.fullmatch(r"[A-Za-z0-9_@%+=:,./-]+", value):
        return value
    return "'" + value.replace("'", "'\"'\"'") + "'"


def format_status(status: JobStatus) -> str:
    if not status.loaded:
        return "stopped"
    if status.pid is not None:
        return "running"
    return status.state


def print_jobs(manager: AgentManager) -> None:
    jobs = manager.iter_jobs()
    if not jobs:
        print("No managed jobs.")
        return

    rows: List[List[str]] = []
    for name, data in jobs:
        status = manager.status(name)
        command = data.get("ProgramArguments", [])
        rows.append(
            [
                name,
                format_status(status),
                str(status.pid) if status.pid is not None else "-",
                str(status.last_exit_code) if status.last_exit_code is not None else "-",
                display_command(command) if not data.get("Invalid") and isinstance(command, list) else "invalid plist",
            ]
        )

    headers = ["NAME", "STATUS", "PID", "LAST EXIT", "COMMAND"]
    widths = [max(len(headers[index]), *(len(row[index]) for row in rows)) for index in range(4)]
    print("  ".join(headers[index].ljust(widths[index]) for index in range(4)) + "  " + headers[4])
    for row in rows:
        print("  ".join(row[index].ljust(widths[index]) for index in range(4)) + "  " + row[4])


def print_status(manager: AgentManager, name: str) -> None:
    data = manager.read_plist(name)
    status = manager.status(name)
    command = data.get("ProgramArguments", [])
    print(f"Name:       {name}")
    print(f"Label:      {data.get('Label', '-')}")
    print(f"Status:     {format_status(status)}")
    print(f"PID:        {status.pid if status.pid is not None else '-'}")
    print(f"Last exit:  {status.last_exit_code if status.last_exit_code is not None else '-'}")
    print(f"Command:    {display_command(command) if isinstance(command, list) else '-'}")
    print(f"Directory:  {data.get('WorkingDirectory', '-')}")
    print(f"Plist:      {manager.plist_path(name)}")
    print(f"Stdout:     {data.get('StandardOutPath', '-')}")
    print(f"Stderr:     {data.get('StandardErrorPath', '-')}")


def show_plist(manager: AgentManager, name: str) -> None:
    data = manager.read_plist(name)
    plistlib.dump(dict(data), sys.stdout.buffer, fmt=plistlib.FMT_XML, sort_keys=False)


def follow_logs(manager: AgentManager, name: str, stream: str, lines: int, follow: bool) -> int:
    data = manager.read_plist(name)
    keys = {
        "stdout": ["StandardOutPath"],
        "stderr": ["StandardErrorPath"],
        "all": ["StandardOutPath", "StandardErrorPath"],
    }
    paths = [str(data[key]) for key in keys[stream] if data.get(key)]
    if not paths:
        raise LagctlError("This job has no configured log paths")
    for path in paths:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).touch(exist_ok=True)
    command = ["/usr/bin/tail"]
    if follow:
        command.append("-F")
    command.extend(["-n", str(lines), *paths])
    return subprocess.call(command)


def format_byte_count(value: Optional[int], per_second: bool = False) -> str:
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


def print_network(manager: AgentManager, name: str) -> None:
    manager.read_plist(name)
    status = manager.status(name)
    stats = (
        manager.network_collector({name: status.pid}).get(name, NetworkStats())
        if status.pid is not None
        else NetworkStats()
    )
    print(f"Name:          {name}")
    print(f"Process PIDs:  {', '.join(str(pid) for pid in stats.pids) or '-'}")
    print(f"Listening:     {', '.join(stats.listening) or '-'}")
    print(f"RX total:      {format_byte_count(stats.bytes_in)}")
    print(f"TX total:      {format_byte_count(stats.bytes_out)}")
    print(f"RX rate:       {format_byte_count(stats.rate_in, per_second=True)}")
    print(f"TX rate:       {format_byte_count(stats.rate_out, per_second=True)}")
    print(f"Network error: {stats.error or '-'}")


def run_doctor(manager: AgentManager) -> int:
    checks: List[Tuple[str, bool, str]] = []
    mac_version = platform.mac_ver()[0]
    try:
        mac_parts = tuple(int(part) for part in mac_version.split(".")[:2])
    except ValueError:
        mac_parts = ()
    mac_ok = sys.platform == "darwin" and bool(mac_version) and mac_parts >= (14, 0)
    checks.append(("macOS", mac_ok, mac_version or sys.platform))
    checks.append(("Python", sys.version_info >= (3, 9), platform.python_version()))
    checks.append(("launchctl", os.access("/bin/launchctl", os.X_OK), "/bin/launchctl"))
    checks.append(("tail", os.access("/usr/bin/tail", os.X_OK), "/usr/bin/tail"))
    checks.append(("lsof", os.access("/usr/sbin/lsof", os.X_OK), "/usr/sbin/lsof"))
    checks.append(("nettop", os.access("/usr/bin/nettop", os.X_OK), "/usr/bin/nettop"))
    checks.append(("agent directory", manager.agent_dir.parent.is_dir() and os.access(manager.agent_dir.parent, os.W_OK), str(manager.agent_dir)))
    checks.append(("log directory", manager.log_dir.parent.is_dir() and os.access(manager.log_dir.parent, os.W_OK), str(manager.log_dir)))
    domain_result = manager._launchctl("print", manager.domain)
    checks.append(("GUI domain", domain_result.returncode == 0, manager.domain))
    for title, passed, detail in checks:
        print(f"[{'OK' if passed else 'FAIL'}] {title}: {detail}")
    return 0 if all(passed for _, passed, _ in checks) else 1


def completion_script(shell: str) -> str:
    filename = "_lagctl" if shell == "zsh" else "lagctl.bash"
    path = Path(__file__).resolve().parent / "completions" / filename
    if not path.is_file():
        raise LagctlError(f"Completion script is missing: {path}")
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise LagctlError(f"Cannot read completion script {path}: {exc}") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lagctl",
        description="Manage user LaunchAgents for long-running scripts on macOS.",
    )
    parser.add_argument("--version", action="version", version=f"lagctl {VERSION}")
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    add_parser = subparsers.add_parser(
        "add",
        help="create and optionally start a job",
        usage="lagctl add NAME [options] -- COMMAND [ARGUMENT ...]",
    )
    add_parser.add_argument("name", help="short unique job name")
    add_parser.add_argument("--cwd", help="working directory (defaults to script directory or current directory)")
    add_parser.add_argument("--env", action="append", default=[], metavar="KEY=VALUE", help="set an environment variable; repeatable")
    add_parser.add_argument(
        "--mode",
        choices=("keep-alive", "on-failure", "once"),
        default=None,
        help="restart policy (default: keep-alive)",
    )
    add_parser.add_argument("--interval", type=int, metavar="SECONDS", help="run periodically instead of keeping the process alive")
    add_parser.add_argument("--no-run-at-load", action="store_true", help="do not run immediately after login/load")
    add_parser.add_argument("--throttle", type=int, default=10, metavar="SECONDS", help="minimum restart delay (default: 10)")
    add_parser.add_argument("--clean-path", action="store_true", help="do not copy the current PATH into the job")
    add_parser.add_argument("--no-start", action="store_true", help="write the plist without loading it")
    add_parser.add_argument("--force", action="store_true", help="replace an existing managed job")
    add_parser.add_argument(
        "--allow-background-children",
        action="store_true",
        help="let daemonized child processes survive after a once-mode launcher exits",
    )

    subparsers.add_parser("list", aliases=["ls"], help="list managed jobs")
    for command_name in ("status", "show", "network", "start", "stop", "restart", "run"):
        command_parser = subparsers.add_parser(command_name)
        command_parser.add_argument("name")

    logs_parser = subparsers.add_parser("logs", help="show or follow job logs")
    logs_parser.add_argument("name")
    logs_parser.add_argument("-f", "--follow", action="store_true")
    logs_parser.add_argument("-n", "--lines", type=int, default=100)
    logs_parser.add_argument("--stream", choices=("all", "stdout", "stderr"), default="all")

    remove_parser = subparsers.add_parser("remove", aliases=["rm"], help="stop and remove a managed job")
    remove_parser.add_argument("name")
    remove_parser.add_argument("--purge-logs", action="store_true", help="also delete stdout/stderr logs")

    subparsers.add_parser("doctor", help="check the local environment")
    subparsers.add_parser("tui", help="open the interactive terminal dashboard")

    completion_parser = subparsers.add_parser("completion", help="print shell completion support")
    completion_parser.add_argument("shell", choices=("zsh", "bash", "jobs"))
    return parser


def parse_arguments(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    raw = list(sys.argv[1:] if argv is None else argv)
    job_command: Optional[List[str]] = None
    if raw and raw[0] == "add" and "--" in raw:
        separator = raw.index("--")
        job_command = raw[separator + 1 :]
        raw = raw[:separator]

    parser = build_parser()
    args = parser.parse_args(raw)
    if args.subcommand == "add":
        if job_command is None:
            parser.error("add requires '-- COMMAND [ARGUMENT ...]'")
        if not job_command:
            parser.error("add requires a command after '--'")
        args.command = job_command
    return args


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_arguments(argv)
    manager = AgentManager()

    try:
        if args.subcommand == "add":
            path = manager.add(
                name=args.name,
                raw_command=args.command,
                cwd=args.cwd,
                env_items=args.env,
                mode=args.mode,
                interval=args.interval,
                run_at_load=not args.no_run_at_load,
                throttle_interval=args.throttle,
                start=not args.no_start,
                force=args.force,
                inherit_path=not args.clean_path,
                allow_background_children=args.allow_background_children,
            )
            print(f"Created {args.name}: {path}")
            print_status(manager, args.name)
        elif args.subcommand in ("list", "ls"):
            print_jobs(manager)
        elif args.subcommand == "status":
            print_status(manager, args.name)
        elif args.subcommand == "show":
            show_plist(manager, args.name)
        elif args.subcommand == "network":
            print_network(manager, args.name)
        elif args.subcommand == "start":
            manager.start(args.name)
            print(f"Started {args.name}")
        elif args.subcommand == "stop":
            manager.stop(args.name)
            print(f"Stopped {args.name}")
        elif args.subcommand == "restart":
            manager.restart(args.name)
            print(f"Restarted {args.name}")
        elif args.subcommand == "run":
            manager.run_now(args.name)
            print(f"Triggered {args.name}")
        elif args.subcommand == "logs":
            if args.lines < 0:
                raise LagctlError("Line count cannot be negative")
            return follow_logs(manager, args.name, args.stream, args.lines, args.follow)
        elif args.subcommand in ("remove", "rm"):
            manager.remove(args.name, purge_logs=args.purge_logs)
            print(f"Removed {args.name}")
        elif args.subcommand == "doctor":
            return run_doctor(manager)
        elif args.subcommand == "tui":
            try:
                from lagctl_tui import run_tui
            except ImportError as exc:
                raise LagctlError(
                    "The TUI requires Textual; install it with "
                    "python3 -m pip install 'textual>=0.70,<1.0'"
                ) from exc
            return run_tui(manager)
        elif args.subcommand == "completion":
            if args.shell == "jobs":
                for name, _ in manager.iter_jobs():
                    print(name)
            else:
                print(completion_script(args.shell), end="")
        return 0
    except LagctlError as exc:
        print(f"lagctl: error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
