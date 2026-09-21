"""A completed parent response must not hide its running terminal commands."""
import types
import unittest
from unittest.mock import patch
from test_plugin import load_plugin


class BackgroundProcessTitleTests(unittest.TestCase):
    def test_background_lifecycle_is_owned_and_preserves_other_work(self):
        plugin = load_plugin()
        plugin._active_cli = types.SimpleNamespace(session_id='parent', agent=None)
        rows = []
        registry = types.SimpleNamespace(list_sessions=lambda: list(rows))
        titles = []
        with patch.object(plugin, '_write_terminal_title', side_effect=lambda: titles.append(plugin._render_title())):
            plugin._refresh_background_processes(registry)
            rows.extend([
                dict(session_id='a', owner_task_id='parent', status='running'),
                dict(session_id='b', owner_task_id='parent', status='running'),
                dict(session_id='other', owner_task_id='other', status='running'),
            ])
            plugin._refresh_background_processes(registry)
            plugin._set_lifecycle(plugin._SUCCESS)
            self.assertTrue(titles[-1].startswith(plugin._WORKING))
            plugin._owned_children.add('child')
            self.assertTrue(plugin._render_title().startswith('👥'))
            plugin._owned_children.clear()
            rows[0].update(status='exited', exit_code=0)
            plugin._refresh_background_processes(registry)
            self.assertTrue(titles[-1].startswith(plugin._WORKING))
            rows[1].update(status='exited', exit_code=0)
            plugin._refresh_background_processes(registry)
            self.assertTrue(titles[-1].startswith(plugin._SUCCESS))
            rows[0].update(status='running')
            plugin._refresh_background_processes(registry)
            rows[0].update(status='exited', exit_code=7)
            plugin._refresh_background_processes(registry)
            self.assertTrue(titles[-1].startswith(plugin._FAILURE))

    def test_handoff_and_closed_cli_do_not_leak_titles(self):
        plugin = load_plugin()
        plugin._active_cli = types.SimpleNamespace(session_id='parent', agent=None)
        rows = [dict(session_id='a', owner_task_id='child', status='running')]
        registry = types.SimpleNamespace(list_sessions=lambda: list(rows))
        with patch.object(plugin, '_write_terminal_title') as write:
            plugin._refresh_background_processes(registry)
            self.assertTrue(plugin._render_title().startswith(plugin._SUCCESS))
            rows[0]['owner_task_id'] = 'parent'
            plugin._refresh_background_processes(registry)
            self.assertTrue(plugin._render_title().startswith(plugin._WORKING))
            plugin._active_cli = None
            write.reset_mock()
            plugin._refresh_background_processes(registry)
            write.assert_not_called()
