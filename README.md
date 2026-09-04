# agent-lifecycle-terminal-title

A native [Hermes Agent](https://github.com/NousResearch/hermes-agent) plugin that prefixes the current terminal title with the foreground agent lifecycle state while retaining the session title.

## Lifecycle markers

- `⌛️` — the local Hermes CLI is processing a user turn
- `🎯` — the local Hermes CLI is working on an active Goal
- `✅` — the most recent turn completed successfully
- `❗️` — the most recent turn failed
- `🚫` — an active Goal was judged unachievable, blocked, or in need of user input

The plugin mirrors manual `/title` changes, including a title queued before the first message, automatic Hermes titles, and titles on resumed sessions. It emits OSC 0 and OSC 2 only to a controlling TTY, so gateway, cron, and background work do not rename terminals. When the interactive CLI closes, it changes the title to the opaque session ID as a direct `hermes --resume` target.

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
