"""Reflect the active Hermes CLI turn and session title in the terminal title."""

from __future__ import annotations

import sys
import threading
from typing import Any, Callable

_ORIGINAL_SETTER_ATTR = "_agent_lifecycle_terminal_title_original_setter"
_ORIGINAL_CLI_COMMAND_ATTR = "_agent_lifecycle_terminal_title_original_process_command"
_ORIGINAL_CHAT_ATTR = "_agent_lifecycle_terminal_title_original_chat"

_WORKING = "⌛️"
_SUCCESS = "✅"
_FAILURE = "❗️"
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


def _write_terminal_title() -> None:
    """Write an OSC 0 title sequence to the controlling terminal, if any."""
    sequence = f"\x1b]0;{_safe_terminal_title(_render_title())}\x07"
    try:
        # prompt-toolkit may proxy stdout; /dev/tty is the launch terminal.
        with open("/dev/tty", "w", encoding="utf-8", errors="ignore") as tty:
            if not tty.isatty():
                return
            tty.write(sequence)
            tty.flush()
        return
    except OSError:
        pass
    try:
        if not sys.stdout.isatty():
            return
        sys.stdout.write(sequence)
        sys.stdout.flush()
    except Exception:
        # Terminal decoration is cosmetic and must never affect an agent turn.
        return


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
        _restore_persisted_title(self)
        _set_lifecycle(_WORKING)
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
        else:
            _set_lifecycle(_SUCCESS)
        return response

    setattr(HermesCLI, _ORIGINAL_CHAT_ATTR, original)
    HermesCLI.chat = wrapped


def register(ctx: Any) -> None:
    """Install idempotent title and foreground-CLI lifecycle integrations."""
    del ctx
    _install_title_writer()
    _install_pending_cli_title_writer()
    _install_cli_lifecycle_writer()
