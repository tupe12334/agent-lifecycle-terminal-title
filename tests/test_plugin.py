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
            self.assertIn(sequence("🎯 Blocked goal"), captured.getvalue())
            self.assertTrue(captured.getvalue().endswith(sequence("🙋 Blocked goal")))

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


class FakeAdapter:
    def __init__(self, rename_thread=None):
        self.calls = []
        if rename_thread is not None:
            self.rename_thread = rename_thread

    async def rename_thread(self, thread_id, name, *, lifecycle_emoji):
        self.calls.append((thread_id, name, lifecycle_emoji))


class FakeAgentContext:
    def __init__(
        self,
        platform="discord",
        thread_id="thread-1",
        adapter=None,
        session_id="session-1",
        tool_names=(),
        failed=False,
    ):
        self.platform = platform
        self.thread_id = thread_id
        self.adapter = FakeAdapter() if adapter is None else adapter
        self.session_id = session_id
        self.tool_names = tool_names
        self.failed = failed


class FakeHookRegistry:
    _builtin_hook_calls = 0

    def __init__(self):
        self._handlers = {}

    def _register_builtin_hooks(self):
        type(self)._builtin_hook_calls += 1
        self._handlers.setdefault("agent:start", []).append("builtin-start")


def run_async(coro):
    import asyncio

    return asyncio.run(coro)


class DiscordLifecycleHookInstallationTests(unittest.TestCase):
    def setUp(self):
        self.original_modules = {
            name: sys.modules.get(name) for name in ("gateway", "gateway.hooks")
        }
        gateway_pkg = types.ModuleType("gateway")
        hooks_module = types.ModuleType("gateway.hooks")
        hooks_module.HookRegistry = FakeHookRegistry
        gateway_pkg.hooks = hooks_module
        sys.modules["gateway"] = gateway_pkg
        sys.modules["gateway.hooks"] = hooks_module
        FakeHookRegistry._builtin_hook_calls = 0

    def tearDown(self):
        for name, module in self.original_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    def test_wraps_register_builtin_hooks_idempotently(self):
        plugin = load_plugin("lifecycle_discord_install_test")
        plugin.register(object())
        plugin.register(object())  # second registration must stay a no-op

        registry = sys.modules["gateway.hooks"].HookRegistry()
        registry._register_builtin_hooks()
        registry._register_builtin_hooks()  # calling twice must not duplicate

        self.assertEqual(sys.modules["gateway.hooks"].HookRegistry._builtin_hook_calls, 2)
        self.assertEqual(registry._handlers["agent:start"].count("builtin-start"), 2)
        for event in plugin.DISCORD_LIFECYCLE_EVENTS:
            handlers = registry._handlers[event]
            self.assertEqual(
                handlers.count(plugin._DISCORD_LIFECYCLE_HANDLERS[event]), 1
            )

    def test_missing_gateway_package_is_a_harmless_noop(self):
        sys.modules.pop("gateway", None)
        sys.modules.pop("gateway.hooks", None)
        plugin = load_plugin("lifecycle_discord_no_gateway_test")
        plugin.register(object())  # must not raise


class DiscordLifecycleHandlerTests(unittest.TestCase):
    def setUp(self):
        self.plugin = load_plugin("lifecycle_discord_handler_test")
        self.original_modules = {
            "tools.delegate_tool_registry": sys.modules.get(
                "tools.delegate_tool_registry"
            )
        }

    def tearDown(self):
        for name, module in self.original_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    def _install_subagent_registry(self, subagents=(), raises=False):
        module = types.ModuleType("tools.delegate_tool_registry")

        def list_active_subagents():
            if raises:
                raise RuntimeError("registry unavailable")
            return subagents

        module.list_active_subagents = list_active_subagents
        sys.modules["tools.delegate_tool_registry"] = module

    def test_start_renames_thread_with_hourglass(self):
        self._install_subagent_registry()
        adapter = FakeAdapter()
        context = {
            "platform": "discord",
            "thread_id": "thread-1",
            "adapter": adapter,
            "session_id": "session-1",
        }
        run_async(self.plugin._handle_discord_lifecycle("agent:start", context))
        self.assertEqual(adapter.calls, [("thread-1", "", "⏳")])

    def test_start_ignores_non_discord_platform(self):
        context = FakeAgentContext(platform="slack")
        run_async(self.plugin._handle_discord_lifecycle("agent:start", context))
        self.assertEqual(context.adapter.calls, [])

    def test_start_ignores_missing_thread_id(self):
        context = FakeAgentContext(thread_id="")
        run_async(self.plugin._handle_discord_lifecycle("agent:start", context))
        self.assertEqual(context.adapter.calls, [])

    def test_start_ignores_noncallable_rename_thread(self):
        adapter = FakeAdapter()
        adapter.rename_thread = "not callable"
        context = FakeAgentContext(adapter=adapter)
        run_async(self.plugin._handle_discord_lifecycle("agent:start", context))
        self.assertEqual(adapter.calls, [])

    def test_step_updates_to_people_with_active_owned_child(self):
        self._install_subagent_registry(
            subagents=[{"owner_agent_session_id": "session-1"}]
        )
        context = FakeAgentContext(tool_names=("delegate_task",))
        run_async(self.plugin._handle_discord_lifecycle("agent:step", context))
        self.assertEqual(context.adapter.calls, [("thread-1", "", "👥")])

    def test_step_noops_without_delegate_task_tool(self):
        self._install_subagent_registry(
            subagents=[{"owner_agent_session_id": "session-1"}]
        )
        context = FakeAgentContext(tool_names=())
        run_async(self.plugin._handle_discord_lifecycle("agent:step", context))
        self.assertEqual(context.adapter.calls, [])

    def test_step_noops_without_owned_child(self):
        self._install_subagent_registry(
            subagents=[{"owner_agent_session_id": "someone-else"}]
        )
        context = FakeAgentContext(tool_names=("delegate_task",))
        run_async(self.plugin._handle_discord_lifecycle("agent:step", context))
        self.assertEqual(context.adapter.calls, [])

    def test_step_noops_with_empty_child_session_id(self):
        self._install_subagent_registry(subagents=[{"owner_agent_session_id": ""}])
        context = FakeAgentContext(tool_names=("delegate_task",))
        run_async(self.plugin._handle_discord_lifecycle("agent:step", context))
        self.assertEqual(context.adapter.calls, [])

    def test_step_noops_with_empty_context_session_id(self):
        self._install_subagent_registry(
            subagents=[{"owner_agent_session_id": "session-1"}]
        )
        context = FakeAgentContext(tool_names=("delegate_task",), session_id="")
        run_async(self.plugin._handle_discord_lifecycle("agent:step", context))
        self.assertEqual(context.adapter.calls, [])

    def test_step_noops_when_registry_raises(self):
        self._install_subagent_registry(raises=True)
        context = FakeAgentContext(tool_names=("delegate_task",))
        run_async(self.plugin._handle_discord_lifecycle("agent:step", context))
        self.assertEqual(context.adapter.calls, [])

    def test_step_noops_when_registry_module_missing(self):
        sys.modules.pop("tools.delegate_tool_registry", None)
        context = FakeAgentContext(tool_names=("delegate_task",))
        run_async(self.plugin._handle_discord_lifecycle("agent:step", context))
        self.assertEqual(context.adapter.calls, [])

    def test_step_ignores_non_discord_context(self):
        self._install_subagent_registry(
            subagents=[{"owner_agent_session_id": "session-1"}]
        )
        context = FakeAgentContext(platform="slack", tool_names=("delegate_task",))
        run_async(self.plugin._handle_discord_lifecycle("agent:step", context))
        self.assertEqual(context.adapter.calls, [])

    def test_end_uses_cross_mark_on_failure(self):
        self._install_subagent_registry()
        context = FakeAgentContext(failed=True)
        run_async(self.plugin._handle_discord_lifecycle("agent:end", context))
        self.assertEqual(context.adapter.calls, [("thread-1", "", "❌")])

    def test_end_prefers_failure_over_active_child(self):
        self._install_subagent_registry(
            subagents=[{"owner_agent_session_id": "session-1"}]
        )
        context = FakeAgentContext(failed=True, tool_names=("delegate_task",))
        run_async(self.plugin._handle_discord_lifecycle("agent:end", context))
        self.assertEqual(context.adapter.calls, [("thread-1", "", "❌")])

    def test_end_uses_people_with_active_owned_child(self):
        self._install_subagent_registry(
            subagents=[{"owner_agent_session_id": "session-1"}]
        )
        context = FakeAgentContext(failed=False, tool_names=("delegate_task",))
        run_async(self.plugin._handle_discord_lifecycle("agent:end", context))
        self.assertEqual(context.adapter.calls, [("thread-1", "", "👥")])

    def test_end_uses_check_mark_without_active_child(self):
        self._install_subagent_registry()
        context = FakeAgentContext(failed=False)
        run_async(self.plugin._handle_discord_lifecycle("agent:end", context))
        self.assertEqual(context.adapter.calls, [("thread-1", "", "✅")])

    def test_end_ignores_non_discord_context(self):
        context = FakeAgentContext(platform="slack", failed=True)
        run_async(self.plugin._handle_discord_lifecycle("agent:end", context))
        self.assertEqual(context.adapter.calls, [])

    def test_rename_failure_is_swallowed(self):
        async def raising_rename_thread(*_args, **_kwargs):
            raise RuntimeError("discord API error")

        adapter = FakeAdapter(rename_thread=raising_rename_thread)
        context = FakeAgentContext(adapter=adapter)
        run_async(self.plugin._handle_discord_lifecycle("agent:start", context))  # must not raise


class FakePlatformActions:
    def __init__(self):
        self.calls = []

    async def set_thread_lifecycle_emoji(self, platform, chat_id, thread_id, emoji, *, profile=None):
        self.calls.append((platform, chat_id, thread_id, emoji, profile))
        return {"ok": True}


class PublicDiscordLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.plugin = load_plugin("lifecycle_public_discord_test")
        self.original_modules = {
            name: sys.modules.get(name) for name in ("hermes_cli", "hermes_cli.plugins")
        }

    def tearDown(self):
        for name, module in self.original_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    def test_public_event_routes_emoji_with_source_profile(self):
        actions = FakePlatformActions()
        run_async(self.plugin._handle_core_discord_lifecycle(
            actions,
            event_type="agent:end",
            platform="discord",
            chat_id="chat-1",
            thread_id="thread-1",
            session_id="session-1",
            profile="team-b",
            failed=True,
        ))
        self.assertEqual(actions.calls, [("discord", "chat-1", "thread-1", "❌", "team-b")])

    def test_register_prefers_public_core_stream_over_private_hook_wrapper(self):
        hermes_cli = types.ModuleType("hermes_cli")
        plugins_module = types.ModuleType("hermes_cli.plugins")
        plugins_module.emit_core_event = lambda *_args, **_kwargs: 0
        hermes_cli.plugins = plugins_module
        sys.modules["hermes_cli"] = hermes_cli
        sys.modules["hermes_cli.plugins"] = plugins_module
        subscriptions = []
        ctx = types.SimpleNamespace(
            platform_actions=FakePlatformActions(),
            subscribe=lambda event, callback: subscriptions.append((event, callback)),
        )

        self.plugin.register(ctx)

        self.assertEqual([event for event, _callback in subscriptions], ["hermes:gateway_agent_lifecycle"])


if __name__ == "__main__":
    unittest.main()
