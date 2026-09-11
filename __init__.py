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


def _safe_terminal_title(value: Any) -> str:
    """Return a single-line OSC-safe terminal title."""
    return " ".join(
        str(value or _DEFAULT_TITLE).replace("\x1b", "").replace("\x07", "").split()
    ) or _DEFAULT_TITLE


def _render_title() -> str:
    with _title_lock:
        return f"{_lifecycle_marker} {_base_title}"


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
        if changed:
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
        goal_was_active = _goal_is_active(self)
        _restore_persisted_title(self)
        _set_lifecycle(_GOAL_WORKING if goal_was_active else _WORKING)
        try:
            response = original(self, *args, **kwargs)
        except BaseException:
            _set_lifecycle(_FAILURE)
            raise
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
        try:
            return original(self, *args, **kwargs)
        finally:
            session_id = getattr(self, "session_id", None)
            agent = getattr(self, "agent", None)
            session_id = getattr(agent, "session_id", None) or session_id
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
    del ctx
    _install_title_writer()
    _install_pending_cli_title_writer()
    _install_cli_lifecycle_writer()
    _install_cli_close_title_writer()
    _install_discord_lifecycle_hooks()
