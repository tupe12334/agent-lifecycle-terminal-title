"""Run under a PTY with HERMES_HOME isolated; uses real registry-owned subprocesses.

Usage: HERMES_HOME=<scratch-home> python tests/integration_background_processes.py <hermes-source>
The CLI chat body is replaced to avoid paid LLM calls; process creation, ownership,
exit observation, context-bound monitoring and terminal output are real.
"""
import importlib.util
import os
from pathlib import Path
import shlex
import socket
import sys
import time
import types

sys.path.insert(0, sys.argv[1])
assert os.environ.get('HERMES_HOME'), 'Run with an isolated HERMES_HOME'
from tools.process_registry import process_registry as registry

class CLI:
    session_id = 'background-title-integration'
    agent = None
    def chat(self):
        self.body()
        return 'Background tests are still running.'
    def process_command(self, command):
        return True
    def run(self):
        return 'closed'

sys.modules['cli'] = types.SimpleNamespace(HermesCLI=CLI)
spec = importlib.util.spec_from_file_location('background_title_integration', Path(__file__).resolve().parents[1] / '__init__.py')
plugin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plugin)
hooks = {}
plugin.register(types.SimpleNamespace(register_hook=lambda name, fn: hooks.__setitem__(name, fn)))
cli = CLI()
listener = socket.socket()
listener.bind(('127.0.0.1', 0))
listener.listen()
listener.settimeout(10)
port = listener.getsockname()[1]
created = []
connections = []

def spawn(owner, notify=False):
    script = f'import socket,sys; s=socket.create_connection(("127.0.0.1",{port})); sys.exit(int(s.recv(1)))'
    process = registry.spawn_local(f'{shlex.quote(sys.executable)} -c {shlex.quote(script)}', task_id=owner, owner_task_id=owner)
    process.notify_on_complete = notify
    conn, _ = listener.accept()
    created.append(process)
    connections.append(conn)
    return process, conn

def wait_for(predicate):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.025)
    raise AssertionError(f'Timed out; current title: {plugin._render_title()}')

def is_marker(marker):
    return plugin._render_title().startswith(marker + ' ')

try:
    jobs = []
    cli.body = lambda: jobs.extend([spawn(cli.session_id, True), spawn(cli.session_id), spawn('unrelated')])
    assert cli.chat() == 'Background tests are still running.'
    assert is_marker(plugin._WORKING), 'Parent completion hid running tests'
    monitor = plugin._process_monitor
    plugin._start_process_monitor()
    assert plugin._process_monitor is monitor, 'Duplicate monitor'
    jobs[0][1].sendall(b'0')
    wait_for(lambda: jobs[0][0].exited)
    assert is_marker(plugin._WORKING), 'First exit hid the second process'
    jobs[1][1].sendall(b'0')
    wait_for(lambda: is_marker(plugin._SUCCESS))
    assert not jobs[2][0].exited, 'Unrelated process must not block completion'
    notification = registry.completion_queue.get_nowait()
    assert notification['session_id'] == jobs[0][0].id, 'Title monitor consumed completion'
    print('PASS: parent return, multiple processes, silent process, unrelated owner, notification retained', flush=True)

    failure = []
    cli.body = lambda: failure.append(spawn(cli.session_id))
    cli.chat()
    failure[0][1].sendall(b'7')
    wait_for(lambda: is_marker(plugin._FAILURE))
    assert failure[0][0].exit_code == 7
    print('PASS: nonzero command exit shows failure without another LLM turn', flush=True)

    handed = []
    cli.body = lambda: handed.append(spawn('child'))
    cli.chat()
    hooks['subagent_start'](parent_session_id=cli.session_id, child_session_id='child')
    assert is_marker('👥')
    process, connection = handed[0]
    assert registry.transfer_ownership(process.id, from_owner='child', to_owner=cli.session_id, to_task_id=cli.session_id, to_session_key='') is process
    hooks['subagent_stop'](parent_session_id=cli.session_id, child_session_id='child')
    assert is_marker(plugin._WORKING), 'Handoff briefly turned green'
    connection.sendall(b'0')
    wait_for(lambda: is_marker(plugin._SUCCESS))
    print('PASS: child handoff stays working through subagent completion', flush=True)
    cli.run()
    monitor.join(2)
    assert not monitor.is_alive()
    print('PASS: monitor stops on CLI close', flush=True)
finally:
    plugin._process_monitor_stop.set()
    for conn in connections:
        try:
            conn.sendall(b'0')
        except OSError:
            pass
        conn.close()
    for process in created:
        if not process._completion_event.wait(5):
            registry.kill(process.id)
    listener.close()
