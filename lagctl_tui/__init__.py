"""Optional Textual dashboard for lagctl."""

from .app import LagctlApp


def run_tui(manager) -> int:
    """Run the dashboard and return its exit status."""
    LagctlApp(manager).run()
    return 0


__all__ = ["LagctlApp", "run_tui"]
