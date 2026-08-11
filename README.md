# agent-lifecycle-terminal-title

A native [Hermes Agent](https://github.com/NousResearch/hermes-agent) plugin that prefixes the current terminal title with the foreground agent lifecycle state while retaining the session title.

## Lifecycle markers

- `⌛️` — the local Hermes CLI is processing a user turn
- `✅` — the most recent turn completed successfully
- `❗️` — the most recent turn failed

The plugin mirrors manual `/title` changes, including a title queued before the first message, automatic Hermes titles, and titles on resumed sessions. It uses OSC 0 and writes only to a controlling TTY, so gateway, cron, and background work do not rename terminals.

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
