# Project lifecycle

This page is for the lead developer, the person at the keyboard. It lists, in the order of a project's life, what you do and which commands you run yourself, in six phases: adopt, set up, run, repair, change and upgrade. It is a selection: the commands that agents run during missions (`checkpoint.py`, `worktree.py create`, `integrate` and `remove`, and the `local_jobs.py` subcommands) stay in the [Coordinator role](ROLE-COORDINATOR.md), the [worker role](ROLE-WORKER.md) and the workflow documents, and each phase links to the section that owns the details instead of repeating them. The page is framework-owned, so an upgrade replaces it; it states no rule and grants no authority, and where it and a rule disagree, the rule in [Constraints](CONSTRAINTS.md) prevails.

Commands run from the root of the repository. `python` means any Python 3.13 or later ([interpreter policy](CONSTRAINTS.md#python-interpreter-policy)); to use a specific interpreter, run it by its path. On Windows run the shell commands in Git Bash, and use PowerShell only where a step says so. Git and Python 3.13 or later are the only requirements of the tools; Node.js is needed only for the Mission Control terminal checks.

"Lifecycle" here means the life of a project. It is not the checkpoint tool's `lifecycle/` package, the lifecycle lock or a worktree lifecycle operation ([Worktree workflow](WORKTREE-WORKFLOW.md)), which are other things.

| Phase | You are | You run |
| --- | --- | --- |
| [1. Adopt](#1-adopt) | starting a repository from Comitas | `git archive`, `git init`, `git add -A`, `adopt.py`, the first commit |
| [2. Set up](#2-set-up) | making the copy your project | `runtime_guard.py --check`, `validate_continuity.py`, `run_tests.py`, `claim-coordinator` |
| [3. Run](#3-run) | directing the work | `claims.sh status` and `watch`, `mission-control.sh`, the data refresh, your goes and Approves, `git push` |
| [4. Repair](#4-repair) | something stopped or was left behind | `claims.sh clear` and `doctor`, removing a stale lock |
| [5. Change](#5-change) | changing a setting or the framework's shape | the manual commit that turns a feature off, `remove_extension.py` |
| [6. Upgrade](#6-upgrade) | taking a new release of the framework | replace `governance/`, read the Migration notes, run the checks |

## 1. Adopt

You start a project by exporting Comitas into a new, empty folder. Do not copy Comitas's folder: a copy also takes its `.git` history and its git-ignored `.local/`. Export only the tracked files of `main`.

In Git Bash, from the Comitas repository:

```sh
git archive --format=tar main | tar -x -C <new-folder>
```

In PowerShell, from the Comitas repository:

```powershell
git archive --format=zip -o <new-folder>.zip main
Expand-Archive <new-folder>.zip <new-folder>
```

Then, in the new folder, in Git Bash:

```sh
git init -b main
git add -A
python -B governance/tools/adopt.py
git commit -m 'Adopt Comitas'
```

The [adoption helper](CHECKPOINT-TOOL.md#the-adoption-helper) is run once, after `git add -A` and before the first commit. It makes the new project pass the checks Comitas passes: it sets the executable bit of the staged shell scripts in the index (a Windows checkout has no permission bits, so `git add` stages them as `100644` and a conformance test fails) and it declares the label `template` in `project-governance/config/citations.json` (the framework cites its own commits as `template commit`, and a copy has none of Comitas's history), then stages that file. It writes no Git configuration, changes nothing a second time before the first commit, and refuses where a commit exists, so it never runs in Comitas or in a project that is already going. The reasons are in [G053](DECISIONS.md#g053---a-new-project-runs-one-adoption-helper-and-its-first-coordinator-claims-the-role-by-command-2026-10-20-accepted).

The first commit comes before any governed work, because the tools read the feature switches from the tip of `main`. The export carries `LICENSE` and `governance/NOTICE`; keep both ([License](../README.md#license)).

## 2. Set up

Make the copy yours, then check that it works.

1. **Rewrite your own files.** Rewrite `docs/ARCHITECTURE.md`, `docs/PLAN.md` and `docs/PROGRESS.md`, and the root `README.md`. Edit `AGENTS.md` to say what your project is and to map your own documents; the pointer line at its top stays. Keep `LICENSE`. If your project licenses its own work under other terms, keep Comitas's license and its Required Notice line for the material you retain from Comitas and say which files each license covers. Never remove a notice from retained material ([licensing and provenance](CONSTRAINTS.md#licensing-and-provenance)). Remove an extension you do not want as in [phase 5](#5-change).
2. **Fill `project-governance/`.** [Project governance files](PROJECT-GOVERNANCE.md) is the one source for every file in it: its keys, its defaults and what the tools refuse. Comitas ships a starter of each. The minimum a project does:
   - `ARTIFACT-CLASSIFICATION.md` and `config/artifact-classification-golden.json`: one row per path of your project, with its pin. Comitas ships the rows of its own documents: replace them. Every tracked file needs a row in one of the two tables, and a new file needs its row in the commit that adds it. `python -B governance/tools/show_classification.py <path> --checks` shows how any path resolves and which checks it would select.
   - `CONSTRAINTS.md`: your own rules, next to the framework's, which they never override.
   - `DECISIONS.md` and `RATIONALE.md`: your own decisions, numbered with the prefix D from any free number (the framework's are G), and the reasons for them.
   - `config/mission-control.json`: your own Mission Control port, `{"version": 1, "port": <port>}`, because several projects on one host would otherwise collide on the default.
3. **Check the Python runtime.** `python -B governance/tools/runtime_guard.py --check` prints the interpreter's fingerprint. If your `python` is older than 3.13, run the commands with the path of a newer interpreter ([runtime guard](CHECKPOINT-TOOL.md#runtime-guard)).
4. **Run the checks.** Continuity, then the tests of the tools and of your configuration:

   ```sh
   python -B governance/tools/validate_continuity.py
   python -B governance/tools/run_tests.py -s governance/tests -s governance/config -p 'test_*.py'
   ```

   The suite builds real Git repositories and takes about fifteen minutes on a 16-core machine; the runner fans the tests out over the cores when `parallel_tests` is true in `project-governance/config/checkpoint-features.toml` ([the test runner](CHECKPOINT-TOOL.md#test-runner)). Commit your edits once both pass.
5. **Claim the Coordinator role.** The Coordinator is the thread that talks to you and keeps the queue ([the role](ROLE-COORDINATOR.md)). Its record is machine-local and nobody creates it for you: until it exists, `local_jobs.py queue` stops with `Missing or indirect record` and the Queue page of Mission Control has nothing to read. You name the thread, and that thread runs, giving its own identity in one line:

   ```sh
   python -B governance/tools/local_jobs.py --repo . claim-coordinator --identity '<who you are>'
   ```

   The record is made in the git-ignored `.local/coordination/` of the main checkout with an empty queue and is never committed. A later thread that finds the role held gets the holder's name and a refusal. It takes the role over only with your word, by running the command again with `--take-over --reason '<your designation, in a line>'`; the previous holder is recorded as superseded ([claiming the role](ROLE-COORDINATOR.md#claiming-the-role)).

## 3. Run

Day to day you give orders to the Coordinator, watch the work and answer the two questions only you can answer. The agents follow the [workflow](CHECKPOINT-WORKFLOW.md); the shortest list of what goes wrong is [Worker hazards](WORKER-HAZARDS.md).

**Giving orders.** You talk to the Coordinator, which keeps the queue and gives each Job to one worker in a session of its own ([Local coordination](LOCAL-COORDINATION.md)). `R` and `SR` ask it for the queue; `WWW` and `A` are answered by every agent ([shortcuts](AGENT-ORIENTATION.md#shortcuts-and-phrases)). A mission begins with a worktree of its own and ends in a commit only after your approval ([Worktree workflow](WORKTREE-WORKFLOW.md)).

**Watching.** In Git Bash, from any folder of the repository:

```sh
governance/scripts/claims.sh status
governance/scripts/claims.sh watch
```

`status` reads the execution status once (CLEAR, OVERLAP, FROZEN or UNKNOWN, with the windows and claims behind it) and `watch` prints one line per landing and per thing that needs attention ([claims and execution status](CHECKPOINT-TOOL.md#claims-and-execution-status), [execution status](LOCAL-COORDINATION.md#execution-status)). The Mission Control terminal shows the queue, the status light and the project's documents, decisions, rationale, devlog and commits. It is an optional [extension](extensions/README.md):

```sh
governance/extensions/mission_control/mission-control.sh
governance/extensions/mission_control/mission-control.sh --port <port>
governance/extensions/mission_control/mission-control-data.sh refresh
```

The first line starts the server (the port comes from `project-governance/config/mission-control.json`, and `--port` overrides it), and the third regenerates the local data files at the commit `HEAD` points to, which the Refresh button of the terminal does too ([local refresh](extensions/mission_control/README.md#local-refresh), [the terminal](extensions/mission_control/ui/README.md)). A server that is running keeps the code it loaded: restart it after the framework changes ([H7](WORKER-HAZARDS.md#h7-the-mission-control-server-loads-its-code-once)).

**Goes and Approves.** A worker shows you a preview and asks for your go, and later shows the sealed review and asks for your Approve or Reject. Each is asked alone, never with another question, and each is typed or selected by you in the worker's own session. Nobody relays it, not the Coordinator and not another worker ([approval words](CHECKPOINT-WORKFLOW.md#approval-words), [the go and the Approve are asked alone](CHECKPOINT-WORKFLOW.md#the-go-and-the-approve-are-asked-alone)). Say "no preview" or "skip preview" in an order, not in a reply, to waive a preview ([the waiver](ROLE-WORKER.md#the-preview-waiver)).

**Pushing.** The tools never push. You push, and the sensible moment is when `claims.sh status` reads CLEAR, so that no mission is in the middle of an integration.

**Only you do these:**

- give the go and the Approve or Reject on a presented review, and the order "no preview";
- push;
- name the Coordinator thread, and designate a successor to take the role over;
- give the word to remove a stale lock ([phase 4](#4-repair));
- run `claims.sh clear` and `claims.sh doctor`; workers and the Coordinator never run them, not even as a dry run;
- turn a feature switch off by a manual commit ([phase 5](#5-change)).

## 4. Repair

The tools stop with a message when something is in the way, and report what they found instead of repairing it. Read the message and the status first (`claims.sh status`); a command that fails because another operation is running is run again after that operation finishes, and it is never put in a loop.

**A stale claim.** A claim whose worktree is gone is stale: it is ignored, counted and reported to you. In Git Bash:

```sh
governance/scripts/claims.sh clear
governance/scripts/claims.sh clear --apply
governance/scripts/claims.sh doctor
governance/scripts/claims.sh doctor --repair
```

`clear` lists every claim as STALE or LIVE with its evidence and changes nothing; `clear --apply` clears every STALE claim, or exactly the ids you name; `doctor` checks the claims database and says what a repair would do, and `doctor --repair` applies exactly that. The script adds no `--apply` or `--repair` by itself ([the clearing rules](CHECKPOINT-TOOL.md#claims-and-execution-status), [clear and doctor](LOCAL-COORDINATION.md#execution-status)).

**A stale lock.** A crashed command can leave the lifecycle lock (`.local/locks/worktree-workflow.lock` in the primary checkout) or the checkpoint `operation.lock` (in the `governance/` folder of the checkout's Git directory). A lock carries no owner, so its presence proves nothing. First establish that no process owns it: look at the running processes and their command lines. Only then remove the file, and only after you have given the word; an agent reports a stale lock and waits ([H3](WORKER-HAZARDS.md#h3-stale-lock-files)). A message sent into a session that is preparing kills the prepare and leaves a lock and staged files ([H20](WORKER-HAZARDS.md#h20-a-message-into-a-session-interrupts-its-turn)).

**Leftovers.** A mission that ended without a clean removal can leave a worktree, a `tx/<mission-id>` branch or a review folder. The owner removes its own mission; a superseded one is removed by its owner too ([removal](WORKTREE-WORKFLOW.md#remove-only-the-owned-completed-mission), [a superseded mission](WORKTREE-WORKFLOW.md#a-superseded-mission)). The Coordinator's closing of a Job refuses while a mission's worktree or branch exists and records a folder you decide to leave as a rider ([updates, handoff and disposal](LOCAL-COORDINATION.md#updates-handoff-and-disposal)). Do not delete a branch or worktree by hand, and never run `git clean` with `-x`: it deletes `.local/`, with the Job notes and the claims database ([H22](WORKER-HAZARDS.md#h22-git-clean--x-deletes-local)).

## 5. Change

**A feature switch.** `project-governance/config/checkpoint-features.toml` holds the switches of the checkpoint tool. Turning one on takes the sealed Approve, because the file is of `high` approval sensitivity. Turning one off is your manual commit in the main checkout, with the recipe in [feature switches](CHECKPOINT-TOOL.md#feature-switches); [Project governance files](PROJECT-GOVERNANCE.md#the-feature-switches) lists the keys and their defaults.

**Your own files.** Every file in `project-governance/` is of `high` approval sensitivity, so a change to one takes the sealed Approve ([Project governance files](PROJECT-GOVERNANCE.md)). Settings that rename or move something, such as the devlog folder, say what moves with them ([the devlog](PROJECT-GOVERNANCE.md#the-devlog)).

**Removing an extension.** From the repository root, with the extension layer still in place:

```sh
python -B governance/extensions/remove_extension.py mission_control
```

The script deletes the extension's folder and tests, its rows and pins, its policy commands and its project files, and the core still passes its tests. Read the [removal list](extensions/README.md#removal-list) first: it names every step and what stays for you to do.

## 6. Upgrade

The Comitas version is in [VERSION](VERSION), and each release is described in the [Changelog](CHANGELOG.md). To take a release:

1. Export the new release into a scratch folder as in [phase 1](#1-adopt), outside your repository.
2. Replace everything under `governance/` with Comitas's copy, and leave `project-governance/` alone. The new `governance/` brings its own `NOTICE`; keep it, and keep your root `LICENSE`. Root files such as `AGENTS.md` and the root `README.md` are yours and are not replaced.
3. Check the mode of the shell scripts: `git ls-files -s '*.sh'` must show `100755` for each. A Windows checkout has no permission bits, so a script that was re-created is staged as `100644`; set the bit with `git update-index --chmod=+x <path>` ([H19](WORKER-HAZARDS.md#h19-a-scripts-mode-lives-in-the-index-not-in-the-working-tree)).
4. Read the Migration notes of every changelog entry newer than your version. An entry has them only when something you must change by hand moved. Edit your files accordingly.
5. Run the checks of [phase 2](#2-set-up), continuity and the test suite, and commit the result.
