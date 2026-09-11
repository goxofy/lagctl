import asyncio
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from lagctl import AgentManager

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
            manager = AgentManager(root / "agents", root / "logs", runner=runner, uid=501)
            manager.add("alpha", ["/bin/echo", "alpha"], start=False)
            manager.add("beta", ["/bin/echo", "beta"], start=False)
            (root / "logs" / "alpha.stdout.log").write_text("alpha out\n", encoding="utf-8")
            (root / "logs" / "alpha.stderr.log").write_text("alpha err\n", encoding="utf-8")

            app = LagctlApp(manager)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                job_list = app.query_one("#job-list")
                job_list.focus()
                await pilot.press("down")
                await pilot.pause()
                self.assertEqual(app.selected_name, "beta")
                self.assertTrue(job_list.children[1].has_class("-highlight"))

                await pilot.press("enter")
                await pilot.press("s")
                await pilot.press("s")
                await pilot.press("R")
                await pilot.press("x")
                await pilot.pause()

                job_list.index = 0
                await pilot.press("l")
                await pilot.pause()
                self.assertIsInstance(app.screen, LogsScreen)
                for key in ("o", "e", "a"):
                    await pilot.press(key)
                    await pilot.pause()
                await pilot.pause(2.5)
                await pilot.press("escape")
                await pilot.pause()
                self.assertNotIsInstance(app.screen, LogsScreen)
                self.assertTrue(job_list.children[0].has_class("-highlight"))

                await pilot.press("d")
                await pilot.pause()
                self.assertIsInstance(app.screen, ConfirmDeleteScreen)
                await pilot.press("escape")
                await pilot.pause()
                self.assertNotIsInstance(app.screen, ConfirmDeleteScreen)


if __name__ == "__main__":
    unittest.main()
