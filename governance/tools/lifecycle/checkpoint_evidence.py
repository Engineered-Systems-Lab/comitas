"""Validation evidence: running and replaying checks, input binding, timing and the seal.

One of the four components of the checkpoint tool; governance/tools/checkpoint.py composes them.
"""
from governance.tools.continuity import artifact_classification
from governance.tools.lifecycle import early_approval
import os
from pathlib import Path
import secrets
import shutil
import sys
from governance.tools.support import timing_record
from governance.tools.lifecycle.checkpoint_state import REF_POLICY_V1, REF_POLICY_V2, canonical, check_ref_context, digest, ref_projection, require, restamp


# Only disposable, repository-contained regression suites have replayable semantics. Each names the folders its tests
# are discovered in: the tests folder holds the tests of the tools, the configuration the tests that pin this project.
TOOLS = artifact_classification.TOOLS
TESTS = artifact_classification.TESTS

CONFIG = artifact_classification.CONFIG

REPLAYABLE = {
    'governance-regressions': ((TESTS, CONFIG), 'test_checkpoint*.py'),
    'continuity-regressions': ((TESTS, CONFIG), 'test_continuity.py'),
}

# Exact disposable group commands; configuration cannot grant arbitrary replay.
IMPACT_REPLAYABLE = {
    'checkpoint-selector': ((CONFIG,), 'test_checkpoint_scope.py', None),
    'checkpoint-evidence': ((TESTS,), 'test_checkpoint_evidence.py', None),
    'checkpoint-reconcile': ((TESTS,), 'test_checkpoint_reconcile.py', None),
    'checkpoint-worktrees': ((TESTS,), 'test_checkpoint_worktrees.py', None),
    'checkpoint-invariants': ((TESTS,), 'test_checkpoint.py', 'CheckpointTest'),
}


class Evidence:
    def checks(self, checks):
        results = []
        for check in checks:
            argv = [sys.executable if a == '{python}' else a for a in check['argv']]
            result = self.runner(argv, self.root)
            results.append(dict(result, id=check['id']))
        return results

    def validation_binding(self, packet, check, content=False):
        """Conservative exact-input binding, private to this checkpoint transaction."""
        git = shutil.which('git')
        require(git, 'Git executable unavailable')
        inputs = {
            'transaction': packet['id'], 'repo': self.identity(),
            'parent': self.head(), 'branch': self.branch(),
            'manifest': packet['manifest'], 'tree': self.git('write-tree').decode().strip(),
            'index': digest(self.git('ls-files', '--stage', '-z')),
            'working': self.snapshot(packet['manifest'], packet['policy']),
            'configuration': self.configuration(), 'profile': packet['profile_data'],
            'policy': packet['policy'], 'check': check,
            'python': [sys.executable, sys.version, digest(Path(sys.executable).read_bytes())],
            'driver_sha256': self.driver_digest(),
            'git': [git, digest(Path(git).read_bytes()), self.git('--version').decode()],
            'environment': dict(os.environ),
            'refs': (digest(canonical(ref_projection(restamp(self.ref_snapshot(), packet['validation_ref_context']))))
                     if packet.get('ref_policy') else digest(self.git('show-ref'))),
            'status': self.status(),
        }
        if content:
            # Reviewed disposable suites read tooling/policy and authored inputs,
            # not live HEAD/history. Continuity regressions clone history: keep
            # their original base-sensitive binding. Unknown suites never replay.
            names = set(packet['manifest']) | set(self.bound_paths(packet['policy']))
            names.update(('.gitignore', '.gitattributes'))
            inputs['working'] = {n: h for n, h in inputs['working'].items()
                                 if n in names or n.startswith(artifact_classification.TOOLING_PREFIXES)}
            for name in ('parent', 'tree', 'index', 'refs', 'status'):
                del inputs[name]
            inputs['binding_kind'] = 'content-v1'
        return digest(canonical(inputs))

    def replayable(self, check):
        found = IMPACT_REPLAYABLE.get(check['id'])
        if found is None and check['id'] in REPLAYABLE:
            found = (*REPLAYABLE[check['id']], None)
        if found is None:
            return False
        starts, pattern, test_filter = found
        tail = [word for start in starts for word in ('-s', start)] + ['-p', pattern, '-v'] +                (['-k', test_filter] if test_filter else [])
        # The same discovery, run by unittest itself (one folder only) or by the test runner; a packet sealed before
        # the runner keeps the unittest command.
        forms = [['{python}', '-B', TOOLS + '/run_tests.py'] + tail]
        if len(starts) == 1:
            forms.append(['{python}', '-B', '-m', 'unittest', 'discover'] + tail)
        return check['argv'] in forms

    def timing(self, policy):
        """(record, settings) when the policy enables the machine-local timing record, otherwise None."""
        cfg = timing_record.settings(policy)
        return None if cfg is None else (timing_record.Record(self.common), cfg)

    def record_timings(self, packet, timed, validation_run, phase):
        """Advisory only: flag departures from history, then append this run. Never alters validation."""
        try:
            found = self.timing(packet['policy'])
            if found is None:
                return
            record, cfg = found
            timed = [(i, s, ok) for i, s, ok in timed if type(s) in (int, float)]
            token, history = timing_record.machine(), record.entries()
            packet['timing_observations'] = timing_record.observations(
                history, [('check:' + i, s, ok) for i, s, ok in timed], token, cfg)
            context = {'parent': packet['parent'], 'profile': packet['profile'], 'paths': len(packet['manifest']),
                       'transaction': packet['id'], 'candidate_tree': packet.get('tree'),
                       'worker': packet['profile_data'].get('worker_context'),
                       'session_id': packet.get('session_id'), 'attribution': 'declared',
                       'phase': phase, 'sealed_digest': None,
                       'python': {'executable': sys.executable, 'version': sys.version,
                                  'sha256': digest(Path(sys.executable).read_bytes())}}
            executed = {e['id']: e for e in packet['evidence']}
            record.append([timing_record.entry('check:' + i, s, ok, validation_run, token,
                           dict(context, argv=executed[i]['argv'])) for i, s, ok in timed])
        except (ValueError, OSError, KeyError, TypeError) as error:
            packet['timing_observations'] = {'unavailable': str(error)}

    def sealed_estimate(self, policy, checks):
        """The expected duration, captured before this run's own times enter the history."""
        found = self.timing(policy)
        if found is None:
            return None
        try:
            record, cfg = found
            subjects = ['check:staged-whitespace'] + ['check:' + c['id'] for c in checks]
            result = timing_record.estimate(record.entries(), subjects, timing_record.machine(), cfg)
            return {'basis': result['basis'], 'expected_seconds': result['expected_seconds'],
                    'high_seconds': result['high_seconds'], 'gaps': result['gaps'],
                    'summary': timing_record.describe(result),
                    'checks': {r['subject'].split(':', 1)[1]: {'mean': r['mean'], 'n': r['n'], 'basis': r['basis']}
                               for r in result['checks']}}
        except (ValueError, OSError, KeyError, TypeError) as error:
            return {'unavailable': str(error)}

    def estimate(self, manifest, requested='auto'):
        """Read-only: how long sealing this manifest should take, with an honest basis. Stages nothing."""
        policy, profiles = self.data()
        found = self.timing(policy)
        require(found is not None, 'timing_record is not enabled in the policy')
        manifest = self.paths(manifest, policy)
        name, profile = self.select_profile(manifest, policy, profiles, requested)
        record, cfg = found
        subjects = ['check:staged-whitespace'] + ['check:' + c['id'] for c in profile['checks']]
        result = timing_record.estimate(record.entries(), subjects, timing_record.machine(), cfg)
        return dict(result, profile=name, paths=len(manifest), summary=timing_record.describe(result))

    def validate_packet(self, packet, seal):
        self.assert_prepared(packet)
        self.assert_selection_current(packet)
        checks = self.check_commands(packet['profile_data'], packet['policy'], packet['runtime'])
        refs_before = self.ref_snapshot(include_main=False)
        ref_audits = packet.setdefault('transient_ref_audits', [])
        if packet.get('ref_policy'):
            check_ref_context(packet['validation_ref_context'], refs_before,
                              'Refs changed since preparation', ref_audits)
        old = packet.get('evidence', [])
        old = old if isinstance(old, list) else []
        packet['evidence'] = []
        packet['validation_runs'] = packet.get('validation_runs', [])
        validation_run = secrets.token_hex(16)
        whitespace = self.runner(['git', 'diff', '--cached', '--check'], self.root)
        packet['evidence'].append(dict(whitespace, id='staged-whitespace'))
        timed = [('staged-whitespace', whitespace.get('duration_seconds'), whitespace.get('exit_code') == 0)]
        self.save(packet)
        for check in checks:
            if not self.replayable(check):
                result = self.checks([check])[0]
                timed.append((check['id'], result.get('duration_seconds'), result.get('exit_code') == 0))
                packet['evidence'].append(result)
                packet['validation_runs'].append({'id': check['id'], 'action': 'executed', 'run_id': validation_run})
                self.save(packet)
                continue
            content = check['id'] == 'governance-regressions' or check['id'] in IMPACT_REPLAYABLE
            binding = self.validation_binding(packet, check, content=content)
            matches = [e for e in old if isinstance(e, dict) and e.get('id') == check['id']]
            prior = matches[0] if len(matches) == 1 else {}
            result = prior.get('result', {})
            result = result if isinstance(result, dict) else {}
            reusable = (self.replayable(check) and prior.get('binding') == binding
                        and prior.get('result_sha256') == digest(canonical(result))
                        and type(result.get('exit_code')) is int and result['exit_code'] == 0
                        and result.get('argv') == [sys.executable if a == '{python}' else a
                                                  for a in check['argv']]
                        and all(isinstance(result.get(k), str) for k in ('stdout', 'stderr'))
                        and type(result.get('duration_seconds')) in (int, float)
                        and result['duration_seconds'] >= 0)
            if not reusable:
                result = self.checks([check])[0]
                timed.append((check['id'], result.get('duration_seconds'), result.get('exit_code') == 0))
                require(self.validation_binding(packet, check, content=content) == binding,
                        'Validation inputs changed during execution; evidence cannot be reused')
            entry = dict(result, id=check['id'], binding=binding,
                         binding_kind='content-v1' if content else {REF_POLICY_V2: 'base-v3', REF_POLICY_V1: 'base-v2'}.get(packet.get('ref_policy'), 'base-v1'), result=result,
                         result_sha256=digest(canonical(result)))
            packet['evidence'].append(entry)
            packet['validation_runs'].append({'id': check['id'], 'action': 'reused' if reusable else 'executed',
                                               'binding': binding, 'result_sha256': entry['result_sha256'], 'run_id': validation_run})
            self.save(packet)  # A crash retains every completed result, never an in-flight PASS.
        refs_after = self.ref_snapshot(include_main=False)
        audits = packet.setdefault('transient_ref_audits', [])
        check_ref_context(restamp(refs_before, packet.get('validation_ref_context', {})), refs_after,
                          'Refs changed during validation', audits)
        self.record_timings(packet, timed, validation_run, 'seal-validation' if seal else 'validation-only')
        self.assert_prepared(packet)
        require(self.validation_ok(packet), 'Validation failed; see sealed evidence')
        packet['state'] = 'prepared' if seal else 'validated'
        if seal:
            early_approval.decide(self, packet)  # A carried go approves the exact candidate; otherwise stay prepared.
        self.save(packet)
        return packet

    def seal(self):
        packet = self.assert_pinned(self.load())
        require(packet['state'] in ('preparing', 'validated', 'prepare-failed'),
                'Seal/resume requires an unapproved validation transaction')
        require(all(k in packet for k in ('tree', 'index', 'working', 'prepared_status')),
                'Interrupted staging requires operator inspection, not automatic recovery')
        return self.validate_packet(packet, True)

    def assert_prepared(self, packet):
        require(self.configuration() == packet['configuration'], 'Stale Git configuration')
        self.assert_prepared_repository(packet)

    def assert_prepared_repository(self, packet):
        """Check the sealed repository facts independently of the approver's configuration."""
        self.quiet_operation()
        require(self.head() == packet['parent'] and self.branch() == packet['branch'], 'Stale HEAD/branch')
        require(self.inventory() == packet['manifest'], 'Stale staged manifest inventory')
        require(self.git('write-tree').decode().strip() == packet['tree'], 'Stale staged tree')
        require(digest(self.git('ls-files', '--stage', '-z')) == packet['index'], 'Stale index entries')
        require(self.snapshot(packet['manifest'], packet['policy']) == packet['working'],
                'Stale working content/message/policy/profile/tool; re-prepare and re-approve')
        require(digest(Path(packet['message_file']).read_bytes()) == packet['message_sha256'], 'Stale message file')
        require(self.status() == packet['prepared_status'], 'Stale unrelated/untracked status')
