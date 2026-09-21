"""Real plugin discovery, tool hooks, clarify, and approval gate; no LLM or commands executed.
Run in a PTY: python tests/integration_attention.py <hermes-source>.
"""
import os
from pathlib import Path
import sys
import tempfile
import types

sys.path.insert(0, sys.argv[1])
with tempfile.TemporaryDirectory(prefix='title-attention-', dir=os.environ.get('TMPDIR')) as home:
    os.environ['HERMES_HOME'] = home
    plugin_dir = Path(__file__).resolve().parents[1]
    (Path(home) / 'plugins').mkdir()
    (Path(home) / 'plugins' / 'agent-lifecycle-terminal-title').symlink_to(plugin_dir, target_is_directory=True)
    (Path(home) / 'config.yaml').write_text('plugins:\n  enabled: [agent-lifecycle-terminal-title]\napprovals:\n  mode: manual\n')
    from hermes_cli.plugins import discover_plugins, get_plugin_manager, _dispatch_pre_tool_call_hooks
    from hermes_cli.lifecycle import invoke_hook
    discover_plugins()
    manager = get_plugin_manager()
    plugin = next(p.module for p in manager._plugins.values() if p.manifest.name == 'agent-lifecycle-terminal-title')
    owner = 'attention-integration'
    plugin._active_cli = types.SimpleNamespace(session_id=owner, agent=None)
    plugin._set_lifecycle(plugin._WORKING)

    from tools.clarify_tool import clarify_tool
    def answer(question, choices, **kwargs):
        assert plugin._render_title().startswith('🙋 '), plugin._render_title()
        return 'Test answer'
    block, modified = _dispatch_pre_tool_call_hooks('clarify', {'questions': [{'question': 'Test question?'}]}, task_id=owner, session_id=owner, tool_call_id='clarify-1')
    assert block is None
    result = clarify_tool(question='Test question?', callback=answer)
    assert 'Test answer' in result
    invoke_hook('post_tool_call', tool_name='clarify', task_id=owner, session_id=owner, tool_call_id='clarify-1', result=result)
    assert plugin._render_title().startswith(plugin._WORKING)
    print('PASS: real clarify dispatch shows attention inside callback and restores work', flush=True)

    from tools import approval_context
    from tools.approval import request_tool_approval
    interactive = approval_context.set_hermes_interactive_context(True)
    session = approval_context.set_current_session_key(owner)
    correlation = approval_context.set_current_observability_context(session_id=owner, tool_call_id='approval-1')
    try:
        for choice in ('once', 'deny', 'timeout'):
            called = []
            def approve(*args, **kwargs):
                assert plugin._render_title().startswith('🙋 '), plugin._render_title()
                called.append(True)
                return choice
            result = request_tool_approval('attention_probe', 'Cosmetic title test only', rule_key='attention-test-' + choice, approval_callback=approve)
            assert called, result
            assert result['approved'] == (choice == 'once'), result
            assert plugin._render_title().startswith(plugin._WORKING)
            print('PASS: approval choice preserved:', choice, flush=True)
    finally:
        approval_context.reset_current_observability_context(correlation)
        approval_context.reset_current_session_key(session)
        approval_context.reset_hermes_interactive_context(interactive)
    plugin._active_cli = None
