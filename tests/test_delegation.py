"""Foreground delegation must retain ownership and survive between-turn completion."""
import sys
import types
import unittest
from unittest.mock import patch
from test_plugin import load_plugin


class DelegationTests(unittest.TestCase):
    def exercise(self, goal=False):
        plugin = load_plugin()
        titles = []
        hooks = {}
        class CLI:
            session_id = 'parent'
            agent = types.SimpleNamespace(session_id='parent')
            def _get_goal_manager(self):
                return types.SimpleNamespace(is_active=lambda: goal, state=None)
            def chat(self):
                hooks['subagent_start'](parent_session_id='unrelated', child_session_id='foreign')
                self_test.assertNotIn('👥', plugin._render_title())
                hooks['subagent_start'](parent_session_id='parent', child_session_id='a')
                hooks['subagent_start'](parent_session_id='parent', child_session_id='a')
                hooks['subagent_start'](parent_session_id='parent', child_session_id='b')
                self_test.assertTrue(plugin._render_title().startswith('👥 '))
                plugin._set_base_title('Renamed')
                hooks['subagent_stop'](parent_session_id='parent', child_session_id='a')
                self_test.assertTrue(plugin._render_title().startswith('👥 '))
                hooks['subagent_stop'](parent_session_id='parent', child_session_id='b')
                self_test.assertTrue(plugin._render_title().startswith('🎯 ' if goal else '⌛️ '))
                hooks['subagent_start'](parent_session_id='parent', child_session_id='b')
                return 'answer'
            def process_command(self, command):
                return True
            def run(self):
                return 'closed'
        self_test = self
        ctx = types.SimpleNamespace(register_hook=lambda name, fn: hooks.__setitem__(name, fn))
        with patch.dict(sys.modules, {'cli': types.SimpleNamespace(HermesCLI=CLI)}), patch.object(plugin, '_write_terminal_title', side_effect=lambda title=None: titles.append(title or plugin._render_title())):
            plugin.register(ctx)
            cli = CLI()
            self.assertEqual(cli.chat(), 'answer')
            self.assertEqual(titles[-1], '👥 Renamed')
            hooks['subagent_stop'](parent_session_id='unrelated', child_session_id='b')
            self.assertEqual(titles[-1], '👥 Renamed')
            hooks['subagent_stop'](parent_session_id='parent', child_session_id='b')
            self.assertEqual(titles[-1], '✅ Renamed')
            cli.run()
            hooks['subagent_start'](parent_session_id='parent', child_session_id='late')
            self.assertEqual(titles[-1], 'parent')
    def test_multiple_children_and_background_completion(self):
        self.exercise()
    def test_goal_delegation(self):
        self.exercise(goal=True)
