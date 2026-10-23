"""What a manifest is and which checks it needs: policy, table, profile and impact selection.

One of the four components of the checkpoint tool; governance/tools/checkpoint.py composes them.
"""
from governance.tools.continuity import artifact_classification
import ast
import base64
from governance.tools.lifecycle.commit_metadata import parse_metadata, refine_selection, validate_worker_context
from governance.tools.support import link_retarget
from pathlib import PurePosixPath, PureWindowsPath
import subprocess
from governance.tools.lifecycle.checkpoint_state import canonical, digest, require


class Selection:
    def attach_classification(self, manifest, policy, profiles, profile):
        """Seal each manifest path's resolved table classification, which must agree with the policy."""
        table = self.table(policy, profiles)
        head = self.head_table(profiles)
        view = {}
        for path in manifest:
            entry = artifact_classification.view(table, path, head)
            if not entry['resolved']:
                before = self.deleted_scope(path, table, profiles)
                if before is not None:
                    entry = artifact_classification.deleted_view(table, path, before)
            changes = profile.get('link_retarget', {}).get(path)
            require(entry['resolved'] and (changes or (entry['class'] == profile['classification'][path] and
                                                       entry['profile'] == profile['path_profiles'][path])),
                    'Artifact classification table disagrees with the checkpoint selection for ' + path)
            view[path] = dict(entry, link_retarget=changes) if changes else entry
        return dict(profile, artifact_classification=view)

    def derive_selection(self, manifest, message_text, policy, profiles, worker_context, requested, branch):
        """The one pipeline that turns a manifest and message into the sealed selection.

        Returns (profile name, profile data, runtime). `prepare` seals the result, and `assert_selection_current`
        derives it again from the live rules to prove that a reconciled transaction still matches them.
        """
        table = self.table(policy, profiles)
        for path in manifest:  # A path can become frozen without changing its class.
            require(not table.in_boundary(path, 'frozen'), 'Frozen path cannot enter a checkpoint: ' + path)
        head = self.head_table(profiles)
        runtime = any(self.in_runtime(n, table, head) for n in manifest)
        name, profile = self.select_profile(manifest, policy, profiles, requested)
        profile = self.attach_classification(manifest, policy, profiles, profile)
        if policy.get('commit_metadata_required'):
            metadata = parse_metadata(message_text, profiles)
            context = validate_worker_context(metadata, worker_context)
            if context.get('version') == 1:
                require(context['transaction'] == branch, 'Worker bootstrap transaction mismatch')
            profile = refine_selection(metadata, profile, policy, profiles)
            profile['worker_context'] = context
        return name, profile, runtime

    def assert_selection_current(self, packet):
        """Refuse a packet whose sealed selection differs from what the live rules derive for it.

        Preparation derives the selection once and reconciliation moves the base without deriving it again, so a
        classification, policy or profile change upstream could otherwise reach `prepared` under the old rules.
        The derivation uses only the sealed manifest, message and worker context, and the live rules and files.
        """
        stale = ('The classification or selection rules changed since preparation; '
                 'abandon this transaction and prepare again from the current base')
        try:
            policy, profiles = self.data()
            name, profile, runtime = self.derive_selection(
                packet['manifest'], base64.b64decode(packet['message_b64']).decode('utf-8'), policy, profiles,
                packet['profile_data'].get('worker_context'), 'auto', packet['branch'])
        except (ValueError, KeyError, TypeError, OSError) as error:
            raise ValueError(stale + ': ' + str(error)) from error
        require(digest(canonical([name, profile, runtime, policy])) ==
                digest(canonical([packet['profile'], packet['profile_data'], packet['runtime'], packet['policy']])),
                stale)

    def data(self):
        """The policy and the profiles of the working tree: the framework's files plus the project's, when it has them."""
        policy = artifact_classification.load_policy(self.root)
        profiles = artifact_classification.load_profiles(self.root)
        require(policy['version'] == profiles['version'] == 4, 'Unsupported policy/profile version')
        artifact_classification.check_project_impact(artifact_classification.file_reader(self.root), policy)
        return policy, profiles

    def table(self, policy, profiles=None):
        """The classification table that decides scope; it is the only source of path facts."""
        if profiles is None:
            profiles = artifact_classification.load_profiles(self.root)
        return artifact_classification.load_table(self.root, profiles)

    def bound_paths(self, policy):
        """Files whose bytes are sealed into approvals even when they are not in the manifest."""
        return self.table(policy).bound_paths()

    def paths(self, manifest, policy):
        require(isinstance(manifest, list) and manifest, 'Manifest must be a nonempty JSON path array')
        require(all(isinstance(p, str) for p in manifest), 'Manifest paths must be strings')
        require(len({p.casefold() for p in manifest}) == len(manifest), 'Duplicate manifest entries')
        tracked = set(self.git('ls-files', '-z').decode('utf-8').split('\0'))
        table = self.table(policy)
        for name in manifest:
            parts = PurePosixPath(name).parts
            require(name and not any(c in name for c in '\\:*?[]\x00\n\r\t') and
                    not name.startswith(('/', '-')) and name == '/'.join(parts) and
                    all(p not in ('.', '..') and not p.endswith(('.', ' ')) and
                        not PureWindowsPath(p).is_reserved() for p in parts),
                    'Invalid/traversal manifest path: ' + name)
            path = self.root / name
            require(path.resolve().is_relative_to(self.root), 'Outside repository: ' + name)
            require(not any((self.root.joinpath(*parts[:i])).is_symlink()
                            for i in range(1, len(parts) + 1)), 'Symlink manifest path: ' + name)
            require(not any(p.casefold() in policy['prohibited_components'] for p in parts),
                    'Transient/prohibited manifest path: ' + name)
            require(not table.in_boundary(name, 'frozen'), 'Frozen path cannot enter a checkpoint: ' + name)
            ignored = subprocess.run(['git', '-C', str(self.root), 'check-ignore', '--no-index', '-q', '--', name])
            # A tracked deletion adds no content, so an ignore rule that matches it, such as one added in this very
            # transaction to cover files being untracked, does not refuse it. An uncheckable path still does.
            deletion = not path.exists() and name in tracked
            require(ignored.returncode == 1 or (deletion and ignored.returncode == 0),
                    'Ignored or uncheckable manifest path: ' + name)
            require(path.is_file() or deletion,
                    'Manifest must name a file or tracked deletion: ' + name)
        return sorted(manifest)

    def select_profile(self, manifest, policy, profiles, requested):
        """Derive the exact check union from every explicit path, including deletions."""
        require(manifest, 'Empty validation scope')
        selected = set()
        classification = {}
        path_profiles = {}
        table = self.table(policy, profiles)
        head = self.head_table(profiles)
        retargets = self.link_retargets(manifest, policy, table)
        for path in manifest:
            if path in retargets:
                # A reviewed link-only edit needs continuity only, which proves every new link resolves.
                category, name = 'record', 'record-only'
            else:
                scope = table.scope(path)
                if scope is None:  # A deletion whose row moved to Historical paths is validated by its HEAD row.
                    before = self.deleted_scope(path, table, profiles)
                    scope = before and (before.klass, before.profile)
                require(scope and scope[1] != 'blocked',
                        'No reviewed validation scope for path; review the classification table before preparation: ' + path)
                category, name = scope
            require(name in profiles['profiles'], 'Unknown scope profile: ' + name)
            classification[path] = category
            path_profiles[path] = name
            selected.add(name)
        # Records add no gates beyond the mandatory continuity check.
        if len(selected) > 1:
            selected.discard('record-only')
        names = sorted(selected)
        name = '+'.join(names)
        require(requested in ('auto', name), 'Profile must match manifest scope: ' + name)
        checks = {}
        for item in names:
            for check in self.check_commands(profiles['profiles'][item], policy, False):
                require(check['id'] not in checks or checks[check['id']] == check,
                        'Conflicting scope commands: ' + check['id'])
                checks[check['id']] = check
        profile = {'checks': list(checks.values()), 'classification': classification,
                   'limitations': ' '.join(profiles['profiles'][n]['limitations'] for n in names)}
        if retargets:
            profile['link_retarget'] = retargets
        categories = sorted(set(classification.values()))
        profile.update(path_profiles=path_profiles, categories=categories,
                       change_classification=categories[0] if len(categories) == 1 else 'mixed')
        # Every runtime path's reviewed profile names the gates it owns; there is no shared
        # default, so no component inherits another's. Paths outside the runtime boundary never
        # contribute, and the union over the manifest's runtime paths is sealed with the profile.
        runtime_required = []
        for path in manifest:
            if self.in_runtime(path, table, head):
                declared = profiles['profiles'][path_profiles[path]].get('runtime_checks')
                require(declared, 'Runtime path profile declares no runtime gates: ' + path)
                for gate in declared:
                    if gate not in runtime_required:
                        runtime_required.append(gate)
        if runtime_required:
            profile['runtime_checks'] = runtime_required
        return name, self.select_impact(manifest, policy, profiles, profile)

    def deleted_scope(self, path, table, profiles):
        """The HEAD row that scopes a tracked deletion whose row this transaction moved to Historical paths.

        None for everything else: a live file, a path still covered by a live row, a path with no Historical row,
        one the HEAD table did not classify as an unblocked, unbounded live row, or an unreadable HEAD table.
        """
        if (self.root / path).exists():
            return None
        try:
            self.git('cat-file', '-e', 'HEAD:' + path)
        except ValueError:
            return None
        return table.deletion_scope(path, self.head_table(profiles))

    def head_text(self, relative):
        """A file as committed at HEAD, or None when HEAD does not hold it (a project file the project does not have)."""
        try:
            self.git('cat-file', '-e', 'HEAD:' + relative)
        except ValueError:
            return None
        return self.git('show', 'HEAD:' + relative).decode('utf-8')

    def head_table(self, profiles):
        """The classification table as committed at HEAD, or None when it cannot be read or parsed.

        It is the union of the framework's table and the project's, both read at HEAD with the same loader as the
        working tree's. The early approval judges every path by the higher of this table's row and the candidate's
        own, so a candidate never lowers its own sensitivity, and a row it adds to or moves into the project's table
        has no row here to lower; None makes every path count as `high`."""
        try:
            return artifact_classification.table_from(self.head_text, profiles)
        except (ValueError, UnicodeError):
            return None

    def link_retargets(self, manifest, policy, table):
        """Manifest documents whose edit only retargets links as the policy's moves imply: {path: changes}."""
        documents, moves = link_retarget.settings(policy, table)
        found = {}
        for path in manifest:
            if path not in documents:
                continue
            try:
                before = self.git('show', 'HEAD:' + path).decode('utf-8-sig')
            except (ValueError, UnicodeError):
                before = None
            try:
                after = (self.root / path).read_text(encoding='utf-8-sig')
            except (OSError, UnicodeError):
                after = None
            result = link_retarget.evaluate(path, before, after, documents, moves)
            if result['eligible']:
                found[path] = result['changes']
        return found

    @staticmethod
    def in_runtime(path, table, head=None):
        """Whether a path is on the runtime continuity boundary in the candidate's table or in the table at HEAD.

        The higher-of rule of the early approval applies to the Boundary column too: a candidate that removes a
        Boundary cell from its own table cannot lift the runtime treatment, and so cannot skip the records follow-up.
        `head` is None only when the HEAD table cannot be read, which leaves the candidate's table alone."""
        return table.in_boundary(path, 'runtime') or (head is not None and head.in_boundary(path, 'runtime'))

    def changed_symbols(self, path):
        """Compare authored Python structure with HEAD; no prose/worker override."""
        try:
            before = self.git('show', 'HEAD:' + path).decode('utf-8-sig')
            after = (self.root / path).read_text(encoding='utf-8-sig')
            def symbols(source):
                tree = ast.parse(source)
                result = {}
                for node in tree.body:
                    if isinstance(node, ast.ClassDef):
                        for child in node.body:
                            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                                symbol = node.name + '.' + child.name
                                require(symbol not in result, 'Ambiguous Python symbol')
                                result[symbol] = ast.dump(child)
                        # Class headers/attributes must also be accounted for.
                        node.body = [n for n in node.body if not isinstance(
                            n, (ast.FunctionDef, ast.AsyncFunctionDef))]
                        key = 'class:' + node.name
                    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        key = node.name
                    elif isinstance(node, (ast.Import, ast.ImportFrom)):
                        key = 'import:' + ast.unparse(node)
                    elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                        key = 'assignment:' + ast.unparse(node.targets[0] if isinstance(
                            node, ast.Assign) else node.target)
                    else:
                        key = 'module:' + digest(ast.dump(node).encode())
                    require(key not in result, 'Ambiguous Python symbol')
                    result[key] = ast.dump(node)
                return result
            old, new = symbols(before), symbols(after)
            return sorted(k for k in old.keys() | new.keys() if old.get(k) != new.get(k))
        except (AttributeError, OSError, ValueError, SyntaxError, UnicodeError):
            return None  # Missing context/deletion/ambiguous syntax uses broad checks.

    def select_impact(self, manifest, policy, profiles, profile):
        """Reviewed paths/symbols -> invariants -> groups, below unchanged envelopes."""
        impact = profiles['impact']
        require(impact['version'] == 1, 'Unsupported impact mapping version')
        invariants = impact['invariants']
        table = self.table(policy, profiles)
        checks = {}
        details = {}
        # Only these broad regression buckets may be refined. All other envelope
        # gates, particularly component/runtime and mandatory continuity, survive.
        # Narrowing semantics are shared with the read-only explanation in show_classification.py.
        def add(check):
            require(check['id'] not in checks or checks[check['id']] == check,
                    'Conflicting impact commands: ' + check['id'])
            checks[check['id']] = check
        for path in sorted(manifest):
            envelope = profiles['profiles'][profile['path_profiles'][path]]
            # The table names each path's invariants; `symbol-map` defers to the reviewed symbol map.
            if path in profile.get('link_retarget', {}):
                mapped = []
            else:
                mapped = (table.resolve(path) or self.deleted_scope(path, table, profiles)).invariants
            matches = []
            if mapped is not None:
                matches = [{'invariants': [i for i in mapped if i != artifact_classification.SYMBOL_MAP]}]
                if artifact_classification.SYMBOL_MAP in mapped:
                    matches[0]['symbols'] = impact['symbol_maps'].get(path, {})
            affected = set()
            fallback = not matches
            symbols = None
            for rule in matches:
                affected.update(rule.get('invariants', []))
                if 'symbols' in rule:
                    symbols = self.changed_symbols(path)
                    if not symbols or any(k not in rule['symbols'] for k in symbols):
                        fallback = True
                    else:
                        for symbol in symbols:
                            affected.update(rule['symbols'][symbol])
            pending = list(affected)
            groups = set()
            while pending:
                invariant = pending.pop()
                if invariant not in invariants:
                    fallback = True
                    continue
                entry = invariants[invariant]
                groups.update(entry['groups'])
                for dependency in entry.get('requires', []):
                    if dependency not in affected:
                        affected.add(dependency)
                        pending.append(dependency)
            if any(group not in policy['commands'] for group in groups):
                fallback = True
            # Runtime/component envelopes cannot be narrowed by an impact rule.
            replaceable = artifact_classification.replaceable_checks(profile['path_profiles'][path], envelope)
            for check in self.check_commands(envelope, policy, False):
                if fallback or check['id'] not in replaceable:
                    add(check)
            if not fallback:
                for group in sorted(groups):
                    add({'id': group, 'argv': policy['commands'][group]})
            details[path] = {'mode': 'broad' if fallback else 'mapped',
                             'invariants': sorted(affected), 'groups': sorted(groups),
                             'symbols': symbols}
        selected = dict(profile, checks=list(checks.values()), impact=details,
                        broad_checks=profile['checks'])
        self.check_commands(selected, policy, False)
        return selected

    def check_commands(self, profile, policy, runtime):
        checks = profile['checks']
        require(isinstance(checks, list) and checks, 'Empty validation profile')
        names = [c['id'] for c in checks]
        require(len(set(names)) == len(names), 'Duplicate check ID')
        # Profiles seal the gates their runtime paths own; there is no policy-level default,
        # so no component can inherit another's gates.
        gates = profile.get('runtime_checks', [])
        require(isinstance(gates, list) and all(isinstance(g, str) and g in policy['commands'] for g in gates)
                and ('runtime_checks' not in profile or gates), 'Invalid runtime gate requirement')
        require(not runtime or gates, 'Runtime packet declares no runtime gates')
        required = list(policy['required_checks']) + (gates if runtime else [])
        for name in required:
            require(name in names, 'Profile missing required suite: ' + name)
            require(next(c['argv'] for c in checks if c['id'] == name) == policy['commands'][name],
                    'Required suite command differs from reviewed policy: ' + name)
        for check in checks:
            require(check['id'] in policy['commands'] and
                    check['argv'] == policy['commands'][check['id']],
                    'Suite command differs from reviewed policy: ' + check['id'])
            require(isinstance(check['argv'], list) and check['argv'] and
                    all(isinstance(a, str) and a for a in check['argv']), 'Expected command argument array')
        return checks
