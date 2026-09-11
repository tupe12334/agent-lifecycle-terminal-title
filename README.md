# agent-lifecycle-terminal-title

A native [Hermes Agent](https://github.com/NousResearch/hermes-agent) plugin that prefixes the current terminal title with the foreground agent lifecycle state while retaining the session title.

## Lifecycle markers

- `⌛️` — the local Hermes CLI is processing a user turn
- `🎯` — the local Hermes CLI is working on an active Goal
- `✅` — the most recent turn completed successfully
- `❗️` — the most recent turn failed
- `🚫` — an active Goal was judged unachievable, blocked, or in need of user input

The plugin mirrors manual `/title` changes, including a title queued before the first message, automatic Hermes titles, and titles on resumed sessions. It emits OSC 0 and OSC 2 only to a controlling TTY, so gateway, cron, and background work do not rename terminals. When the interactive CLI closes, it changes the title to the opaque session ID as a direct `hermes --resume` target.

## Discord thread lifecycle emojis

This plugin is the single source of truth for Discord lifecycle emojis. When Hermes starts a Discord thread turn it sets `⏳`; after a `delegate_task` step with a live child owned by that session it sets `👥`; when the turn ends it sets `❌` on failure, otherwise `👥` while an owned child remains live and `✅` when none does. It changes only the lifecycle emoji via `adapter.rename_thread(thread_id, "", lifecycle_emoji=...)`.

The integration wraps Hermes's internal gateway hook-registration boundary because the public out-of-tree plugin API does not yet expose gateway lifecycle events with Discord adapter/thread handles. It is idempotent, preserves existing hook registrations, and is cosmetic/fail-open. Current Hermes `agent:step` payloads do not include the adapter or thread ID, so `👥` is applied on the step only when those fields are supplied; the end-state transition still follows the owned-child check. Do not install a separate config-owned Discord lifecycle hook with this plugin enabled.

## tmux

Inside tmux, the plugin targets the originating `$TMUX_PANE`: it names only that window, enables tmux outer-title propagation, and makes the outer terminal tab follow the active window name. This replaces tmux's default session-summary title (for example, `1222: 1 windows (attached)`) with the Hermes lifecycle/session title. Explicit naming also disables tmux automatic renaming for that window.

## Install

```sh
hermes plugins install tupe12334/agent-lifecycle-terminal-title --enable
```

Restart Hermes after installation so the plugin can register. If you previously used the older `terminal-session-title` plugin, disable it to avoid two title writers:

```sh
hermes plugins disable terminal-session-title
```

## VS Code integrated terminal

Enable this VS Code User setting, then open a new integrated terminal:

```jsonc
"terminal.integrated.tabs.allowAgentCliTitle": true
```

## Compatibility

Hermes currently has no public session-title-change hook. The plugin therefore wraps the shared internal `SessionDB._set_session_title` persistence boundary and the foreground `HermesCLI.chat` boundary. Re-run the focused checks after Hermes upgrades; replace the internal wrapper with an official hook when one becomes available.

## Development

Run the standard-library test suite:

```sh
python3 -m unittest discover -s tests -v
```

## License

MIT. See [LICENSE](LICENSE).
