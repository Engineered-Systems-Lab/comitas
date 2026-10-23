"""Representative reconstruction and negative recovery-chain tests of the continuity checker, on disposable fixtures; no repo mutations."""
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from governance.tools.continuity import artifact_classification as ac
from governance.tools.continuity import validate_continuity as v
from governance.tests.fake_git import FakeGit
from governance.tools.worktrees import guard
import repo_paths as rp



GOVERNANCE_EVIDENCE = [rp.AGENTS, rp.CONSTRAINTS, rp.DECISIONS, rp.PLAN,
                       rp.DEVLOG_INDEX]


CHECKPOINT_DATE = '2026-10-10T12:00:00+00:00'  # The commit time the checkpoint entry of the fixture is dated at.


CHECKPOINT_MOMENT = datetime(2026, 10, 10, 12, 0)


DEFAULT_PATTERN = ac.DEVLOG_DEFAULTS['date_format']


LOG = 'devlog/' + ac.date_prefix(CHECKPOINT_MOMENT.strftime(DEFAULT_PATTERN)) + 'runtime-checkpoint.md'


REPORT = 'docs/REPORT.md#completion'


PROGRESS = 'docs/PROGRESS.md#recovery-facts'


FIXTURE_FROZEN = ['contracts']


FIXTURE_RUNTIME = ['src']


def required_documents(folder='devlog'):
    return [rp.AGENTS, rp.README, rp.CONSTRAINTS, rp.DECISIONS,
            rp.ARCHITECTURE, rp.PLAN, rp.PROGRESS, 'docs/REPORT.md',
            folder + '/README.md', v.CONTRACT]


FIXTURE_REQUIRED = required_documents()


FIXTURE_CONTRACT = {
    'version': 1,
    'bootstrap': 'governance/COMMAND-RECOVERY-PROMPT-v1.md',
    'completion_evidence_fact': 'checks_passed',
    'facts': {
        'implementation_checkpoint': {'source': PROGRESS, 'pattern': r'^\| implementation_checkpoint \| ([^|]+?) \|$', 'type': 'string'},
        'service': {'source': PROGRESS, 'pattern': r'^\| service \| ([^|]+?) \|$', 'type': 'string'},
        'manual_acceptance': {'source': PROGRESS, 'pattern': r'^\| manual_acceptance \| ([^|]+?) \|$', 'type': 'string'},
        'next_authorized_action': {'source': PROGRESS, 'pattern': r'^\| next_authorized_action \| ([^|]+?) \|$', 'type': 'string'},
        'checks_passed': {'source': REPORT, 'pattern': r'OK: (\d+) checks;', 'type': 'integer'},
        'smoke_result': {'source': REPORT, 'pattern': r'\| Smoke \| (PASS):', 'type': 'string'},
        'probe_calls': {'source': REPORT, 'pattern': r'\| Probe, [^\n]+?\| PASS: (\d+) requests', 'type': 'sum'},
        'frozen_contracts_and_fixtures_unchanged': {'source': 'git:frozen-invariants', 'type': 'boolean'},
    },
    'governance_evidence': GOVERNANCE_EVIDENCE,
    'required_sections': ['current_state', 'governing_constraints', 'validation_evidence', 'pending_work',
                          'next_action', 'limitations'],
}


def representative(checkpoint):
    values = {
        'implementation_checkpoint': (checkpoint, PROGRESS),
        'service': ('Fixture service', PROGRESS),
        'manual_acceptance': ('PASS', PROGRESS),
        'next_authorized_action': ('Specify the next component; nothing else is authorized', PROGRESS),
        'checks_passed': (35, REPORT),
        'smoke_result': ('PASS', REPORT),
        'probe_calls': (7, REPORT),
        'frozen_contracts_and_fixtures_unchanged': (True, 'git:frozen-invariants'),
    }
    narratives = {
        'current_state': ('The fixture service is implemented at the recorded checkpoint.', [rp.PROGRESS]),
        'governing_constraints': ('Current documents govern; frozen contracts outrank convenience; logs are historical. '
                                  'Do not stage or commit without orders.',
                                  [rp.AGENTS, rp.CONSTRAINTS, rp.DECISIONS, rp.DEVLOG_INDEX]),
        'validation_evidence': ('The completion report records 35 checks, a smoke result and seven probe calls. These are '
                                'recorded results, not newly executed checks.', [REPORT]),
        'pending_work': ('Manual acceptance is PASS on reported observations; later components remain future work.',
                         [rp.PROGRESS, rp.PLAN]),
        'next_action': ('Specify the next component; no new implementation or commit authorization is inferred.', [rp.PROGRESS]),
        'limitations': ('This artifact is a synthetic validator example, not a fresh-agent trial.', [rp.ARCHITECTURE, rp.PROGRESS]),
    }
    return {'contract_version': 1,
            'facts': {key: {'value': val, 'evidence': [ref]} for key, (val, ref) in values.items()},
            'sections': {key: {'text': text, 'evidence': refs} for key, (text, refs) in narratives.items()}}


def write_frozen(write):
    """The frozen contract of the fixture and its manifest entry."""
    write('contracts/c.txt', 'frozen contract\n')
    write(ac.FROZEN_MANIFEST, json.dumps({'version': 1, 'files': {'contracts/c.txt': v.digest(b'frozen contract\n')}}) + '\n')


def build_fixture(root, folder='devlog', pattern=None, keep_log=True, frozen_with='base'):
    """A disposable repository with a runtime checkpoint, its devlog entry, a frozen contract and the log settings.

    The frozen contract and its manifest enter with the base commit, or with the runtime checkpoint when `frozen_with` is
    'checkpoint'.

    With the defaults it has no settings file, which is how a fresh adopter starts. A folder or a date pattern other
    than the defaults is written to the settings file the tools read. With `keep_log` false the settings file says
    `"enabled": false` and the repository has no log folder, no index and no entry (the entry's path is still returned,
    unwritten). Returns the checkpoint SHA, the SHA of the older base commit and the path of the checkpoint entry.
    Nothing here touches this repository."""
    env = dict(os.environ, GIT_AUTHOR_NAME='Fixture', GIT_AUTHOR_EMAIL='fixture@example.test',
               GIT_COMMITTER_NAME='Fixture', GIT_COMMITTER_EMAIL='fixture@example.test')
    env = {k: val for k, val in env.items() if k not in ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE')}

    def git(*args, when=None):
        environment = dict(env, GIT_AUTHOR_DATE=when, GIT_COMMITTER_DATE=when) if when else env
        done = subprocess.run(['git', '-C', str(root), *args], capture_output=True, env=environment)
        assert done.returncode == 0, done.stderr.decode()
        return done.stdout.decode().strip()

    def write(name, text):
        path = Path(root) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8', newline='\n')

    Path(root).mkdir(parents=True, exist_ok=True)
    git('init', '-q', '-b', 'main')
    write(rp.AGENTS, '# Agents\n\n[Constraints](governance/CONSTRAINTS.md) [Decisions](governance/DECISIONS.md) '
          '[Architecture](docs/ARCHITECTURE.md) [Plan](docs/PLAN.md) [Progress](docs/PROGRESS.md) '
          '[Report](docs/REPORT.md)' + (' [Logs](' + folder + '/README.md)' if keep_log else '') + '\n')
    write(rp.README, '# Fixture\n')
    write(rp.CONSTRAINTS, '# Constraints\n')
    write(rp.DECISIONS, '# Decisions\n')
    write(rp.ARCHITECTURE, '# Architecture\n')
    write(rp.PLAN, '# Plan\n')
    write('docs/REPORT.md', '# Report\n\n## Completion\n\nOK: 35 checks;\n\n| Check | Result |\n| --- | --- |\n'
          '| Smoke | PASS: all |\n| Probe, one | PASS: 3 requests |\n| Probe, two | PASS: 4 requests |\n')
    write(rp.PROGRESS, '# Progress\n\n## Recovery facts\n\n| Fact | Value |\n| --- | --- |\n'
          '| implementation_checkpoint | ' + '0' * 40 + ' |\n| service | Fixture service |\n'
          '| manual_acceptance | PASS |\n'
          '| next_authorized_action | Specify the next component; nothing else is authorized |\n')
    if keep_log:
        write(folder + '/README.md', '# Logs\n')
    if not keep_log:
        write(ac.DEVLOG, json.dumps({'enabled': False}) + '\n')
    elif folder != 'devlog' or pattern is not None:
        write(ac.DEVLOG, json.dumps({'version': 1, 'folder': folder, 'date_format': pattern or DEFAULT_PATTERN}) + '\n')
    write(v.CONTRACT, json.dumps(FIXTURE_CONTRACT, indent=2) + '\n')
    write('src/app.txt', 'runtime v1\n')
    if frozen_with == 'base':
        write_frozen(write)
    git('add', '-A')
    git('commit', '-q', '-m', 'Base', when='2026-10-09T12:00:00+00:00')
    base = git('rev-parse', 'HEAD')
    write('src/app.txt', 'runtime v2\n')
    if frozen_with == 'checkpoint':
        write_frozen(write)
    git('add', '-A')
    git('commit', '-q', '-m', 'Runtime checkpoint', when=CHECKPOINT_DATE)
    checkpoint = git('rev-parse', 'HEAD')
    progress = (Path(root) / rp.PROGRESS).read_text(encoding='utf-8').replace('0' * 40, checkpoint)
    write(rp.PROGRESS, progress)
    date = CHECKPOINT_MOMENT.strftime(pattern or DEFAULT_PATTERN)
    entry = folder + '/' + ac.date_prefix(date) + 'runtime-checkpoint.md'
    if keep_log:
        write(entry, '# Runtime checkpoint\n\nDate: ' + date + '\nSubject: Runtime checkpoint\n'
              'Status: Historical / non-normative\nProvenance: Fixture\nRepository checkpoint: `' + checkpoint + '`\n')
        write(folder + '/README.md', '# Logs\n\n[Checkpoint](' + Path(entry).name + ')\n')
    git('add', '-A')
    git('commit', '-q', '-m', 'Records', when='2026-10-10T13:00:00+00:00')
    return checkpoint, base, entry


class ContinuityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = tempfile.TemporaryDirectory(prefix='continuity-fixture-')
        cls.addClassCleanup(cls.fixture.cleanup)
        cls.root = Path(cls.fixture.name) / 'repo'
        live_scope = v.scope

        def scope(root):  # the fixture has no table; every other root reads its own
            if Path(root).resolve() == cls.root.resolve():
                return FIXTURE_FROZEN, FIXTURE_RUNTIME, FIXTURE_REQUIRED
            return live_scope(root)
        patcher = patch.object(v, 'scope', scope)
        patcher.start()
        cls.addClassCleanup(patcher.stop)
        cls.checkpoint, cls.base, cls.entry = build_fixture(cls.root)
        cls.head = v.git(cls.root, 'rev-parse', 'HEAD').decode().strip()
        cls.contract, cls.expected = v.verify_repository(cls.root)

    def test_fixture_is_pinned_and_clean(self):
        self.assertEqual(v.git(self.root, 'rev-parse', 'HEAD').decode().strip(), self.head)
        self.assertEqual(v.git(self.root, 'status', '--porcelain'), b'')
        self.assertEqual(self.expected['implementation_checkpoint'], self.checkpoint)

    @classmethod
    def tearDownClass(cls):
        # Negative assertions use mocks/external files; the fixture stays unchanged.
        if v.git(cls.root, 'status', '--porcelain'):
            raise AssertionError('Continuity fixture was mutated')

    def validate(self, artifact):
        v.validate_reconstruction(self.root, artifact, self.contract, self.expected)

    def test_representative_reconstruction(self):
        self.validate(representative(self.checkpoint))

    def test_wrong_fact(self):
        artifact = representative(self.checkpoint)
        artifact['facts']['checks_passed']['value'] = 31
        with self.assertRaisesRegex(ValueError, 'Incorrect fact/type'):
            self.validate(artifact)

    def test_wrong_types_including_bool_as_integer(self):
        for key, val in [('checks_passed', '35'), ('probe_calls', True),
                         ('frozen_contracts_and_fixtures_unchanged', 1)]:
            with self.subTest(key=key):
                artifact = representative(self.checkpoint)
                artifact['facts'][key]['value'] = val
                with self.assertRaisesRegex(ValueError, 'Incorrect fact/type'):
                    self.validate(artifact)

    def test_missing_and_extra_facts(self):
        for extra in (False, True):
            artifact = representative(self.checkpoint)
            if extra:
                artifact['facts']['invented'] = {'value': 1, 'evidence': []}
            else:
                del artifact['facts']['service']
            with self.assertRaisesRegex(ValueError, 'fact keys'):
                self.validate(artifact)

    def test_bad_version(self):
        artifact = representative(self.checkpoint)
        artifact['contract_version'] = True
        with self.assertRaisesRegex(ValueError, 'version'):
            self.validate(artifact)

    def test_missing_authoritative_citation(self):
        artifact = representative(self.checkpoint)
        artifact['facts']['checks_passed']['evidence'] = [rp.README]
        with self.assertRaisesRegex(ValueError, 'Missing authoritative'):
            self.validate(artifact)

    def test_bad_fragment(self):
        artifact = representative(self.checkpoint)
        artifact['sections']['limitations']['evidence'] = ['docs/PROGRESS.md#nonexistent']
        with self.assertRaisesRegex(ValueError, 'heading'):
            self.validate(artifact)

    def test_missing_governance(self):
        artifact = representative(self.checkpoint)
        artifact['sections']['governing_constraints']['evidence'] = [rp.AGENTS]
        with self.assertRaisesRegex(ValueError, 'Missing governance'):
            self.validate(artifact)

    def test_empty_narrative(self):
        artifact = representative(self.checkpoint)
        artifact['sections']['pending_work']['text'] = ' '
        with self.assertRaisesRegex(ValueError, 'Empty narrative'):
            self.validate(artifact)

    def test_scratch_is_prohibited_even_when_file_exists(self):
        with patch.object(Path, 'is_file', return_value=True):
            with self.assertRaisesRegex(ValueError, 'Transient/private evidence prohibited'):
                v.safe_path(self.root, 'tmp/checkpoint/handoff.json')

    def test_transient_and_escaping_evidence(self):
        for ref in ['work/before.json', 'tmp/checkpoint/handoff.json', '../outside.md', '.cache/result.md']:
            with self.subTest(ref=ref), self.assertRaises(ValueError):
                v.source_text(self.root, ref)

    def changed_read(self, path, old, new):
        original = v.read
        def read(root, relative):
            text = original(root, relative)
            return text.replace(old, new) if relative == path else text
        return patch.object(v, 'read', side_effect=read)

    def test_broken_link(self):
        with self.changed_read(rp.AGENTS, '(governance/CONSTRAINTS.md)', '(governance/MISSING.md)'):
            with self.assertRaisesRegex(ValueError, 'Missing recovery evidence'):
                v.validate_docs(self.root)

    def test_unindexed_log(self):
        with self.changed_read(rp.DEVLOG_INDEX, '(' + Path(self.entry).name + ')', '(README.md)'):
            with self.assertRaisesRegex(ValueError, 'Log absent from index'):
                v.validate_docs(self.root)

    def test_entry_dates_and_titles_are_enforced(self):
        for old, new, message in [('Date: 2026-10-10T12:00Z', 'Date: 2026-10-10T25:00Z', 'Log date|Invalid date'),
                                  ('Date: 2026-10-10T12:00Z', 'Date: 2026-10-10T12:01Z', 'Log date'),
                                  ('Date: 2026-10-10T12:00Z', 'Date: tomorrow', 'Log date|Invalid date'),
                                  ('Date: 2026-10-10T12:00Z\n', '', 'Log metadata Date: missing'),
                                  ('# Runtime checkpoint', 'Runtime checkpoint', 'title line')]:
            with self.subTest(message=message), self.changed_read(self.entry, old, new):
                with self.assertRaisesRegex(ValueError, message):
                    v.validate_docs(self.root)

    def test_missing_required_document(self):
        original = v.read
        def read(root, relative):
            if relative == rp.CONSTRAINTS:
                raise ValueError('Missing recovery evidence: ' + relative)
            return original(root, relative)
        with patch.object(v, 'read', side_effect=read), self.assertRaisesRegex(ValueError, 'Missing recovery'):
            v.validate_docs(self.root)

    def test_stale_checkpoint(self):
        with self.changed_read(rp.PROGRESS, self.expected['implementation_checkpoint'], self.base):
            with self.assertRaisesRegex(ValueError, 'Stale implementation checkpoint'):
                v.verify_repository(self.root)

    def test_source_cardinality(self):
        with self.changed_read(rp.PROGRESS, '| service |', '| removed_service |'):
            with self.assertRaisesRegex(ValueError, 'Evidence cardinality'):
                v.extract_facts(self.root, self.contract)

    def test_frozen_mutation(self):
        relative = 'contracts/c.txt'
        original = v.safe_path
        with tempfile.TemporaryDirectory(prefix='continuity-test-') as directory:
            changed = Path(directory) / 'changed.txt'
            changed.write_bytes((self.root / relative).read_bytes() + b'changed')
            with patch.object(v, 'safe_path', side_effect=lambda root, rel: changed if rel == relative else original(root, rel)):
                with self.assertRaisesRegex(ValueError, 'Frozen content changed'):
                    v.frozen_invariants(self.root, self.expected['implementation_checkpoint'])

    def test_completion_evidence_drift(self):
        with self.changed_read('docs/REPORT.md', 'OK: 35 checks;', 'OK: 36 checks;'):
            with self.assertRaisesRegex(ValueError, 'Completion evidence no longer matches'):
                v.verify_repository(self.root)


class NoRuntimeBoundaryTest(unittest.TestCase):
    """A project with no runtime boundary declares no checkpoint fact, and is verified by its documents and logs."""

    def fixture_with_contract(self, directory, change):
        root = Path(directory) / 'repo'
        build_fixture(root)
        contract = json.loads((root / v.CONTRACT).read_text(encoding='utf-8'))
        change(contract)
        (root / v.CONTRACT).write_text(json.dumps(contract), encoding='utf-8')
        return root

    def test_a_contract_without_a_checkpoint_fact_verifies_documents_and_facts_only(self):
        def change(contract):
            contract['facts'] = {k: val for k, val in contract['facts'].items()
                                 if k in ('service', 'manual_acceptance', 'next_authorized_action')}
            contract.pop('completion_evidence_fact')
        with tempfile.TemporaryDirectory(prefix='continuity-no-runtime-') as directory:
            root = self.fixture_with_contract(directory, change)
            with patch.object(v, 'scope', return_value=([], [], FIXTURE_REQUIRED)):
                _, facts = v.verify_repository(root)
            self.assertEqual(sorted(facts), ['manual_acceptance', 'next_authorized_action', 'service'])

    def test_a_frozen_fact_needs_a_checkpoint_fact(self):
        with tempfile.TemporaryDirectory(prefix='continuity-no-runtime-') as directory:
            root = self.fixture_with_contract(directory, lambda contract: contract['facts'].pop('implementation_checkpoint'))
            with patch.object(v, 'scope', return_value=([], [], FIXTURE_REQUIRED)):
                with self.assertRaisesRegex(ValueError, 'need an implementation_checkpoint'):
                    v.verify_repository(root)


class DevlogConventionsTest(unittest.TestCase):
    def setUp(self):
        self.fixture = tempfile.TemporaryDirectory(prefix='log-conventions-')
        self.addCleanup(self.fixture.cleanup)
        self.root = Path(self.fixture.name)
        self.log = 'devlog/2026-10-10T1200Z-event.md'
        self.write(rp.AGENTS, '[Index](devlog/README.md)\n')
        self.write(rp.README, '# Repository\n')
        self.write(v.CONTRACT, '{"bootstrap":"governance/COMMAND-RECOVERY-PROMPT-v1.md"}')
        self.write(rp.DEVLOG_INDEX, '[Rules](CONVENTIONS.md)\n[Event](2026-10-10T1200Z-event.md)\n')
        self.write('devlog/CONVENTIONS.md', '# Rules\n\nAuthoring conventions; no event metadata.\n')
        self.write(self.log, '# Event\nDate: 2026-10-10T12:00Z\nSubject: Event\n'
                   'Status: Historical / non-normative\nProvenance: Fixture\n'
                   'Repository checkpoint: `' + 'a' * 40 + '`\n')

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')

    def validate(self):
        with patch.object(v, 'scope', return_value=([], [], [])), \
                patch.object(v, 'git', FakeGit().rooted):
            return v.validate_docs(self.root)

    def test_conventions_are_linked_but_not_an_event(self):
        self.assertEqual(self.validate(), [self.log])

    def test_conventions_links_still_fail_when_broken(self):
        self.write('devlog/CONVENTIONS.md', '[Missing](missing.md)\n')
        with self.assertRaisesRegex(ValueError, 'Missing recovery evidence'):
            self.validate()

    def test_other_markdown_still_requires_log_metadata(self):
        self.write('devlog/notes.md', '# Undated notes\n')
        with (self.root / rp.DEVLOG_INDEX).open('a', encoding='utf-8') as index:
            index.write('[Notes](notes.md)\n')
        with self.assertRaisesRegex(ValueError, 'Log metadata'):
            self.validate()

    def test_event_index_coverage_remains_required(self):
        self.write(rp.DEVLOG_INDEX, '[Rules](CONVENTIONS.md)\n')
        with self.assertRaisesRegex(ValueError, 'Log absent from index'):
            self.validate()

    def test_an_impossible_date_fails_even_when_the_filename_agrees(self):
        name = 'devlog/2026-13-45T1200Z-event.md'
        self.write(name, (self.root / self.log).read_text(encoding='utf-8').replace('2026-10-10T12:00Z', '2026-13-45T12:00Z'))
        (self.root / self.log).unlink()
        self.write(rp.DEVLOG_INDEX, '[Rules](CONVENTIONS.md)\n[Event](2026-13-45T1200Z-event.md)\n')
        with self.assertRaisesRegex(ValueError, 'Invalid date'):
            self.validate()

    def test_event_date_still_validates(self):
        path = self.root / self.log
        path.write_text(path.read_text(encoding='utf-8').replace('2026-10-10T12:00Z', '2026-13-45T12:00Z'), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Log date|Invalid date'):
            self.validate()


class EntryLinkTest(unittest.TestCase):
    """An entry may link to a file or to an anchor of another entry; a broken target or anchor still fails."""
    SOURCE = 'devlog/2026-10-12T1515Z-source.md'
    TARGET = 'devlog/2026-10-08T1848Z-target.md'

    def setUp(self):
        self.fixture = tempfile.TemporaryDirectory(prefix='entry-links-')
        self.addCleanup(self.fixture.cleanup)
        self.root = Path(self.fixture.name)
        self.write(self.TARGET, '# Target\n\n## First\n\nText\n\n## Twice\n\n## Twice\n')

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')

    def links(self, target):
        self.write(self.SOURCE, '# Source\n\n[Link](' + target + ')\n')
        return v.local_links(self.root, self.SOURCE)

    def test_anchor_link_passes(self):
        self.assertEqual(self.links('2026-10-08T1848Z-target.md#first'), [self.TARGET])

    def test_whole_file_link_passes(self):
        self.assertEqual(self.links('2026-10-08T1848Z-target.md'), [self.TARGET])

    def test_missing_anchor_in_target_fails(self):
        with self.assertRaisesRegex(ValueError, 'Missing/ambiguous heading'):
            self.links('2026-10-08T1848Z-target.md#nonexistent')

    def test_duplicate_heading_slugs_resolve_once(self):
        # Duplicate headings get distinct slugs (twice, twice-1), so each resolves exactly once.
        self.assertEqual(self.links('2026-10-08T1848Z-target.md#twice-1'), [self.TARGET])

    def test_missing_target_fails(self):
        with self.assertRaisesRegex(ValueError, 'Missing recovery evidence'):
            self.links('2026-10-08T1848Z-absent.md#first')


class NonDefaultDevlogTest(unittest.TestCase):
    """A folder other than devlog and a date pattern other than the default, end to end through the continuity check."""
    FOLDER, PATTERN = 'journal', '%Y%m%d'

    @classmethod
    def setUpClass(cls):
        cls.fixture = tempfile.TemporaryDirectory(prefix='continuity-journal-')
        cls.addClassCleanup(cls.fixture.cleanup)
        cls.root = Path(cls.fixture.name) / 'repo'
        cls.checkpoint, cls.base, cls.entry = build_fixture(cls.root, cls.FOLDER, cls.PATTERN)
        patcher = patch.object(v, 'scope', lambda root: (FIXTURE_FROZEN, FIXTURE_RUNTIME, required_documents(cls.FOLDER)))
        patcher.start()
        cls.addClassCleanup(patcher.stop)

    def changed_read(self, path, old, new):
        original = v.read
        return patch.object(v, 'read', side_effect=lambda root, rel: original(root, rel).replace(old, new) if rel == path else original(root, rel))

    def test_the_entry_is_named_and_dated_by_the_pattern(self):
        self.assertEqual(self.entry, 'journal/20261010-runtime-checkpoint.md')
        self.assertIn('Date: 20261010\n', (self.root / self.entry).read_text(encoding='utf-8'))

    def test_the_whole_chain_verifies_on_the_named_folder_and_pattern(self):
        self.assertEqual(v.validate_docs(self.root), [self.entry])
        _, facts = v.verify_repository(self.root)
        self.assertEqual(facts['implementation_checkpoint'], self.checkpoint)

    def test_the_default_folder_is_no_longer_read(self):
        stray = self.root / 'devlog' / 'stray.md'
        stray.parent.mkdir()
        stray.write_text('# Not an entry of this log\n', encoding='utf-8')
        self.addCleanup(lambda: (stray.unlink(), stray.parent.rmdir()))
        self.assertEqual(v.validate_docs(self.root), [self.entry])

    def test_the_pattern_is_enforced_on_the_date_and_on_the_filename(self):
        for old, new, message in [('Date: 20261010', 'Date: 20261345', 'Log date|Invalid date'),
                                  ('Date: 20261010', 'Date: 20261011', 'Log date'),
                                  ('Date: 20261010', 'Date: 2026-10-10', 'Log date|Invalid date')]:
            with self.subTest(message=message), self.changed_read(self.entry, old, new):
                with self.assertRaisesRegex(ValueError, message):
                    v.validate_docs(self.root)

    def test_the_checkpoint_entry_must_be_dated_at_its_commit(self):
        with tempfile.TemporaryDirectory(prefix='continuity-journal-late-') as directory:
            root = Path(directory) / 'repo'
            _, _, entry = build_fixture(root, self.FOLDER, self.PATTERN)
            late = entry.replace('20261010', '20261011')
            (root / entry).rename(root / late)
            for name, old, new in ((late, 'Date: 20261010', 'Date: 20261011'),
                                   ('journal/README.md', Path(entry).name, Path(late).name)):
                path = root / name
                path.write_text(path.read_text(encoding='utf-8').replace(old, new), encoding='utf-8')
            self.assertEqual(v.validate_docs(root), [late])
            with self.assertRaisesRegex(ValueError, 'Checkpoint log timestamp mismatch'):
                v.verify_repository(root)


class DevlogOptOutTest(unittest.TestCase):
    """A project that sets `"enabled": false` keeps no devlog: the checkpoint pointer needs no entry beside it, and
    nothing else about the continuity check changes. With `enabled` true or absent the behavior is exactly the old one."""
    REQUIRED = [rp.AGENTS, rp.README, rp.CONSTRAINTS, rp.DECISIONS, rp.ARCHITECTURE, rp.PLAN, rp.PROGRESS, 'docs/REPORT.md',
                v.CONTRACT]

    def build(self, **options):
        directory = tempfile.TemporaryDirectory(prefix='continuity-optout-')
        self.addCleanup(directory.cleanup)
        root = Path(directory.name) / 'repo'
        built = build_fixture(root, **options)
        required = self.REQUIRED if not options.get('keep_log', True) else required_documents()
        patcher = patch.object(v, 'scope', lambda r: (FIXTURE_FROZEN, FIXTURE_RUNTIME, required))
        patcher.start()
        self.addCleanup(patcher.stop)
        return (root, *built)

    def test_the_whole_chain_verifies_without_any_log_entry(self):
        root, checkpoint, _, entry = self.build(keep_log=False)
        self.assertFalse((root / 'devlog').exists())
        self.assertIs(ac.devlog_settings(root)['enabled'], False)
        self.assertEqual(v.validate_docs(root), [])
        _, facts = v.verify_repository(root)
        self.assertEqual(facts['implementation_checkpoint'], checkpoint)
        self.assertTrue(facts[v.FROZEN_FACT])

    def test_the_log_folder_and_its_index_are_not_validated_when_the_log_is_off(self):
        root, *_ = self.build(keep_log=False)
        (root / 'devlog').mkdir()
        (root / 'devlog' / 'README.md').write_text('# Logs\n\n[Gone](missing.md)\n', encoding='utf-8')
        (root / 'devlog' / 'stray.md').write_text('# Not an entry\n', encoding='utf-8')
        self.assertEqual(v.validate_docs(root), [])
        v.verify_repository(root)

    def test_the_same_repository_with_the_log_on_needs_the_entry(self):
        root, *_ = self.build(keep_log=False)
        (root / 'devlog').mkdir()
        (root / 'devlog' / 'README.md').write_text('# Logs\n', encoding='utf-8')
        for text in ('{"enabled": true}\n', '{}\n'):
            with self.subTest(settings=text):
                (root / ac.DEVLOG).write_text(text, encoding='utf-8')
                self.assertEqual(v.validate_docs(root), [])
                with self.assertRaisesRegex(ValueError, 'Implementation checkpoint needs exactly one indexed log'):
                    v.verify_repository(root)

    def test_everything_but_the_log_is_still_checked(self):
        root, checkpoint, base, _ = self.build(keep_log=False)
        progress = root / rp.PROGRESS
        original = progress.read_text(encoding='utf-8')
        progress.write_text(original.replace(checkpoint, base), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Stale implementation checkpoint'):
            v.verify_repository(root)
        progress.write_text(original, encoding='utf-8')
        report = root / 'docs' / 'REPORT.md'
        report.write_text(report.read_text(encoding='utf-8').replace('OK: 35 checks;', 'OK: 36 checks;'), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Completion evidence no longer matches'):
            v.verify_repository(root)

    def test_a_frozen_change_is_still_refused(self):
        root, *_ = self.build(keep_log=False)
        (root / 'contracts' / 'c.txt').write_text('changed\n', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Frozen content changed'):
            v.verify_repository(root)

    def test_the_default_repository_is_unchanged(self):
        root, checkpoint, _, entry = self.build()
        self.assertTrue(ac.devlog_settings(root)['enabled'])
        self.assertEqual(v.validate_docs(root), [entry])
        _, facts = v.verify_repository(root)
        self.assertEqual(facts['implementation_checkpoint'], checkpoint)


class IsolatedOptOutTest(unittest.TestCase):
    """The integration guard's isolated candidate check honors the opt-out too: it loads the checker alone, with its
    parser, and so reads the setting through the parser and the table of the candidate's own root."""
    ROWS = [('AGENTS.md', 'record', '-', 'yes'), ('README.md', 'record', '-', '-'), ('contracts/', 'runtime', 'frozen', '-'),
            ('devlog/', 'record', '-', '-'), ('docs/', 'record', '-', '-'),
            ('docs/ARCHITECTURE.md', '', '', 'yes'), ('docs/PLAN.md', '', '', 'yes'), ('docs/PROGRESS.md', '', '', 'yes'),
            ('docs/REPORT.md', '', '', 'yes'), ('governance/', 'record', '-', '-'),
            ('governance/CONSTRAINTS.md', '', '', 'yes'), ('governance/DECISIONS.md', '', '', 'yes'),
            ('project-governance/', 'record', '-', '-'), ('src/', 'runtime', 'runtime', '-')]
    PROFILES = {'version': 4, 'profiles': {'record-only': {'checks': [], 'limitations': 'Fixture.'}},
                'impact': {'version': 1, 'invariants': {}, 'symbol_maps': {}},
                'commit_metadata': {'version': 2, 'classes': ['record', 'governance', 'tooling', 'runtime'], 'subclasses': []}}

    def table(self):
        lines = [ac.HEADER, ac.SEPARATOR]
        for path, klass, boundary, reachable in self.ROWS:
            if klass:  # a complete row; the rows without a class inherit everything but Reachable from their folder
                cells = [klass, 'record-only', boundary, '-', '-', '-', reachable, '-']
            else:
                cells = ['', '', '', '', '', '', reachable, '']
            lines.append('| `' + path + '` | ' + ' | '.join(cells) + ' |')
        return '# Fixture classification\n\n' + '\n'.join(lines) + '\n'

    def build(self, keep_log):
        directory = tempfile.TemporaryDirectory(prefix='continuity-isolated-')
        self.addCleanup(directory.cleanup)
        root = Path(directory.name) / 'repo'
        built = build_fixture(root, keep_log=keep_log)
        files = {ac.TABLE: self.table(), ac.POLICY: json.dumps({'version': 4}), ac.PROFILES: json.dumps(self.PROFILES)}
        for name, text in files.items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text(text, encoding='utf-8', newline='\n')
        env = {k: val for k, val in os.environ.items() if not k.startswith('GIT_')}
        env.update(GIT_AUTHOR_NAME='Fixture', GIT_AUTHOR_EMAIL='fixture@example.test',
                   GIT_COMMITTER_NAME='Fixture', GIT_COMMITTER_EMAIL='fixture@example.test')
        for args in (('add', '-A'), ('commit', '-q', '-m', 'Layout')):
            done = subprocess.run(['git', '-C', str(root), *args], capture_output=True, env=env)
            self.assertEqual(done.returncode, 0, done.stderr.decode())
        checker = root / 'governance' / 'tools' / 'continuity'
        checker.mkdir(parents=True)
        for name in ('validate_continuity.py', 'artifact_classification.py'):
            shutil.copyfile(rp.ROOT / rp.TOOLS / 'continuity' / name, checker / name)
        return root, built

    def isolated(self, root):
        return subprocess.run([sys.executable, '-I', '-B', '-c', guard.CANDIDATE_CONTINUITY,
                               str(root / rp.VALIDATE_CONTINUITY), str(root)], capture_output=True, text=True,
                              timeout=300, cwd=root)

    def test_a_repository_that_keeps_a_log_is_checked_as_before(self):
        root, _ = self.build(keep_log=True)
        result = self.isolated(root)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('PASS: candidate committed continuity', result.stdout)
        self.assertNotIn('keeps no devlog', result.stdout)

    def test_a_repository_that_keeps_no_log_needs_no_entry(self):
        root, _ = self.build(keep_log=False)
        result = self.isolated(root)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('the project keeps no devlog', result.stdout)

    def test_the_same_repository_with_the_log_on_fails_without_the_entry(self):
        root, _ = self.build(keep_log=False)
        (root / ac.DEVLOG).write_text('{"enabled": true}\n', encoding='utf-8', newline='\n')
        (root / 'devlog').mkdir()
        (root / 'devlog' / 'README.md').write_text('# Logs\n', encoding='utf-8', newline='\n')
        result = self.isolated(root)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('exactly one indexed log', result.stderr)


if __name__ == '__main__':
    unittest.main()
