import asyncio
import subprocess
import unittest
from types import SimpleNamespace
from pathlib import Path
from tempfile import TemporaryDirectory

from lagctl import AgentManager, NetworkStats

try:
    import textual  # noqa: F401
except ImportError:
    TEXTUAL_AVAILABLE = False
else:
    TEXTUAL_AVAILABLE = True


class FakeRunner:
    def __init__(self):
        self.loaded = set()
        self.calls = []

    def __call__(self, args, **kwargs):
        self.calls.append(list(args))
        action = args[1]
        if action == "print" and len(args) == 3:
            if args[2] in self.loaded:
                return subprocess.CompletedProcess(
                    args, 0, "state = running\npid = 4321\nlast exit code = 0\n", ""
                )
            return subprocess.CompletedProcess(args, 113, "", "Could not find service")
        if action == "bootstrap":
            self.loaded.add(args[2] + "/" + Path(args[3]).stem)
        elif action == "bootout":
            self.loaded.discard(args[2])
        return subprocess.CompletedProcess(args, 0, "", "")


def fake_network_collector(root_pids):
    return {
        name: NetworkStats(
            pids=(pid, pid + 1),
            listening=("*:3000", "127.0.0.1:8080"),
            bytes_in=2048,
            bytes_out=4096,
            rate_in=128,
            rate_out=256,
        )
        for name, pid in root_pids.items()
    }


@unittest.skipUnless(TEXTUAL_AVAILABLE, "Textual is not installed")
class TuiTests(unittest.TestCase):
    def test_dashboard_actions_and_secondary_screens(self):
        asyncio.run(self._test_dashboard_actions_and_secondary_screens())

    async def _test_dashboard_actions_and_secondary_screens(self):
        from lagctl_tui import LagctlApp
        from lagctl_tui.app import ConfirmDeleteScreen, LogsScreen

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            runner = FakeRunner()
            manager = AgentManager(root / "agents", root / "logs", runner=runner, uid=501, network_collector=fake_network_collector)
            manager.add(
                "alpha",
                ["/bin/echo", "alpha"],
                env_items=["APP_ENV=test", "PORT=3000"],
                start=False,
            )
            manager.add("beta", ["/bin/echo", "beta"], start=False)
            (root / "logs" / "alpha.stdout.log").write_text("alpha out\n", encoding="utf-8")
            (root / "logs" / "alpha.stderr.log").write_text("alpha err\n", encoding="utf-8")

            app = LagctlApp(manager)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                job_list = app.query_one("#job-list")
                details = app.query_one("#details")
                self.assertFalse(details.soft_wrap)
                self.assertTrue(any(line.split() == ["APP_ENV", "test"] for line in details.text.splitlines()))
                self.assertTrue(any(line.split() == ["PORT", "3000"] for line in details.text.splitlines()))
                job_list.focus()
                await pilot.press("down")
                await pilot.pause()
                self.assertEqual(app.selected_name, "beta")
                self.assertTrue(job_list.children[1].has_class("-highlight"))

                await pilot.press("enter")
                await pilot.pause()
                self.assertEqual(type(app.screen).__name__, "DetailsScreen")
                self.assertFalse(app.screen.query_one("#details-output").soft_wrap)
                await pilot.press("escape")
                await pilot.pause()
                await pilot.press("s")
                await pilot.press("s")
                await pilot.press("R")
                await pilot.press("x")
                await pilot.pause(1)

                job_list.index = 0
                await pilot.press("l")
                await pilot.pause()
                self.assertIsInstance(app.screen, LogsScreen)
                for key in ("o", "e", "a"):
                    await pilot.press(key)
                    await pilot.pause()
                await pilot.pause(2.5)
                await pilot.press("q")
                await pilot.pause()
                self.assertNotIsInstance(app.screen, LogsScreen)
                self.assertTrue(job_list.children[0].has_class("-highlight"))

                await pilot.press("d")
                await pilot.pause()
                self.assertIsInstance(app.screen, ConfirmDeleteScreen)
                await pilot.press("escape")
                await pilot.pause()
                self.assertNotIsInstance(app.screen, ConfirmDeleteScreen)

    def test_lifecycle_actions_run_and_refresh_after_worker_completion(self):
        asyncio.run(self._test_lifecycle_actions_run_and_refresh_after_worker_completion())

    def test_ctrl_c_on_details_copies_without_quitting(self):
        asyncio.run(self._test_ctrl_c_on_details_copies_without_quitting())

    async def _test_ctrl_c_on_details_copies_without_quitting(self):
        from lagctl_tui import LagctlApp

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = AgentManager(root / "agents", root / "logs", runner=FakeRunner(), uid=501, network_collector=fake_network_collector)
            manager.add("worker", ["/bin/echo", "worker"], start=False)

            app = LagctlApp(manager)
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                details = app.query_one("#details")
                details.focus()
                details.select_all()
                copied = []
                app.copy_to_clipboard = copied.append
                await pilot.press("ctrl+c")
                await pilot.pause()
                self.assertEqual(type(app.screen).__name__, "Screen")
                self.assertTrue(copied)
                self.assertIn("worker", copied[0])

    def test_detail_values_share_one_column_with_environment_values(self):
        from lagctl import JobStatus
        from lagctl_tui.app import format_job_details

        text = format_job_details(
            "worker",
            {
                "ProgramArguments": ["/bin/echo"],
                "WorkingDirectory": "/tmp",
                "RunAtLoad": True,
                "KeepAlive": True,
                "StandardOutPath": "/tmp/out.log",
                "StandardErrorPath": "/tmp/err.log",
                "EnvironmentVariables": {"PORT": "3000"},
                "LagctlExplicitEnvironmentKeys": ["PORT"],
            },
            JobStatus(loaded=False),
            "/tmp/worker.plist",
        )
        lines = text.splitlines()
        plist_value_column = next(line.index("/tmp/worker.plist") for line in lines if "Plist" in line)
        port_value_column = next(line.index("3000") for line in lines if "PORT" in line)
        self.assertEqual(plist_value_column, port_value_column)

    def test_detail_formats_ports_and_network_usage(self):
        from lagctl import JobStatus
        from lagctl_tui.app import format_job_details

        text = format_job_details(
            "worker",
            {
                "ProgramArguments": ["/bin/echo"],
                "WorkingDirectory": "/tmp",
                "RunAtLoad": True,
                "StandardOutPath": "/tmp/out.log",
                "StandardErrorPath": "/tmp/err.log",
                "_network": fake_network_collector({"worker": 4321})["worker"],
            },
            JobStatus(loaded=True, state="running", pid=4321),
            "/tmp/worker.plist",
        )
        self.assertIn("Process PIDs   4321, 4322", text)
        self.assertIn("Listening      *:3000, 127.0.0.1:8080", text)
        self.assertIn("RX total       2.0 KiB", text)
        self.assertIn("TX rate        256 B/s", text)

    def test_detail_accepts_network_stats_from_script_module_instance(self):
        from lagctl import JobStatus
        from lagctl_tui.app import format_job_details

        network = SimpleNamespace(
            pids=(100, 101),
            listening=("*:9000",),
            bytes_in=1024,
            bytes_out=2048,
            rate_in=64,
            rate_out=128,
            error=None,
        )
        text = format_job_details(
            "worker",
            {
                "ProgramArguments": ["/bin/echo"],
                "WorkingDirectory": "/tmp",
                "RunAtLoad": True,
                "StandardOutPath": "/tmp/out.log",
                "StandardErrorPath": "/tmp/err.log",
                "_network": network,
            },
            JobStatus(loaded=True, state="running", pid=100),
            "/tmp/worker.plist",
        )
        self.assertIn("Process PIDs   100, 101", text)
        self.assertIn("Listening      *:9000", text)
        self.assertIn("RX total       1.0 KiB", text)

    def test_add_job_form_creates_job_with_options(self):
        asyncio.run(self._test_add_job_form_creates_job_with_options())

    async def _test_add_job_form_creates_job_with_options(self):
        from lagctl_tui import LagctlApp
        from lagctl_tui.app import AddJobScreen

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = AgentManager(root / "agents", root / "logs", runner=FakeRunner(), uid=501, network_collector=fake_network_collector)
            app = LagctlApp(manager)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                await pilot.press("a")
                await pilot.pause()
                self.assertIsInstance(app.screen, AddJobScreen)
                screen = app.screen
                screen.query_one("#add-name").value = "new-job"
                screen.query_one("#add-command").value = "/bin/echo"
                screen.query_one("#add-arguments").value = "hello world"
                screen.query_one("#add-env").load_text("PORT=3000\nAPP_ENV=test")
                payload = screen.submit()
                self.assertEqual(payload["env_items"], ["PORT=3000", "APP_ENV=test"])
                screen.dismiss(payload)
                await pilot.pause(1)
                self.assertIn("new-job", app.jobs)
                self.assertTrue(
                    any(line.split() == ["APP_ENV", "test"] for line in app.query_one("#details").text.splitlines())
                )

    def test_edit_form_prefills_and_uses_force_update(self):
        asyncio.run(self._test_edit_form_prefills_and_uses_force_update())

    async def _test_edit_form_prefills_and_uses_force_update(self):
        from lagctl_tui import LagctlApp
        from lagctl_tui.app import AddJobScreen

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = AgentManager(root / "agents", root / "logs", runner=FakeRunner(), uid=501, network_collector=fake_network_collector)
            manager.add("worker", ["/bin/echo", "old"], env_items=["PORT=3000"], start=False)
            app = LagctlApp(manager)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause(0.5)
                app.action_edit_selected()
                await pilot.pause()
                self.assertIsInstance(app.screen, AddJobScreen)
                screen = app.screen
                self.assertEqual(screen.query_one("#add-name").value, "worker")
                self.assertEqual(screen.query_one("#add-arguments").value, "old")
                self.assertEqual(screen.query_one("#add-env").text, "PORT=3000")
                screen.query_one("#add-arguments").value = "new"
                payload = screen.submit()
                self.assertTrue(payload["force"])
                screen.dismiss(payload)
                await pilot.pause(1)
                self.assertEqual(manager.read_plist("worker")["ProgramArguments"], ["/bin/echo", "new"])

    def test_logs_support_filter_pause_and_line_count(self):
        asyncio.run(self._test_logs_support_filter_pause_and_line_count())

    async def _test_logs_support_filter_pause_and_line_count(self):
        from lagctl_tui import LagctlApp
        from lagctl_tui.app import LogsScreen

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = AgentManager(root / "agents", root / "logs", runner=FakeRunner(), uid=501, network_collector=fake_network_collector)
            manager.add("worker", ["/bin/echo"], start=False)
            (root / "logs" / "worker.stdout.log").write_text("keep\ndrop\nkeep again\n", encoding="utf-8")
            app = LagctlApp(manager)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause(0.5)
                app.action_logs_selected()
                await pilot.pause()
                self.assertIsInstance(app.screen, LogsScreen)
                screen = app.screen
                self.assertFalse(screen.query_one("#logs-search").has_focus)
                self.assertTrue(screen.query_one("#logs-search").disabled)
                await pilot.press("o")
                await pilot.pause()
                self.assertEqual(screen.stream, "stdout")
                self.assertEqual(screen.query_one("#logs-search").value, "")
                await pilot.press("slash")
                await pilot.pause()
                self.assertTrue(screen.query_one("#logs-search").has_focus)
                self.assertFalse(screen.query_one("#logs-search").disabled)
                screen.query_one("#logs-search").value = "keep"
                await pilot.pause()
                self.assertEqual(screen.search, "keep")
                await pilot.press("enter")
                await pilot.pause()
                self.assertFalse(screen.query_one("#logs-search").has_focus)
                screen.action_toggle_pause()
                self.assertTrue(screen.paused)
                screen.action_increase_lines()
                self.assertEqual(screen.lines, 300)
                screen.action_decrease_lines()
                self.assertEqual(screen.lines, 200)

    def test_job_search_filters_visible_items(self):
        asyncio.run(self._test_job_search_filters_visible_items())

    async def _test_job_search_filters_visible_items(self):
        from lagctl_tui import LagctlApp

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = AgentManager(root / "agents", root / "logs", runner=FakeRunner(), uid=501, network_collector=fake_network_collector)
            manager.add("alpha", ["/bin/echo"], start=False)
            manager.add("beta", ["/bin/echo"], start=False)
            app = LagctlApp(manager)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause(0.5)
                search = app.query_one("#job-search")
                search.value = "bet"
                await pilot.pause()
                self.assertEqual([item.job_name for item in app.query_one("#job-list").children], ["beta"])

    def test_status_filter_and_responsive_layout(self):
        asyncio.run(self._test_status_filter_and_responsive_layout())

    async def _test_status_filter_and_responsive_layout(self):
        from lagctl_tui import LagctlApp

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            runner = FakeRunner()
            manager = AgentManager(root / "agents", root / "logs", runner=runner, uid=501, network_collector=fake_network_collector)
            manager.add("running", ["/bin/echo"], start=True)
            manager.add("stopped", ["/bin/echo"], start=False)
            app = LagctlApp(manager)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause(0.5)
                app.query_one("#status-filter").value = "running"
                await pilot.pause()
                self.assertEqual([item.job_name for item in app.query_one("#job-list").children], ["running"])
                await pilot.resize_terminal(80, 30)
                await pilot.pause()
                self.assertIn("vertical", str(app.query_one("#dashboard").styles.layout))
                await pilot.resize_terminal(120, 40)
                await pilot.pause()
                self.assertIn("horizontal", str(app.query_one("#dashboard").styles.layout))

    def test_log_clear_auto_scroll_details_buttons_and_history(self):
        asyncio.run(self._test_log_clear_auto_scroll_details_buttons_and_history())

    async def _test_log_clear_auto_scroll_details_buttons_and_history(self):
        from lagctl_tui import LagctlApp
        from lagctl_tui.app import DetailsScreen, HistoryScreen, LogsScreen

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            runner = FakeRunner()
            manager = AgentManager(root / "agents", root / "logs", runner=runner, uid=501, network_collector=fake_network_collector)
            manager.add("worker", ["/bin/echo"], start=False)
            stdout = root / "logs" / "worker.stdout.log"
            stdout.write_text("needle\n", encoding="utf-8")
            app = LagctlApp(manager)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause(0.5)
                await pilot.press("enter")
                await pilot.pause()
                self.assertIsInstance(app.screen, DetailsScreen)
                app.screen.query_one("#details-toggle").press()
                await pilot.pause(0.5)
                self.assertTrue(app.events)
                app.screen.action_show_logs()
                await pilot.pause()
                self.assertIsInstance(app.screen, LogsScreen)
                screen = app.screen
                screen.action_toggle_auto_scroll()
                self.assertFalse(screen.auto_scroll)
                screen.action_show_stdout()
                screen.action_clear_logs()
                await pilot.pause(0.5)
                self.assertEqual(stdout.read_text(encoding="utf-8"), "")
                await pilot.press("q")
                await pilot.press("q")
                await pilot.pause()
                app.action_show_history()
                await pilot.pause()
                self.assertIsInstance(app.screen, HistoryScreen)
                self.assertIn("worker", app.screen.query_one("#history-output").text)

    async def _test_lifecycle_actions_run_and_refresh_after_worker_completion(self):
        from lagctl_tui import LagctlApp

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            runner = FakeRunner()
            manager = AgentManager(root / "agents", root / "logs", runner=runner, uid=501, network_collector=fake_network_collector)
            manager.add("worker", ["/bin/echo", "worker"], start=False)

            app = LagctlApp(manager)
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                app.action_toggle_selected()
                self.assertTrue(app.operation_running)
                await pilot.pause(0.5)
                self.assertFalse(app.operation_running)
                self.assertTrue(any(call[1] == "bootstrap" for call in runner.calls))

                app.action_restart_selected()
                await pilot.pause(0.5)
                app.action_run_selected()
                await pilot.pause(0.5)
                app.action_toggle_selected()
                await pilot.pause(0.5)
                actions = [call[1] for call in runner.calls]
                self.assertIn("kickstart", actions)
                self.assertIn("bootout", actions)


if __name__ == "__main__":
    unittest.main()
