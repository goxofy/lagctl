import io
import plistlib
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import lagctl


class FakeRunner:
    def __init__(self):
        self.calls = []
        self.loaded = set()
        self.outputs = {}
        self.fail_actions = set()

    def __call__(self, args, **kwargs):
        self.calls.append(list(args))
        action = args[1]
        if action in self.fail_actions:
            return subprocess.CompletedProcess(args, 1, "", f"simulated {action} failure")
        if action == "print" and len(args) == 3 and args[2].startswith("gui/"):
            target = args[2]
            if target in self.outputs:
                return subprocess.CompletedProcess(args, 0, self.outputs[target], "")
            if target in self.loaded:
                return subprocess.CompletedProcess(args, 0, "state = waiting\n", "")
            return subprocess.CompletedProcess(args, 113, "", "Could not find service")
        if action == "bootstrap":
            with open(args[3], "rb") as handle:
                label = plistlib.load(handle)["Label"]
            self.loaded.add(f"{args[2]}/{label}")
        elif action == "bootout":
            self.loaded.discard(args[2])
        return subprocess.CompletedProcess(args, 0, "", "")


class AgentManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.agent_dir = root / "agents"
        self.log_dir = root / "logs"
        self.runner = FakeRunner()
        self.manager = lagctl.AgentManager(
            agent_dir=self.agent_dir,
            log_dir=self.log_dir,
            runner=self.runner,
            uid=501,
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_add_python_script_creates_valid_plist_and_loads_it(self):
        script = Path(self.temp.name) / "worker.py"
        script.write_text("print('hello')\n", encoding="utf-8")

        path = self.manager.add("worker", [str(script)], env_items=["TOKEN=a=b"])

        with path.open("rb") as handle:
            data = plistlib.load(handle)
        self.assertEqual(data["Label"], lagctl.LABEL_PREFIX + "worker")
        self.assertEqual(Path(data["ProgramArguments"][1]).resolve(), script.resolve())
        self.assertTrue(data["ProgramArguments"][0].endswith("python3"))
        self.assertEqual(Path(data["WorkingDirectory"]).resolve(), script.parent.resolve())
        self.assertEqual(data["EnvironmentVariables"]["TOKEN"], "a=b")
        self.assertTrue(data["KeepAlive"])
        self.assertTrue(data["RunAtLoad"])
        self.assertIn(["/bin/launchctl", "bootstrap", "gui/501", str(path)], self.runner.calls)

    def test_interval_job_does_not_set_keep_alive(self):
        path = self.manager.add("timer", ["/bin/echo", "ok"], interval=60, start=False)
        with path.open("rb") as handle:
            data = plistlib.load(handle)
        self.assertEqual(data["StartInterval"], 60)
        self.assertNotIn("KeepAlive", data)

    def test_once_job_can_allow_background_children(self):
        path = self.manager.add(
            "legacy-launcher",
            ["/bin/sh", "run.sh"],
            mode="once",
            allow_background_children=True,
            start=False,
        )
        with path.open("rb") as handle:
            data = plistlib.load(handle)
        self.assertTrue(data["AbandonProcessGroup"])
        self.assertNotIn("KeepAlive", data)

    def test_background_children_rejects_keep_alive_and_interval_modes(self):
        with self.assertRaises(lagctl.LagctlError):
            self.manager.add(
                "bad-daemon",
                ["/bin/echo"],
                allow_background_children=True,
                start=False,
            )
        with self.assertRaises(lagctl.LagctlError):
            self.manager.add(
                "bad-timer",
                ["/bin/echo"],
                mode="once",
                interval=60,
                allow_background_children=True,
                start=False,
            )

    def test_force_replacement_boots_out_loaded_job(self):
        self.manager.add("worker", ["/bin/echo", "old"])
        self.runner.calls.clear()

        self.manager.add("worker", ["/bin/echo", "new"], force=True)

        target = "gui/501/" + lagctl.LABEL_PREFIX + "worker"
        self.assertIn(["/bin/launchctl", "bootout", target], self.runner.calls)
        with self.manager.plist_path("worker").open("rb") as handle:
            data = plistlib.load(handle)
        self.assertEqual(data["ProgramArguments"], ["/bin/echo", "new"])

    def test_restart_bootstraps_and_kickstarts_unloaded_job(self):
        self.manager.add("worker", ["/bin/echo", "ok"], run_at_load=False, start=False)

        self.manager.restart("worker")

        path = self.manager.plist_path("worker")
        self.assertIn(["/bin/launchctl", "bootstrap", "gui/501", str(path)], self.runner.calls)
        self.assertIn(
            ["/bin/launchctl", "kickstart", "-k", "gui/501/" + lagctl.LABEL_PREFIX + "worker"],
            self.runner.calls,
        )

    def test_status_raises_for_launchctl_errors_other_than_missing_service(self):
        self.runner.fail_actions.add("print")

        with self.assertRaises(lagctl.LagctlError):
            self.manager.status("worker")

    def test_read_plist_rejects_mismatched_label_and_log_paths(self):
        path = self.manager.add("worker", ["/bin/echo"], start=False)
        with path.open("wb") as handle:
            plistlib.dump(
                {
                    "Label": "other.label",
                    "ProgramArguments": ["/bin/echo"],
                    "WorkingDirectory": self.temp.name,
                    "StandardOutPath": str(self.log_dir / "worker.stdout.log"),
                    "StandardErrorPath": str(self.log_dir / "worker.stderr.log"),
                },
                handle,
            )

        with self.assertRaises(lagctl.LagctlError):
            self.manager.read_plist("worker")

    def test_force_replacement_restores_old_plist_and_job_on_start_failure(self):
        self.manager.add("worker", ["/bin/echo", "old"])
        old_data = self.manager.read_plist("worker")
        self.runner.fail_actions.add("bootstrap")

        with self.assertRaises(lagctl.LagctlError):
            self.manager.add("worker", ["/bin/echo", "new"], force=True)

        self.assertEqual(self.manager.read_plist("worker"), old_data)
        self.assertIn(
            ["/bin/launchctl", "bootstrap", "gui/501", str(self.manager.plist_path("worker"))],
            self.runner.calls,
        )

    def test_status_parses_running_process(self):
        target = "gui/501/" + lagctl.LABEL_PREFIX + "worker"
        self.runner.outputs[target] = (
            "gui/501/local.launch-agent-manager.worker = {\n"
            "\tstate = running\n\tpid = 4242\n\tlast exit code = 7\n}\n"
        )
        status = self.manager.status("worker")
        self.assertTrue(status.loaded)
        self.assertEqual(status.state, "running")
        self.assertEqual(status.pid, 4242)
        self.assertEqual(status.last_exit_code, 7)

    def test_remove_keeps_logs_by_default_and_can_purge(self):
        self.manager.add("worker", ["/bin/echo", "ok"], start=False)
        stdout = self.log_dir / "worker.stdout.log"
        stderr = self.log_dir / "worker.stderr.log"
        stdout.write_text("out", encoding="utf-8")
        stderr.write_text("err", encoding="utf-8")

        self.manager.remove("worker")
        self.assertTrue(stdout.exists())
        self.assertTrue(stderr.exists())

        self.manager.add("worker", ["/bin/echo", "ok"], start=False)
        self.manager.remove("worker", purge_logs=True)
        self.assertFalse(stdout.exists())
        self.assertFalse(stderr.exists())

    def test_completion_jobs_lists_managed_names_only(self):
        self.manager.add("beta", ["/bin/echo"], start=False)
        self.manager.add("alpha", ["/bin/echo"], start=False)
        names = [name for name, _ in self.manager.iter_jobs()]
        self.assertEqual(names, ["alpha", "beta"])

    def test_rejects_invalid_name_and_environment(self):
        with self.assertRaises(lagctl.LagctlError):
            self.manager.add("../bad", ["/bin/echo"])
        with self.assertRaises(lagctl.LagctlError):
            self.manager.add("good", ["/bin/echo"], env_items=["MISSING_VALUE"])
        with self.assertRaises(lagctl.LagctlError):
            self.manager.add("good", ["/bin/echo"], env_items=["BAD-NAME=value"])

    def test_interval_rejects_explicit_restart_mode(self):
        with self.assertRaises(lagctl.LagctlError):
            self.manager.add("timer", ["/bin/echo"], interval=60, mode="keep-alive", start=False)


class UtilityTests(unittest.TestCase):
    def test_completion_scripts_are_available(self):
        zsh_script = lagctl.completion_script("zsh")
        bash_script = lagctl.completion_script("bash")
        self.assertIn("#compdef lagctl", zsh_script)
        self.assertIn("compdef _lagctl lagctl", zsh_script)
        self.assertIn("complete -F _lagctl_completion lagctl", bash_script)

    def test_explicit_executable_symlink_is_not_resolved(self):
        with tempfile.TemporaryDirectory() as temporary:
            real_executable = Path(temporary) / "python-real"
            virtualenv_executable = Path(temporary) / ".venv/bin/python"
            real_executable.write_text("#!/bin/sh\n", encoding="utf-8")
            real_executable.chmod(0o755)
            virtualenv_executable.parent.mkdir(parents=True)
            virtualenv_executable.symlink_to(real_executable)

            command, _ = lagctl.prepare_command([str(virtualenv_executable), "app.py"])

            self.assertEqual(command[0], str(virtualenv_executable))

    def test_shell_display_quotes_without_executing(self):
        self.assertEqual(lagctl.display_command(["/bin/echo", "hello world", "a'b"]), "/bin/echo 'hello world' 'a'\"'\"'b'")

    def test_parser_requires_subcommand(self):
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                lagctl.parse_arguments([])

    def test_parser_supports_tui_subcommand_without_optional_import(self):
        args = lagctl.parse_arguments(["tui"])
        self.assertEqual(args.subcommand, "tui")

    def test_add_options_can_follow_name(self):
        args = lagctl.parse_arguments(
            ["add", "worker", "--no-start", "--env", "PORT=3000", "--", "/bin/echo", "--verbose"]
        )
        self.assertEqual(args.name, "worker")
        self.assertTrue(args.no_start)
        self.assertEqual(args.env, ["PORT=3000"])
        self.assertEqual(args.command, ["/bin/echo", "--verbose"])


if __name__ == "__main__":
    unittest.main()
