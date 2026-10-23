"""Explicit-scope checkpoint transactions; Python 3.13+, Git, standard library only.

The tool is four components that this module composes into one `Checkpoint` class and drives from the command line:
`checkpoint_state` (the repository, scratch store and packet file), `checkpoint_selection` (policy, classification table
and check selection), `checkpoint_evidence` (validation, binding, replay, timing and the seal) and `checkpoint_lifecycle`
(prepare, preview, approval, commit, verify, continuity and finish). The names the tool and its tests have always imported
from this module are re-exported here.
"""
import argparse
import json
import os  # noqa: F401 (the module is part of the facade surface: tests patch c.os)
from pathlib import Path
import subprocess
import sys
import time  # noqa: F401 (the module is part of the facade surface)

# The import root: the repository (for the dotted package imports) and the tools folder (for the bare module names).
ROOT = Path(__file__).resolve().parents[2]
for entry in (ROOT / 'governance' / 'tools', ROOT):  # absolute and resolved, inserted at the front once per process
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

import runtime_guard  # noqa: E402
if __name__ == '__main__':
    runtime_guard.enter('checkpoint')  # first: version check, GIT_* scrub, lifecycle gate

from governance.tools.worktrees import claims
from governance.tools.lifecycle import early_approval
from governance.tools.lifecycle import review_render

# The components, and every name that the tool, its guard and its tests have always imported from this module.
from governance.tools.lifecycle.checkpoint_state import (  # noqa: F401
    CLEANUP, EARLY_USES, HANDOFF, OPERATION_LOCK, PENDING, POLICY, PROFILES, PROJECT_POLICY, PROJECT_PROFILES,
    PROJECT_TABLE, RECEIPT_KEY, REF_POLICY,
    REF_POLICY_V1, REF_POLICY_V2, TABLE, TOLERATED_REF_NAMES, TOLERATED_REF_PREFIXES, TRANSACTION,
    TRANSIENT_REF_PREFIXES, RepoState, atomic, canonical, check_ref_context, digest, exclusive_evidence,
    own_worktree_row, ref_projection, ref_tolerated, require, restamp, run, validate_git_environment)
from governance.tools.lifecycle.checkpoint_selection import Selection
from governance.tools.lifecycle.checkpoint_evidence import CONFIG, TOOLS, IMPACT_REPLAYABLE, REPLAYABLE, Evidence  # noqa: F401
from governance.tools.lifecycle.checkpoint_lifecycle import (  # noqa: F401
    ABANDONABLE, DECISION_CHANNELS, DECISION_CONTROLS, DECISION_TOKENS, Lifecycle)


DRIVER_MODULES = ('checkpoint', 'lifecycle/checkpoint_state', 'lifecycle/checkpoint_selection', 'lifecycle/checkpoint_evidence',
                  'lifecycle/checkpoint_lifecycle')


def driver_paths():
    """The files that make up the checkpoint driver, beside this one."""
    folder = Path(__file__).resolve().parent
    return [folder / (name + '.py') for name in DRIVER_MODULES]


def driver_digest():
    """One digest of the whole driver, so a change to any component changes what a binding or a guard sealed."""
    return digest(canonical({path.name: digest(path.read_bytes()) for path in driver_paths()}))


class Checkpoint(Lifecycle, Evidence, Selection, RepoState):
    """The checkpoint transaction tool: the composition of its four components."""

    def driver_digest(self):
        """The digest every validation binding carries for the driver; one method so a test can substitute the driver."""
        return driver_digest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', required=True, type=Path)
    sub = parser.add_subparsers(dest='action', required=True)
    prepare = sub.add_parser('prepare')
    prepare.add_argument('--validate-only', action='store_true')
    prepare.add_argument('--session-id', help='Declared chat/session identifier when available; never discovered')
    prepare.add_argument('--worker-context', type=Path)
    prepare.add_argument('--job-handle', help='Declared handle of the Job being delivered; shown in the review, never verified')
    for name in ('manifest', 'message', 'profile', 'expected-head', 'expected-branch', 'delegation'):
        prepare.add_argument('--' + name, required=True)
    for name in ('inspect', 'status', 'approve', 'commit', 'verify', 'handoff', 'finish', 'continue-records', 'seal', 'resume'):
        sub.add_parser(name)
    present = sub.add_parser('present', help='Show the sealed review; --text prints only the rendered Markdown')
    present.add_argument('--text', action='store_true')
    estimate = sub.add_parser('estimate', help='Read-only: expected seal time for a manifest, from this machine\'s history')
    estimate.add_argument('--manifest', required=True, type=Path)
    abandon = sub.add_parser('abandon', help='Withdraw a transaction that has not begun to commit')
    abandon.add_argument('--reason', help='optional note, echoed in the result')
    prepare.add_argument('--early-go', type=Path, help='Go record from preview-go; carries the approval only if the sealed result is identical')
    preview = sub.add_parser('preview', help='Read-only: name the exact candidate and whether a go may approve it')
    preview.add_argument('--worker-context', type=Path)
    preview.add_argument('--job-handle', help='Declared handle of the Job being delivered; names the owner of the path claims the preview writes')
    for name in ('manifest', 'message', 'expected-head', 'expected-branch', 'output'):
        preview.add_argument('--' + name, required=True)
    go = sub.add_parser('preview-go', help='Agent-only recording of a direct active-task lead developer go at a preview')
    for name in ('preview', 'go', 'output'):
        go.add_argument('--' + name, required=True, type=Path)
    sub.add_parser('landing', help='Print the landing notice of the last finished transaction')
    consent = sub.add_parser('approve-consent', help='Agent-only recording of a direct active-task lead developer approve/reject decision')
    consent.add_argument('--consent', required=True, type=Path)
    args = parser.parse_args()
    try:
        context_file = getattr(args, 'worker_context', None)
        worker_context = json.loads(context_file.read_text(encoding='utf-8-sig')) if context_file else None
        tool = Checkpoint(args.repo)
        if args.action == 'estimate':
            # Read-only and lock-free, so it also works while another seal is running.
            print(json.dumps(tool.estimate(json.loads(args.manifest.read_text(encoding='utf-8-sig'))), indent=2))
            return 0
        if args.action == 'preview':
            review, record, readable = {}, Path(args.output), Path(args.output).with_suffix('.md')
            require(readable != record, 'The preview output must not end in .md: the readable preview is written there')
            result = tool.preview(json.loads(Path(args.manifest).read_text(encoding='utf-8-sig')), args.message,
                                  worker_context, args.expected_head, args.expected_branch, args.job_handle, review)
            text = review_render.render_preview(dict(review, body=result['body'])).encode('utf-8')
            early_approval.write_new(record, canonical(result))
            early_approval.write_new(readable, text)  # Beside the record, which stays the machine record.
            sys.stdout.buffer.write(text)
            return 0
        if args.action == 'preview-go':
            result = early_approval.record_go(tool, json.loads(args.preview.read_text(encoding='utf-8-sig')),
                                              json.loads(args.go.read_text(encoding='utf-8-sig')))
            early_approval.write_new(args.output, canonical(result))
            print(json.dumps({'go': result['body']['go']['response'], 'preview': result['body']['preview']['id'],
                              'tree': result['body']['preview']['tree'], 'output': str(args.output)}, indent=2))
            return 0
        if args.action == 'landing':
            sys.stdout.buffer.write(tool.landing().encode('utf-8'))
            return 0
        if args.action == 'prepare':
            try:  # Best effort: say how long this is expected to take before the checks start.
                print('estimate: ' + tool.estimate(json.loads(Path(args.manifest).read_text(encoding='utf-8-sig')))['summary'],
                      file=sys.stderr)
            except (ValueError, OSError, KeyError, TypeError):
                pass
        with tool.lock(), claims.command_window(tool, args.action, getattr(args, 'job_handle', None)):
            if args.action == 'prepare':
                result = tool.prepare(json.loads(Path(args.manifest).read_text(encoding='utf-8-sig')),
                                      args.message, args.profile, args.expected_head, args.expected_branch, args.delegation,
                                      validate_only=getattr(args, 'validate_only', False), worker_context=worker_context,
                                      session_id=getattr(args, 'session_id', None),
                                      job_handle=getattr(args, 'job_handle', None),
                                      early_go=json.loads(args.early_go.read_text(encoding='utf-8-sig')) if args.early_go else None)
            elif args.action in ('seal', 'resume'):
                result = tool.seal()
            elif args.action == 'present':
                result = tool.present()
                if args.text:
                    require('review' in result, 'Review rendering failed: ' + result.get('review_error', 'unknown'))
                    sys.stdout.buffer.write(result['review'].encode('utf-8'))
                    return 0
            elif args.action == 'abandon':
                result = tool.abandon(args.reason)
            elif args.action == 'approve-consent':
                result = tool.approve_consent(json.loads(args.consent.read_text(encoding='utf-8-sig')))
            elif args.action == 'approve':
                require(sys.stdin.isatty() and sys.stdout.isatty(), 'Approval requires an interactive lead developer terminal')
                result = tool.approve(input)
            elif args.action == 'finish':
                result = tool.finish()
            elif args.action == 'continue-records':
                result = tool.continue_records()
            elif args.action == 'commit':
                result = tool.commit()
            elif args.action in ('verify', 'handoff'):
                result = tool.verify()
            else:
                result = tool.inspect()
            print(json.dumps(result, indent=2, ensure_ascii=True))
        return 0
    except (ValueError, OSError, KeyError, TypeError, StopIteration, subprocess.SubprocessError) as error:
        print('FAIL: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
