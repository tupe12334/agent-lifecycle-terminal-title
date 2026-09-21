"""User-input state outranks background work without changing approval decisions."""
import sys
import types
import unittest
from unittest.mock import patch
from test_plugin import load_plugin


class AttentionTests(unittest.TestCase):
    def test_interrupted_chat_clears_pending_attention(self):
        plugin = load_plugin()
        class CLI:
            session_id = 'parent'
            def chat(self):
                plugin._on_pre_tool_call(tool_name='clarify', task_id='parent', tool_call_id='q')
                raise KeyboardInterrupt()
        with patch.dict(sys.modules, {'cli': types.SimpleNamespace(HermesCLI=CLI)}), patch.object(plugin, '_start_process_monitor'), patch.object(plugin, '_write_terminal_title'):
            plugin._install_cli_lifecycle_writer()
            with self.assertRaises(KeyboardInterrupt):
                CLI().chat()
            self.assertFalse(plugin._pending_attention)
            self.assertTrue(plugin._render_title().startswith(plugin._FAILURE))

    def test_clarify_and_approval_overlap_restore_actual_work(self):
        plugin = load_plugin()
        plugin._active_cli = types.SimpleNamespace(session_id='parent', agent=None)
        plugin._owned_children.add('child')
        plugin._owned_processes.add('process')
        with patch.object(plugin, '_write_terminal_title'):
            plugin._on_pre_tool_call(tool_name='clarify', task_id='parent', tool_call_id='question')
            self.assertTrue(plugin._render_title().startswith('🙋 '))
            plugin._set_lifecycle(plugin._SUCCESS)
            self.assertTrue(plugin._render_title().startswith('🙋 '))
            self.assertIsNone(plugin._on_pre_approval_request(surface='cli', session_id='parent', tool_call_id='approval'))
            plugin._on_post_tool_call(tool_name='clarify', task_id='parent', tool_call_id='question')
            self.assertTrue(plugin._render_title().startswith('🙋 '))
            self.assertIsNone(plugin._on_post_approval_response(surface='cli', session_id='parent', tool_call_id='approval', choice='deny'))
            self.assertTrue(plugin._render_title().startswith('👥 '))
            plugin._owned_children.clear()
            self.assertTrue(plugin._render_title().startswith(plugin._WORKING))
            plugin._owned_processes.clear()
            self.assertTrue(plugin._render_title().startswith(plugin._SUCCESS))

    def test_unrelated_and_automatic_prompts_do_not_mark_attention(self):
        plugin = load_plugin()
        plugin._active_cli = types.SimpleNamespace(session_id='parent', agent=None)
        with patch.object(plugin, '_write_terminal_title') as write:
            plugin._on_pre_tool_call(tool_name='clarify', task_id='other', tool_call_id='x')
            plugin._on_pre_tool_call(tool_name='terminal', task_id='parent', tool_call_id='x')
            plugin._on_pre_approval_request(surface='smart', session_id='parent', tool_call_id='x')
            plugin._on_pre_approval_request(surface='gateway', session_id='parent', tool_call_id='x')
            plugin._on_pre_approval_request(surface='cli', session_id='other', tool_call_id='x')
            write.assert_not_called()
            self.assertTrue(plugin._render_title().startswith(plugin._SUCCESS))
            plugin._on_pre_tool_call(tool_name='clarify', task_id='parent', tool_call_id='x')
            plugin._on_post_tool_call(tool_name='clarify', task_id='parent', tool_call_id='x', status='error')
            self.assertTrue(plugin._render_title().startswith(plugin._SUCCESS))
