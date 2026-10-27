"""Loopback preview: UI assets, the five local datasets, the declared display groups, a local read-only queue and one local refresh action."""
import argparse
from datetime import datetime, timezone
from functools import partial
from html import escape
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
for entry in (ROOT.parents[1] / 'tools', ROOT.parents[2]):  # absolute and resolved, inserted at the front once per process
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))
import data_launcher
import declared_inputs
import repo_paths as rp
from governance.tools.coordination import local_jobs

REPO = rp.ROOT

# The port is the project's setting (G045: one project file, the default in this code). The server binds it exclusively and
# never falls back to another port, so several repositories on one host each name their own.
PORT_SETTING = 'project-governance/config/mission-control.json'
DEFAULT_PORT = 8765

# The execution status the status light shows: one fixed read-only command, never built from a request.
EXECUTION = [sys.executable, '-B', 'governance/tools/worktree.py', '--main', str(REPO), 'execution-status', '--json-only']
LEVELS = ('CLEAR', 'OVERLAP', 'FROZEN', 'UNKNOWN')
# The window fields the page may see; the helper's paths, pids and evidence text never leave the server.
WINDOW_FIELDS = ('owner', 'scope', 'state', 'since', 'job_handle', 'flags', 'age_flag')
# The claims strip fields, likewise: no worktree path, pid or claim id leaves the server.
CLAIM_FIELDS = ('owner', 'job_handle', 'level', 'subclasses', 'files', 'since', 'flags')
OVERLAP_FIELDS = ('owners', 'kind', 'text')
# A browser that closes a connection before its response is written is not an error worth a traceback.
CLOSED_BY_CLIENT = (ConnectionAbortedError, ConnectionResetError, BrokenPipeError)
MIN_POLL_SECONDS = 10
light_poll_seconds = 0  # --light-poll-seconds; 0 means the light refreshes only on load and when the tab regains focus
KINDS = ('decisions', 'commits', 'devlog', 'rationale', 'documents')
ALLOWED = {'/ui/', '/ui/index.html', '/ui/style.css', '/ui/app.mjs', '/ui/core.mjs'}
DATASETS = {f'/data/{kind}.json' for kind in KINDS}
# The server's whole surface: GET of the paths above, GET /queue.json (the queue plus the `execution` field for the status light), GET /groups.json (the display groups of the Status briefing) and the one action POST /refresh.
QUEUE = '/queue.json'
GROUPS = '/groups.json'
REFRESH = '/refresh'
HELPER = [sys.executable, '-B', 'governance/tools/local_jobs.py', '--repo', str(REPO)]
# A fixed command: nothing in a request can change it. It writes only the shared ignored local folder.
REFRESH_COMMAND = [sys.executable, '-B', 'governance/extensions/mission_control/data_launcher.py', 'refresh-local']
# The fields the page may see. Anything else the helper returns, such as record paths, the worker,
# the current step or pending text, never leaves the server.
JOB_FIELDS = ('handle', 'status', 'activity', 'description', 'work_kind', 'waiting', 'dependencies',
              'unsatisfied_dependencies', 'missing_dependencies', 'awaiting_lead', 'pending_count')


def project_identity(root):
    """Use the queue/data helper's primary checkout discovery; never fall back to a mission's folder name."""
    try:
        primary = local_jobs.roots(root)[1]
        settings, problem = project_settings(primary)
        name = primary.name + (' · ' + settings['tagline'] if settings['tagline'] else '')
        return name, problem or 'Primary checkout folder name'
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        return 'Project unavailable', 'Primary checkout discovery failed: ' + str(error)


def poll_seconds(value):
    """0 (no timer) unless the value is a positive whole number, which is raised to the minimum."""
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return 0
    return 0 if seconds <= 0 else max(seconds, MIN_POLL_SECONDS)


def project_settings(root):
    """(settings, problem): optional project settings, or code defaults with the existing invalid-file diagnostic."""
    defaults = {'port': DEFAULT_PORT, 'tagline': ''}
    path = Path(root) / PORT_SETTING
    if not path.is_file():
        return defaults, None
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (ValueError, OSError) as error:  # includes UnicodeError and json.JSONDecodeError
        return defaults, f'{PORT_SETTING} cannot be read ({error})'
    if not isinstance(data, dict) or not set(data) <= {'version', 'port', 'tagline'} or data.get('version', 1) != 1 \
            or type(data.get('version', 1)) is not int:
        return defaults, f'{PORT_SETTING} may hold only "version" (1), "port" and "tagline"'
    port = data.get('port', DEFAULT_PORT)
    if type(port) is not int or not 1 <= port <= 65535:
        return defaults, f'{PORT_SETTING}: "port" must be a whole number from 1 to 65535'
    tagline = data.get('tagline', '')
    if not isinstance(tagline, str) or any(character in tagline for character in '\r\n'):
        return defaults, f'{PORT_SETTING}: "tagline" must be a single-line string'
    return {'port': port, 'tagline': tagline.strip()}, None


def port_setting(root):
    """(port, problem): preserve the port interface and its fallback for invalid project settings."""
    settings, problem = project_settings(root)
    return settings['port'], problem


def chosen_port(explicit, root):
    """(port, problem): an explicit --port first, then the project setting, then DEFAULT_PORT."""
    return (explicit, None) if explicit is not None else port_setting(root)


def execution_payload():
    """The execution status for the light, from the read-only command; an unreadable one is UNKNOWN, never CLEAR."""
    unknown = {'schema': '1.0', 'level': 'UNKNOWN', 'line': 'STATUS UNKNOWN', 'read_at': now(), 'windows': [],
               'claims': [], 'overlaps': [], 'stale_claims': 0,
               'database_state': 'unknown', 'declared_without_evidence': 0, 'poll_seconds': poll_seconds(light_poll_seconds)}
    try:
        result = subprocess.run(EXECUTION, cwd=REPO, capture_output=True, text=True, encoding='utf-8', timeout=60)
        data = json.loads(result.stdout)
        if data['level'] not in LEVELS:
            raise ValueError('unknown level')
        windows = [dict({key: row.get(key) for key in WINDOW_FIELDS}, command_running=(row.get('command') or {}).get('running'))
                   for row in data['windows']]
        # Claims arrived with schema 1.1: an older payload simply has none.
        strip = [{key: row.get(key) for key in CLAIM_FIELDS} for row in data.get('claims', [])]
        overlaps = [{key: row.get(key) for key in OVERLAP_FIELDS} for row in data.get('overlaps', [])]
        stale = data.get('stale_claims', 0)
        return dict(unknown, schema=str(data['schema']), level=data['level'], line=str(data['line'])[:300],
                    read_at=str(data['read_at']), windows=windows, claims=strip, overlaps=overlaps,
                    stale_claims=stale if isinstance(stale, int) else 0,
                    database_state=str(data['database']['state'])[:40],
                    declared_without_evidence=len(data['declared_without_evidence']))
    except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.SubprocessError) as error:
        return dict(unknown, line=('STATUS UNKNOWN: ' + str(error))[:300])


def now():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def helper(*args):
    return subprocess.run([*HELPER, *args], cwd=REPO, capture_output=True, text=True, encoding='utf-8', timeout=60)


def failure(result):
    lines = [line for line in (result.stderr or '').splitlines() if line.strip() and not line.startswith('scrubbed')]
    return (lines[-1] if lines else 'The local job helper failed').removeprefix('FAIL: ')[:300]


def public_job(row):
    job = {key: row.get(key) for key in JOB_FIELDS}
    job['has_record'] = row.get('status') is not None
    return job


def queue_payload():
    """The live queue and the execution status, read at request time and never cached."""
    return dict(queue_core(), execution=execution_payload())


def queue_core():
    """The live queue from the validated helper."""
    payload = {'schema': 1, 'authority': 'local/non-authoritative', 'read_at': now(), 'queue': [], 'unqueued': []}
    try:
        result = helper('queue')
        if result.returncode:
            located = helper('paths')
            primary = Path(json.loads(located.stdout)['primary']) if not located.returncode else None
            if primary is not None and not (primary / 'coordinator.json').exists():
                return dict(payload, state='absent',
                            message='No local coordination records exist on this machine, so no queue is shown.')
            return dict(payload, state='error', message=failure(result))
        data = json.loads(result.stdout)
        queue = [{'handle': entry['handle'], 'kind': 'epic', 'priority': entry['priority'],
                  'closure_policy': entry.get('closure_policy', 'automatic'),
                  'jobs': [public_job(job) for job in entry['jobs']]} if entry['kind'] == 'epic'
                 else dict(public_job(entry), kind='job', priority=entry['priority']) for entry in data['queue']]
        return dict(payload, state='ok', queue=queue, unqueued=[public_job(job) for job in data['unqueued']])
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        return dict(payload, state='error', message=str(error)[:300])


def groups_payload():
    """The display groups declared by the manifests of the installed extensions and by the project, read at request time.

    Only labels and exact paths leave the server; a refused declaration is an error with its one message, never a guess."""
    payload = {'schema': 1, 'authority': 'local/non-authoritative', 'groups': []}
    try:
        found = declared_inputs.groups(REPO, declared_inputs.load(REPO))
        return dict(payload, state='ok', groups=[{'label': group['label'], 'paths': group['paths']} for group in found])
    except (OSError, ValueError, KeyError, TypeError) as error:
        return dict(payload, state='error', message=str(error).removeprefix('FAIL: ')[:300])


class PreviewServer(ThreadingHTTPServer):
    # Exclusive on Windows, where SO_REUSEADDR lets a second server bind a port another one listens on and take its
    # requests: there the socket takes SO_EXCLUSIVEADDRUSE instead. Elsewhere SO_REUSEADDR stays, because there it never
    # admits a second listener and it lets a restart reuse a port whose old connections are still closing.
    allow_reuse_address = not hasattr(socket, 'SO_EXCLUSIVEADDRUSE')

    def server_bind(self):
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def handle_error(self, request, client_address):
        # The browser went away mid-response (a reload, a tab change or a newer fetch): nothing was lost, so stay quiet.
        # Every other exception still prints its traceback.
        if isinstance(sys.exc_info()[1], CLOSED_BY_CLIENT):
            return
        super().handle_error(request, client_address)


class Preview(SimpleHTTPRequestHandler):
    extensions_map = {**SimpleHTTPRequestHandler.extensions_map, '.mjs': 'text/javascript'}
    # Overridable seams for tests: the local snapshot lookup, the queue source and the refresh command.
    local_snapshot = staticmethod(lambda: data_launcher.local_snapshot(REPO))
    queue_source = staticmethod(queue_payload)
    groups_source = staticmethod(groups_payload)
    refresh_command = REFRESH_COMMAND
    refresh_lock = threading.Lock()

    @staticmethod
    def head():
        try:
            return subprocess.run(['git', '-C', str(REPO), 'rev-parse', 'HEAD'], capture_output=True, text=True,
                                  timeout=30).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ''

    def path_only(self):
        return urlsplit(self.path).path

    def send_body(self, status, body, content_type, head=False, **headers):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        for name, value in headers.items():
            self.send_header(name.replace('_', '-'), value)
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def send_json(self, status, value, head=False, **headers):
        self.send_body(status, json.dumps(value, ensure_ascii=True).encode('ascii'), 'application/json', head, **headers)

    def dataset(self, head):
        kind = self.path_only().rsplit('/', 1)[1].removesuffix('.json')
        snapshot = self.local_snapshot()
        if not snapshot:
            self.send_error(404, 'No local dataset copy yet: press Refresh')
            return
        source = snapshot['folder'] / f'{kind}.json'
        label = (f"local; revision={snapshot['revision']}; generated={snapshot['generated_at']}; "
                 f"generator={snapshot['generator']}; head={'current' if self.head() == snapshot['revision'] else 'older'}")
        try:
            body = source.read_bytes()
        except OSError:
            self.send_error(404, 'Dataset unavailable')
            return
        self.send_body(200, body, 'application/json', head, X_MC_Data_Source=label)

    def index(self, head):
        name, detail = project_identity(REPO)
        try:
            template = (ROOT / 'ui' / 'index.html').read_text(encoding='utf-8')
        except OSError:
            self.send_error(404, 'UI unavailable')
            return
        placeholder = '<p class="tagline" id="project-name">Project unavailable</p>'
        rendered = template.replace(placeholder,
                                    '<p class="tagline" id="project-name" title="' + escape(detail, quote=True) +
                                    '">' + escape(name) + '</p>', 1)
        self.send_body(200, rendered.encode('utf-8'), 'text/html; charset=utf-8', head)

    def do_GET(self):
        path = self.path_only()
        if path in ('/', '/ui'):
            self.send_response(302)
            self.send_header('Location', '/ui/')
            self.end_headers()
        elif path == QUEUE:
            self.send_json(200, self.queue_source())
        elif path == GROUPS:
            self.send_json(200, self.groups_source())
        elif path in DATASETS:
            self.dataset(False)
        elif path in ('/ui/', '/ui/index.html'):
            self.index(False)
        elif path in ALLOWED:
            super().do_GET()
        else:
            self.send_error(404, 'Only UI assets, declared datasets, the declared groups and the local queue are served')

    def do_HEAD(self):
        path = self.path_only()
        if path in (QUEUE, GROUPS, REFRESH):
            self.send_error(405, 'GET only' if path != REFRESH else 'POST only')
        elif path in DATASETS:
            self.dataset(True)
        elif path in ('/ui/', '/ui/index.html'):
            self.index(True)
        elif path in ALLOWED:
            super().do_HEAD()
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path != REFRESH:
            self.send_error(501, 'Unsupported method')
            return
        port = self.server.server_address[1]
        # A page on another site must not be able to start a refresh, nor a rebound hostname.
        if self.headers.get('Host') != f'127.0.0.1:{port}' or self.headers.get('Origin') != f'http://127.0.0.1:{port}' \
                or self.headers.get('Sec-Fetch-Site', 'same-origin') != 'same-origin':
            self.send_json(403, {'state': 'error', 'message': 'Refresh is allowed only from this page.'})
            return
        if self.headers.get('Content-Length', '0') != '0' or 'Transfer-Encoding' in self.headers:
            self.send_json(400, {'state': 'error', 'message': 'Refresh takes no request body or parameters.'})
            return
        if not self.refresh_lock.acquire(blocking=False):
            self.send_json(409, {'state': 'busy', 'message': 'A refresh is already running.'})
            return
        started = time.monotonic()
        try:
            result = subprocess.run(self.refresh_command, cwd=REPO, capture_output=True, text=True,
                                    encoding='utf-8', timeout=600)
            lines = [line for line in (result.stdout + result.stderr).splitlines()
                     if line.strip() and not line.startswith('scrubbed')]
            message = (lines[-1] if lines else 'No output')[:300].removeprefix('FAIL: ')
            state = 'ok' if result.returncode == 0 else 'error'
        except (OSError, subprocess.SubprocessError) as error:
            state, message = 'error', str(error)[:300]
        finally:
            self.refresh_lock.release()
        self.send_json(200 if state == 'ok' else 500,
                       {'state': state, 'message': message, 'finished_at': now(),
                        'seconds': round(time.monotonic() - started, 1)})

    def end_headers(self):
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        super().end_headers()


def listen(port):
    """The server on 127.0.0.1:`port`; when the port cannot be bound, one line on standard error and exit 1, never another port."""
    try:
        return PreviewServer(('127.0.0.1', port), partial(Preview, directory=str(ROOT)))
    except OSError as error:
        print(f'Mission Control: port {port} on 127.0.0.1 is not available ({error.strerror or error}); another server may '
              f'hold it. Set another "port" in {PORT_SETTING} or start with --port.', file=sys.stderr, flush=True)
        raise SystemExit(1)


if __name__ == '__main__':
    import runtime_guard  # the core's guard, on the path set up above
    runtime_guard.enter('serve', extension_mode=runtime_guard.READ)  # first: version check, GIT_* scrub, lifecycle gate
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=None,
                        help=f'the port to serve on; default: the "port" of {PORT_SETTING}, else {DEFAULT_PORT}')
    parser.add_argument('--light-poll-seconds', type=int, default=0,
                        help='refresh the status light on this interval while the page is visible; 0 (default) means only on load and focus; minimum 10')
    args = parser.parse_args()
    light_poll_seconds = args.light_poll_seconds
    port, problem = chosen_port(args.port, REPO)
    if problem:
        print(f'Mission Control: {problem}; using port {DEFAULT_PORT}.', file=sys.stderr, flush=True)
    server = listen(port)
    print(f'Mission Control preview: http://127.0.0.1:{port}/ui/', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
