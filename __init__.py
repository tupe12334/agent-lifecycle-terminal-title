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

_WORKING = "⌛️"
_GOAL_WORKING = "🎯"
_SUCCESS = "✅"
_FAILURE = "❗️"
_UNACHIEVABLE = "🚫"
_DEFAULT_TITLE = "Hermes"

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


def register(ctx: Any) -> None:
    """Install idempotent title and foreground-CLI lifecycle integrations."""
    del ctx
    _install_title_writer()
    _install_pending_cli_title_writer()
    _install_cli_lifecycle_writer()
    _install_cli_close_title_writer()
