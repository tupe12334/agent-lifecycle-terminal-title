"""Reflect the active Hermes CLI turn and session title in the terminal title."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from typing import Any, Callable

_ORIGINAL_SETTER_ATTR = "_agent_lifecycle_terminal_title_original_setter"
_ORIGINAL_CLI_COMMAND_ATTR = "_agent_lifecycle_terminal_title_original_process_command"
_ORIGINAL_CHAT_ATTR = "_agent_lifecycle_terminal_title_original_chat"
_ORIGINAL_CLI_RUN_ATTR = "_agent_lifecycle_terminal_title_original_run"
_ORIGINAL_REGISTER_BUILTIN_HOOKS_ATTR = (
    "_agent_lifecycle_terminal_title_original_register_builtin_hooks"
)

_WORKING = "⌛️"
_GOAL_WORKING = "🎯"
_SUCCESS = "✅"
_FAILURE = "❗️"
_UNACHIEVABLE = "🚫"
_DEFAULT_TITLE = "Hermes"

# Discord thread lifecycle emoji (see `register_discord_lifecycle_hooks` below).
DISCORD_PLATFORM = "discord"
DELEGATE_TASK_TOOL_NAME = "delegate_task"
DISCORD_LIFECYCLE_EVENTS = ("agent:start", "agent:step", "agent:end")
_DISCORD_EMOJI_START = "⏳"
_DISCORD_EMOJI_DELEGATING = "👥"
_DISCORD_EMOJI_DONE = "✅"
_DISCORD_EMOJI_FAILED = "❌"

_title_lock = threading.RLock()
_base_title = _DEFAULT_TITLE
_lifecycle_marker = _SUCCESS
_active_cli: Any = None
_owned_children: set[str] = set()
_owned_processes: set[str] = set()
_seen_processes: set[str] = set()
_process_owner: Any = None
_background_failed = False
_process_registry: Any = None
_process_monitor: Any = None
_process_monitor_stop = threading.Event()
_process_refresh_lock = threading.Lock()


def _refresh_background_processes(registry: Any = None) -> None:
    # Hooks and the monitor may refresh concurrently. Serialize snapshots so an
    # older running snapshot cannot overwrite a newer completion or handoff.
    with _process_refresh_lock:
        _refresh_background_snapshot(registry)


def _refresh_background_snapshot(registry: Any = None) -> None:
    """Observe registry state without consuming completion notifications."""
    global _process_owner, _background_failed
    registry = registry if registry is not None else _process_registry
    with _title_lock:
        owner = _cli_session_id()
    if registry is None or not owner:
        return
    # Registry reconciliation can acquire its own locks and perform I/O.
    # Never call it while holding the terminal-title lock.
    try:
        rows = registry.list_sessions()
    except Exception:
        return  # Cosmetic monitoring must not interrupt agent work.
    with _title_lock:
        if owner != _cli_session_id():
            return
        before = _render_title()
        owned = {row['session_id']: row for row in rows if row.get('owner_task_id') == owner}
        if _process_owner != owner:
            _process_owner = owner
            _owned_processes.clear()
            _seen_processes.clear()
            _seen_processes.update(owned)
            _background_failed = False
        for process_id, row in owned.items():
            if (row.get('status') == 'exited'
                    and (process_id in _owned_processes or process_id not in _seen_processes)
                    and row.get('exit_code') != 0):
                _background_failed = True
        _owned_processes.clear()
        _owned_processes.update(pid for pid, row in owned.items() if row.get('status') == 'running')
        _seen_processes.update(owned)
        if _render_title() != before:
            _write_terminal_title()


def _start_process_monitor() -> None:
    """Only a foreground CLI starts a context-bound, process-local observer."""
    global _process_registry, _process_monitor, _process_monitor_stop
    if _process_monitor is not None and _process_monitor.is_alive():
        _refresh_background_processes()
        return
    try:
        from tools.process_registry import process_registry
        from agent.memory_provider import spawn_context_thread
    except ImportError:
        return
    _process_registry = process_registry
    stop = threading.Event()
    _process_monitor_stop = stop
    _refresh_background_processes()

    def monitor() -> None:
        while not stop.wait(0.25):
            _refresh_background_processes()

    _process_monitor = spawn_context_thread(monitor, name='terminal-title-processes')
    _process_monitor.start()


def _on_post_tool_call(*, task_id=None, session_id=None, **_: Any) -> None:
    owner = _cli_session_id()
    if owner and owner in (task_id, session_id):
        _refresh_background_processes()


def _cli_session_id() -> Any:
    agent = getattr(_active_cli, "agent", None)
    return getattr(agent, "session_id", None) or getattr(_active_cli, "session_id", None)


def _on_subagent_start(*, parent_session_id=None, child_session_id=None, **_: Any) -> None:
    with _title_lock:
        if not child_session_id or not parent_session_id or parent_session_id != _cli_session_id():
            return
        _owned_children.add(child_session_id)
        _write_terminal_title()


def _on_subagent_stop(*, parent_session_id=None, child_session_id=None, **_: Any) -> None:
    _refresh_background_processes()  # Include any work handed off before the child stopped.
    with _title_lock:
        if not parent_session_id or parent_session_id != _cli_session_id():
            return
        if child_session_id in _owned_children:
            _owned_children.remove(child_session_id)
            _write_terminal_title()


def _safe_terminal_title(value: Any) -> str:
    """Return a single-line OSC-safe terminal title."""
    return " ".join(
        str(value or _DEFAULT_TITLE).replace("\x1b", "").replace("\x07", "").split()
    ) or _DEFAULT_TITLE


def _render_title() -> str:
    with _title_lock:
        marker = _lifecycle_marker
        if marker != _FAILURE:
            if _owned_children:
                marker = "👥"
            elif _owned_processes:
                marker = _GOAL_WORKING if marker == _GOAL_WORKING else _WORKING
            elif _background_failed and marker == _SUCCESS:
                marker = _FAILURE
        return f"{marker} {_base_title}"


def _run_tmux(*args: str) -> None:
    """Run a cosmetic tmux command without affecting a foreground turn."""
    try:
        subprocess.run(
            ["tmux", *args],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=1,
        )
    except (OSError, subprocess.SubprocessError):
        return


def _rename_tmux_window(title: str) -> None:
    """Mirror a title to its tmux window and its outer terminal tab.

    tmux owns the outer title and defaults ``set-titles`` to off; that leaves a
    terminal displaying its session summary (for example, ``1222: 1 windows``)
    despite a successful window rename. Scope configuration and naming to the
    originating ``TMUX_PANE`` so a concurrent tmux client cannot be redirected.
    """
    pane = os.environ.get("TMUX_PANE")
    if not os.environ.get("TMUX") or not pane:
        return
    _run_tmux("set-option", "-t", pane, "set-titles", "on")
    _run_tmux("set-option", "-t", pane, "set-titles-string", "#W")
    _run_tmux("rename-window", "-t", pane, title)


def _write_terminal_title(title: Any | None = None) -> None:
    """Write a safe title to the terminal, tmux window, and outer tab."""
    safe_title = _safe_terminal_title(_render_title() if title is None else title)
    sequence = f"\x1b]0;{safe_title}\x07\x1b]2;{safe_title}\x07"
    wrote_to_terminal = False
    try:
        # prompt-toolkit may proxy stdout; /dev/tty is the launch terminal.
        with open("/dev/tty", "w", encoding="utf-8", errors="ignore") as tty:
            if tty.isatty():
                tty.write(sequence)
                tty.flush()
                wrote_to_terminal = True
    except OSError:
        pass

    if not wrote_to_terminal:
        try:
            if not sys.stdout.isatty():
                return
            sys.stdout.write(sequence)
            sys.stdout.flush()
            wrote_to_terminal = True
        except Exception:
            # Terminal decoration is cosmetic and must never affect an agent turn.
            return

    if wrote_to_terminal:
        _rename_tmux_window(safe_title)


def _set_base_title(title: Any) -> None:
    global _base_title
    with _title_lock:
        _base_title = _safe_terminal_title(title)
        _write_terminal_title()


def _set_lifecycle(marker: str) -> None:
    global _lifecycle_marker
    with _title_lock:
        _lifecycle_marker = marker
        _write_terminal_title()


def _goal_is_active(cli: Any) -> bool:
    """Return whether this CLI turn begins with an active standing goal."""
    try:
        manager = cli._get_goal_manager()
        return bool(manager and manager.is_active())
    except Exception:
        return False


def _goal_was_judged_unachievable(cli: Any) -> bool:
    """Identify a goal judge's terminal blocked/unachievable DONE verdict.

    Hermes represents a blocked goal as the normal ``done`` verdict so the
    Ralph loop stops spending turns. The reason is the only distinction exposed
    at the CLI boundary, so use its documented blocked vocabulary rather than
    treating every achieved goal as unavailable.
    """
    try:
        state = cli._get_goal_manager().state
        if (
            state is None
            or getattr(state, "status", None) != "done"
            or getattr(state, "last_verdict", None) != "done"
        ):
            return False
        reason = str(getattr(state, "last_reason", "") or "").casefold()
    except Exception:
        return False
    return any(
        phrase in reason
        for phrase in (
            "unachievable",
            "blocked",
            "need user input",
            "needs user input",
            "requires user input",
            "awaiting user input",
        )
    )


def _restore_persisted_title(cli: Any) -> None:
    """Use the resumed session title before setting a lifecycle state."""
    try:
        session_id = getattr(cli, "session_id", None)
        db = getattr(cli, "_session_db", None)
        title = db.get_session_title(session_id) if db and session_id else None
        if title:
            _set_base_title(title)
    except Exception:
        pass


def _install_title_writer() -> None:
    """Patch the shared SessionDB title persistence boundary exactly once."""
    try:
        from hermes_state import SessionDB
    except Exception:
        return
    if getattr(SessionDB, _ORIGINAL_SETTER_ATTR, None) is not None:
        return
    original: Callable[..., bool] = SessionDB._set_session_title

    def wrapped(self: Any, session_id: str, title: str, *, source: str) -> bool:
        changed = original(self, session_id, title, source=source)
        if changed and (_active_cli is None or session_id == _cli_session_id()):
            _set_base_title(title)
        return changed

    setattr(SessionDB, _ORIGINAL_SETTER_ATTR, original)
    SessionDB._set_session_title = wrapped


def _install_pending_cli_title_writer() -> None:
    """Mirror a queued ``/title`` before a new session has a database row."""
    try:
        from cli import HermesCLI
    except Exception:
        return
    if getattr(HermesCLI, _ORIGINAL_CLI_COMMAND_ATTR, None) is not None:
        return
    original: Callable[..., bool] = HermesCLI.process_command

    def wrapped(self: Any, command: str) -> bool:
        result = original(self, command)
        parts = command.strip().split(maxsplit=1) if isinstance(command, str) else []
        if len(parts) == 2 and parts[0].lower() == "/title":
            pending = getattr(self, "_pending_title", None)
            if pending:
                _set_base_title(pending)
        return result

    setattr(HermesCLI, _ORIGINAL_CLI_COMMAND_ATTR, original)
    HermesCLI.process_command = wrapped


def _install_cli_lifecycle_writer() -> None:
    """Track foreground interactive turns, excluding subagents/review work."""
    try:
        from cli import HermesCLI
    except Exception:
        return
    if getattr(HermesCLI, _ORIGINAL_CHAT_ATTR, None) is not None:
        return
    original: Callable[..., Any] = HermesCLI.chat

    def wrapped(self: Any, *args: Any, **kwargs: Any) -> Any:
        global _active_cli, _background_failed
        with _title_lock:
            if _active_cli is not self:
                _owned_children.clear()
            _active_cli = self
            _background_failed = False
        _start_process_monitor()
        goal_was_active = _goal_is_active(self)
        _restore_persisted_title(self)
        _set_lifecycle(_GOAL_WORKING if goal_was_active else _WORKING)
        try:
            response = original(self, *args, **kwargs)
        except BaseException:
            _set_lifecycle(_FAILURE)
            raise
        _refresh_background_processes()
        # HermesCLI.chat returns None for setup/exception failures and converts
        # unrecovered turn failures into an ``Error: ...`` response.
        if response is None or (
            isinstance(response, str) and response.lstrip().startswith("Error:")
        ):
            _set_lifecycle(_FAILURE)
        elif goal_was_active and _goal_was_judged_unachievable(self):
            _set_lifecycle(_UNACHIEVABLE)
        else:
            _set_lifecycle(_SUCCESS)
        return response

    setattr(HermesCLI, _ORIGINAL_CHAT_ATTR, original)
    HermesCLI.chat = wrapped


def _install_cli_close_title_writer() -> None:
    """Leave a finished interactive terminal as a direct resume target."""
    try:
        from cli import HermesCLI
    except Exception:
        return
    if getattr(HermesCLI, _ORIGINAL_CLI_RUN_ATTR, None) is not None:
        return
    original: Callable[..., Any] = HermesCLI.run

    def wrapped(self: Any, *args: Any, **kwargs: Any) -> Any:
        global _active_cli
        try:
            return original(self, *args, **kwargs)
        finally:
            session_id = getattr(self, "session_id", None)
            agent = getattr(self, "agent", None)
            session_id = getattr(agent, "session_id", None) or session_id
            _process_monitor_stop.set()
            with _title_lock:
                _active_cli = None
                _owned_children.clear()
                _owned_processes.clear()
                if session_id:
                    _write_terminal_title(session_id)

    setattr(HermesCLI, _ORIGINAL_CLI_RUN_ATTR, original)
    HermesCLI.run = wrapped


def _read_field(obj: Any, name: str) -> Any:
    """Read ``name`` off a dict-like or attribute-like record."""
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _has_active_owned_child(session_id: Any) -> bool:
    """Whether a gateway session still owns a live delegated child."""
    session_id = str(session_id or "").strip()
    if not session_id:
        return False
    try:
        from tools.delegate_tool_registry import list_active_subagents

        return any(
            str(_read_field(child, "owner_agent_session_id") or "") == session_id
            for child in (list_active_subagents() or ())
        )
    except Exception:
        return False


async def _handle_discord_lifecycle(event_type: str, context: Any) -> None:
    """Update only a Discord thread's lifecycle emoji; cosmetic failures are ignored."""
    try:
        if _read_field(context, "platform") != DISCORD_PLATFORM:
            return
        thread_id = str(_read_field(context, "thread_id") or "").strip()
        adapter = _read_field(context, "adapter")
        rename_thread = getattr(adapter, "rename_thread", None)
        if not thread_id or not callable(rename_thread):
            return

        if event_type == "agent:start":
            emoji = _DISCORD_EMOJI_START
        elif event_type == "agent:step":
            tool_names = _read_field(context, "tool_names") or ()
            emoji = (
                _DISCORD_EMOJI_DELEGATING
                if DELEGATE_TASK_TOOL_NAME in tool_names
                and _has_active_owned_child(_read_field(context, "session_id"))
                else None
            )
        elif _read_field(context, "failed"):
            emoji = _DISCORD_EMOJI_FAILED
        else:
            emoji = (
                _DISCORD_EMOJI_DELEGATING
                if _has_active_owned_child(_read_field(context, "session_id"))
                else _DISCORD_EMOJI_DONE
            )
        if emoji:
            await rename_thread(thread_id, "", lifecycle_emoji=emoji)
    except Exception:
        # Thread decoration is cosmetic and must never interrupt a gateway turn.
        return


async def _handle_core_discord_lifecycle(actions: Any, **event: Any) -> None:
    """Apply lifecycle state through Hermes's public event and platform-action contracts."""
    try:
        if event.get("platform") != DISCORD_PLATFORM:
            return
        thread_id = str(event.get("thread_id") or "").strip()
        chat_id = str(event.get("chat_id") or "").strip()
        if not thread_id or not chat_id:
            return
        event_type = str(event.get("event_type") or "")
        session_id = event.get("session_id")
        if event_type == "agent:start":
            emoji = _DISCORD_EMOJI_START
        elif event_type == "agent:step":
            tool_names = event.get("tool_names") or ()
            emoji = (
                _DISCORD_EMOJI_DELEGATING
                if DELEGATE_TASK_TOOL_NAME in tool_names and _has_active_owned_child(session_id)
                else None
            )
        elif event.get("failed"):
            emoji = _DISCORD_EMOJI_FAILED
        else:
            emoji = (
                _DISCORD_EMOJI_DELEGATING
                if _has_active_owned_child(session_id)
                else _DISCORD_EMOJI_DONE
            )
        if emoji:
            profile = str(event.get("profile") or "").strip() or None
            await actions.set_thread_lifecycle_emoji(
                DISCORD_PLATFORM, chat_id, thread_id, emoji, profile=profile,
            )
    except Exception:
        # Cosmetic failures must never interrupt a gateway turn or event worker.
        return


def _register_core_discord_lifecycle(ctx: Any) -> bool:
    """Subscribe to the public core lifecycle stream when the installed Hermes supports it."""
    try:
        from hermes_cli.plugins import emit_core_event
    except Exception:
        return False
    if not callable(emit_core_event):
        return False

    async def handle(**event: Any) -> None:
        await _handle_core_discord_lifecycle(ctx.platform_actions, **event)

    ctx.subscribe("hermes:gateway_agent_lifecycle", handle)
    return True


_DISCORD_LIFECYCLE_HANDLERS = {
    event: _handle_discord_lifecycle for event in DISCORD_LIFECYCLE_EVENTS
}


def _attach_discord_lifecycle_handlers(registry: Any) -> None:
    """Register the Discord lifecycle handlers on a ``HookRegistry`` instance.

    Appends into ``registry._handlers[event]`` alongside whatever the builtin
    registration and any other plugin already installed, skipping an event
    whose handler is already present so repeated calls stay idempotent.
    """
    handlers_by_event = getattr(registry, "_handlers", None)
    if handlers_by_event is None:
        return
    for event, handler in _DISCORD_LIFECYCLE_HANDLERS.items():
        try:
            bucket = handlers_by_event.setdefault(event, [])
        except Exception:
            continue
        if handler not in bucket:
            bucket.append(handler)


def _install_discord_lifecycle_hooks() -> None:
    """Wrap ``gateway.hooks.HookRegistry._register_builtin_hooks`` once.

    Hermes has no public gateway-lifecycle hook or adapter handle for
    out-of-tree plugins, so this wraps the internal builtin-hook
    registration boundary instead of touching gateway/core source. It
    preserves the original method and calls it unchanged before attaching
    the Discord thread lifecycle handlers, so existing builtin hooks and any
    other plugin's callbacks are unaffected. No-ops harmlessly if the
    gateway package isn't importable.
    """
    try:
        from gateway.hooks import HookRegistry
    except Exception:
        return
    if getattr(HookRegistry, _ORIGINAL_REGISTER_BUILTIN_HOOKS_ATTR, None) is not None:
        return
    original: Callable[[Any], None] = HookRegistry._register_builtin_hooks

    def wrapped(self: Any) -> None:
        original(self)
        _attach_discord_lifecycle_handlers(self)

    setattr(HookRegistry, _ORIGINAL_REGISTER_BUILTIN_HOOKS_ATTR, original)
    HookRegistry._register_builtin_hooks = wrapped


def register(ctx: Any) -> None:
    """Install idempotent title and foreground-CLI lifecycle integrations."""
    _install_title_writer()
    _install_pending_cli_title_writer()
    _install_cli_lifecycle_writer()
    _install_cli_close_title_writer()
    register_hook = getattr(ctx, "register_hook", None)
    if callable(register_hook):
        register_hook("subagent_start", _on_subagent_start)
        register_hook("subagent_stop", _on_subagent_stop)
        register_hook("post_tool_call", _on_post_tool_call)
    # New Hermes versions publish an adapter-free lifecycle event and expose a
    # capability-gated emoji action. Retain the old private hook wrapper only
    # as a compatibility fallback for older Hermes installations.
    if not _register_core_discord_lifecycle(ctx):
        _install_discord_lifecycle_hooks()
