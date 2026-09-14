#!/bin/bash
#
# azzio -- ISO build entrypoint (thin shim).
#
# The build itself is Python: everything (steps, staging, the package cache, the
# progress bar, the ownership handback) lives as flat modules in libraries/. This
# shim only does the two things that genuinely must be bash BEFORE Python starts:
#
#   1. Prime sudo ONCE on the real controlling terminal. After the PTY re-exec
#      below there is no interactive channel, so this is the only point a password
#      prompt can reach the user. The primed credential lets the Python sudo
#      keepalive + ownership handback run `sudo -n` for the rest of the build.
#
#   2. Re-exec on a PTY via util-linux `script`. The PTY is the point: pacman/
#      mkarchiso detect a terminal and keep their live, \r-redrawn progress bars
#      (piping through plain tee makes them buffer and appear frozen for minutes
#      during big downloads), and the process being a real tty is what lets the
#      progress bar paint. `script` here writes its capture to /dev/null -- it is
#      kept ONLY for the PTY. Python (logstream) owns logs/compile-full.log itself,
#      so the progress bar (painted to the raw terminal) never pollutes the log.
#
# Then it hands off to `python3 -m compiler`, which does the rest.
#
# Each run builds EXACTLY ONE medium. By default that is the base `azzio-headed` medium
# (ssh DISABLED). --ssh="<PASSWORD>" (see ARGS) instead builds the `azzio-headed-ssh`
# medium INDIVIDUALLY -- on its own, NOT alongside the base ISO: identical contents, but
# named azzio-headed-ssh-<ver>-x86_64.iso, with `main`'s login password set from --ssh,
# sshd ENABLED, and port 22 opened -- in BOTH the live session and the installed system.
# Without --ssh, ONLY the base ISO is built -- no default password is ever shipped
# (see data/PROMPT.md DECISION 2).
#
# ARGS: any args are passed straight through to the Python build driver.
#   --ssh="<PASSWORD>"       Build the `azzio-headed-ssh` ISO INDIVIDUALLY (instead of
#                            the base ISO, not in addition to it), with <PASSWORD> as the
#                            live `main` user's login password (hashed sha-512 into that
#                            ISO's /etc/shadow -- never blank, never plaintext-in-image).
#                            The flag DEMANDS a password: a bare `--ssh` or an empty
#                            `--ssh=` is a hard error (it stops the build and explains why
#                            -- no ssh ISO is silently skipped). Omit --ssh entirely to
#                            build just the base headed ISO.
#   --logs                   (only with --instant) bake `azzioinstall --logs` into the
#                            instant ISO's boot hook, so the unattended install tees its
#                            whole run to Shared/install.log in the host<->guest shared
#                            folder (beside INSTALL_DONE) for the HOST to read. Boolean;
#                            a hard error without --instant.
#   --full-compile           build Azzio's own packages ENTIRELY from source
#                            (incl. a multi-hour LibreWolf/Firefox compile) instead
#                            of the default, which repackages LibreWolf's verified
#                            upstream binary tarball (sha256 + PGP checked).
#   --use-each-cpu           use EVERY logical CPU for the compile (one job per core).
#                            By default the compile is HARDCODED to 75% of the cores so
#                            the desktop stays usable; this flag lifts that cap for a
#                            maximum-speed build on a machine you don't need meanwhile.
#                            Governs the actual build only (an --estimate* run is a pure
#                            prediction and ignores it). See makepkg.build_jobs.
#   --estimate*              don't build anything -- estimate how long a build would
#                            take on THIS machine and exit. Six variants pick the
#                            tier (default vs --full-compile) and what to estimate:
#                              --estimate                            default: compute + network
#                              --estimate-only-compute               default: compute only
#                              --estimate-only-network               default: network only
#                              --estimate-full-compile               full:    compute + network
#                              --estimate-full-compile-only-compute  full:    compute only
#                              --estimate-full-compile-only-network  full:    network only
#                            The network variants run a short, timeout-bounded
#                            bandwidth probe against an Arch mirror; still no sudo.
# See compiler.py.

set -o pipefail

REPODIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOGDIR="$REPODIR/logs"
FULL_LOG="$LOGDIR/compile-full.log"
STEPS_LOG="$LOGDIR/compile-steps.log"
mkdir -p "$LOGDIR"

# Never scatter __pycache__ around the source tree. This shim is the entry point;
# the Python build driver runs once per invocation, so byte-compiling its modules
# to disk buys nothing and only litters libraries/**/__pycache__. Exported HERE,
# before any branch, so every hand-off inherits it: the --estimate* early exec,
# the PTY re-exec into `script`, and the main `python3 -m compiler` build. This
# repo treats __pycache__ as pollution -- see clear.sh / tests.sh.
export PYTHONDONTWRITEBYTECODE=1

# Stopwatch: format a whole-second duration as e.g. "1h 04m 09s" / "7m 32s" / "12s".
# Used at the very end to report how long the compile took, on success AND failure.
# The SAME elapsed time also ticks LIVE during the build: _COMPILE_START (set below,
# before the PTY re-exec) is exported through it, and the Python progress bar
# (progress.ProgressBar) reads it to paint a once-a-second stopwatch inside
# the pinned bar -- so the duration is visible AS IT GROWS, not only at the end.
# This bash helper mirrors progress.format_clock so both render identically.
_format_duration() {
    local secs=$1 h m s
    h=$(( secs / 3600 )); m=$(( (secs % 3600) / 60 )); s=$(( secs % 60 ))
    if   [ "$h" -gt 0 ]; then printf '%dh %02dm %02ds' "$h" "$m" "$s"
    elif [ "$m" -gt 0 ]; then printf '%dm %02ds' "$m" "$s"
    else                      printf '%ds' "$s"
    fi
}

# Any --estimate* variant is a pure, read-only query (no build, no privileged
# steps, no live progress bar): hand straight to Python WITHOUT priming sudo or
# re-execing on a PTY, so it runs instantly and never prompts for a password. The
# network-including variants DO open a client socket for a short bandwidth probe,
# but still need no sudo and no PTY. The `--estimate*` glob matches all six flags
# (and any future --estimate...).
for _arg in "$@"; do
    case "$_arg" in
        --estimate*)
            export PYTHONPATH="$REPODIR/libraries${PYTHONPATH:+:$PYTHONPATH}"
            exec python3 -u -m compiler "$@"
            ;;
    esac
done

if [ -z "$_COMPILE_LOGGING" ]; then
    export _COMPILE_LOGGING=1
    # Prime sudo once, interactively, on the real terminal (see note 1 above).
    if [ "$(id -u)" -ne 0 ] && command -v sudo >/dev/null 2>&1; then
        sudo -n -v 2>/dev/null || sudo -n true 2>/dev/null || sudo -v || {
            echo "[!] sudo is required for the privileged build steps." >&2; exit 1; }
    fi
    # Start the stopwatch HERE -- after the (possibly slow, interactive) sudo prompt
    # so the user's password-typing time is not counted as build time, and BEFORE the
    # PTY re-exec so it spans the whole build. Exported, so it survives the re-exec
    # into `script` and reaches the innermost hand-off where the elapsed time is
    # reported. Whole seconds from the epoch (SECONDS/date are always available).
    export _COMPILE_START="$(date +%s)"
    # Truncate both logs so each launch overwrites the previous run's logs. Python
    # (logstream / progress) reopens them in append mode afterwards.
    : > "$FULL_LOG"
    : > "$STEPS_LOG"
    # Re-exec on a PTY. `script` flags: -q quiet, -e propagate child exit status,
    # -f flush after each write, -c run our command. Output goes to /dev/null:
    # `script` is kept ONLY to provide the PTY (see note 2); Python owns compile-full.log,
    # so the progress bar's escapes/glyphs never get captured into the log.
    if command -v script >/dev/null 2>&1; then
        exec script -qefc "_COMPILE_LOGGING=1 _COMPILE_ONPTY=1 bash '${BASH_SOURCE[0]}' $*" /dev/null
    else
        # No `script` available: run without a PTY. Python still writes compile-full.log
        # itself; child \r-progress bars degrade to plain lines but the build works.
        :
    fi
fi

# --- Under the PTY now: hand off to the Python build driver. ----------------
# PYTHONPATH points at libraries/ so the flat compiler modules (compiler, paths,
# ...) and the modifications.* packages resolve. -u = unbuffered, so the bar and build
# output interleave correctly on the PTY and in compile-full.log.
export PYTHONPATH="$REPODIR/libraries${PYTHONPATH:+:$PYTHONPATH}"
export _COMPILE_ONPTY
# NOT exec'd (unlike before): the shell must OUTLIVE the Python build so it can stop
# the stopwatch and report the elapsed time afterwards -- on success AND on failure.
# We capture Python's exit code, print how long the compile took, then exit with that
# same code so callers/CI still see the real build result.
python3 -u -m compiler "$@"
_rc=$?

# Stop the stopwatch. _COMPILE_START was set before the PTY re-exec and exported
# through it; fall back to now (0s) if somehow unset so this never divides on empty.
_elapsed=$(( $(date +%s) - ${_COMPILE_START:-$(date +%s)} ))
if [ "$_rc" -eq 0 ]; then
    _line="[time] Compile finished in $(_format_duration "$_elapsed")."
else
    _line="[time] Compile FAILED after $(_format_duration "$_elapsed") (exit $_rc)."
fi
echo "$_line"
# Mirror the timing line into compile-steps.log so it is captured alongside the build log.
echo "$_line" >> "$STEPS_LOG" 2>/dev/null || true
exit "$_rc"
