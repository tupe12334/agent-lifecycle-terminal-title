# agent-lifecycle-terminal-title

A native [Hermes Agent](https://github.com/NousResearch/hermes-agent) plugin that prefixes the current terminal title with the foreground agent lifecycle state while retaining the session title.

## Lifecycle markers

- `🙋` — waiting for your clarification answer or manual approval, or a Goal explicitly blocked on user input
- `⌛️` — the local Hermes CLI is processing a user turn
- `🎯` — the local Hermes CLI is working on an active Goal
- `👥` — this CLI session has delegated children still running (including between turns)

- `✅` — the most recent turn completed successfully
- `❗️` — the most recent turn failed
- `🚫` — an active Goal was judged unachievable or blocked for a reason other than user input

Delegation uses Hermes's public `subagent_start` / `subagent_stop` hooks. Multiple children are tracked by session ID; unrelated sessions cannot change this terminal. The last child finishing restores the foreground lifecycle marker. Child title writes cannot replace the parent session title.

The plugin mirrors manual `/title` changes, including a title queued before the first message, automatic Hermes titles, and titles on resumed sessions. It emits OSC 0 and OSC 2 only to a controlling TTY, so gateway, cron, and background work do not rename terminals. When the interactive CLI closes, it changes the title to the opaque session ID as a direct `hermes --resume` target.

## Waiting for you

`🙋` takes precedence over running children/background work while this CLI has a pending `clarify` question or manual approval prompt. The marker restores the actual work status after an answer, approval, denial, timeout, or cancellation. Overlapping requests are tracked separately, and automatic/smart approvals or unrelated sessions cannot mark this tab. Hooks are observers only: they never approve, deny, or change a tool result.

A completed Goal whose blocked reason explicitly mentions user input also uses `🙋`; unrelated blocked/unachievable Goals retain `🚫`. Ordinary final-response prose is not heuristically classified as a request: use `clarify` for a decision that requires an answer. Opening an idle input prompt alone does not mean the agent needs you.

Verify real discovery, hook dispatch, clarification callbacks, and the approval gate in a PTY without executing commands or calling an LLM:

```sh
python tests/integration_attention.py /path/to/hermes-agent
```

## Background terminal commands

A successful parent response does not mean its background tests/builds have finished. The CLI keeps `⌛️` while any registry-owned command is running, even when completion notifications are disabled. `👥` still takes precedence while delegated agents are active. After the last command exits, the title returns to the parent outcome; an observed nonzero or unknown exit shows `❗️` rather than a false success. A new parent turn clears that failure latch.

A CLI-only context-bound monitor checks `ProcessRegistry.list_sessions()` every 250 ms, with immediate refreshes after tools, child handoff, and parent completion. It filters by raw `owner_task_id`, never OS descendants or notification policy, and does not drain completion messages. It stops when the CLI closes. This query is a Hermes implementation API; rerun the integration check after upgrades.

The integration test exercises real subprocesses without LLM calls (only the foreground chat body is substituted):

```sh
HERMES_HOME=/path/to/isolated/scratch-home python tests/integration_background_processes.py /path/to/hermes-agent
```

Run it in a PTY to verify emitted OSC titles as well as lifecycle state. It covers multiple/silent processes, foreign ownership, nonzero exit, delegated handoff, preserved completion notifications, and monitor shutdown. Restart existing CLI sessions after upgrading, but let their running jobs finish first.

## Discord thread lifecycle emojis

This plugin is the single source of truth for Discord lifecycle emojis. When Hermes starts a Discord thread turn it sets `⏳`; after a `delegate_task` step with a live child owned by that session it sets `👥`; when the turn ends it sets `❌` on failure, otherwise `👥` while an owned child remains live and `✅` when none does. It subscribes to Hermes's public `hermes:gateway_agent_lifecycle` stream and changes only the lifecycle emoji through the capability-gated `ctx.platform_actions.set_thread_lifecycle_emoji(...)` action.

Grant the plugin `gateway.platform_actions` before enabling its Discord behavior. The core event payload carries the source profile so a multiplexed gateway updates through the same bot identity that received the turn. Delivery is queued and cosmetic/fail-open. For compatibility with older Hermes versions that do not expose the public lifecycle stream, the plugin retains its private hook wrapper as a fallback only. Do not install a separate config-owned Discord lifecycle hook with this plugin enabled.

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
