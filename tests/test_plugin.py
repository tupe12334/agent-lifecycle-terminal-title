"""Focused lifecycle title test without requiring a Hermes installation."""

import builtins
import importlib.util
import io
from pathlib import Path
import sys
import types
import unittest

PLUGIN_PATH = Path(__file__).resolve().parents[1] / "__init__.py"


class FakeTTY(io.StringIO):
    def isatty(self):
        return True


class FakeSessionDB:
    def _set_session_title(self, session_id, title, *, source):
        self.calls.append((session_id, title, source))
        return True

    def __init__(self):
        self.calls = []


class FakeCLI:
    def __init__(self, result="done", title="Existing title"):
        self.result = result
        self.session_id = "session-1"
        self._pending_title = None
        self._session_db = types.SimpleNamespace(get_session_title=lambda _: title)

    def process_command(self, command):
        if command.startswith("/title "):
            self._pending_title = command.split(" ", 1)[1]
        return True

    def chat(self, *_args, **_kwargs):
        return self.result


class LifecycleTitleTests(unittest.TestCase):
    def test_lifecycle_markers_and_session_titles(self):
        original_modules = {name: sys.modules.get(name) for name in ("cli", "hermes_state")}
        original_open, original_stdout = builtins.open, sys.stdout
        captured = FakeTTY()
        try:
            sys.modules["hermes_state"] = types.SimpleNamespace(SessionDB=FakeSessionDB)
            sys.modules["cli"] = types.SimpleNamespace(HermesCLI=FakeCLI)
            spec = importlib.util.spec_from_file_location("lifecycle_title_test_plugin", PLUGIN_PATH)
            self.assertIsNotNone(spec)
            self.assertIsNotNone(spec.loader)
            plugin = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(plugin)
            builtins.open = lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("no tty"))
            sys.stdout = captured
            plugin.register(object())

            self.assertTrue(FakeSessionDB()._set_session_title("s", "Safe\x1b title\x07", source="user"))
            self.assertTrue(captured.getvalue().endswith("\x1b]0;✅ Safe title\x07"))

            captured.seek(0); captured.truncate(0)
            self.assertEqual(FakeCLI("answer", "Resume").chat("hello"), "answer")
            output = captured.getvalue()
            self.assertIn("\x1b]0;⌛️ Resume\x07", output)
            self.assertTrue(output.endswith("\x1b]0;✅ Resume\x07"))

            captured.seek(0); captured.truncate(0)
            self.assertTrue(FakeCLI("Error: failed", "Broken").chat("hello").startswith("Error:"))
            self.assertTrue(captured.getvalue().endswith("\x1b]0;❗️ Broken\x07"))

            captured.seek(0); captured.truncate(0)
            queued = FakeCLI()
            queued.process_command("/title First title")
            self.assertTrue(captured.getvalue().endswith("\x1b]0;❗️ First title\x07"))
        finally:
            builtins.open, sys.stdout = original_open, original_stdout
            for name, module in original_modules.items():
                if module is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = module


if __name__ == "__main__":
    unittest.main()
