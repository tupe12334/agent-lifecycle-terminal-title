"""Focused lifecycle title checks without requiring a Hermes installation."""

import builtins
import importlib.util
import io
import os
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import call, patch

PLUGIN_PATH = Path(__file__).resolve().parents[1] / "__init__.py"


def load_plugin(name="lifecycle_title_test_plugin"):
    spec = importlib.util.spec_from_file_location(name, PLUGIN_PATH)
    assert spec and spec.loader
    plugin = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(plugin)
    return plugin


class FakeTTY(io.StringIO):
    def isatty(self):
        return True


class FakeSessionDB:
    def _set_session_title(self, session_id, title, *, source):
        self.calls.append((session_id, title, source))
        return True

    def __init__(self):
        self.calls = []


class FakeGoalManager:
    def __init__(self):
        self.state = types.SimpleNamespace(
            status="active", last_verdict=None, last_reason=None
        )

    def is_active(self):
        return self.state.status == "active"


class FakeCLI:
    def __init__(self, result="done", title="Existing title", goal_reason=None):
        self.result = result
        self.session_id = "session-1"
        self._pending_title = None
        self._session_db = types.SimpleNamespace(get_session_title=lambda _: title)
        self._goal = FakeGoalManager() if goal_reason is not None else None
        self._goal_reason = goal_reason

    def process_command(self, command):
        if command.startswith("/title "):
            self._pending_title = command.split(" ", 1)[1]
        return True

    def chat(self, *_args, **_kwargs):
        if self._goal is not None:
            self._goal.state.status = "done"
            self._goal.state.last_verdict = "done"
            self._goal.state.last_reason = self._goal_reason
        return self.result

    def _get_goal_manager(self):
        return self._goal

    def run(self, *_args, **_kwargs):
        return self.result


def sequence(title):
    return f"\x1b]0;{title}\x07\x1b]2;{title}\x07"


class LifecycleTitleTests(unittest.TestCase):
    def test_lifecycle_markers_session_titles_and_close_resume_target(self):
        original_modules = {name: sys.modules.get(name) for name in ("cli", "hermes_state")}
        original_open, original_stdout = builtins.open, sys.stdout
        captured = FakeTTY()
        try:
            sys.modules["hermes_state"] = types.SimpleNamespace(SessionDB=FakeSessionDB)
            sys.modules["cli"] = types.SimpleNamespace(HermesCLI=FakeCLI)
            plugin = load_plugin()
            builtins.open = lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("no tty"))
            sys.stdout = captured
            plugin.register(object())

            self.assertTrue(FakeSessionDB()._set_session_title("s", "Safe\x1b title\x07", source="user"))
            self.assertTrue(captured.getvalue().endswith(sequence("✅ Safe title")))

            captured.seek(0); captured.truncate(0)
            self.assertEqual(FakeCLI("answer", "Resume").chat("hello"), "answer")
            output = captured.getvalue()
            self.assertIn(sequence("⌛️ Resume"), output)
            self.assertTrue(output.endswith(sequence("✅ Resume")))

            captured.seek(0); captured.truncate(0)
            self.assertEqual(
                FakeCLI(
                    "blocked",
                    "Blocked goal",
                    "Goal is blocked pending user input",
                ).chat("hello"),
                "blocked",
            )
            self.assertTrue(captured.getvalue().endswith(sequence("🚫 Blocked goal")))

            captured.seek(0); captured.truncate(0)
            self.assertTrue(FakeCLI("Error: failed", "Broken").chat("hello").startswith("Error:"))
            self.assertTrue(captured.getvalue().endswith(sequence("❗️ Broken")))

            captured.seek(0); captured.truncate(0)
            queued = FakeCLI()
            queued.process_command("/title First title")
            self.assertTrue(captured.getvalue().endswith(sequence("❗️ First title")))

            captured.seek(0); captured.truncate(0)
            self.assertEqual(FakeCLI("closed").run(), "closed")
            self.assertEqual(captured.getvalue(), sequence("session-1"))
        finally:
            builtins.open, sys.stdout = original_open, original_stdout
            for name, module in original_modules.items():
                if module is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = module


class TmuxTitleTests(unittest.TestCase):
    def test_propagates_the_originating_window_name_to_the_outer_tab(self):
        plugin = load_plugin("lifecycle_title_tmux_test")
        with patch.dict(
            os.environ, {"TMUX": "/tmp/tmux.sock,1,2", "TMUX_PANE": "%42"}, clear=False
        ), patch.object(plugin.subprocess, "run") as run:
            plugin._rename_tmux_window("✅ Focused title")

        expected = dict(
            check=False,
            stdout=plugin.subprocess.DEVNULL,
            stderr=plugin.subprocess.DEVNULL,
            timeout=1,
        )
        self.assertEqual(
            run.call_args_list,
            [
                call(["tmux", "set-option", "-t", "%42", "set-titles", "on"], **expected),
                call(["tmux", "set-option", "-t", "%42", "set-titles-string", "#W"], **expected),
                call(["tmux", "rename-window", "-t", "%42", "✅ Focused title"], **expected),
            ],
        )

    def test_tmux_is_not_used_without_an_originating_pane(self):
        plugin = load_plugin("lifecycle_title_no_tmux_test")
        with patch.dict(os.environ, {}, clear=True), patch.object(plugin.subprocess, "run") as run:
            plugin._rename_tmux_window("Ignored")
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
