"""Deleted paths in a checkpoint: their rows move to Historical paths, an ignore rule may cover them, and an
abandoned prepare leaves nothing that stops the owner removing its worktree. Disposable repositories only."""
from contextlib import redirect_stdout
import io
import subprocess
import unittest

from governance.tools.continuity import artifact_classification as ac
import checkpoint as c
from governance.tools.lifecycle import early_approval as ea
from governance.tests.lifecycle import test_checkpoint as fixtures
from governance.tests.worktrees import test_checkpoint_worktrees as lifecycle
from governance.tools.worktrees import guard as w

BLOCKED = 'No reviewed validation scope'


def table_with(rows, historical=()):
    """The fixture table plus a Historical paths section: rows are (path, class, profile, boundary) tuples."""
    text = fixtures.fixture_table(sorted(rows))
    if historical:
        text += '\n' + '\n'.join([ac.HISTORICAL_HEADER, ac.HISTORICAL_SEPARATOR] +
                                 ['| `%s` | %s | - |' % row for row in sorted(historical)]) + '\n'
    return text


class DeletedPathTest(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.CheckpointTest()
        self.addCleanup(self.f.doCleanups)
        self.f.setUp()
        self.root, self.tool = self.f.root, self.f.tool
        self.policy, self.profiles = self.tool.data()
        self.rows = list(fixtures.FIXTURE_ROWS)

    def track(self, path, klass='tooling', profile='test', boundary='-'):
        """Commit a file with a live exact row, as the repository holds it before a transaction deletes it."""
        self.rows.append((path, klass, profile, boundary))
        (self.root / c.TABLE).write_text(table_with(self.rows), encoding='utf-8', newline='\n')
        (self.root / path).parent.mkdir(parents=True, exist_ok=True)
        (self.root / path).write_text('content\n')
        self.f.git('add', '--', path, c.TABLE)
        self.f.git('commit', '-m', 'Track ' + path)
        self.f.parent = self.f.git('rev-parse', 'HEAD').strip()

    def move_row(self, path, klass='tooling', delete=True):
        """What the documented one-transaction move does: the row goes to Historical paths, the file goes away."""
        live = [r for r in self.rows if r[0] != path]
        (self.root / c.TABLE).write_text(table_with(live, [(path, klass)]), encoding='utf-8', newline='\n')
        if delete:
            (self.root / path).unlink()

    def select(self, manifest):
        return self.tool.select_profile(manifest, self.policy, self.profiles, 'auto')

    def prepare(self, manifest):
        return self.tool.prepare(manifest, self.f.message, 'test', self.f.parent, 'refs/heads/main',
                                 'Lead developer delegated this exact fixture scope')

    # (a) a deletion or a rename whose row moved to Historical paths is classified and validated
    def test_a_deleted_path_whose_row_moved_is_scoped_by_its_head_row(self):
        self.track('gone.txt')
        self.move_row('gone.txt')
        name, profile = self.select(['gone.txt'])
        self.assertEqual((name, profile['classification'], profile['path_profiles']),
                         ('test', {'gone.txt': 'tooling'}, {'gone.txt': 'test'}))
        self.assertEqual({v['id'] for v in profile['checks']}, {'continuity', 'regressions'})

    def test_a_deletion_is_sealed_with_the_sensitivity_of_its_head_row(self):
        self.track('gone.txt')
        self.move_row('gone.txt')
        packet = self.prepare(['gone.txt'])
        self.assertEqual(packet['state'], 'prepared')
        self.assertEqual(self.tool.inventory(), ['gone.txt'])
        view = packet['profile_data']['artifact_classification']['gone.txt']
        self.assertEqual((view['class'], view['profile'], view['deleted'], view['approval_sensitivity']),
                         ('tooling', 'test', True, 'low'))
        self.assertEqual((view['head_sensitivity'], view['candidate_sensitivity']), ('low', 'none'))
        for reach in ('low', 'medium'):
            self.assertEqual(ea.sealed_reasons(packet, reach), [])
        head = self.tool.head_table(self.profiles)
        self.assertEqual(self.tool.table(self.policy).ineligible_paths(['gone.txt'], 'medium', head), [])
        self.assertEqual(self.tool.table(self.policy).ineligible_paths(['gone.txt'], 'medium', None), ['gone.txt'])

    def test_a_move_deletes_the_old_path_and_adds_the_new_one_in_one_transaction(self):
        self.track('gone.txt')
        self.move_row('gone.txt')
        (self.root / 'new-component').mkdir()
        (self.root / 'new-component/moved.txt').write_text('content\n')
        name, profile = self.select(['gone.txt', 'new-component/moved.txt'])
        self.assertEqual(profile['classification'], {'gone.txt': 'tooling', 'new-component/moved.txt': 'tooling'})
        packet = self.prepare(['gone.txt', 'new-component/moved.txt'])
        self.assertEqual(self.tool.inventory(), ['gone.txt', 'new-component/moved.txt'])
        with redirect_stdout(io.StringIO()):
            self.tool.approve(lambda prompt: prompt.split('Type exactly: ')[1].split('\n')[0])
        self.assertEqual(self.tool.commit()['state'], 'verified')
        self.assertEqual(self.f.git('ls-files', '--', 'gone.txt', 'new-component/moved.txt').split(),
                         ['new-component/moved.txt'])
        self.assertEqual(packet['manifest'], ['gone.txt', 'new-component/moved.txt'])

    def test_a_deleted_governance_path_keeps_its_class(self):
        self.track('elsewhere/old.md', 'governance')
        self.move_row('elsewhere/old.md', 'governance')
        name, profile = self.select(['elsewhere/old.md'])
        self.assertEqual(profile['classification'], {'elsewhere/old.md': 'governance'})

    def test_the_estimate_and_the_preview_derive_the_same_selection(self):
        self.track('gone.txt')
        self.move_row('gone.txt')
        name, profile, runtime = self.tool.derive_selection(
            ['gone.txt'], 'Subject\n', self.policy, self.profiles, None, 'auto', 'refs/heads/main')
        self.assertEqual((name, runtime), ('test', False))

    # (d) everything else stays blocked
    def test_a_live_file_with_only_a_historical_row_stays_blocked(self):
        self.track('gone.txt')
        self.move_row('gone.txt', delete=False)
        with self.assertRaisesRegex(ValueError, BLOCKED):
            self.select(['gone.txt'])

    def test_a_deleted_path_with_no_row_anywhere_stays_blocked(self):
        self.track('gone.txt')
        (self.root / c.TABLE).write_text(table_with([r for r in self.rows if r[0] != 'gone.txt']),
                                         encoding='utf-8', newline='\n')
        (self.root / 'gone.txt').unlink()
        with self.assertRaisesRegex(ValueError, BLOCKED):
            self.select(['gone.txt'])

    def test_a_historical_row_without_a_head_row_stays_blocked(self):
        for name in ('orphan.txt', 'ghost.txt'):  # tracked without any row at HEAD, and never tracked at all
            (self.root / name).write_text('x\n')
        self.f.git('add', '--', 'orphan.txt')
        self.f.git('commit', '-m', 'Track a file the table never classified')
        (self.root / 'ghost.txt').unlink()
        (self.root / 'orphan.txt').unlink()
        (self.root / c.TABLE).write_text(table_with(self.rows, [('ghost.txt', 'tooling'), ('orphan.txt', 'tooling')]),
                                         encoding='utf-8', newline='\n')
        for name in ('orphan.txt', 'ghost.txt'):
            with self.subTest(name), self.assertRaisesRegex(ValueError, BLOCKED):
                self.select([name])

    def test_a_move_cannot_lower_the_class_a_path_had(self):
        self.track('gone.txt', 'governance')
        self.move_row('gone.txt', 'record')
        with self.assertRaisesRegex(ValueError, BLOCKED):
            self.select(['gone.txt'])

    def test_a_deleted_runtime_boundary_or_blocked_path_stays_blocked(self):
        self.track('gone-runtime.txt', 'runtime', 'test', 'runtime')
        self.move_row('gone-runtime.txt', 'runtime')
        with self.assertRaisesRegex(ValueError, BLOCKED):
            self.select(['gone-runtime.txt'])
        self.track('gone-blocked.txt', 'tooling', 'blocked')
        self.move_row('gone-blocked.txt')
        with self.assertRaisesRegex(ValueError, BLOCKED):
            self.select(['gone-blocked.txt'])

    def test_a_malformed_or_duplicate_historical_row_stops_the_tools(self):
        self.track('gone.txt')
        self.move_row('gone.txt')
        good = (self.root / c.TABLE).read_text(encoding='utf-8')
        for bad in (good.replace('| tooling | - |', '| nonsense | - |'),
                    good.replace('| tooling | - |', '| tooling | - | extra |'),
                    good + '| `gone.txt` | tooling | - |\n'):
            (self.root / c.TABLE).write_text(bad, encoding='utf-8', newline='\n')
            with self.subTest(bad[-60:]), self.assertRaises(ValueError):
                self.select(['gone.txt'])

    def test_a_live_row_for_the_deleted_path_keeps_the_old_behavior(self):
        self.track('gone.txt')
        (self.root / 'gone.txt').unlink()  # the row stays live: scoped by it, as before
        self.assertEqual(self.select(['gone.txt'])[1]['classification'], {'gone.txt': 'tooling'})

    # (b) a deletion that an ignore rule matches is accepted in the same transaction as the rule
    def test_a_tracked_deletion_matched_by_an_ignore_rule_is_accepted(self):
        self.track('gone.txt')
        (self.root / '.gitignore').write_text('ignored/\n__pycache__/\ntmp/\ngone.txt\n')
        (self.root / 'gone.txt').unlink()
        self.assertEqual(self.tool.paths(['gone.txt', '.gitignore'], self.policy), ['.gitignore', 'gone.txt'])

    def test_a_deletion_and_its_ignore_rule_are_prepared_in_one_transaction(self):
        self.track('gone.txt')
        (self.root / '.gitignore').write_text('ignored/\n__pycache__/\ntmp/\ngone.txt\n')
        (self.root / 'gone.txt').unlink()
        self.rows.append(('.gitignore', 'tooling', 'test', '-'))
        (self.root / c.TABLE).write_text(table_with(self.rows), encoding='utf-8', newline='\n')
        packet = self.prepare(['.gitignore', 'gone.txt'])
        self.assertEqual(packet['state'], 'prepared')
        self.assertEqual(self.tool.inventory(), ['.gitignore', 'gone.txt'])

    def test_an_ignored_file_that_exists_or_is_untracked_is_still_refused(self):
        self.track('gone.txt')
        (self.root / '.gitignore').write_text('ignored/\n__pycache__/\ntmp/\ngone.txt\nprivate.txt\n')
        (self.root / 'gone.txt').write_text('still here and edited\n')
        (self.root / 'private.txt').write_text('never tracked\n')
        for name in ('gone.txt', 'private.txt'):
            with self.subTest(name), self.assertRaisesRegex(ValueError, 'Ignored or uncheckable'):
                self.tool.paths([name], self.policy)
        with self.assertRaisesRegex(ValueError, 'tracked deletion'):  # an untracked absent path is still no deletion
            (self.root / 'nothing.txt').parent.mkdir(exist_ok=True)
            self.tool.paths(['nothing.txt'], self.policy)


class AbandonedPrepareRemovalTest(lifecycle.WorktreeTest):
    """The lifecycle fixtures of the worktree tests, with the abandoned-prepare case added."""

    def discard_edit(self):
        """Abandon leaves the working bytes alone, so the owner puts its own edit back before removing."""
        subprocess.check_call(['git', '-C', str(self.path), 'checkout', '--', 'a.txt'])

    def spent_go(self, tool):
        tool.ensure_key()
        ea.save_uses(tool, {'a' * 64: {'attempts': 1, 'committed': False}})
        self.assertTrue((tool.store / c.EARLY_USES).is_file())

    def test_removal_after_an_abandoned_prepare_with_a_spent_go_record(self):
        t, p = self.prepared()
        self.spent_go(t)
        self.assertEqual(t.abandon('Candidate restructured')['state'], 'idle')
        self.discard_edit()
        self.assertEqual(sorted(x.name for x in t.store.iterdir()), [c.EARLY_USES, c.RECEIPT_KEY])
        result = self.flow.remove(self.path, self.owner, self.base, self.base)
        self.assertEqual(result['state'], 'removed')
        self.assertFalse(self.path.exists())

    def test_removal_still_refuses_live_scratch_beside_the_spent_record(self):
        t = self.create()
        self.spent_go(t)
        for name in (c.TRANSACTION, 'unknown.json'):
            (t.store / name).write_text('{}')
            with self.subTest(name), self.assertRaisesRegex(ValueError, 'scratch not idle'):
                self.flow.remove(self.path, self.owner, self.base, self.base)
            (t.store / name).unlink()
        self.assertEqual(self.flow.remove(self.path, self.owner, self.base, self.base)['state'], 'removed')

    def test_integration_still_refuses_a_spent_record(self):
        t, p = self.prepared()
        self.spent_go(t)
        t.abandon()
        self.discard_edit()
        with self.assertRaisesRegex(ValueError, 'scratch not idle'):
            self.flow.integrate(self.path, self.owner, self.base, self.base)


# Only the added tests belong to this file; the inherited lifecycle tests already run in their own module.
for _name in [n for n in dir(lifecycle.WorktreeTest) if n.startswith('test_')]:
    setattr(AbandonedPrepareRemovalTest, _name, None)


if __name__ == '__main__':
    unittest.main()
