"""Running a child process with a bound on every wait, whichever program a name on PATH starts (standard library only).

On Windows a name on PATH can be a launcher: Git for Windows' `git` in PowerShell or cmd is `cmd\\git.exe`, which
starts the real `git.exe` as its child, and a `.cmd` shim starts `cmd.exe`. The child inherits the output pipes. Killing
the launcher alone leaves the child running with the pipes open, so a read of them never ends, and CPython's
`subprocess.run` reads them again after a timeout on Windows: the timeout became a hang. Here every stop ends the whole
process tree first on Windows (`taskkill /T /F`, while the launcher still lives and the tree can still be found), and
every wait after a stop has a bound. Elsewhere a stop kills the child; an orphan that still holds a pipe is bounded too.

The continuity checker imports this module, and the integration guard loads it, byte-verified, beside the checker, so
it imports nothing of the repository.
"""
import os
from pathlib import Path
import subprocess
import threading
import time

WINDOWS = os.name == 'nt'

# How long a stopped process, and whatever it started, may take to exit and release its pipes.
STOP_SECONDS = 10


def taskkill():
    """The system's taskkill, by its full path, so that nothing on PATH can stand in for it."""
    return str(Path(os.environ.get('SystemRoot') or r'C:\Windows') / 'System32' / 'taskkill.exe')


def end_tree(process):
    """Stop the process and, on Windows, every process it started. Never raises and never waits without a bound.

    `taskkill /T` finds the tree through the living process, so a descendant of a process that has already exited
    cannot be found and is left running; only a job object assigned at the start could end it. The waits after a stop
    stay bounded in that case too."""
    if process.poll() is not None:
        return
    if WINDOWS:
        try:
            subprocess.run([taskkill(), '/T', '/F', '/PID', str(process.pid)], stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=STOP_SECONDS)
        except (OSError, subprocess.SubprocessError):
            pass  # the kill below still stops the direct child; the waits that follow are bounded
    try:
        process.kill()
    except OSError:
        pass


def named(argv, words=6):
    """The command as a message names it: its first words, and an ellipsis when there are more."""
    argv = [str(arg) for arg in argv]
    return ' '.join(argv[:words]) + (' ...' if len(argv) > words else '')


def feed(stdin, data):
    """Write the input and close the pipe; a child that ended or never reads only makes the write fail."""
    try:
        stdin.write(data)
    except (OSError, ValueError):
        pass
    finally:
        try:
            stdin.close()
        except (OSError, ValueError):
            pass


class StallError(ValueError):
    """A child that did not exit, or did not release its pipes, within the bound after it was stopped or ended."""


def run(argv, *, input=None, capture_output=False, timeout=None, check=False, **kwargs):
    """`subprocess.run` with the same arguments, result and errors, except that a timeout never becomes a hang.

    On a timeout the process tree is ended, the output written so far is collected for at most STOP_SECONDS, and
    `subprocess.TimeoutExpired` is raised with it, as `subprocess.run` does. A pipe that an orphan still holds is
    left open rather than waited on, and the output read before it is kept. The input is written on its own thread,
    so a child that never reads it cannot hold the caller before the timed wait begins."""
    if capture_output:
        kwargs['stdout'] = kwargs['stderr'] = subprocess.PIPE
    if input is not None:
        kwargs['stdin'] = subprocess.PIPE
    process = subprocess.Popen(argv, **kwargs)  # no `with`: its exit closes the pipes, which can block on a held one
    if input is not None:
        stdin, process.stdin = process.stdin, None  # communicate must not write or close it: the feeder owns it
        threading.Thread(target=feed, args=(stdin, input), daemon=True).start()
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as first:
        end_tree(process)
        stdout, stderr = first.stdout, first.stderr
        try:
            stdout, stderr = process.communicate(timeout=STOP_SECONDS)
        except subprocess.TimeoutExpired as again:  # a pipe is still held: keep what was read before it
            stdout = stdout if again.stdout is None else again.stdout
            stderr = stderr if again.stderr is None else again.stderr
        except (OSError, ValueError):
            pass
        raise subprocess.TimeoutExpired(argv, timeout, output=stdout, stderr=stderr) from None
    except BaseException:
        end_tree(process)
        raise
    finally:
        if process.poll() is None:
            end_tree(process)
    code = process.poll()
    if check and code:
        raise subprocess.CalledProcessError(code, argv, output=stdout, stderr=stderr)
    return subprocess.CompletedProcess(argv, code, stdout, stderr)


class Stream:
    """A child whose standard output the caller reads as a stream and may stop reading early.

    Standard error is read on its own thread from the start, so neither pipe can fill and stall the child, and
    `finish` ends the child's tree when the caller stopped early, then waits for it with a bound."""

    def __init__(self, argv, **kwargs):
        self.argv = list(argv)
        self.process = subprocess.Popen(self.argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, **kwargs)
        self.errors = []
        self.reader = threading.Thread(target=self.drain, daemon=True)
        self.reader.start()

    def drain(self):
        try:
            self.errors.append(self.process.stderr.read())
        except (OSError, ValueError):
            pass

    def fileno(self):
        return self.process.stdout.fileno()

    def finish(self, stop, bound=None):
        """(exit code, standard error bytes) once the child has ended; StallError naming it when the bound expires.

        `stop` says that the caller stopped reading before the end of the output: the tree is ended first."""
        bound = STOP_SECONDS if bound is None else bound
        if stop:
            end_tree(self.process)
        try:
            self.process.stdout.close()  # nobody reads it any more; a writer that is left gets a broken pipe
        except OSError:
            pass
        deadline = time.monotonic() + bound
        self.reader.join(bound)
        try:
            self.process.wait(max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            pass
        if self.reader.is_alive() or self.process.poll() is None:
            raise StallError('%s (pid %d) did not exit and release its output within %g s after %s'
                             % (named(self.argv), self.process.pid, bound,
                                'the early stop' if stop else 'its output ended'))
        self.process.stderr.close()
        return self.process.returncode, b''.join(self.errors)
