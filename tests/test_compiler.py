"""compiler -- the ordered build-step sequencer and its two live sub-progress drivers.

Almost all of compiler.py is orchestration glue over ~40 side effects (subprocess,
Docker, pacstrap, mkarchiso) that must NOT run in a unit test. But two spots are
pure arithmetic/data whose silent drift would visibly corrupt the progress bar or
mis-size the build, and one tiny helper (`_sudo`) encodes a deliberate asymmetry:

  1. STEP_WEIGHTS is a hand-maintained list whose length is coupled BY AN INVARIANT
     the module's own comment states: `len(STEP_WEIGHTS) - 1` MUST equal the number
     of `bar.step(...)` calls in `run()`. Add a step and forget a weight (or vice
     versa) and the bar's percentages silently skew for the whole build with nothing
     to catch it. We assert the count coupling and the exact weight shape.
  2. `_PACMAN_BANDS` + the frame regex in `_drive_mkarchiso_progress` map each
     pacstrap `(N/M) installing ...` frame onto a permille sub-band. A wrong base or
     span, or a regex that fails to match a phase word, makes the bar jump backwards
     or freeze. We rebuild the exact regex the driver uses and check both the parse
     and the `base + n*span//mm` math, plus drive the parser end-to-end with a fake
     process pipe and a recording bar (no real mkarchiso).
  3. `_sudo()` returns `["sudo", "-n"]` off-root -- the `-n` (non-interactive) flag is
     deliberate and distinct from makepkg's plain `["sudo"]`, so a Ctrl-C teardown
     after the sudo timestamp expired fails fast instead of blocking on a password
     prompt. Empty when already root.

No network, subprocess, Docker, or sudo is invoked here: the driver reads from an
in-memory BytesIO pipe and writes into a recording bar object.
"""

from __future__ import annotations

import inspect
import io
import re

import compiler


# --- STEP_WEIGHTS <-> bar.step() count invariant ---------------------------

def test_step_weights_length_and_shape():
    # 13 lightweight setup/emit steps (index 0 unused sentinel + 12 real "8"s) then
    # the four giants. Total 17 entries. There are TWO mkarchiso giants because the bar
    # is sized for the MAX variant set (base + sshd); only one variant builds per run.
    assert len(compiler.STEP_WEIGHTS) == 17
    assert compiler.STEP_WEIGHTS[0] == 0
    assert compiler.STEP_WEIGHTS[1:13] == [8] * 12
    # Final four, in order: package-cache giant, makepkg stage, and the TWO
    # mkarchiso giants (one per POSSIBLE ISO variant; the run uses one of them).
    assert compiler.STEP_WEIGHTS[-4:] == [250, 120, 270, 270]


def test_step_weights_matches_executed_step_count():
    # The invariant the module comment stresses: len(STEP_WEIGHTS) - 1 MUST equal the
    # number of milestones run() EXECUTES. run() makes 15 literal bar.step() calls, but
    # the last one lives inside the per-variant finalize loop and executes once per
    # variant -- so the number of executed milestones is (15 - 1 loop call) + one call
    # per variant = 14 + len(VARIANTS). The weights list must carry exactly that many
    # real entries (plus the index-0 sentinel), so a step added/removed OR a variant
    # added/removed without matching STEP_WEIGHTS fails here.
    src = inspect.getsource(compiler.run)
    n_literal = src.count("bar.step(")
    assert n_literal == 15
    executed = (n_literal - 1) + len(compiler.VARIANTS)  # loop's single call runs per variant
    assert executed == len(compiler.STEP_WEIGHTS) - 1


def test_step_weights_all_positive_after_sentinel():
    # Only index 0 may be zero; a zero-weight real step would be invisible on the bar.
    assert all(w > 0 for w in compiler.STEP_WEIGHTS[1:])


# --- _PACMAN_BANDS data invariants -----------------------------------------

def test_pacman_bands_install_family_shares_one_band():
    # installing/upgrading/reinstalling/downgrading are the same on-disk write work,
    # so they map to one band (240, 580) -- the widest, as pacstrap install dominates.
    for key in ("installing", "upgrading", "reinstalling", "downgrading"):
        assert compiler._PACMAN_BANDS[key] == (240, 580)


def test_pacman_bands_pre_install_phase_bases():
    # The read-only pre-install phases occupy the 20..240 lead-in, ascending by base.
    assert compiler._PACMAN_BANDS["checking keys in keyring"] == (20, 90)
    assert compiler._PACMAN_BANDS["checking package integrity"] == (110, 70)
    assert compiler._PACMAN_BANDS["loading package files"] == (180, 20)
    assert compiler._PACMAN_BANDS["checking for file conflicts"] == (200, 20)
    assert compiler._PACMAN_BANDS["checking available disk space"] == (220, 20)


def test_pacman_bands_bases_are_monotonic_non_overlapping():
    # Pre-install bands must not overlap (a later frame must never map below an
    # earlier one), and each ends exactly where sensible before the install band.
    pre = [
        compiler._PACMAN_BANDS["checking keys in keyring"],
        compiler._PACMAN_BANDS["checking package integrity"],
        compiler._PACMAN_BANDS["loading package files"],
        compiler._PACMAN_BANDS["checking for file conflicts"],
        compiler._PACMAN_BANDS["checking available disk space"],
    ]
    bases = [b for b, _ in pre]
    assert bases == sorted(bases)
    # each band's top (base+span) does not exceed the next band's base.
    for (b, s), (nb, _ns) in zip(pre, pre[1:]):
        assert b + s <= nb


def test_pacman_bands_stay_within_the_20_820_window():
    # The docstring pins these sub-bands inside 20..820 of the mkarchiso step; the
    # install band's top is 240+580 == 820 exactly.
    for base, span in compiler._PACMAN_BANDS.values():
        assert base >= 20
        assert base + span <= 820
    assert 240 + 580 == 820


# --- frame regex parse + band arithmetic -----------------------------------

def _rebuild_frame_regex() -> re.Pattern:
    # Exactly the pattern _drive_mkarchiso_progress compiles internally.
    return re.compile(
        r"\(\s*(\d+)/(\d+)\)\s+(" + "|".join(re.escape(k) for k in compiler._PACMAN_BANDS) + r")"
    )


def test_frame_regex_parses_padded_count_and_phase():
    frame = _rebuild_frame_regex()
    m = frame.search("(  7/210) installing linux")
    assert m is not None
    assert m.groups() == ("7", "210", "installing")


def test_frame_regex_matches_every_band_phase_word():
    # If any band key stopped matching the regex, its frames would fall through to
    # creep() and that phase would never advance by its real fraction.
    frame = _rebuild_frame_regex()
    for phase in compiler._PACMAN_BANDS:
        m = frame.search(f"(  1/2) {phase} something")
        assert m is not None, phase
        assert m.group(3) == phase


def test_frame_regex_ignores_unknown_phase_word():
    frame = _rebuild_frame_regex()
    assert frame.search("(  1/2) frobnicating widgets") is None


def test_band_permille_math_matches_driver_formula():
    # The driver computes base + n * span // mm. For a linux frame at 7/210 in the
    # install band: 240 + 7*580//210 == 259.
    base, span = compiler._PACMAN_BANDS["installing"]
    assert base == 240 and span == 580
    assert base + 7 * span // 210 == 259
    # start-of-phase and end-of-phase pin to the band's base and (near) its top.
    assert base + 0 * span // 210 == 240
    assert base + 210 * span // 210 == 820


# --- end-to-end drive with a fake pipe + recording bar ---------------------

class _RecordingBar:
    """Captures sub()/phase() calls; supplies the _clip() the driver calls when
    sys.stdout has no write_split (as under pytest capture)."""

    def __init__(self) -> None:
        self.subs: list[int] = []
        self.phases: list[str] = []

    def sub(self, permille: int) -> None:
        self.subs.append(permille)

    def phase(self, sublabel: str) -> None:
        self.phases.append(sublabel)

    def _clip(self, text: str) -> str:
        return text


class _FakeProc:
    def __init__(self, data: bytes) -> None:
        self.stdout = io.BytesIO(data)


def test_drive_installing_frame_then_creep():
    # Feed: the "Installing packages to" milestone, a real install frame, an
    # unparseable line (creep), then the SquashFS milestone.
    data = (
        b"Installing packages to /work\n"
        b"(  5/100) installing foo\r"
        b"random noise line\n"
        b"Creating SquashFS image\n"
    )
    bar = _RecordingBar()
    compiler._drive_mkarchiso_progress(_FakeProc(data), bar)
    # 20 from the install milestone; 240+5*580//100==269 from the frame; creep from
    # (floor=20,ceil=810,at=20): 20 + max(1, 790//16) == 69; 840 from SquashFS.
    assert bar.subs == [20, 269, 69, 840]


def test_drive_installing_milestone_sets_phase_span_floor():
    # The very first sub after "Installing packages to" is the floor (20), and the
    # phase label narrates the install step.
    data = b"Installing packages to /work\n"
    bar = _RecordingBar()
    compiler._drive_mkarchiso_progress(_FakeProc(data), bar)
    assert bar.subs == [20]
    assert bar.phases == ["pacstrap: installing packages into airootfs"]


def test_drive_frame_ignored_before_install_milestone():
    # inpac is False until "Installing packages to"; a frame arriving before it must
    # NOT emit a band sub -- it falls through to creep() with a zero-width span
    # (floor==ceil==0 -> room==0 -> no sub).
    data = b"(  5/100) installing foo\n"
    bar = _RecordingBar()
    compiler._drive_mkarchiso_progress(_FakeProc(data), bar)
    assert bar.subs == []


def test_drive_squashfs_checksum_iso_milestones():
    # The tail-end mksquashfs/checksum/xorriso milestones map to fixed permille floors.
    data = (
        b"Creating SquashFS image\n"
        b"Creating checksum file\n"
        b"Creating ISO image\n"
    )
    bar = _RecordingBar()
    compiler._drive_mkarchiso_progress(_FakeProc(data), bar)
    assert bar.subs == [840, 930, 960]
    assert bar.phases == [
        "mksquashfs: compressing root filesystem (slow)",
        "writing SquashFS checksum",
        "xorriso: writing bootable ISO image",
    ]


def test_drive_install_done_snaps_to_820():
    # "Done! Packages installed" closes the install phase at 820 and re-narrates.
    data = (
        b"Installing packages to /work\n"
        b"Done! Packages installed in the airootfs\n"
    )
    bar = _RecordingBar()
    compiler._drive_mkarchiso_progress(_FakeProc(data), bar)
    assert bar.subs == [20, 820]
    assert bar.phases[-1] == "pacstrap done, running customize hooks"


def test_drive_splits_on_both_cr_and_lf():
    # pacman redraws with \r, not \n; the driver splits on BOTH so each frame is seen
    # live. Two \r-separated frames in the install phase yield two distinct subs.
    data = (
        b"Installing packages to /work\n"
        b"(  1/100) installing a\r"
        b"( 50/100) installing b\r"
    )
    bar = _RecordingBar()
    compiler._drive_mkarchiso_progress(_FakeProc(data), bar)
    # 20 (milestone), 240+1*580//100==245, 240+50*580//100==530.
    assert bar.subs == [20, 245, 530]


def test_drive_zero_denominator_frame_emits_no_band_sub():
    # A malformed (n/0) frame: the driver guards with `if mm > 0`, so no band sub is
    # emitted -- but the line still counts as a frame match, so creep does NOT run
    # for it either.
    data = (
        b"Installing packages to /work\n"
        b"(  5/0) installing broken\r"
    )
    bar = _RecordingBar()
    compiler._drive_mkarchiso_progress(_FakeProc(data), bar)
    assert bar.subs == [20]


def test_drive_empty_stream_no_calls():
    bar = _RecordingBar()
    compiler._drive_mkarchiso_progress(_FakeProc(b""), bar)
    assert bar.subs == []
    assert bar.phases == []


# --- _sudo prefix: the deliberate `-n` asymmetry ----------------------------

def test_sudo_non_root_is_sudo_dash_n(monkeypatch):
    # Off-root: ["sudo", "-n"] -- the -n is load-bearing (fail fast on an expired
    # timestamp during teardown instead of blocking on a password prompt).
    monkeypatch.setattr(compiler.paths, "is_root", lambda: False)
    assert compiler._sudo() == ["sudo", "-n"]


def test_sudo_root_is_empty(monkeypatch):
    # Already root: no sudo prefix at all.
    monkeypatch.setattr(compiler.paths, "is_root", lambda: True)
    assert compiler._sudo() == []


def test_sudo_carries_noninteractive_flag(monkeypatch):
    # Distinct from makepkg's plain ["sudo"]: compiler must include the non-interactive
    # flag so a Ctrl-C teardown never stalls on stdin.
    monkeypatch.setattr(compiler.paths, "is_root", lambda: False)
    assert "-n" in compiler._sudo()


# --- module wiring sanity ---------------------------------------------------

def test_active_child_pgid_starts_at_zero():
    # 0 means "no mkarchiso child running"; kill_active_child is a no-op then.
    assert compiler._ACTIVE_CHILD_PGID == 0


def test_kill_active_child_noop_when_no_child(monkeypatch):
    # With no active child (pgid <= 0) kill_active_child must return without touching
    # os.killpg or spawning any process.
    called = []
    monkeypatch.setattr(compiler.os, "killpg", lambda *a, **k: called.append(a))
    # module global is the sentinel 0 for this call
    monkeypatch.setattr(compiler, "_ACTIVE_CHILD_PGID", 0, raising=False)
    compiler.kill_active_child(["sudo", "-n"])
    assert called == []
