"""Exercise the preview allow-list, the local queue, the refresh action's guards and the served snapshot bytes."""
from functools import partial
import http.client
from http.server import ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Event, Thread
import unittest
from unittest import mock

import serve
from serve import Preview, ROOT
import repo_paths as rp

GROUPS = {'schema': 1, 'authority': 'local/non-authoritative', 'state': 'ok', 'groups': [{'label': 'Demo', 'paths': ['docs/demo.md']}]}
QUEUE = {'schema': 1, 'authority': 'local/non-authoritative', 'read_at': '2026-10-10T12:00:00Z', 'state': 'ok',
         'queue': [], 'unqueued': []}
ORIGIN = 'http://127.0.0.1:{}'


class Fixture(unittest.TestCase):
    overrides = {}

    @classmethod
    def setUpClass(cls):
        handler = type('Handler', (Preview,), {'local_snapshot': staticmethod(lambda: None),
                                               'queue_source': staticmethod(lambda: QUEUE),
                                               'groups_source': staticmethod(lambda: GROUPS),
                                               'refresh_command': [sys.executable, '-c', 'print("refreshed ok")'],
                                               'refresh_lock': type(Preview.refresh_lock)(), **cls.overrides})
        cls.handler = handler
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), partial(handler, directory=str(ROOT)))
        cls.thread = Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.port = cls.server.server_port

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def request(self, method, path, headers=None, body=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=30)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            return response.status, response.read(), response
        finally:
            connection.close()

    def post(self, path='/refresh', **headers):
        headers = {'Origin': ORIGIN.format(self.port), **headers}
        return self.request('POST', path, headers)


class PreviewTest(Fixture):
    def test_both_index_routes_render_inert_utf8_project_text_without_datasets(self):
        name = 'Projeto café & <img src=x onerror=alert(1)> "demo"'
        with mock.patch.object(serve, 'project_identity', return_value=(name, 'Primary checkout folder name')):
            for path in ('/ui/', '/ui/index.html'):
                with self.subTest(path=path):
                    status, body, response = self.request('GET', path)
                    self.assertEqual(status, 200)
                    self.assertEqual(response.getheader('Content-Type'), 'text/html; charset=utf-8')
                    self.assertIn('Projeto café &amp; &lt;img src=x onerror=alert(1)&gt; &quot;demo&quot;', body.decode('utf-8'))
                    self.assertNotIn(b'<img src=x', body)
                    self.assertNotIn(b'replace with your project name', body)
                    self.assertEqual(response.getheader('Cache-Control'), 'no-store')
                    head_status, head_body, head = self.request('HEAD', path)
                    self.assertEqual((head_status, head_body), (200, b''))
                    self.assertEqual(head.getheader('Content-Length'), str(len(body)))

    def test_failed_discovery_is_visible_and_its_error_is_escaped(self):
        with mock.patch.object(serve.local_jobs, 'roots', side_effect=ValueError('missing "primary" <checkout>')):
            status, body, _ = self.request('GET', '/ui/')
        self.assertEqual(status, 200)
        self.assertIn(b'>Project unavailable</p>', body)
        self.assertIn(b'Primary checkout discovery failed: missing &quot;primary&quot; &lt;checkout&gt;', body)

    def test_without_a_local_copy_there_are_no_datasets_and_no_committed_fallback(self):
        for kind in ('decisions', 'commits', 'devlog', 'rationale', 'documents'):
            for method in ('GET', 'HEAD'):
                with self.subTest(kind=kind, method=method):
                    status, _, response = self.request(method, f'/data/{kind}.json')
                    self.assertEqual(status, 404)
                    self.assertIsNone(response.getheader('X-MC-Data-Source'))

    def test_the_address_without_the_final_slash_goes_to_the_terminal_like_the_root(self):
        for path in ('/', '/ui'):
            with self.subTest(path=path):
                status, _, response = self.request('GET', path)
                self.assertEqual(status, 302)
                self.assertEqual(response.getheader('Location'), '/ui/')

    def test_module_mime(self):
        status, _, response = self.request('GET', '/ui/app.mjs')
        self.assertEqual(response.getheader('Content-Type').split(';')[0], 'text/javascript')

    def test_source_scratch_and_traversal_denied(self):
        for path in ('/README.md', '/.git/HEAD', '/data/state.json', '/ui/../README.md',
                     '/ui/%2e%2e/README.md', '/ui/test_core.mjs', '/ui/serve.py', '/queue.json/', '/queue',
                     '/data/../ui/serve.py', '/data/decisions.json/'):
            for method in ('GET', 'HEAD'):
                with self.subTest(path=path, method=method):
                    self.assertEqual(self.request(method, path)[0], 404)

    def test_only_the_refresh_post_exists(self):
        for method, path in (('POST', '/data/decisions.json'), ('POST', '/queue.json'), ('POST', '/refresh/'),
                             ('POST', '/refresh?x=1'), ('PUT', '/data/decisions.json'), ('PUT', '/refresh'),
                             ('DELETE', '/refresh'), ('PATCH', '/queue.json')):
            with self.subTest(method=method, path=path):
                self.assertEqual(self.request(method, path, {'Content-Length': '0'})[0], 501)

    def test_queue_is_get_only_and_never_cached(self):
        status, body, response = self.request('GET', '/queue.json')
        self.assertEqual((status, json.loads(body)), (200, QUEUE))
        self.assertEqual(response.getheader('Cache-Control'), 'no-store')
        self.assertEqual(response.getheader('Content-Type'), 'application/json')
        self.assertIn("connect-src 'self'", response.getheader('Content-Security-Policy'))
        self.assertEqual(self.request('HEAD', '/queue.json')[0], 405)
        self.assertEqual(self.request('HEAD', '/refresh')[0], 405)
        self.assertEqual(self.request('GET', '/refresh')[0], 404)

    def test_groups_are_get_only_and_never_cached(self):
        status, body, response = self.request('GET', '/groups.json')
        self.assertEqual((status, json.loads(body)), (200, GROUPS))
        self.assertEqual(response.getheader('Cache-Control'), 'no-store')
        self.assertEqual(self.request('HEAD', '/groups.json')[0], 405)
        self.assertEqual(self.request('POST', '/groups.json', {'Content-Length': '0'})[0], 501)
        self.assertEqual(self.request('GET', '/groups.json/')[0], 404)

    def test_refresh_requires_this_pages_origin_host_and_fetch_site(self):
        for headers in ({'Origin': 'http://evil.example'}, {'Origin': 'http://localhost:{}'},
                        {'Origin': 'null'}, {'Sec-Fetch-Site': 'cross-site'}, {'Sec-Fetch-Site': 'same-site'},
                        {'Host': 'evil.example'}, {'Host': 'localhost:{}'}):
            headers = {k: v.format(self.port) for k, v in headers.items()}
            with self.subTest(headers=headers):
                status, body, _ = self.post(**headers)
                self.assertEqual(status, 403)
                self.assertEqual(json.loads(body)['state'], 'error')
        # No Origin header at all is refused as well.
        self.assertEqual(self.request('POST', '/refresh', {'Content-Length': '0'})[0], 403)

    def test_refresh_takes_no_body_or_parameters(self):
        self.assertEqual(self.request('POST', '/refresh', {'Origin': ORIGIN.format(self.port)}, body=b'x=1')[0], 400)
        self.assertEqual(self.post('/refresh?x=1')[0], 501)

    def test_refresh_runs_the_fixed_command_and_reports_plainly(self):
        status, body, _ = self.post()
        result = json.loads(body)
        self.assertEqual((status, result['state'], result['message']), (200, 'ok', 'refreshed ok'))
        self.assertIsInstance(result['seconds'], float)
        self.assertTrue(result['finished_at'].endswith('Z'))

    def test_the_production_command_is_fixed_and_writes_only_the_local_refresh(self):
        self.assertEqual(Preview.refresh_command[2:], ['governance/extensions/mission_control/data_launcher.py', 'refresh-local'])
        self.assertIs(Preview.refresh_command, serve.REFRESH_COMMAND)
        self.assertNotIn('mission-control-data-sync.sh', ' '.join(Preview.refresh_command))


class RefreshFailureTest(Fixture):
    overrides = {'refresh_command': [sys.executable, '-c', 'import sys; print("FAIL: generator broke", file=sys.stderr); sys.exit(1)']}

    def test_failure_is_a_plain_error_message(self):
        status, body, _ = self.post()
        self.assertEqual((status, json.loads(body)['state'], json.loads(body)['message']), (500, 'error', 'generator broke'))
        # The lock is released, so a second press is not stuck busy.
        self.assertEqual(self.post()[0], 500)


class RefreshBusyTest(Fixture):
    def test_one_run_at_a_time(self):
        self.assertTrue(self.handler.refresh_lock.acquire(blocking=False))
        try:
            status, body, _ = self.post()
            self.assertEqual((status, json.loads(body)['state']), (409, 'busy'))
        finally:
            self.handler.refresh_lock.release()
        self.assertEqual(self.post()[0], 200)


class LocalSnapshotTest(Fixture):
    temp = None

    @classmethod
    def setUpClass(cls):
        cls.temp = TemporaryDirectory()
        folder = Path(cls.temp.name)
        for kind in ('decisions', 'commits', 'devlog', 'rationale', 'documents'):
            (folder / f'{kind}.json').write_bytes(f'{{"local":"{kind}"}}'.encode())
        cls.overrides = {'local_snapshot': staticmethod(lambda: {
            'folder': folder, 'revision': 'a' * 40, 'generated_at': '2026-10-10T12:00:00Z', 'generator': 'b' * 12}),
            'head': staticmethod(lambda: 'a' * 40)}
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls.temp.cleanup()

    def test_a_local_refresh_is_served_first_and_labelled(self):
        status, body, response = self.request('GET', '/data/rationale.json')
        self.assertEqual((status, body), (200, b'{"local":"rationale"}'))
        self.assertEqual(response.getheader('X-MC-Data-Source'),
                         'local; revision=' + 'a' * 40 + '; generated=2026-10-10T12:00:00Z; generator=' + 'b' * 12 + '; head=current')
        self.assertEqual(self.request('HEAD', '/data/rationale.json')[0], 200)
        self.assertEqual(self.request('GET', '/data/state.json')[0], 404)


def completed(stdout='', stderr='', code=0):
    return subprocess.CompletedProcess([], code, stdout, stderr)


JOB = {'handle': 'a_job', 'status': 'WIP', 'activity': 'ACTIVE', 'worker': 'Secret worker', 'step': 'Private step',
       'description': 'Does a thing.', 'created_at': '2026-10-10T01:00:00Z', 'work_kind': 'fix', 'waiting': None,
       'record': 'C:/somewhere/a_job.json', 'dependencies': [], 'unsatisfied_dependencies': [],
       'dependency_ready': True, 'missing_dependencies': [], 'awaiting_lead': None, 'pending_count': 2,
       'pending': [{'kind': 'finding', 'text': 'Private pending text', 'source': 'x'}]}


EXECUTION = {'schema': '1.0', 'level': 'CLEAR', 'line': 'CLEAR: no ref window is open.', 'read_at': '2026-10-13T12:00:00Z',
             'windows': [], 'database_state': 'missing', 'declared_without_evidence': 0, 'poll_seconds': 0}


class QueuePayloadTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(serve, 'execution_payload', return_value=EXECUTION)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_payload_carries_the_execution_field_beside_the_queue(self):
        with mock.patch.object(serve, 'helper', return_value=completed(json.dumps({'queue': [], 'unqueued': []}))):
            payload = serve.queue_payload()
        self.assertEqual(payload['execution'], EXECUTION)
        self.assertEqual(payload['state'], 'ok')
        with mock.patch.object(serve, 'helper', return_value=completed(stderr='FAIL: broke', code=1)):
            self.assertEqual(serve.queue_payload()['execution'], EXECUTION)

    def test_only_whitelisted_fields_leave_the_server(self):
        queue = {'queue': [dict(JOB, kind='job', priority=1),
                           {'handle': 'an_epic', 'kind': 'epic', 'priority': None, 'jobs': [dict(JOB, handle='member_job')]}],
                 'unqueued': [dict(JOB, handle='loose_job', status=None)]}
        with mock.patch.object(serve, 'helper', return_value=completed(json.dumps(queue))) as helper:
            payload = serve.queue_payload()
        helper.assert_called_once_with('queue')
        self.assertEqual(payload['state'], 'ok')
        self.assertEqual(payload['authority'], 'local/non-authoritative')
        wire = json.dumps(payload)
        for secret in ('Secret worker', 'Private step', 'Private pending text', 'C:/somewhere', '"record"', '"pending"', '"worker"', '"step"'):
            self.assertNotIn(secret, wire)
        job, epic = payload['queue']
        self.assertEqual((job['handle'], job['priority'], job['pending_count'], job['has_record']), ('a_job', 1, 2, True))
        self.assertEqual(epic['jobs'][0]['handle'], 'member_job')
        self.assertEqual(epic['closure_policy'], 'automatic')
        self.assertFalse(payload['unqueued'][0]['has_record'])

    def test_empty_explicit_epic_and_its_policy_leave_the_server(self):
        queue = {'queue': [{'handle': 'an_epic', 'kind': 'epic', 'priority': 2,
                            'closure_policy': 'explicit', 'jobs': []}], 'unqueued': []}
        with mock.patch.object(serve, 'helper', return_value=completed(json.dumps(queue))):
            payload = serve.queue_payload()
        self.assertEqual(payload['state'], 'ok')
        self.assertEqual(payload['queue'], queue['queue'])

    def test_the_page_gets_the_helpers_order_unchanged(self):
        # The order of execution is decided once, in the helper, so the page and the report cannot disagree.
        queue = {'queue': [dict(JOB, handle='zulu_job', kind='job', priority=None),
                           {'handle': 'an_epic', 'kind': 'epic', 'priority': 3,
                            'jobs': [dict(JOB, handle='second_member'), dict(JOB, handle='first_member')]},
                           dict(JOB, handle='alpha_job', kind='job', priority=1)],
                 'unqueued': [dict(JOB, handle='zz_loose'), dict(JOB, handle='aa_loose')]}
        with mock.patch.object(serve, 'helper', return_value=completed(json.dumps(queue))):
            payload = serve.queue_payload()
        self.assertEqual([entry['handle'] for entry in payload['queue']], ['zulu_job', 'an_epic', 'alpha_job'])
        self.assertEqual([job['handle'] for job in payload['queue'][1]['jobs']], ['second_member', 'first_member'])
        self.assertEqual([job['handle'] for job in payload['unqueued']], ['zz_loose', 'aa_loose'])

    def test_absent_records_are_reported_never_invented(self):
        with TemporaryDirectory() as directory:
            responses = [completed(stderr='FAIL: Missing or indirect record: x', code=1),
                         completed(json.dumps({'local': 'x', 'primary': directory}))]
            with mock.patch.object(serve, 'helper', side_effect=responses):
                payload = serve.queue_payload()
        self.assertEqual((payload['state'], payload['queue'], payload['unqueued']), ('absent', [], []))
        self.assertIn('No local coordination records', payload['message'])

    def test_a_real_failure_is_an_error_with_a_plain_message(self):
        with TemporaryDirectory() as directory:
            (Path(directory) / 'coordinator.json').write_text('{}')
            responses = [completed(stderr='scrubbed inherited GIT_* variables: GIT_EDITOR\nFAIL: Duplicate JSON key: x', code=1),
                         completed(json.dumps({'local': 'x', 'primary': directory}))]
            with mock.patch.object(serve, 'helper', side_effect=responses):
                payload = serve.queue_payload()
        self.assertEqual((payload['state'], payload['message'], payload['queue']), ('error', 'Duplicate JSON key: x', []))

    def test_unreadable_helper_output_is_an_error(self):
        for stdout in ('not json', '{"queue": [{"kind": "job"}], "unqueued": []}'):
            with self.subTest(stdout=stdout), mock.patch.object(serve, 'helper', return_value=completed(stdout)):
                self.assertEqual(serve.queue_payload()['state'], 'error')
        with mock.patch.object(serve, 'helper', side_effect=OSError('no launcher')):
            self.assertEqual(serve.queue_payload()['state'], 'error')

    def test_the_helper_is_the_validated_queue_command_through_its_thin_launcher(self):
        with mock.patch.object(serve.subprocess, 'run', return_value=completed('{}')) as run:
            serve.helper('queue')
        argv = run.call_args.args[0]
        self.assertEqual(argv[2:5], ['governance/tools/local_jobs.py', '--repo', str(serve.REPO)])
        self.assertEqual(argv[-1], 'queue')
        self.assertEqual(run.call_args.kwargs['cwd'], serve.REPO)


STATUS = {'schema': '1.0', 'level': 'FROZEN', 'line': 'REFS FROZEN: m1, since 2026-10-13T10:00:00Z',
          'read_at': '2026-10-13T10:01:00Z', 'database': {'state': 'ok', 'message': 'private'},
          'declared_without_evidence': [{'owner': 'x'}], 'derived': {'worktrees_inspected': 3, 'lifecycle_lock': False},
          'windows': [{'owner': 'm1', 'scope': 'prepare', 'state': 'preparing', 'since': '2026-10-13T10:00:00Z',
                       'job_handle': 'worker_claims', 'worktree': 'C:/secret/place', 'evidence': ['packet state preparing'],
                       'command': {'pid': 4242, 'running': True}, 'declared': True, 'flags': ['age'], 'age_flag': True}]}


class GroupsPayloadTest(unittest.TestCase):
    def test_the_payload_carries_only_labels_and_paths_in_declared_order(self):
        found = [{'label': 'One', 'paths': ['a.md'], 'source': 'the project'}, {'label': 'Two', 'paths': ['b.md'], 'source': 'the project'}]
        with mock.patch.object(serve.declared_inputs, 'load', return_value={}), mock.patch.object(serve.declared_inputs, 'groups', return_value=found):
            payload = serve.groups_payload()
        self.assertEqual((payload['state'], payload['groups']), ('ok', [{'label': 'One', 'paths': ['a.md']}, {'label': 'Two', 'paths': ['b.md']}]))

    def test_a_refused_declaration_is_one_plain_error_and_no_groups(self):
        with mock.patch.object(serve.declared_inputs, 'load', side_effect=ValueError('Mission Control groups: the label "X" is declared twice')):
            payload = serve.groups_payload()
        self.assertEqual((payload['state'], payload['groups']), ('error', []))
        self.assertIn('label "X"', payload['message'])

    def test_this_repositorys_declarations_are_valid(self):
        payload = serve.groups_payload()
        self.assertEqual(payload['state'], 'ok', payload.get('message'))
        self.assertEqual(payload['groups'][0]['label'], 'Mission Control')


class ExecutionPayloadTest(unittest.TestCase):
    def tearDown(self):
        serve.light_poll_seconds = 0

    def test_only_allow_listed_fields_leave_the_server(self):
        with mock.patch.object(serve.subprocess, 'run', return_value=completed(json.dumps(STATUS))) as run:
            payload = serve.execution_payload()
        wire = json.dumps(payload)
        for secret in ('C:/secret/place', 'packet state preparing', '4242', 'private', '"worktree"', '"evidence"', '"derived"'):
            self.assertNotIn(secret, wire)
        self.assertEqual((payload['level'], payload['schema'], payload['database_state'], payload['declared_without_evidence']),
                         ('FROZEN', '1.0', 'ok', 1))
        self.assertEqual(payload['windows'], [{'owner': 'm1', 'scope': 'prepare', 'state': 'preparing',
                                               'since': '2026-10-13T10:00:00Z', 'job_handle': 'worker_claims',
                                               'flags': ['age'], 'age_flag': True, 'command_running': True}])
        self.assertEqual(run.call_args.args[0], serve.EXECUTION)
        self.assertEqual(run.call_args.kwargs['cwd'], serve.REPO)

    def test_the_claims_strip_fields_are_allow_listed_and_an_older_payload_has_none(self):
        status = dict(STATUS, schema='1.1', stale_claims=2,
                      claims=[{'owner': 'm1', 'job_handle': 'worker_claims', 'level': 'OVERLAP', 'subclasses': ['checkpoint'],
                               'files': 3, 'since': '2026-10-13T10:00:00Z', 'flags': ['age'], 'worktree': 'C:/secret/place', 'id': 7}],
                      overlaps=[{'owners': ['m1', 'm2'], 'kind': 'same_file', 'text': 'same file(s): a.py', 'files': ['a.py'], 'subclasses': ['x']}])
        with mock.patch.object(serve.subprocess, 'run', return_value=completed(json.dumps(status))):
            payload = serve.execution_payload()
        self.assertEqual(payload['claims'], [{'owner': 'm1', 'job_handle': 'worker_claims', 'level': 'OVERLAP',
                                              'subclasses': ['checkpoint'], 'files': 3, 'since': '2026-10-13T10:00:00Z', 'flags': ['age']}])
        self.assertEqual(payload['overlaps'], [{'owners': ['m1', 'm2'], 'kind': 'same_file', 'text': 'same file(s): a.py'}])
        self.assertEqual((payload['schema'], payload['stale_claims']), ('1.1', 2))
        self.assertNotIn('C:/secret/place', json.dumps(payload))
        with mock.patch.object(serve.subprocess, 'run', return_value=completed(json.dumps(STATUS))):
            old = serve.execution_payload()
        self.assertEqual((old['claims'], old['overlaps'], old['stale_claims']), ([], [], 0))
        with mock.patch.object(serve.subprocess, 'run', return_value=completed(json.dumps(dict(STATUS, stale_claims='many')))):
            self.assertEqual(serve.execution_payload()['stale_claims'], 0)

    def test_the_command_is_fixed_read_only_and_goes_through_its_thin_launcher(self):
        self.assertEqual(serve.EXECUTION[2:], ['governance/tools/worktree.py', '--main',
                                               str(serve.REPO), 'execution-status', '--json-only'])

    def test_an_unreadable_status_is_unknown_never_clear(self):
        for outcome in ({'return_value': completed('not json')}, {'return_value': completed('{"level": "CLEAR"}')},
                        {'return_value': completed(json.dumps(dict(STATUS, level='GREEN')))},
                        {'side_effect': OSError('no launcher')}, {'return_value': completed('', 'FAIL: x', 1)}):
            with self.subTest(outcome=outcome), mock.patch.object(serve.subprocess, 'run', **outcome):
                payload = serve.execution_payload()
            self.assertEqual((payload['level'], payload['windows']), ('UNKNOWN', []))
            self.assertTrue(payload['line'].startswith('STATUS UNKNOWN'))

    def test_the_poll_interval_is_off_by_default_clamped_and_forgiving(self):
        self.assertEqual([serve.poll_seconds(v) for v in (0, -5, 'x', None, 1, 9, 10, 30, '45')], [0, 0, 0, 0, 10, 10, 10, 30, 45])
        with mock.patch.object(serve.subprocess, 'run', return_value=completed(json.dumps(STATUS))):
            self.assertEqual(serve.execution_payload()['poll_seconds'], 0)
            serve.light_poll_seconds = 3
            self.assertEqual(serve.execution_payload()['poll_seconds'], 10)
            serve.light_poll_seconds = 30
            self.assertEqual(serve.execution_payload()['poll_seconds'], 30)

    def test_the_server_option_exists_and_defaults_to_no_timer(self):
        source = (Path(serve.__file__)).read_text(encoding='utf-8')
        self.assertIn("'--light-poll-seconds', type=int, default=0", source)


class ClosedConnectionTest(unittest.TestCase):
    """A browser that closes its connection before the response is written must not print a traceback."""
    started, release = None, None

    def serve_with(self, source, server_class=serve.PreviewServer):
        handler = type('Handler', (Preview,), {'local_snapshot': staticmethod(lambda: None), 'queue_source': staticmethod(source)})
        server = server_class(('127.0.0.1', 0), partial(handler, directory=str(ROOT)))
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close()))
        return server.server_port

    def abandon_request(self, port, started, release):
        """Send a request, wait until the handler is busy, reset the connection and let the handler write."""
        connection = socket.create_connection(('127.0.0.1', port))
        connection.sendall(b'GET /queue.json HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n')
        self.assertTrue(started.wait(10), 'the handler never started')
        connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack('ii', 1, 0))  # close with a reset
        connection.close()
        release.set()

    def quiet_run(self, source, server_class=serve.PreviewServer):
        started, release = Event(), Event()
        errors = io.StringIO()

        def slow():
            started.set()
            release.wait(10)
            return source()
        with mock.patch.object(sys, 'stderr', errors):
            port = self.serve_with(slow, server_class)
            self.abandon_request(port, started, release)
            Event().wait(1.0)  # the handler thread finishes and any traceback is printed
        return port, errors.getvalue()

    def fetch(self, port):
        connection = http.client.HTTPConnection('127.0.0.1', port, timeout=10)
        connection.request('GET', '/queue.json')
        response = connection.getresponse()
        return response.status, json.loads(response.read())

    def test_a_connection_closed_before_the_response_is_written_prints_nothing_and_the_server_keeps_serving(self):
        big = {'schema': 1, 'padding': 'x' * 3_000_000}
        port, printed = self.quiet_run(lambda: big)
        for noise in ('Exception occurred', 'Traceback', 'Error'):
            self.assertNotIn(noise, printed)  # the access log line is ordinary; only an error report would be noise
        self.assertEqual(self.fetch(port), (200, big))

    def test_without_the_override_the_same_closed_connection_does_print_a_traceback(self):
        # The control: this proves the test above can fail, so it cannot pass by accident.
        _, printed = self.quiet_run(lambda: {'padding': 'x' * 3_000_000}, ThreadingHTTPServer)
        self.assertRegex(printed, r'Exception occurred during processing of request')
        self.assertRegex(printed, r'ConnectionAbortedError|ConnectionResetError|BrokenPipeError')

    def test_any_other_exception_still_prints_and_the_server_keeps_serving(self):
        errors = io.StringIO()
        calls = []

        def source():
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError('a real bug')
            return {'ok': True}
        with mock.patch.object(sys, 'stderr', errors):
            port = self.serve_with(source)
            connection = http.client.HTTPConnection('127.0.0.1', port, timeout=10)
            with self.assertRaises((http.client.RemoteDisconnected, ConnectionError)):
                connection.request('GET', '/queue.json')
                connection.getresponse()
            Event().wait(0.5)
            self.assertEqual(self.fetch(port), (200, {'ok': True}))
        self.assertIn('RuntimeError: a real bug', errors.getvalue())

    def test_the_override_names_exactly_three_exceptions(self):
        self.assertEqual(set(serve.CLOSED_BY_CLIENT), {ConnectionAbortedError, ConnectionResetError, BrokenPipeError})
        self.assertTrue(issubclass(serve.PreviewServer, ThreadingHTTPServer))
        self.assertIn('PreviewServer((', Path(serve.__file__).read_text(encoding='utf-8'))


class PortSettingTest(unittest.TestCase):
    """The port: an explicit --port, then the project setting, then 8765; an invalid setting means 8765 and says why."""

    def root(self, text=None):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        if text is not None:
            path = Path(folder.name) / serve.PORT_SETTING
            path.parent.mkdir(parents=True)
            path.write_text(text, encoding='utf-8')
        return folder.name

    def test_a_missing_file_falls_back_to_8765(self):
        self.assertEqual(serve.port_setting(self.root()), (8765, None))

    def test_the_setting_is_read(self):
        self.assertEqual(serve.port_setting(self.root('{"version": 1, "port": 9123}')), (9123, None))
        self.assertEqual(serve.port_setting(self.root('{"port": 9124}')), (9124, None))
        self.assertEqual(serve.port_setting(self.root('{"version": 1}')), (8765, None))

    def test_the_optional_tagline_is_trimmed_and_does_not_change_the_port(self):
        for tagline in ('Governed work for AI agents', '', '   ', '  café & <demo>  '):
            with self.subTest(tagline=tagline):
                root = self.root(json.dumps({'port': 9123, 'tagline': tagline}))
                self.assertEqual(serve.project_settings(root), ({'port': 9123, 'tagline': tagline.strip()}, None))
                self.assertEqual(serve.port_setting(root), (9123, None))
        for text in (None, '{}', '{"port": 9123}'):
            self.assertEqual(serve.project_settings(self.root(text))[0]['tagline'], '')

    def test_invalid_taglines_use_the_existing_invalid_file_fallback(self):
        for tagline in (None, 3, True, [], {}, 'two\nlines', 'two\rlines'):
            with self.subTest(tagline=tagline):
                settings, problem = serve.project_settings(self.root(json.dumps({'port': 9123, 'tagline': tagline})))
                self.assertEqual(settings, {'port': 8765, 'tagline': ''})
                self.assertIn('"tagline" must be a single-line string', problem)

    def test_an_invalid_setting_falls_back_to_8765_and_says_why(self):
        for text in ('not json', '[8000]', '{"port": "9000"}', '{"port": 0}', '{"port": 65536}', '{"port": true}',
                     '{"port": 9000.0}', '{"version": 2, "port": 9000}', '{"version": true, "port": 9000}',
                     '{"port": 9000, "host": "0.0.0.0"}'):
            with self.subTest(text=text):
                port, problem = serve.port_setting(self.root(text))
                self.assertEqual(port, 8765)
                self.assertIn(serve.PORT_SETTING, problem)

    def test_an_explicit_port_wins_over_the_setting(self):
        root = self.root('{"version": 1, "port": 9123}')
        self.assertEqual(serve.chosen_port(9999, root), (9999, None))
        self.assertEqual(serve.chosen_port(None, root), (9123, None))

    def test_the_default_is_the_one_governance_documents(self):
        """governance/PROJECT-GOVERNANCE.md states the default of every setting in one table; the conformance test of the core
        checks the core's rows, and this checks the row of the port, which the extension owns."""
        text = (rp.ROOT / 'governance' / 'PROJECT-GOVERNANCE.md').read_text(encoding='utf-8')
        row = '| `config/' + Path(serve.PORT_SETTING).name + '` | `port` | `' + str(serve.DEFAULT_PORT) + '` |'
        self.assertIn(row, text.splitlines())
        self.assertIn('| `config/mission-control.json` | `tagline` | `""` |', text.splitlines())

    def test_the_shipped_setting_is_valid(self):
        self.assertTrue((rp.ROOT / serve.PORT_SETTING).is_file())
        self.assertIsNone(serve.port_setting(rp.ROOT)[1])


EXCLUSIVE = getattr(socket, 'SO_EXCLUSIVEADDRUSE', None)


class ExclusiveBindTest(unittest.TestCase):
    """A port another server listens on is refused, with one plain line and exit 1, and no other port is tried."""

    def hold(self, option):
        """A free loopback port with a listener bound with `option` (None for none) until the test ends."""
        holder = socket.socket()
        self.addCleanup(holder.close)
        if option is not None:
            holder.setsockopt(socket.SOL_SOCKET, option, 1)
        holder.bind(('127.0.0.1', 0))
        holder.listen()
        return holder.getsockname()[1]

    def test_a_port_another_listener_holds_is_refused_even_one_bound_for_reuse(self):
        for option in (EXCLUSIVE, socket.SO_REUSEADDR):
            with self.subTest(option=option):
                port = self.hold(option)
                with self.assertRaises(OSError):
                    serve.PreviewServer(('127.0.0.1', port), partial(Preview, directory=str(ROOT)))

    @unittest.skipUnless(EXCLUSIVE, 'only Windows lets a second socket bind a listening port with SO_REUSEADDR')
    def test_without_the_exclusive_bind_the_same_port_is_shared(self):
        # The control: a plain ThreadingHTTPServer binds the held port, which is the defect the test above pins.
        port = self.hold(socket.SO_REUSEADDR)
        ThreadingHTTPServer(('127.0.0.1', port), partial(Preview, directory=str(ROOT))).server_close()

    def test_the_server_refuses_a_taken_port_with_one_line_and_exit_1(self):
        port = self.hold(EXCLUSIVE)
        done = subprocess.run([sys.executable, '-B', str(Path(serve.__file__)), '--port', str(port)], cwd=rp.ROOT,
                              capture_output=True, text=True, encoding='utf-8', timeout=60)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        lines = [line for line in done.stderr.splitlines() if line.strip() and not line.startswith('scrubbed')]
        self.assertEqual(len(lines), 1, lines)
        self.assertIn(f'port {port} ', lines[0])
        self.assertIn(serve.PORT_SETTING, lines[0])
        self.assertIn('--port', lines[0])
        self.assertNotIn('http://', done.stdout)


class ProjectIdentityTest(Fixture):
    def test_primary_and_linked_checkouts_share_the_primary_folder_name(self):
        environment = {key: value for key, value in os.environ.items() if not key.upper().startswith('GIT_')}
        environment.update(GIT_AUTHOR_NAME='Test', GIT_AUTHOR_EMAIL='noreply@example.invalid',
                           GIT_COMMITTER_NAME='Test', GIT_COMMITTER_EMAIL='noreply@example.invalid')
        with TemporaryDirectory(prefix='mc-project-') as folder, mock.patch.dict(os.environ, environment, clear=True):
            primary = Path(folder).resolve() / 'Projeto café & demo'
            linked = Path(folder).resolve() / 'different mission'
            def git(*args):
                return subprocess.run(['git', *args], check=True, capture_output=True, env=environment)
            git('init', '-q', '-b', 'main', str(primary))
            git('-C', str(primary), 'commit', '-q', '--allow-empty', '-m', 'fixture')
            git('-C', str(primary), 'worktree', 'add', '-q', '--detach', str(linked))
            for checkout in (primary, linked):
                with self.subTest(checkout=checkout):
                    self.assertEqual(serve.project_identity(checkout),
                                     (primary.name, 'Primary checkout folder name'))
                    with mock.patch.object(serve, 'REPO', checkout):
                        status, body, _ = self.request('GET', '/ui/')
                    self.assertEqual(status, 200)
                    self.assertIn('>Projeto café &amp; demo</p>', body.decode('utf-8'))
                    self.assertNotIn(b'>different mission</p>', body)
            for checkout in (primary, linked):
                setting = checkout / serve.PORT_SETTING
                setting.parent.mkdir(parents=True)
                setting.write_text(json.dumps({'tagline': 'mission override'}), encoding='utf-8')
            setting = primary / serve.PORT_SETTING
            for tagline in ('Governed work for AI agents', 'café & <demo> "quoted"', '', '   '):
                setting.write_text(json.dumps({'tagline': tagline}), encoding='utf-8')
                for checkout in (primary, linked):
                    with self.subTest(tagline=tagline, checkout=checkout), mock.patch.object(serve, 'REPO', checkout):
                        expected = primary.name + (' · ' + tagline.strip() if tagline.strip() else '')
                        self.assertEqual(serve.project_identity(checkout)[0], expected)
                        status, body, _ = self.request('GET', '/ui/')
                        self.assertEqual(status, 200)
                        self.assertNotIn(b'mission override', body)
                        if tagline.strip():
                            self.assertIn(' · ', body.decode('utf-8'))
                        else:
                            self.assertIn('>Projeto café &amp; demo</p>', body.decode('utf-8'))
                        self.assertNotIn(b'<demo>', body)

    def test_discovery_failures_never_substitute_the_launch_folder_name(self):
        for error in (ValueError('No primary checkout found; bare repositories are unsupported'),
                      OSError('Git unavailable'), subprocess.CalledProcessError(128, ['git'])):
            with self.subTest(error=error), mock.patch.object(serve.local_jobs, 'roots', side_effect=error):
                name, detail = serve.project_identity(Path('mission-folder'))
                self.assertEqual(name, 'Project unavailable')
                self.assertIn(str(error), detail)


if __name__ == '__main__':
    unittest.main()
