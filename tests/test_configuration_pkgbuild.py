"""pkgbuild -- the Azzio-authored package recipes.

These PKGBUILDs are Python f-strings emitted to disk and then fed verbatim to
makepkg. Two failure modes here are silent and expensive:

  1. A wrong version literal. LibreWolf has TWO version strings that look almost
     identical -- the upstream tag "153.0.1-1" (used to build the download URL and
     the source filename) and the pacman-legal pkgver "153.0.1.1" (the '-' is a
     pkgrel separator, illegal in pkgver). Swap them and makepkg either 404s the
     download or rejects the version; nothing in Python catches it because both
     are valid strings.

  2. A broken sha256sums / SKIP alignment. makepkg matches each checksum to the
     corresponding source() entry by position. The repackage tier has one real
     hash + three 'SKIP's (tarball hashed, .sig GPG-checked, two local files);
     the from-source tier has three 'SKIP's and no pinned hash at all. An
     off-by-one in that tuple makes makepkg verify the wrong file.

  3. f-string brace-doubling. Every literal shell brace in these recipes is
     written '{{'/'}}' so the f-string collapses it to a single '{'/'}'. A
     missed doubling leaks a stray brace (or an f-string ValueError at import).
     These tests assert no '{{'/'}}' survives into the emitted text.

  4. Tier dispatch. recipe_dirs(full_compile) decides which recipes are emitted:
     BOTH tiers build calamares (from source -- Arch dropped extra/calamares) and
     librewolf. The DEFAULT tier repackages librewolf; --full-compile swaps in the
     from-source librewolf recipe. The set of packages is the same in both tiers.

Pure string logic -- no filesystem, no network, no makepkg invoked.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

import pkgbuild


_HEX = re.compile(r"\A[0-9a-fA-F]+\Z")

# Repo root (tests/ is one level down). Used to locate the vendored, pinned
# calamares tarball the defaults patch is authored against.
_REPO_ROOT = Path(__file__).resolve().parent.parent


def _find_calamares_tarball() -> Path | None:
    """Locate the pinned calamares-<ver>.tar.gz under cache/ (the makepkg source
    cache), or None if it isn't present in this checkout.

    The patch is authored against PRISTINE upstream source, so the integration
    guard must read from the tarball -- NOT the extracted build scratch under
    .build/. makepkg runs the patch in-place during prepare(), so any local
    build leaves that scratch tree already-patched; dry-running the patch against
    it then trips "Reversed (or previously applied) patch detected!" and the test
    false-fails. The SRCDEST tarball is exactly what makepkg downloads and is the
    only trustworthy pristine copy on disk."""
    name = f"calamares-{pkgbuild.CALAMARES_VERSION}.tar.gz"
    # SRCDEST now lives at cache/makepkg-src/<recipe>/ (a persistent sibling of the
    # makepkg scratch that the per-build wipe never touches -- see paths.MAKEPKG_SRC_CACHE).
    # Prefer that; keep the legacy under-scratch .src/ path as a fallback for a checkout
    # whose cache predates the move. Never match extracted trees (.build/).
    candidates = (
        _REPO_ROOT / "cache" / "makepkg-src" / "calamares" / name,
        _REPO_ROOT / "cache" / "makepkg" / "calamares" / ".src" / name,
    )
    for cand in candidates:
        if cand.is_file():
            return cand
    return None


# --- pinned upstream constants ---------------------------------------------

def test_version_constants_distinct():
    # The two LibreWolf version strings must never be equal: the '-1' tag form
    # and the '.1' pkgver form are used in different, non-interchangeable places.
    assert pkgbuild.LIBREWOLF_VERSION == "153.0.1-1"
    assert pkgbuild.LIBREWOLF_PKGVER == "153.0.1.1"
    assert pkgbuild.LIBREWOLF_VERSION != pkgbuild.LIBREWOLF_PKGVER


def test_pgp_key_is_40_hex_chars():
    # makepkg's validpgpkeys=() needs a full 40-char primary key fingerprint.
    key = pkgbuild.LIBREWOLF_PGP_KEY
    assert len(key) == 40
    assert _HEX.match(key)


def test_sha256_constants_are_64_hex():
    # A sha256 is exactly 32 bytes = 64 hex chars; a wrong length would be a
    # truncated/pasted-over hash that makepkg would reject on every build.
    for h in (pkgbuild.LIBREWOLF_SHA256, pkgbuild.CALAMARES_SHA256):
        assert len(h) == 64
        assert _HEX.match(h)


def test_calamares_version_literal():
    assert pkgbuild.CALAMARES_VERSION == "3.4.2"


# --- pkgbuild_librewolf (DEFAULT / repackage tier) -------------------------

def test_librewolf_pkgver_field_correct():
    # The pkgver= field must carry the pacman-legal "153.0.1.1", NOT the tag form.
    # _lwver= carries the tag form "153.0.1-1" for URL/filename construction.
    s = pkgbuild.pkgbuild_librewolf()
    assert "pkgver=153.0.1.1" in s
    assert "pkgver=153.0.1-1" not in s
    assert "_lwver=153.0.1-1" in s


def test_librewolf_sha256sums_shape():
    # One real hash then two 'SKIP's: tarball hashed, .sig GPG-checked (SKIP), and the
    # single shipped-in-repo local file .desktop (SKIP). (The AutoConfig override is no
    # longer a source() entry -- it ships as a home file, not a package companion.)
    s = pkgbuild.pkgbuild_librewolf()
    assert (
        "sha256sums=('%s' 'SKIP' 'SKIP')" % pkgbuild.LIBREWOLF_SHA256
    ) in s
    assert s.count("'SKIP'") == 2


def test_librewolf_validpgpkeys_present():
    # The repackage tier GPG-verifies the tarball, so the primary key must be
    # pinned in validpgpkeys=().
    s = pkgbuild.pkgbuild_librewolf()
    assert ("validpgpkeys=('%s')" % pkgbuild.LIBREWOLF_PGP_KEY) in s


def test_librewolf_repackage_has_no_make_fetch():
    # The repackage tier just unpacks the prebuilt tarball; it never runs the
    # bsys6 make targets. Their presence would mean the from-source recipe leaked.
    s = pkgbuild.pkgbuild_librewolf()
    assert "make fetch" not in s
    assert "make build" not in s


def test_librewolf_download_url_uses_tag_version():
    # The download host path and source filename are built from the tag form.
    # Binaries are served from Codeberg's package API (dl.librewolf.net is down).
    s = pkgbuild.pkgbuild_librewolf()
    assert "https://codeberg.org/api/packages/librewolf/generic/librewolf/153.0.1-1" in s
    assert "librewolf-153.0.1-1-linux-x86_64-package.tar.xz" in s


# --- pkgbuild_librewolf_src (FULL / from-source tier) ----------------------

def test_librewolf_src_skips_no_hash():
    # From-source tier pins nothing by sha (bsys6 verifies Firefox itself): both
    # source() entries (the bsys6 git tree + the .desktop) are 'SKIP', the LibreWolf
    # tarball hash never appears, and there is no validpgpkeys line (no .sig download in
    # this path). (The override is no longer a source() entry -- it is a home file.)
    s = pkgbuild.pkgbuild_librewolf_src()
    assert "sha256sums=('SKIP' 'SKIP')" in s
    assert s.count("'SKIP'") == 2
    assert pkgbuild.LIBREWOLF_SHA256 not in s
    assert "validpgpkeys" not in s


def test_librewolf_src_runs_bsys6_make_targets():
    s = pkgbuild.pkgbuild_librewolf_src()
    assert "make fetch" in s
    assert "make build" in s
    assert "make package" in s


def test_librewolf_src_make_build_caps_jobs():
    # `make build` alone lets Firefox's build spawn one job per core and pin the
    # whole machine. It must carry the -j cap fed via AZZIO_JOBS (exported by
    # makepkg), defaulting to 1 when the var is unset.
    s = pkgbuild.pkgbuild_librewolf_src()
    assert 'make build -j"${AZZIO_JOBS:-1}"' in s


def test_librewolf_src_shares_pkgver_and_lwver():
    # The from-source recipe uses the SAME version split as the repackage one.
    s = pkgbuild.pkgbuild_librewolf_src()
    assert "pkgver=153.0.1.1" in s
    assert "_lwver=153.0.1-1" in s


# --- pkgbuild_calamares -----------------------------------------------------

def test_calamares_pkgver_and_sha():
    s = pkgbuild.pkgbuild_calamares()
    assert "pkgver=3.4.2" in s
    # The tarball hash is pinned; the FIVE shipped-in-repo patches (defaults +
    # region-keyboard + finish-buttons + networkq + networkcfg-static) are each SKIP
    # (local files, matched by position to source() entries two through six).
    assert ("sha256sums=('%s' 'SKIP' 'SKIP' 'SKIP' 'SKIP' 'SKIP')" % pkgbuild.CALAMARES_SHA256) in s


def test_calamares_pkgver_var_survives_brace_collapse():
    # 'calamares-${{pkgver}}.tar.gz' in the f-string must collapse to a single
    # '${pkgver}' shell expansion, not leak double braces.
    s = pkgbuild.pkgbuild_calamares()
    assert "${pkgver}" in s
    assert "calamares-${pkgver}.tar.gz" in s


def test_calamares_cmake_build_caps_jobs():
    # `cmake --build build` auto-detects every core and pins the machine. It must
    # carry the -j cap fed via AZZIO_JOBS (exported by makepkg), defaulting
    # to 1 when unset. The brace pair in the recipe f-string must also have
    # collapsed to a single ${...} shell expansion.
    s = pkgbuild.pkgbuild_calamares()
    assert 'cmake --build build -j"${AZZIO_JOBS:-1}"' in s


def test_calamares_cmake_pins_python_to_system_interpreter():
    # calamares links libpython into libcalamares.so via the LEGACY find_package(Python
    # ... Development) module; a stray user-local interpreter (uv/pipx shim) would link a
    # libpython the target ISO lacks. BOTH module families must be pinned to the system
    # python, or the .so depends on e.g. libpython3.12.so.1.0 and calamares won't start.
    s = pkgbuild.pkgbuild_calamares()
    assert '_pyexe="/usr/bin/python3"' in s
    for prefix in ("Python", "Python3"):
        assert ('-D%s_EXECUTABLE="$_pyexe"' % prefix) in s
        assert ('-D%s_ROOT_DIR=/usr' % prefix) in s
        assert ('-D%s_FIND_STRATEGY=LOCATION' % prefix) in s
        assert ('-D%s_FIND_VIRTUALENV=STANDARD' % prefix) in s


def test_calamares_cmake_disables_pwquality():
    # The users module does an UNCONDITIONAL find_package(LibPWQuality) with no WITH_
    # toggle. If the build host has libpwquality, the module links libpwquality.so.1,
    # which the Azzio ISO does not ship -> the users viewmodule fails to dlopen on the
    # target and calamares aborts. Disabling the find_package keeps the module portable
    # (and matches the design: strong-password checking is force-hidden anyway).
    s = pkgbuild.pkgbuild_calamares()
    assert "-DCMAKE_DISABLE_FIND_PACKAGE_LibPWQuality=ON" in s


# --- calamares source patch (installer UI defaults) ------------------------

def test_calamares_pkgbuild_references_patch_in_source_and_prepare():
    # Each patch must be a source() entry (so makepkg stages it) AND actually applied
    # in prepare(); a patch present but never applied would silently do nothing.
    s = pkgbuild.pkgbuild_calamares()
    assert "prepare() {" in s
    for name in (
        pkgbuild.CALAMARES_DEFAULTS_PATCH_NAME,
        pkgbuild.CALAMARES_REGION_KEYBOARD_PATCH_NAME,
        pkgbuild.CALAMARES_FINISH_BUTTONS_PATCH_NAME,
        pkgbuild.CALAMARES_NETWORKQ_PATCH_NAME,
        pkgbuild.CALAMARES_NETWORKCFG_STATIC_PATCH_NAME,
    ):
        assert ("'%s'" % name) in s                        # listed in source=()
        assert ("patch -p1 < \"$srcdir/%s\"" % name) in s  # applied, -p1, from srcdir


def test_calamares_patch_skip_aligned_after_tarball_hash():
    # sha256sums matches source() by POSITION: real tarball hash first, then SKIP for
    # each local patch file. Five SKIPs (defaults + region + finish-buttons + networkq +
    # networkcfg-static patches).
    s = pkgbuild.pkgbuild_calamares()
    assert ("sha256sums=('%s' 'SKIP' 'SKIP' 'SKIP' 'SKIP' 'SKIP')" % pkgbuild.CALAMARES_SHA256) in s
    assert s.count("'SKIP'") == 5


def test_calamares_patch_name_is_a_patch_file():
    assert pkgbuild.CALAMARES_DEFAULTS_PATCH_NAME.endswith(".patch")


def test_calamares_patch_is_unified_diff_touching_all_files():
    # The patch must be a -p1 unified diff (a/ b/ prefixes) that edits every file the
    # Azzio installer-UI refactor touches. Missing any file means one of the requested
    # changes was dropped:
    #   KeyboardLayoutModel.cpp -- Alt+Shift group-switcher default
    #   page_usersetup.ui       -- rename 4 prompt labels + hostname placeholder "azzio"
    #   UsersPage.cpp           -- hide Full Name row + hide strong-password checkbox
    #   Config.cpp              -- isReady() relax + empty login/hostname errors + host seed
    #   SetPasswordJob.cpp      -- empty password locks any account
    p = pkgbuild.calamares_defaults_patch()
    for rel in (
        "src/modules/keyboard/KeyboardLayoutModel.cpp",
        "src/modules/users/page_usersetup.ui",
        "src/modules/users/UsersPage.cpp",
        "src/modules/users/Config.cpp",
        "src/modules/users/SetPasswordJob.cpp",
    ):
        assert f"--- a/{rel}" in p, rel
        assert f"+++ b/{rel}" in p, rel
    # Sixteen hunks total (kbd 1, ui 7, UsersPage 2, Config 5, SetPasswordJob 1);
    # each hunk header carries two "@@" markers, so at least 32.
    assert p.count("@@") >= 32


def test_calamares_patch_keyboard_selects_alt_shift_toggle():
    # THE Alt+Shift default: the added code must select the group-switcher entry
    # whose xkb id is alt_shift_toggle. Only added ('+') lines are the change.
    p = pkgbuild.calamares_defaults_patch()
    added = [ln[1:] for ln in p.splitlines() if ln.startswith("+") and not ln.startswith("+++")]
    body = "\n".join(added)
    assert "alt_shift_toggle" in body
    assert "setCurrentIndex(" in body


def test_calamares_patch_hostname_seeds_and_marks_custom():
    # THE fixed-hostname default: the added code seeds the hostname from the
    # template once and routes it through setHostName (which sets m_customHostName,
    # taking the field off the name-derived auto-update path).
    p = pkgbuild.calamares_defaults_patch()
    added = [ln[1:] for ln in p.splitlines() if ln.startswith("+") and not ln.startswith("+++")]
    body = "\n".join(added)
    assert "makeHostnameSuggestion(" in body
    assert "setHostName(" in body


def test_calamares_patch_emitted_with_recipe():
    # recipe_dirs must actually emit the patch content under the recipe's filename,
    # in BOTH tiers (calamares is built the same way in each).
    for tier in (False, True):
        files = dict(pkgbuild.recipe_dirs(tier))["calamares"]
        assert files[pkgbuild.CALAMARES_DEFAULTS_PATCH_NAME] == (
            pkgbuild.calamares_defaults_patch()
        )


def test_calamares_patch_context_lines_have_leading_space():
    # A unified-diff context line MUST start with a single space (blank context
    # lines are exactly " "). If an editor stripped those, `patch` would choke.
    # Assert every non-header line is a valid diff body line.
    p = pkgbuild.calamares_defaults_patch()
    for ln in p.splitlines():
        if ln.startswith(("--- ", "+++ ", "@@ ")):
            continue
        assert ln[:1] in (" ", "+", "-"), repr(ln)
    # And there is at least one space-only context line (the blank source lines),
    # proving they survived as " " and not "".
    assert " " in p.splitlines()


def test_calamares_defaults_patch_applies_to_pinned_source():
    # THE integration guard: the patch must apply cleanly to the real, pinned
    # calamares source with the exact command the PKGBUILD runs (`patch -p1`).
    # This catches context drift on a version bump -- the failure mode where the
    # customization silently vanishes because the hunks no longer match.
    #
    # Source the two files from the PRISTINE tarball, not the .build/ scratch:
    # makepkg patches the scratch in place during prepare(), so reading it back
    # would test the patch against already-patched source (see
    # _find_calamares_tarball for the full rationale).
    tarball = _find_calamares_tarball()
    if tarball is None:
        pytest.skip("pinned calamares tarball not present under cache/ (CI checkout)")
    if shutil.which("patch") is None:
        pytest.skip("`patch` not available on this host")

    import tempfile

    # Every file the patch touches, as stored inside the tarball (prefixed by the
    # calamares-<ver>/ top-level directory the archive unpacks into). All five must be
    # extracted or `patch -p1` aborts on the first missing target.
    rels = (
        "src/modules/keyboard/KeyboardLayoutModel.cpp",
        "src/modules/users/page_usersetup.ui",
        "src/modules/users/UsersPage.cpp",
        "src/modules/users/Config.cpp",
        "src/modules/users/SetPasswordJob.cpp",
    )
    top = f"calamares-{pkgbuild.CALAMARES_VERSION}"

    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        # Extract only the two files pristine, dropping the top-level dir so the
        # -p1 a/src/... paths line up when patch runs from `work`.
        with tarfile.open(tarball, "r:gz") as tf:
            for rel in rels:
                member = tf.getmember(f"{top}/{rel}")
                fobj = tf.extractfile(member)
                assert fobj is not None, f"missing {rel} in tarball"
                dst = work / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(fobj.read())

        # Guard against silently testing already-patched source: the pristine
        # files must NOT yet contain the additions the patch introduces.
        pristine_kbd = (work / "src/modules/keyboard/KeyboardLayoutModel.cpp").read_text()
        pristine_users = (work / "src/modules/users/Config.cpp").read_text()
        pristine_ui = (work / "src/modules/users/page_usersetup.ui").read_text()
        assert "alt_shift_toggle" not in pristine_kbd
        assert "seededHostname" not in pristine_users
        # Pristine still has the ORIGINAL upstream field-prompt strings, the
        # root-only lock, and the empty-is-ok status branches (the patch changes all).
        assert "What name do you want to use to log in?" in pristine_ui
        assert "What is the name of this computer?" in pristine_ui
        assert "Choose a password to keep your account safe." in pristine_ui
        assert "Choose a password for the administrator account." in pristine_ui
        assert "User parameter must include" not in pristine_users
        assert "Hostname parameter must include" not in pristine_users

        patch_text = pkgbuild.calamares_defaults_patch()
        # Dry-run first (pure check), then a real apply (proves the result is
        # writable and the offsets are exact, not fuzz-matched).
        dry = subprocess.run(
            ["patch", "-p1", "--fuzz=0", "--dry-run"],
            input=patch_text,
            text=True,
            cwd=work,
            capture_output=True,
            timeout=30,
        )
        assert dry.returncode == 0, f"dry-run failed:\n{dry.stdout}\n{dry.stderr}"

        real = subprocess.run(
            ["patch", "-p1", "--fuzz=0"],
            input=patch_text,
            text=True,
            cwd=work,
            capture_output=True,
            timeout=30,
        )
        assert real.returncode == 0, f"apply failed:\n{real.stdout}\n{real.stderr}"

        # All behaviours actually landed in the patched source.
        kbd = (work / "src/modules/keyboard/KeyboardLayoutModel.cpp").read_text()
        assert "alt_shift_toggle" in kbd
        assert "setCurrentIndex(" in kbd
        users = (work / "src/modules/users/Config.cpp").read_text()
        assert "makeHostnameSuggestion(" in users
        assert "setHostName( seededHostname )" in users
        # isReady() is RELAXED (the Full Name row is hidden, so fullName() is always
        # empty by design). Per PROMPT.md the login IS seeded to "main" (the Username
        # field DEFAULTS to containing "main", not merely hinting it) -- the seed lives
        # in Config.cpp's setConfigurationMap(), right before setConfigurationDefaultGroups.
        assert 'setLoginName( QStringLiteral( "main" ) )' in users
        assert "readyFullName" not in users  # the full-name gate is dropped
        assert "return readyHostname && readyUsername" in users
        # Empty login / hostname now report a required-field error (was "ok").
        assert 'return tr( "User parameter must include at least one character." )' in users
        assert 'return tr( "Hostname parameter must include at least two characters." )' in users
        # The four field-prompt labels are RENAMED to short captions in the .ui; the
        # hostname placeholder becomes "azzio" and the login placeholder becomes "main"
        # (the login VALUE is also seeded to "main" in Config.cpp -- see above -- so the
        # placeholder is now the fallback hint shown only if the field is cleared). The
        # reuse checkbox is re-worded.
        ui = (work / "src/modules/users/page_usersetup.ui").read_text()
        assert "<string>Username:</string>" in ui
        assert "<string>Hostname:</string>" in ui
        assert "<string>Username Password:</string>" in ui
        assert "<string>Root Password:</string>" in ui
        assert "<string>azzio</string>" in ui           # hostname placeholder
        assert "<string>main</string>" in ui             # login placeholder + seeded value
        assert "<string>login</string>" not in ui        # old login placeholder gone
        assert "What name do you want to use to log in?" not in ui
        assert "What is the name of this computer?" not in ui
        assert "Choose a password to keep your account safe." not in ui
        assert "Choose a password for the administrator account." not in ui
        assert "Computer Name" not in ui                  # placeholder renamed
        # The reuse-password checkbox label is RE-WORDED per PROMPT.md Prompt#1.
        assert "Use username password for root password." in ui
        assert "Use the same password for the administrator account." not in ui
        # Full Name row IS hidden (each child widget) + strong-password checkbox hidden.
        # The login seed lives in Config.cpp's setConfigurationMap() (asserted above),
        # NOT here in UsersPage.cpp, so setLoginName must be ABSENT from this file.
        page = (work / "src/modules/users/UsersPage.cpp").read_text()
        assert "ui->labelWhatIsYourName->setVisible( false )" in page
        assert "ui->textBoxFullName->setVisible( false )" in page
        assert "ui->checkBoxRequireStrongPassword->setVisible( false )" in page
        assert 'setLoginName( QStringLiteral( "main" ) )' not in page
        # Empty password locks any account (root-only condition broadened).
        spj = (work / "src/modules/users/SetPasswordJob.cpp").read_text()
        assert 'if ( m_newPassword.isEmpty() )' in spj
        assert 'if ( m_userName == "root" && m_newPassword.isEmpty() )' not in spj


# --- calamares source patch (region-driven keyboard) -----------------------

def test_calamares_region_patch_name_is_a_patch_file():
    assert pkgbuild.CALAMARES_REGION_KEYBOARD_PATCH_NAME.endswith(".patch")
    # Distinct from the defaults patch (two separate files applied in sequence).
    assert (
        pkgbuild.CALAMARES_REGION_KEYBOARD_PATCH_NAME
        != pkgbuild.CALAMARES_DEFAULTS_PATCH_NAME
    )


def test_calamares_pkgbuild_references_region_patch_in_source_and_prepare():
    # The region patch must be BOTH a source() entry and applied in prepare(); the
    # defaults patch must still be too (both are applied, in order).
    s = pkgbuild.pkgbuild_calamares()
    name = pkgbuild.CALAMARES_REGION_KEYBOARD_PATCH_NAME
    assert ("'%s'" % name) in s
    assert ("patch -p1 < \"$srcdir/%s\"" % name) in s
    # Five patches present in source() -> five local files -> five SKIPs.
    assert ("sha256sums=('%s' 'SKIP' 'SKIP' 'SKIP' 'SKIP' 'SKIP')" % pkgbuild.CALAMARES_SHA256) in s
    assert s.count("'SKIP'") == 5


def test_calamares_region_patch_touches_keyboard_and_locale_modules():
    # The feature spans three files: the keyboard module header + impl (the
    # region->layout logic) and the locale module (publishing locationCountry to GS).
    p = pkgbuild.calamares_region_keyboard_patch()
    for f in (
        "src/modules/keyboard/Config.h",
        "src/modules/keyboard/Config.cpp",
        "src/modules/locale/Config.cpp",
    ):
        assert ("--- a/%s" % f) in p
        assert ("+++ b/%s" % f) in p


def test_calamares_region_patch_locale_publishes_country_to_gs():
    # The locale module must insert the selected zone's ISO-3166 country code into
    # GlobalStorage under "locationCountry" -- the only clean country signal the
    # keyboard module can key its layout table on.
    p = pkgbuild.calamares_region_keyboard_patch()
    added = [ln[1:] for ln in p.splitlines() if ln.startswith("+") and not ln.startswith("+++")]
    body = "\n".join(added)
    assert "locationCountry" in body
    assert "location->country()" in body


def test_calamares_region_patch_keeps_english_first_and_alt_shift():
    # The added logic must (a) read regionSecondLayout, (b) force "us" as the
    # additional layout (English first/active in "us,<region>"), and (c) use
    # grp:alt_shift_toggle as the switcher. Non-English scripts and Latin ones
    # (Hebrew "il", Arabic "ara", Spanish "latam") must be in the country table.
    p = pkgbuild.calamares_region_keyboard_patch()
    added = [ln[1:] for ln in p.splitlines() if ln.startswith("+") and not ln.startswith("+++")]
    body = "\n".join(added)
    assert "regionSecondLayout" in body
    assert 'additionalLayout = QStringLiteral( "us" )' in body
    assert "grp:alt_shift_toggle" in body
    assert "guessRegionKeyboardLayout" in body
    # Layout codes are the real base.lst identifiers (Hebrew is "il", not "he").
    assert '"IL", "il"' in body
    assert '"ara"' in body
    assert '"latam"' in body
    # And it must NOT map Hebrew to a bogus "he" layout code.
    assert '"IL", "he"' not in body


def test_calamares_region_patch_reguesses_on_every_activate():
    # BUG (installer keyboard does not follow the region): the stock keyboard guess
    # early-returns unless m_state==State::Initial, so after the first Keyboard visit
    # (state becomes UserSelected) changing the region on the Location page and
    # returning never re-derives the layout. The patch must relax that gate for the
    # region path so it re-runs on every activation. The gate condition must gain the
    # `&& !m_regionSecondLayout` clause (region path bypasses the Initial-only gate).
    p = pkgbuild.calamares_region_keyboard_patch()
    added = [ln[1:] for ln in p.splitlines() if ln.startswith("+") and not ln.startswith("+++")]
    body = "\n".join(added)
    assert "( m_state != State::Initial && !m_regionSecondLayout ) || !m_guessLayout" in body
    # And the ORIGINAL Initial-only gate must be REMOVED (a "-" line), not left behind
    # (else the region path would still early-return on the second visit).
    removed = [ln[1:] for ln in p.splitlines() if ln.startswith("-") and not ln.startswith("---")]
    assert "    if ( m_state != State::Initial || !m_guessLayout )" in removed


def test_calamares_region_patch_preserves_hand_picked_layout_on_revisit():
    # Re-running the region guess on every Keyboard activation (the BUG 2 gate fix) must
    # NOT clobber a layout the user hand-picked when they revisit the page WITHOUT
    # changing the region. The patch must capture whether the user had already selected
    # (m_state==UserSelected) before the scoped assignment resets it, thread it into
    # guessRegionKeyboardLayout(bool), and short-circuit when the region is unchanged
    # (country == m_regionGuessedCountry). Without this, revisiting Keyboard overwrites
    # a hand-picked primary layout back to the region layout every time.
    p = pkgbuild.calamares_region_keyboard_patch()
    added = [ln[1:] for ln in p.splitlines() if ln.startswith("+") and not ln.startswith("+++")]
    body = "\n".join(added)
    # Entry-state captured before the scoped assignment resets m_state.
    assert "const bool azUserHadSelected = ( m_state == State::UserSelected )" in body
    # Threaded into the region guess.
    assert "guessRegionKeyboardLayout( azUserHadSelected )" in body
    assert "void guessRegionKeyboardLayout( bool userHadSelected )" in body \
        or "Config::guessRegionKeyboardLayout( bool userHadSelected )" in body
    # The preserve guard: user hand-picked AND region unchanged -> return without reselecting.
    assert "userHadSelected && !m_regionGuessedCountry.isEmpty() && country == m_regionGuessedCountry" in body
    # And it must record the guessed country so a later same-region revisit is detected.
    assert "m_regionGuessedCountry = country;" in body


def test_calamares_region_patch_falls_back_to_zone_for_default_region():
    # BUG corollary: on the FIRST Keyboard activation GlobalStorage "locationCountry"
    # may not be populated yet (the locale module writes it on location-change /
    # finalize), which would make the default Asia/Jerusalem resolve to English-only
    # instead of us,il. The patch must fall back to the published "locationZone" via a
    # countryForZone() table, and the default Jerusalem MUST map to IL.
    p = pkgbuild.calamares_region_keyboard_patch()
    added = [ln[1:] for ln in p.splitlines() if ln.startswith("+") and not ln.startswith("+++")]
    body = "\n".join(added)
    assert "countryForZone" in body
    assert "locationZone" in body
    # Default region -> IL so out-of-the-box stays us,il.
    assert '{ "Jerusalem", "IL" }' in body
    # A couple of representative non-default zones the PROMPT calls out.
    assert '{ "El_Salvador", "SV" }' in body
    assert '{ "Riyadh", "SA" }' in body
    # The read must be a MUTABLE QString (so the empty-country fallback can reassign it),
    # not the old `const QString country`.
    assert "const QString country = gs->value" not in body
    assert 'QString country = gs->value( QStringLiteral( "locationCountry" ) )' in body


def test_calamares_region_patch_context_lines_have_leading_space():
    # Same unified-diff hygiene as the defaults patch: every body line begins with
    # exactly one of " ", "+", "-"; blank context lines survived as " ".
    p = pkgbuild.calamares_region_keyboard_patch()
    for ln in p.splitlines():
        if ln.startswith(("--- ", "+++ ", "@@ ")):
            continue
        assert ln[:1] in (" ", "+", "-"), repr(ln)
    assert " " in p.splitlines()


def test_calamares_region_patch_emitted_with_recipe():
    # recipe_dirs must emit the region patch under its filename in BOTH tiers.
    for tier in (False, True):
        files = dict(pkgbuild.recipe_dirs(tier))["calamares"]
        assert files[pkgbuild.CALAMARES_REGION_KEYBOARD_PATCH_NAME] == (
            pkgbuild.calamares_region_keyboard_patch()
        )


# --- calamares source patch (hide Back + Next on Finish AND during Install) ---

def test_calamares_finish_buttons_patch_name_is_a_distinct_patch_file():
    n = pkgbuild.CALAMARES_FINISH_BUTTONS_PATCH_NAME
    assert n.endswith(".patch")
    # Distinct from the other two calamares patches (three separate files).
    assert n != pkgbuild.CALAMARES_DEFAULTS_PATCH_NAME
    assert n != pkgbuild.CALAMARES_REGION_KEYBOARD_PATCH_NAME


def test_calamares_finish_buttons_patch_touches_viewmanager_and_hides_both():
    # The patch must be a -p1 unified diff on ViewManager.cpp with TWO hunks, each
    # adding an updateBackAndNextVisibility(false) call: one in the isAtVeryEnd()
    # (Finish page) branch and one in the else branch gated on stepIsExecute() so the
    # running Install (exec) step also hides Back+Next (PROMPT.md: they are greyed out
    # during install anyway).
    p = pkgbuild.calamares_finish_buttons_patch()
    assert "--- a/src/libcalamaresui/ViewManager.cpp" in p
    assert "+++ b/src/libcalamaresui/ViewManager.cpp" in p
    # Two hunks -> four "@@" markers.
    assert p.count("@@") == 4
    added = [ln[1:] for ln in p.splitlines() if ln.startswith("+") and not ln.startswith("+++")]
    body = "\n".join(added)
    # Both nav buttons hidden -- twice (once per branch).
    assert body.count("updateBackAndNextVisibility( false )") == 2
    # The exec-step guard: only the running install step matches.
    assert "if ( stepIsExecute( m_steps, m_currentStep ) )" in body


def test_calamares_finish_buttons_patch_context_lines_have_leading_space():
    # Same unified-diff hygiene as the sibling patches: every body line begins with
    # exactly one of " ", "+", "-".
    p = pkgbuild.calamares_finish_buttons_patch()
    for ln in p.splitlines():
        if ln.startswith(("--- ", "+++ ", "@@ ")):
            continue
        assert ln[:1] in (" ", "+", "-"), repr(ln)


def test_calamares_finish_buttons_patch_emitted_with_recipe():
    # recipe_dirs must emit the finish-buttons patch under its filename in BOTH tiers.
    for tier in (False, True):
        files = dict(pkgbuild.recipe_dirs(tier))["calamares"]
        assert files[pkgbuild.CALAMARES_FINISH_BUTTONS_PATCH_NAME] == (
            pkgbuild.calamares_finish_buttons_patch()
        )


def test_calamares_finish_buttons_patch_applies_to_pinned_source():
    # Integration guard: the finish-buttons patch must apply cleanly to the real,
    # pinned calamares source with `patch -p1` (catches context drift on a version
    # bump). Source ViewManager.cpp from the PRISTINE tarball, not the .build scratch.
    tarball = _find_calamares_tarball()
    if tarball is None:
        pytest.skip("pinned calamares tarball not present under cache/ (CI checkout)")
    if shutil.which("patch") is None:
        pytest.skip("`patch` not available on this host")

    import tempfile

    rel = "src/libcalamaresui/ViewManager.cpp"
    top = f"calamares-{pkgbuild.CALAMARES_VERSION}"
    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        with tarfile.open(tarball, "r:gz") as tf:
            member = tf.getmember(f"{top}/{rel}")
            fobj = tf.extractfile(member)
            assert fobj is not None, f"missing {rel} in tarball"
            dst = work / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(fobj.read())

        # Pristine guard: the added call must not already be present.
        pristine = (work / rel).read_text()
        assert "updateBackAndNextVisibility( false )" not in pristine

        patch_text = pkgbuild.calamares_finish_buttons_patch()
        dry = subprocess.run(
            ["patch", "-p1", "--fuzz=0", "--dry-run"],
            input=patch_text, text=True, cwd=work, capture_output=True, timeout=30,
        )
        assert dry.returncode == 0, f"dry-run failed:\n{dry.stdout}\n{dry.stderr}"
        real = subprocess.run(
            ["patch", "-p1", "--fuzz=0"],
            input=patch_text, text=True, cwd=work, capture_output=True, timeout=30,
        )
        assert real.returncode == 0, f"apply failed:\n{real.stdout}\n{real.stderr}"

        # Both hide-calls landed: one in the very-end (Finish) branch and one in the
        # else branch guarded by stepIsExecute() for the running Install (exec) step.
        patched = (work / rel).read_text()
        assert patched.count("updateBackAndNextVisibility( false )") == 2
        # The exec-step guard sits in the else branch, right after updateCancelEnabled.
        assert (
            "if ( stepIsExecute( m_steps, m_currentStep ) )\n"
            "        {\n"
            "            updateBackAndNextVisibility( false );\n"
            "        }"
        ) in patched


def test_both_calamares_patches_apply_in_sequence_to_pinned_source():
    # THE integration guard for the region feature: BOTH patches must apply cleanly,
    # IN THE ORDER prepare() runs them (defaults first, then region), to the real
    # pinned source. They touch disjoint files/regions, so this also proves they do
    # not conflict. Catches context drift on a version bump for either patch.
    tarball = _find_calamares_tarball()
    if tarball is None:
        pytest.skip("pinned calamares tarball not present under cache/ (CI checkout)")
    if shutil.which("patch") is None:
        pytest.skip("`patch` not available on this host")

    import tempfile

    # Union of every file the two patches touch, extracted pristine. The defaults patch
    # touches five (keyboard model + the four users-module files); the region patch adds
    # keyboard/locale files.
    rels = (
        "src/modules/keyboard/KeyboardLayoutModel.cpp",
        "src/modules/users/page_usersetup.ui",
        "src/modules/users/UsersPage.cpp",
        "src/modules/users/Config.cpp",
        "src/modules/users/SetPasswordJob.cpp",
        "src/modules/keyboard/Config.h",
        "src/modules/keyboard/Config.cpp",
        "src/modules/locale/Config.cpp",
    )
    top = f"calamares-{pkgbuild.CALAMARES_VERSION}"

    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        with tarfile.open(tarball, "r:gz") as tf:
            for rel in rels:
                member = tf.getmember(f"{top}/{rel}")
                fobj = tf.extractfile(member)
                assert fobj is not None, f"missing {rel} in tarball"
                dst = work / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(fobj.read())

        # Pristine guard: the region additions must not already be present.
        assert "guessRegionKeyboardLayout" not in (work / "src/modules/keyboard/Config.cpp").read_text()
        assert "locationCountry" not in (work / "src/modules/locale/Config.cpp").read_text()

        # Apply defaults THEN region, exactly as prepare() does. Dry-run each first.
        for patch_text in (
            pkgbuild.calamares_defaults_patch(),
            pkgbuild.calamares_region_keyboard_patch(),
        ):
            dry = subprocess.run(
                ["patch", "-p1", "--fuzz=0", "--dry-run"],
                input=patch_text, text=True, cwd=work, capture_output=True, timeout=30,
            )
            assert dry.returncode == 0, f"dry-run failed:\n{dry.stdout}\n{dry.stderr}"
            real = subprocess.run(
                ["patch", "-p1", "--fuzz=0"],
                input=patch_text, text=True, cwd=work, capture_output=True, timeout=30,
            )
            assert real.returncode == 0, f"apply failed:\n{real.stdout}\n{real.stderr}"

        # The region feature actually landed in the patched source.
        kbd_cpp = (work / "src/modules/keyboard/Config.cpp").read_text()
        assert "guessRegionKeyboardLayout" in kbd_cpp
        assert "regionLayoutForCountry" in kbd_cpp
        kbd_h = (work / "src/modules/keyboard/Config.h").read_text()
        assert "m_regionSecondLayout" in kbd_h
        loc = (work / "src/modules/locale/Config.cpp").read_text()
        assert 'gs->insert( countryKey, location->country() )' in loc


def test_all_three_calamares_patches_apply_in_sequence_to_pinned_source():
    # THE full integration guard: all THREE calamares patches must apply cleanly, IN
    # prepare() ORDER (defaults, region-keyboard, finish-buttons), to the real pinned
    # source. finish-buttons touches ViewManager.cpp (disjoint from the other two), so
    # this also proves the three do not conflict. Verifies BOTH PROMPT.md changes land:
    # the login is seeded to "main" and Back+Next are hidden on the exec step.
    tarball = _find_calamares_tarball()
    if tarball is None:
        pytest.skip("pinned calamares tarball not present under cache/ (CI checkout)")
    if shutil.which("patch") is None:
        pytest.skip("`patch` not available on this host")

    import tempfile

    rels = (
        "src/modules/keyboard/KeyboardLayoutModel.cpp",
        "src/modules/users/page_usersetup.ui",
        "src/modules/users/UsersPage.cpp",
        "src/modules/users/Config.cpp",
        "src/modules/users/SetPasswordJob.cpp",
        "src/modules/keyboard/Config.h",
        "src/modules/keyboard/Config.cpp",
        "src/modules/locale/Config.cpp",
        "src/libcalamaresui/ViewManager.cpp",
    )
    top = f"calamares-{pkgbuild.CALAMARES_VERSION}"

    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        with tarfile.open(tarball, "r:gz") as tf:
            for rel in rels:
                member = tf.getmember(f"{top}/{rel}")
                fobj = tf.extractfile(member)
                assert fobj is not None, f"missing {rel} in tarball"
                dst = work / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(fobj.read())

        # Pristine guards: neither PROMPT.md change is present upstream.
        assert 'setLoginName( QStringLiteral( "main" ) )' not in (
            work / "src/modules/users/Config.cpp"
        ).read_text()
        assert "updateBackAndNextVisibility( false )" not in (
            work / "src/libcalamaresui/ViewManager.cpp"
        ).read_text()

        for patch_text in (
            pkgbuild.calamares_defaults_patch(),
            pkgbuild.calamares_region_keyboard_patch(),
            pkgbuild.calamares_finish_buttons_patch(),
        ):
            dry = subprocess.run(
                ["patch", "-p1", "--fuzz=0", "--dry-run"],
                input=patch_text, text=True, cwd=work, capture_output=True, timeout=30,
            )
            assert dry.returncode == 0, f"dry-run failed:\n{dry.stdout}\n{dry.stderr}"
            real = subprocess.run(
                ["patch", "-p1", "--fuzz=0"],
                input=patch_text, text=True, cwd=work, capture_output=True, timeout=30,
            )
            assert real.returncode == 0, f"apply failed:\n{real.stdout}\n{real.stderr}"

        # Both PROMPT.md changes landed.
        assert 'setLoginName( QStringLiteral( "main" ) )' in (
            work / "src/modules/users/Config.cpp"
        ).read_text()
        assert (
            work / "src/libcalamaresui/ViewManager.cpp"
        ).read_text().count("updateBackAndNextVisibility( false )") == 2


# --- calamares source patch (the "Network" page: networkq QML view module) -----

def test_calamares_networkq_patch_name_is_a_distinct_patch_file():
    n = pkgbuild.CALAMARES_NETWORKQ_PATCH_NAME
    assert n.endswith(".patch")
    # Distinct from every other calamares patch (five separate files now).
    assert n not in (
        pkgbuild.CALAMARES_DEFAULTS_PATCH_NAME,
        pkgbuild.CALAMARES_REGION_KEYBOARD_PATCH_NAME,
        pkgbuild.CALAMARES_FINISH_BUTTONS_PATCH_NAME,
        pkgbuild.CALAMARES_NETWORKCFG_STATIC_PATCH_NAME,
    )


def test_calamares_networkq_patch_creates_the_module_files():
    # The patch CREATES a whole new QML view module by way of `--- /dev/null` new-file
    # hunks (it edits no existing file). Every file the module needs must be created, or
    # the module fails to build / load.
    p = pkgbuild.calamares_networkq_patch()
    for f in (
        "src/modules/networkq/Config.h",
        "src/modules/networkq/Config.cpp",
        "src/modules/networkq/NetworkQmlViewStep.h",
        "src/modules/networkq/NetworkQmlViewStep.cpp",
        "src/modules/networkq/networkq.qml",
        "src/modules/networkq/networkq.qrc",
        "src/modules/networkq/networkq.conf",
        "src/modules/networkq/CMakeLists.txt",
    ):
        assert ("+++ b/%s" % f) in p, f
    # New-file hunks: the OLD side is /dev/null and the hunk header starts at old-line 0.
    assert "--- /dev/null" in p
    assert "@@ -0,0 +1," in p
    # It must NOT touch any existing file (no `--- a/...`).
    assert "--- a/" not in p


def test_calamares_networkq_patch_publishes_five_fields_to_globalstorage():
    # The page's Config must publish the DHCP/manual method and, for manual, the five
    # static-IPv4 fields to GlobalStorage under the exact keys the networkcfg job reads.
    p = pkgbuild.calamares_networkq_patch()
    added = [ln[1:] for ln in p.splitlines() if ln.startswith("+") and not ln.startswith("+++")]
    body = "\n".join(added)
    for key in (
        "networkMethod",
        "networkIpv4",
        "networkSubnetMask",
        "networkGateway",
        "networkDns1",
        "networkDns2",
    ):
        assert key in body, key
    # The values reach GlobalStorage via gs->insert(...) in Config::finalizeGlobalStorage,
    # which the view step calls from onLeave().
    assert "finalizeGlobalStorage" in body
    assert "gs->insert(" in body
    assert "void\nNetworkQmlViewStep::onLeave()" in body or "NetworkQmlViewStep::onLeave()" in body
    # DHCP is the default method (so a user who never touches the page keeps stock DHCP).
    assert 'm_method = QStringLiteral( "dhcp" )' in body
    # The CMake plugin is a QML viewmodule guarded on WITH_QML.
    assert "calamares_add_plugin(networkq" in body
    assert "TYPE viewmodule" in body


def test_calamares_networkq_patch_context_lines_have_leading_space():
    # Same unified-diff hygiene as the sibling patches: every body line begins with
    # exactly one of " ", "+", "-" (new-file hunks are all "+"/ header lines).
    p = pkgbuild.calamares_networkq_patch()
    for ln in p.splitlines():
        if ln.startswith(("--- ", "+++ ", "@@ ")):
            continue
        assert ln[:1] in (" ", "+", "-"), repr(ln)


def test_calamares_networkq_patch_emitted_with_recipe():
    # recipe_dirs must emit the networkq patch under its filename in BOTH tiers.
    for tier in (False, True):
        files = dict(pkgbuild.recipe_dirs(tier))["calamares"]
        assert files[pkgbuild.CALAMARES_NETWORKQ_PATCH_NAME] == (
            pkgbuild.calamares_networkq_patch()
        )


def test_calamares_networkq_patch_applies_to_pinned_source():
    # Integration guard: the new-file patch must apply cleanly to the real pinned source
    # with `patch -p1` (catches a bad hunk header / drift), and the created module must be
    # a directory the src/modules CMake glob will pick up.
    tarball = _find_calamares_tarball()
    if tarball is None:
        pytest.skip("pinned calamares tarball not present under cache/ (CI checkout)")
    if shutil.which("patch") is None:
        pytest.skip("`patch` not available on this host")

    import tempfile

    top = f"calamares-{pkgbuild.CALAMARES_VERSION}"
    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        # The patch only creates files under src/modules/networkq/; it needs no existing
        # file extracted. But the parent src/modules/ must exist for `patch` to create into
        # it, so make it.
        (work / "src/modules").mkdir(parents=True)
        # Pristine guard: the module must not already exist.
        assert not (work / "src/modules/networkq").exists()

        patch_text = pkgbuild.calamares_networkq_patch()
        dry = subprocess.run(
            ["patch", "-p1", "--fuzz=0", "--dry-run"],
            input=patch_text, text=True, cwd=work, capture_output=True, timeout=30,
        )
        assert dry.returncode == 0, f"dry-run failed:\n{dry.stdout}\n{dry.stderr}"
        real = subprocess.run(
            ["patch", "-p1", "--fuzz=0"],
            input=patch_text, text=True, cwd=work, capture_output=True, timeout=30,
        )
        assert real.returncode == 0, f"apply failed:\n{real.stdout}\n{real.stderr}"

        base = work / "src/modules/networkq"
        assert (base / "CMakeLists.txt").is_file()  # glob discovers dirs with a CMakeLists
        # The view step wires the config into GlobalStorage on leave.
        assert "finalizeGlobalStorage" in (base / "NetworkQmlViewStep.cpp").read_text()
        cfg = (base / "Config.cpp").read_text()
        assert 'gs->insert( QStringLiteral( "networkMethod" )' in cfg
        assert 'gs->insert( QStringLiteral( "networkIpv4" )' in cfg


def _networkq_qml_body() -> str:
    """The added lines of ONLY the networkq.qml hunk (between its `+++ b/...networkq.qml`
    header and the next file's `--- ` header), de-prefixed. Lets the QML-content tests below
    assert on the page markup without re-reading the whole patch."""
    lines = pkgbuild.calamares_networkq_patch().splitlines()
    start = lines.index("+++ b/src/modules/networkq/networkq.qml") + 1
    out = []
    for ln in lines[start:]:
        if ln.startswith("--- "):
            break
        if ln.startswith("+") and not ln.startswith("+++"):
            out.append(ln[1:])
    return "\n".join(out)


def test_calamares_networkq_qml_paints_an_opaque_dark_background():
    # Issue: the Network page's background did not match the other pages, so it was
    # impossible to read/navigate. Root cause: the QML was a bare transparent Item with no
    # background. The page must paint its OWN opaque background rectangle in the SAME colour
    # every other installer page body uses -- #323232 (the user explicitly asked for
    # "323232") -- with light text. This pins the fix so it cannot regress to a transparent
    # page NOR to the earlier near-black #030712 that did not match the other sections.
    qml = _networkq_qml_body()
    assert "Rectangle {" in qml                      # a real background rect exists
    assert "#323232" in qml                          # the exact page-body colour the user asked for
    assert "#030712" not in qml                      # the old mismatched near-black is gone
    assert 'color: networkPage.bgColor' in qml       # the rect is filled with it
    # Light foreground colours so text is readable on the dark background.
    assert "#ffffff" in qml                          # white headings
    assert "#3b82f6" in qml                           # blue accent


def test_calamares_networkq_qml_does_not_import_kirigami():
    # The Azzio ISO ships qt6-declarative (QtQuick/Controls/Layouts) but NOT kirigami --
    # only the widget `users` module is used, so kirigami was never added to the manifest.
    # The stock usersq-qt6.qml uses org.kde.kirigami, but this page must NOT: an
    # `import org.kde.kirigami` would fail to resolve at runtime on the ISO and the page
    # would not render. Only ISO-present QML modules may be imported.
    qml = _networkq_qml_body()
    imports = [l.strip() for l in qml.splitlines() if l.strip().startswith("import ")]
    assert not any("kirigami" in i.lower() for i in imports), imports
    # The imports it DOES use are all shipped on the ISO.
    assert "import QtQuick" in qml
    assert "import QtQuick.Controls" in qml
    assert "import QtQuick.Layouts" in qml
    assert "import io.calamares.ui 1.0" in qml


def test_calamares_networkq_qml_fields_write_back_on_edit():
    # Issue: could not enter any data into the parameters. The five manual fields must be
    # two-way bound -- reading config.<field> AND writing it back as the user types via
    # onTextChanged (the stock usersq idiom), so keystrokes reach the C++ Config and thus
    # GlobalStorage. Guards against a field that displays but silently drops input (and the
    # earlier onTextEdited, which did not fire reliably in this context).
    #
    # The write-backs assign the PROPERTY (`config.<field> = text`), NOT the C++ setter
    # method. The Config setters (setIpv4/...) are plain Qt property WRITE accessors, NOT
    # Q_INVOKABLE/slots, so QML cannot call them by name: `config.setIpv4(text)` throws
    # "Property 'setIpv4' ... is not a function" at runtime. Assigning the property invokes
    # the same WRITE setter the supported way. Each is null-GUARDED (`if ( config ) ...`):
    # config is a context property momentarily null during async QML load.
    qml = _networkq_qml_body()
    for field in ("ipv4", "subnetMask", "gateway", "dns1", "dns2"):
        assign = "config.%s = text" % field
        assert assign in qml, assign
        assert ("if ( config ) " + assign) in qml, "unguarded write-back: " + assign
    assert "onTextChanged:" in qml
    # The C++ setter METHODS must never be called by name from QML -- they are not invokable
    # and doing so is exactly what left every field greyed (the onClicked handler threw
    # before config.method ever changed). This is the regression guard for the live fix.
    import re
    code = "\n".join(raw.split("//", 1)[0] for raw in qml.splitlines())
    bad = re.findall(r"config\.set[A-Z]\w*\s*\(", code)
    assert not bad, "non-invokable C++ setter called from QML: " + repr(bad)
    # The manual section is gated on the Manual radio (DHCP is the default), and picking
    # Manual enables it -- so the radios assign the method property (null-guarded).
    assert 'if ( config ) config.method = "manual"' in qml
    assert 'if ( config ) config.method = "dhcp"' in qml


def test_calamares_networkq_qml_radios_use_native_text_not_custom_contentitem():
    # Issue: "Automatic (DHCP) / Manual and their toggle buttons are not positioned
    # correctly" -- the indicator circle rendered ON TOP of the label. Root cause: each
    # RadioButton set a custom `contentItem: Text {...}` and left the control's own `text`
    # property EMPTY. The Basic style positions the indicator with
    #   x: control.text ? leftPadding : leftPadding + (availableWidth - width)/2
    # so with empty text the indicator is CENTRED over the available width, landing over the
    # label. The fix is to use each RadioButton's NATIVE `text` (indicator stays pinned left,
    # label beside it) and colour it via the palette. Pin both halves of the fix so it can
    # neither regress to a custom contentItem nor drop the native text.
    qml = _networkq_qml_body()
    assert 'text: qsTr("Automatic (DHCP)")' in qml
    assert 'text: qsTr("Manual")' in qml
    # No custom contentItem override on the controls (that was the defect).
    assert "contentItem: Text" not in qml
    # The label/indicator colours come from the palette so the dark theme is preserved
    # without fighting the control's own layout.
    assert "palette.windowText: networkPage.headingColor" in qml
    assert "palette.text: networkPage.accentColor" in qml


def test_calamares_networkq_qml_guards_every_config_access_against_null():
    # Defensive null-guarding of the `config` context property. `config` is a QObject
    # CONTEXT PROPERTY and QML bindings are re-evaluated across the component's life, so
    # every access is guarded (`config &&` for checked, `config ? ... : ""` for reads,
    # `if ( config )` for setters) rather than assuming a bare `config.method` never sees
    # null. (Note: this guarding is defensive, NOT the fix for the greyed-out bug -- see
    # test_calamares_networkq_qml_enables_fields_per_field_not_via_layout for that. The
    # earlier hoisted `property bool manual: config.method === "manual"` is gone regardless.)
    qml = _networkq_qml_body()
    # CODE-only view: strip // comments (they quote the old buggy code to explain the fix,
    # and would otherwise trip the "must be gone" assertions below).
    code = "\n".join(raw.split("//", 1)[0] for raw in qml.splitlines())

    # The fragile hoisted intermediate property must be gone; isManual reads config directly.
    assert "property bool manual:" not in code, "hoisted manual property reintroduced"
    assert "enabled: networkPage.manual" not in code
    assert 'config ? config.method === "manual" : false' in code

    # No BARE `config.<name>` may remain: every occurrence must be preceded by a guard
    # (`config ?`, `config &&`) or be a guarded call (`if ( config ) config.setX`). We scan
    # the CODE lines only (skip // comments, which discuss the fix and mention config.*).
    import re
    bare = []
    for raw in qml.splitlines():
        line = raw.split("//", 1)[0]          # strip trailing/whole-line comments
        for m in re.finditer(r"config\.", line):
            before = line[: m.start()]
            # allowed immediately-preceding guards on the same line
            if before.rstrip().endswith("config ?") or before.rstrip().endswith("config &&"):
                continue
            if "if ( config ) config." in line:
                continue
            bare.append(raw.strip())
    assert not bare, "un-guarded config access(es): " + repr(bare)

    # And the guarded read form is actually used for the five fields.
    for field in ("ipv4", "subnetMask", "gateway", "dns1", "dns2"):
        assert ("config ? config.%s : \"\"" % field) in qml, field


def test_calamares_networkq_qml_enables_fields_per_field_not_via_layout():
    # The TRUE root cause of the LIVE "clicking Manual never un-greys the five inputs" bug was
    # that the radio onClicked handler called `config.setMethod("manual")` -- a non-invokable
    # C++ setter -- which threw before config.method ever changed, so isManual stayed false.
    # That is fixed by property assignment (see test_..._fields_write_back_on_edit). This test
    # pins a SECOND, independent hardening from the same investigation: bind `enabled` on each
    # leaf TextField rather than on the GridLayout. On the real Qt6 build a QtQuick.Layouts
    # item's `enabled` does NOT reliably cascade to the children it positions, so gating the
    # layout alone could leave fields disabled; EVERY stock Qt6 Calamares page sets `enabled:`
    # on the leaf control (usersq-qt6.qml: `enabled: config.isEditable(...)` on each TextField)
    # and NEVER on a Layout. So: bind `enabled: manualGrid.isManual` on each of the five
    # TextFields; keep only `opacity` on the GridLayout (opacity DOES cascade, so the whole
    # section still dims when DHCP is selected).
    qml = _networkq_qml_body()
    code = "\n".join(raw.split("//", 1)[0] for raw in qml.splitlines())

    # opacity stays on the layout (cascades fine); `enabled` must NOT be on the layout.
    assert "opacity: isManual ? 1.0 : 0.4" in code
    assert "enabled: isManual" not in code, "layout-level enabled reintroduced (does not cascade on Qt6)"

    # Each of the five fields gates its OWN enabled on the shared isManual flag.
    field_enable = "enabled: manualGrid.isManual"
    assert code.count(field_enable) == 5, (
        "expected all 5 TextFields to bind %r, found %d" % (field_enable, code.count(field_enable)))

    # Structural belt-and-suspenders: the `enabled: manualGrid.isManual` lines sit inside the
    # TextField blocks (each field has an id), not on the GridLayout declaration line.
    for fid in ("ipv4Field", "subnetField", "gatewayField", "dns1Field", "dns2Field"):
        assert ("id: " + fid) in code, fid


def test_calamares_networkq_qml_has_no_descriptive_paragraph():
    # The user explicitly asked to remove the "Choose how this computer gets its network
    # address..." paragraph. Only the "Network" heading, the two radios, and the fields
    # remain. Pin its removal so it cannot creep back.
    qml = _networkq_qml_body()
    assert "Choose how this computer" not in qml
    assert "asks the network for one" not in qml


def test_calamares_networkq_qml_textfield_backgrounds_reference_field_by_id():
    # A TextField's inline `background:` delegate must NOT use `parent.activeFocus`: inside
    # the delegate `parent` is not reliably the control on Qt6, so the focus highlight bound
    # to the wrong item. Each field carries an id and its background references that id.
    qml = _networkq_qml_body()
    assert "parent.activeFocus" not in qml
    for fid in ("ipv4Field", "subnetField", "gatewayField", "dns1Field", "dns2Field"):
        assert ("id: " + fid) in qml, fid
        assert (fid + ".activeFocus") in qml, fid


def test_calamares_networkq_qml_flickable_does_not_overscroll_when_content_fits():
    # Issue: "you can scroll up and down a tiny bit which is completely stupid" -- the page
    # jiggled even though its content fits the viewport. Root cause: the Flickable used the
    # DEFAULT boundsBehavior (DragAndOvershootBounds), which lets the user drag past the
    # bounds regardless of content size, PLUS a phantom `contentHeight: implicitHeight + 48`
    # that inflated the scrollable range. The fix pins boundsBehavior to StopAtBounds and
    # sizes contentHeight to the content's real bottom edge (y + implicitHeight + margin).
    qml = _networkq_qml_body()
    assert "boundsBehavior: Flickable.StopAtBounds" in qml
    # contentHeight is the real content bottom, not implicitHeight + a phantom pad.
    assert "contentHeight: content.y + content.implicitHeight + 24" in qml
    assert "implicitHeight + 48" not in qml
    # contentWidth pinned to the viewport so it never scrolls sideways either.
    assert "contentWidth: width" in qml


def test_calamares_networkq_qml_root_size_is_intrinsic_not_hardcoded():
    # Inside Calamares the QmlViewStep BINDS the root item's width/height to the embedding
    # window's content item (Qt6 path in QmlViewStep.cpp), so a hard-coded `width: 800 /
    # height: 520` on the root is just overwritten -- and worse, mixing a fixed size with a
    # Flickable + Layout made the content geometry ambiguous. Use implicitWidth/Height as a
    # standalone-preview fallback and let Calamares drive the real size.
    qml = _networkq_qml_body()
    assert "implicitWidth: 800" in qml
    assert "implicitHeight: 520" in qml
    # The root Item must NOT hard-code width/height (that was overwritten and misleading).
    root = qml.split("Item {", 1)[1].split("Rectangle {", 1)[0]
    assert "width: 800" not in root, root
    assert "height: 520" not in root, root


# --- calamares source patch (networkcfg writes a static NM profile) ------------

def test_calamares_networkcfg_static_patch_name_is_a_distinct_patch_file():
    n = pkgbuild.CALAMARES_NETWORKCFG_STATIC_PATCH_NAME
    assert n.endswith(".patch")
    assert n not in (
        pkgbuild.CALAMARES_DEFAULTS_PATCH_NAME,
        pkgbuild.CALAMARES_REGION_KEYBOARD_PATCH_NAME,
        pkgbuild.CALAMARES_FINISH_BUTTONS_PATCH_NAME,
        pkgbuild.CALAMARES_NETWORKQ_PATCH_NAME,
    )


def test_calamares_networkcfg_static_patch_touches_only_networkcfg_main():
    # The patch EDITS exactly one existing file: the networkcfg python job.
    p = pkgbuild.calamares_networkcfg_static_patch()
    assert "--- a/src/modules/networkcfg/main.py" in p
    assert "+++ b/src/modules/networkcfg/main.py" in p
    # Disjoint from the other patches -> no other file is touched.
    assert p.count("--- a/") == 1


def test_calamares_networkcfg_static_patch_writes_0600_manual_profile():
    # The added job logic must: gate on method == "manual", convert the dotted netmask to
    # a CIDR prefix, write /etc/NetworkManager/system-connections/azzio-static.nmconnection
    # at 0600 (NetworkManager ignores world-readable system-connections), and format the
    # keyfile with method=manual + address1=<ip>/<prefix>,<gateway> + dns=...;.
    p = pkgbuild.calamares_networkcfg_static_patch()
    added = [ln[1:] for ln in p.splitlines() if ln.startswith("+") and not ln.startswith("+++")]
    body = "\n".join(added)
    assert 'gs.value("networkMethod") != "manual"' in body      # DHCP short-circuit
    assert "_azzio_netmask_to_prefix" in body                   # mask -> prefix helper
    assert "azzio-static.nmconnection" in body                  # target keyfile
    assert "0o600" in body                                       # NM requires 0600
    assert "method=manual" in body
    assert '"address1="' in body
    assert "dns=" in body
    # A blank gateway must NOT leave a trailing comma on address1 (NM rejects it).
    assert '("," + gateway if gateway else "")' in body
    # The five GlobalStorage keys the page publishes are all consumed here.
    for key in (
        "networkIpv4",
        "networkSubnetMask",
        "networkGateway",
        "networkDns1",
        "networkDns2",
    ):
        assert key in body, key
    # It reads the target root (already read at the top of run()) and is CALLED from run().
    assert "_azzio_write_static_connection(root_mount_point)" in body


def test_calamares_networkcfg_static_patch_context_lines_have_leading_space():
    p = pkgbuild.calamares_networkcfg_static_patch()
    for ln in p.splitlines():
        if ln.startswith(("--- ", "+++ ", "@@ ")):
            continue
        assert ln[:1] in (" ", "+", "-"), repr(ln)


def test_calamares_networkcfg_static_patch_emitted_with_recipe():
    for tier in (False, True):
        files = dict(pkgbuild.recipe_dirs(tier))["calamares"]
        assert files[pkgbuild.CALAMARES_NETWORKCFG_STATIC_PATCH_NAME] == (
            pkgbuild.calamares_networkcfg_static_patch()
        )


def test_calamares_networkcfg_static_patch_applies_and_main_compiles():
    # Integration guard: the patch applies to the pinned networkcfg/main.py and the result
    # is still valid Python (a broken insertion would only surface here, at exec time on
    # the target, not at build time).
    tarball = _find_calamares_tarball()
    if tarball is None:
        pytest.skip("pinned calamares tarball not present under cache/ (CI checkout)")
    if shutil.which("patch") is None:
        pytest.skip("`patch` not available on this host")

    import py_compile
    import tempfile

    rel = "src/modules/networkcfg/main.py"
    top = f"calamares-{pkgbuild.CALAMARES_VERSION}"
    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        with tarfile.open(tarball, "r:gz") as tf:
            member = tf.getmember(f"{top}/{rel}")
            fobj = tf.extractfile(member)
            assert fobj is not None, f"missing {rel} in tarball"
            dst = work / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(fobj.read())

        pristine = (work / rel).read_text()
        assert "_azzio_write_static_connection" not in pristine

        patch_text = pkgbuild.calamares_networkcfg_static_patch()
        dry = subprocess.run(
            ["patch", "-p1", "--fuzz=0", "--dry-run"],
            input=patch_text, text=True, cwd=work, capture_output=True, timeout=30,
        )
        assert dry.returncode == 0, f"dry-run failed:\n{dry.stdout}\n{dry.stderr}"
        real = subprocess.run(
            ["patch", "-p1", "--fuzz=0"],
            input=patch_text, text=True, cwd=work, capture_output=True, timeout=30,
        )
        assert real.returncode == 0, f"apply failed:\n{real.stdout}\n{real.stderr}"

        patched = (work / rel).read_text()
        assert "azzio-static.nmconnection" in patched
        assert "_azzio_write_static_connection(root_mount_point)" in patched
        # The patched job must still compile.
        py_compile.compile(str(work / rel), doraise=True)


def test_all_five_calamares_patches_apply_in_sequence_to_pinned_source():
    # THE full integration guard: all FIVE calamares patches must apply cleanly, IN
    # prepare() ORDER (defaults, region-keyboard, finish-buttons, networkq,
    # networkcfg-static), to the real pinned source. The two network patches touch a NEW
    # module dir and networkcfg/main.py respectively -- disjoint from the first three and
    # from each other -- so this also proves the five do not conflict.
    tarball = _find_calamares_tarball()
    if tarball is None:
        pytest.skip("pinned calamares tarball not present under cache/ (CI checkout)")
    if shutil.which("patch") is None:
        pytest.skip("`patch` not available on this host")

    import py_compile
    import tempfile

    # Files the EDIT patches need extracted (the networkq patch only creates files).
    rels = (
        "src/modules/keyboard/KeyboardLayoutModel.cpp",
        "src/modules/users/page_usersetup.ui",
        "src/modules/users/UsersPage.cpp",
        "src/modules/users/Config.cpp",
        "src/modules/users/SetPasswordJob.cpp",
        "src/modules/keyboard/Config.h",
        "src/modules/keyboard/Config.cpp",
        "src/modules/locale/Config.cpp",
        "src/libcalamaresui/ViewManager.cpp",
        "src/modules/networkcfg/main.py",
    )
    top = f"calamares-{pkgbuild.CALAMARES_VERSION}"

    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        with tarfile.open(tarball, "r:gz") as tf:
            for rel in rels:
                member = tf.getmember(f"{top}/{rel}")
                fobj = tf.extractfile(member)
                assert fobj is not None, f"missing {rel} in tarball"
                dst = work / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(fobj.read())

        # Pristine guards for the two new features.
        assert not (work / "src/modules/networkq").exists()
        assert "_azzio_write_static_connection" not in (
            work / "src/modules/networkcfg/main.py"
        ).read_text()

        for patch_text in (
            pkgbuild.calamares_defaults_patch(),
            pkgbuild.calamares_region_keyboard_patch(),
            pkgbuild.calamares_finish_buttons_patch(),
            pkgbuild.calamares_networkq_patch(),
            pkgbuild.calamares_networkcfg_static_patch(),
        ):
            dry = subprocess.run(
                ["patch", "-p1", "--fuzz=0", "--dry-run"],
                input=patch_text, text=True, cwd=work, capture_output=True, timeout=30,
            )
            assert dry.returncode == 0, f"dry-run failed:\n{dry.stdout}\n{dry.stderr}"
            real = subprocess.run(
                ["patch", "-p1", "--fuzz=0"],
                input=patch_text, text=True, cwd=work, capture_output=True, timeout=30,
            )
            assert real.returncode == 0, f"apply failed:\n{real.stdout}\n{real.stderr}"

        # The Network feature landed: the page module exists and the job writes the profile.
        assert (work / "src/modules/networkq/CMakeLists.txt").is_file()
        assert "azzio-static.nmconnection" in (
            work / "src/modules/networkcfg/main.py"
        ).read_text()
        py_compile.compile(str(work / "src/modules/networkcfg/main.py"), doraise=True)


# --- brace-doubling invariant across every generator -----------------------

def test_no_leftover_double_braces():
    # Any surviving '{{' or '}}' means an f-string brace was not properly doubled
    # -- the shell would then see a literal double brace and misbehave. Also
    # confirm a real shell expansion ('${...}') survived, proving the collapse
    # actually happened rather than the string being brace-free by accident.
    for gen in (
        pkgbuild.pkgbuild_calamares,
        pkgbuild.pkgbuild_librewolf,
        pkgbuild.pkgbuild_librewolf_src,
    ):
        out = gen()
        assert "{{" not in out, gen.__name__
        assert "}}" not in out, gen.__name__
        assert "${" in out, gen.__name__


# --- companion files --------------------------------------------------------

def test_desktop_exec_path_matches_install():
    # The .desktop Exec= and the package()'d binary must point at the SAME path,
    # or the menu entry launches nothing.
    desktop = pkgbuild.librewolf_desktop()
    assert "Exec=/opt/librewolf/librewolf %u" in desktop
    # Cross-check: the repackage PKGBUILD installs the tree at /opt/librewolf and
    # symlinks the same binary.
    pb = pkgbuild.pkgbuild_librewolf()
    assert "/opt/librewolf" in pb
    assert "/opt/librewolf/librewolf" in pb


def test_overrides_first_line_is_comment():
    # AutoConfig files: the engine ignores line 1, so it MUST be a comment.
    from packages import librewolf as lw_patch

    first = lw_patch.overrides_cfg().splitlines()[0]
    assert first.startswith("//")


def test_overrides_disables_sanitize_on_shutdown():
    from packages import librewolf as lw_patch

    cfg = lw_patch.overrides_cfg()
    assert (
        'defaultPref("privacy.sanitize.sanitizeOnShutdown", false);' in cfg
    )


def test_overrides_land_on_timedate_home_and_keep_logins():
    # Azzio's default home page is the local timedate site (localhost:49154), and the
    # browser must LAND on it: browser.startup.homepage = that URL AND browser.startup.
    # page = 1 (open the HOME page on startup). This REPLACED the old restore-session
    # (page = 3) behaviour per the spec ("LibreWolf should default to land on it").
    # Logins must still persist though, so browser.sessionstore.privacy_level stays 0
    # (LibreWolf defaults it to 2 = "save no session data", which would log sites out);
    # that + sanitizeOnShutdown=false is the cookie-persistence half.
    from packages import librewolf as lw_patch
    from packages.librewolf import timedate as td

    cfg = lw_patch.overrides_cfg()
    assert f'defaultPref("browser.startup.homepage", "{td.URL}");' in cfg
    assert 'defaultPref("browser.startup.page", 1);' in cfg
    # We open the home page now, NOT restore the previous session.
    assert 'defaultPref("browser.startup.page", 3);' not in cfg
    # Login persistence half is retained.
    assert 'defaultPref("browser.sessionstore.privacy_level", 0);' in cfg
    # network.cookie.lifetimePolicy is OBSOLETE in modern Firefox/LibreWolf (the
    # engine migrates it away and ClearUser()s it), so it must NOT be set anymore.
    assert "network.cookie.lifetimePolicy" not in cfg


def test_overrides_hide_bookmarks_toolbar_by_default():
    # "For quick access" (the bookmarks toolbar, Ctrl+Shift+B) must be HIDDEN by
    # default. LibreWolf ships browser.toolbars.bookmarks.visibility="always"; our
    # override sets it to "never" (hides it on every window AND the new-tab page).
    from packages import librewolf as lw_patch

    cfg = lw_patch.overrides_cfg()
    assert (
        'defaultPref("browser.toolbars.bookmarks.visibility", "never");' in cfg
    )


def test_overrides_follow_system_theme_and_report_prefers_color_scheme():
    # The browser follows the system theme (dark by default), AND -- the fix for the
    # "timedate home page is only ever white" bug -- it must report the REAL
    # prefers-color-scheme to web content. Stock LibreWolf's RFP hard-forces
    # prefers-color-scheme=light, so we swap RFP for FPP with every target except the
    # colour-scheme spoof; only then do ui.systemUsesDarkTheme / content-override (and the
    # timedate page) actually follow the theme.
    from packages import librewolf as lw_patch

    dark = lw_patch.overrides_cfg(dark=True)
    light = lw_patch.overrides_cfg(dark=False)
    # The theme prefs flip with dark/white.
    assert 'defaultPref("ui.systemUsesDarkTheme", 1);' in dark
    assert 'defaultPref("layout.css.prefers-color-scheme.content-override", 0);' in dark
    assert 'defaultPref("ui.systemUsesDarkTheme", 0);' in light
    assert 'defaultPref("layout.css.prefers-color-scheme.content-override", 1);' in light
    # The RFP -> FPP swap (constant in both) that makes prefers-color-scheme reach content.
    for cfg in (dark, light):
        assert 'defaultPref("privacy.resistFingerprinting", false);' in cfg
        assert 'defaultPref("privacy.fingerprintingProtection", true);' in cfg
        assert (
            'defaultPref("privacy.fingerprintingProtection.overrides", '
            '"+AllTargets,-CSSPrefersColorScheme");' in cfg
        )


def test_overrides_delivered_to_profile_path_not_opt():
    # THE load-bearing fact (this was the regression): LibreWolf's AutoConfig loader
    # reads librewolf.overrides.cfg from the user's PROFILE/CONFIG dir
    # (~/.config/librewolf/librewolf/librewolf.overrides.cfg -- doubled "librewolf"),
    # NEVER from /opt. So the override must be delivered as a HOME file by the patch's
    # emit_plan(), and the PKGBUILD must NOT ship it into /opt (a dead file there).
    from packages import librewolf as lw_patch

    plan = lw_patch.emit_plan()
    assert len(plan) == 1
    entry = plan[0]
    assert entry["dest"] == (
        "/home/main/.config/librewolf/librewolf/librewolf.overrides.cfg"
    ), "override must land at the profile path LibreWolf actually reads"
    assert entry["owner"] == "home"          # chowned 1000:998 + mirrored into /etc/skel
    assert entry["builder"]() == lw_patch.overrides_cfg()
    # The recipe must NOT package the override into /opt anymore (it was never read).
    for pb in (pkgbuild.pkgbuild_librewolf(), pkgbuild.pkgbuild_librewolf_src()):
        assert "/opt/librewolf/librewolf.overrides.cfg" not in pb, (
            "the override must NOT be installed under /opt -- LibreWolf never reads it "
            "there; it ships as a home file via packages/librewolf.emit_plan()"
        )
    # And it is no longer a recipe companion file in either tier.
    for full in (False, True):
        files = dict(pkgbuild.recipe_dirs(full))["librewolf"]
        assert "librewolf.overrides.cfg" not in files


# --- recipe_dirs tier dispatch ---------------------------------------------

def test_recipe_dirs_default_tier():
    # DEFAULT tier: calamares first (Arch dropped extra/calamares, so it must be
    # built here now), then file_manager (built from the vendored source, which already
    # carries the symlink-resolve change), then librewolf. calamares carries its PKGBUILD
    # + the five source patches (installer UI defaults, region keyboard, finish-page
    # buttons, the Network page, and the networkcfg static-profile job); file_manager carries
    # ONLY its PKGBUILD (the source tree is copied in separately, no patch companion);
    # the librewolf dir carries PKGBUILD + the .desktop, its PKGBUILD the repackage
    # recipe (no bsys6 make targets). (Recipe-dir key is "file_manager" -- it MUST equal the
    # produced package name so the staleness gate globs file_manager-*.pkg.tar.zst.)
    dirs = pkgbuild.recipe_dirs(False)
    names = [name for name, _ in dirs]
    assert names == ["calamares", "file_manager", "librewolf"]
    assert set(dict(dirs)["calamares"]) == {
        "PKGBUILD",
        pkgbuild.CALAMARES_DEFAULTS_PATCH_NAME,
        pkgbuild.CALAMARES_REGION_KEYBOARD_PATCH_NAME,
        pkgbuild.CALAMARES_FINISH_BUTTONS_PATCH_NAME,
        pkgbuild.CALAMARES_NETWORKQ_PATCH_NAME,
        pkgbuild.CALAMARES_NETWORKCFG_STATIC_PATCH_NAME,
    }
    # file_manager builds from the VENDORED source tree (packages/file_manager), copied into the
    # recipe dir by makepkg._emit_recipes (recipe_source_trees), so PKGBUILD is the ONLY text
    # companion -- the symlink-resolve change is baked into that source, not a patch file.
    assert set(dict(dirs)["file_manager"]) == {"PKGBUILD"}
    files = dict(dirs)["librewolf"]
    # PKGBUILD + the .desktop only. The AutoConfig override is NO LONGER a companion
    # (it ships as a home file at the profile path -- /opt was never read).
    assert set(files) == {"PKGBUILD", "librewolf.desktop"}
    assert "make fetch" not in files["PKGBUILD"]


def test_recipe_dirs_full_tier():
    # FULL tier: calamares first (index 0), then file_manager, then librewolf; librewolf's
    # PKGBUILD is now the from-source recipe (has the bsys6 make targets).
    dirs = pkgbuild.recipe_dirs(True)
    names = [name for name, _ in dirs]
    assert names == ["calamares", "file_manager", "librewolf"]
    assert dirs[0][0] == "calamares"
    assert set(dict(dirs)["calamares"]) == {
        "PKGBUILD",
        pkgbuild.CALAMARES_DEFAULTS_PATCH_NAME,
        pkgbuild.CALAMARES_REGION_KEYBOARD_PATCH_NAME,
        pkgbuild.CALAMARES_FINISH_BUTTONS_PATCH_NAME,
        pkgbuild.CALAMARES_NETWORKQ_PATCH_NAME,
        pkgbuild.CALAMARES_NETWORKCFG_STATIC_PATCH_NAME,
    }
    # file_manager: PKGBUILD-only text companion in BOTH tiers (built from the vendored source).
    assert set(dict(dirs)["file_manager"]) == {"PKGBUILD"}
    assert "make fetch" in dict(dirs)["librewolf"]["PKGBUILD"]


def test_file_manager_configure_enables_maintainer_mode():
    # REGRESSION: the vendored file-manager source is a git checkout, so it ships NO pre-generated
    # built sources (thunar-marshal.c/.h, the gdbus-codegen stubs, thunar-resources.c). Those
    # are produced by rules gated behind `if MAINTAINER_MODE` in thunar/Makefile.am, and the
    # tree's configure.ac uses the bare AM_MAINTAINER_MODE() which DEFAULTS OFF. Because the
    # recipe runs its own ./configure (NOCONFIGURE=1 stops autogen before it would pass the flag
    # itself), we MUST pass --enable-maintainer-mode or `make` dies with
    # "No rule to make target 'thunar-marshal.c'". Lock the flag in, and lock in that it is
    # passed to configure AFTER the autogen bootstrap (order matters: autogen writes configure).
    s = pkgbuild.pkgbuild_file_manager()
    # Anchor on the real command lines, not the prose that also mentions these tokens: the
    # autogen bootstrap runs with NOCONFIGURE=1, the configure command opens a `\`-continued
    # block, and the flag is one of that block's indented continuation lines.
    assert "NOCONFIGURE=1 ./autogen.sh\n" in s
    autogen_at = s.index("NOCONFIGURE=1 ./autogen.sh\n")
    configure_at = s.index("./configure \\")
    flag_at = s.index("\n    --enable-maintainer-mode \\")
    assert autogen_at < configure_at < flag_at


def test_file_manager_pkgname_is_file_manager_not_thunar():
    # The package is Azzio's OWN file manager: its pkgname is `file_manager`, NOT `thunar`.
    # (It carries no provides/conflicts/replaces shim -- see the dedicated test below.)
    s = pkgbuild.pkgbuild_file_manager()
    assert "pkgname=file_manager\n" in s
    assert "pkgname=thunar\n" not in s


def test_file_manager_has_no_semver_pkgver():
    # It is our own thing, not a versioned upstream drop-in, so it carries NO semver -- the pkgver
    # is a plain monotonic integer. The old pinned 4.20.9 must be gone, and there is no
    # FILE_MANAGER_VERSION / SOURCE_VERSION constant feeding it anymore.
    s = pkgbuild.pkgbuild_file_manager()
    assert "pkgver=1\n" in s
    assert "4.20.9" not in s
    assert not hasattr(pkgbuild, "FILE_MANAGER_VERSION")
    from packages import file_manager as fm
    assert not hasattr(fm, "SOURCE_VERSION")


def test_file_manager_has_no_thunar_shim():
    # The provides/conflicts/replaces=('thunar') shim is GONE. It existed only to satisfy the two
    # Xfce plugins that `depend=('thunar')` (thunar-volman, thunar-archive-plugin) and to fence off
    # stock extra/thunar; both plugins were dropped from the manifest, so nothing depends on, names,
    # or pulls `thunar` anymore and the shim serves no one. The package name is simply `file_manager`.
    s = pkgbuild.pkgbuild_file_manager()
    assert "provides=('thunar')" not in s
    assert "conflicts=('thunar')" not in s
    assert "replaces=('thunar')" not in s


def test_recipe_dirs_companion_files_shared_across_tiers():
    # The .desktop is the sole companion file now and is identical across tiers. (The
    # AutoConfig override is no longer packaged -- it ships as a home file.)
    default_lw = dict(pkgbuild.recipe_dirs(False))["librewolf"]
    full_lw = dict(pkgbuild.recipe_dirs(True))["librewolf"]
    assert default_lw["librewolf.desktop"] == full_lw["librewolf.desktop"]
    assert default_lw["librewolf.desktop"] == pkgbuild.librewolf_desktop()
    assert "librewolf.overrides.cfg" not in default_lw
    assert "librewolf.overrides.cfg" not in full_lw


# --- calamares recipe extracted to its own module (pkgbuild_calamares) ------
# The calamares recipe (pinned facts + the 3 Azzio source patches + the PKGBUILD
# text) lives in libraries/pkgbuild_calamares.py; pkgbuild.py re-exports every name so
# the flat `pkgbuild.X` surface these tests use is unchanged, and recipe_dirs() still
# assembles the calamares dir from them. These tests lock that re-export.

# Names moved into pkgbuild_calamares and re-exported by pkgbuild.
_MOVED_CONSTANTS = (
    "CALAMARES_VERSION",
    "CALAMARES_SHA256",
    "CALAMARES_DEFAULTS_PATCH_NAME",
    "CALAMARES_REGION_KEYBOARD_PATCH_NAME",
    "CALAMARES_FINISH_BUTTONS_PATCH_NAME",
    "CALAMARES_NETWORKQ_PATCH_NAME",
    "CALAMARES_NETWORKCFG_STATIC_PATCH_NAME",
)
_MOVED_FUNCTIONS = (
    "calamares_defaults_patch",
    "calamares_region_keyboard_patch",
    "calamares_finish_buttons_patch",
    "calamares_networkq_patch",
    "calamares_networkcfg_static_patch",
    "pkgbuild_calamares",
)


def test_calamares_names_reexported_from_pkgbuild_calamares():
    # Every moved name resolves on BOTH modules and is the SAME object (a re-export,
    # not a copy) -- so there is exactly one source of truth for the calamares recipe.
    import pkgbuild_calamares

    for name in _MOVED_CONSTANTS:
        assert getattr(pkgbuild, name) == getattr(pkgbuild_calamares, name)
    for name in _MOVED_FUNCTIONS:
        assert getattr(pkgbuild, name) is getattr(pkgbuild_calamares, name), (
            f"pkgbuild.{name} must BE pkgbuild_calamares.{name} (re-export, not re-def)"
        )


def test_calamares_module_is_self_contained():
    # pkgbuild_calamares builds the full recipe with NO dependency on pkgbuild.py: the
    # PKGBUILD text is authored here and the five patches are re-exported from their
    # own modules -- so the whole recipe resolves without importing pkgbuild.py.
    import pkgbuild_calamares as pc

    assert pc.CALAMARES_VERSION == "3.4.2"
    assert ("sha256sums=('%s' 'SKIP' 'SKIP' 'SKIP' 'SKIP' 'SKIP')" % pc.CALAMARES_SHA256) in pc.pkgbuild_calamares()
    # The four EDIT patches start at `--- a/`; the networkq patch CREATES files, so its
    # first hunk header is `--- /dev/null`. Both are valid unified-diff openings.
    for patch in (
        pc.calamares_defaults_patch(),
        pc.calamares_region_keyboard_patch(),
        pc.calamares_finish_buttons_patch(),
        pc.calamares_networkcfg_static_patch(),
    ):
        assert patch.startswith("--- a/"), "each edit patch is a unified diff starting at --- a/"
    assert pc.calamares_networkq_patch().startswith("--- /dev/null"), (
        "the networkq patch creates files, so it opens with --- /dev/null"
    )


def test_each_patch_lives_in_its_own_module_and_is_reexported():
    # Each of the FIVE source patches is authored in its OWN focused module (they are
    # large unified diffs). pkgbuild_calamares re-exports them (same object, not a copy),
    # and pkgbuild re-exports that -- so there is exactly one source of truth per patch
    # and a future edit opens one small file.
    import calamares_patch_defaults as pd
    import calamares_patch_finish_buttons as pf
    import calamares_patch_networkcfg_static as pn
    import calamares_patch_networkq as pq
    import calamares_patch_region_keyboard as pr
    import pkgbuild_calamares as pc

    # Each entry: (func, const, module, diff-opening). The networkq patch CREATES files so
    # it opens with `--- /dev/null`; the four EDIT patches open with `--- a/`.
    chain = [
        ("calamares_defaults_patch", "CALAMARES_DEFAULTS_PATCH_NAME", pd, "--- a/"),
        ("calamares_region_keyboard_patch", "CALAMARES_REGION_KEYBOARD_PATCH_NAME", pr, "--- a/"),
        ("calamares_finish_buttons_patch", "CALAMARES_FINISH_BUTTONS_PATCH_NAME", pf, "--- a/"),
        ("calamares_networkq_patch", "CALAMARES_NETWORKQ_PATCH_NAME", pq, "--- /dev/null"),
        ("calamares_networkcfg_static_patch", "CALAMARES_NETWORKCFG_STATIC_PATCH_NAME", pn, "--- a/"),
    ]
    for func_name, const_name, module, opening in chain:
        # The patch module is the definition site.
        assert getattr(pc, func_name) is getattr(module, func_name)
        assert getattr(pkgbuild, func_name) is getattr(module, func_name)
        assert getattr(pc, const_name) == getattr(module, const_name)
        assert getattr(pkgbuild, const_name) == getattr(module, const_name)
        # And it actually produces a valid unified diff.
        assert getattr(module, func_name)().startswith(opening)


def test_recipe_dirs_calamares_uses_the_extracted_builders():
    # recipe_dirs() (still in pkgbuild.py) wires the calamares dir to the extracted
    # builders. The emitted files must equal the extracted module's output verbatim.
    import pkgbuild_calamares as pc

    for tier in (False, True):
        files = dict(pkgbuild.recipe_dirs(tier))["calamares"]
        assert files["PKGBUILD"] == pc.pkgbuild_calamares()
        assert files[pc.CALAMARES_DEFAULTS_PATCH_NAME] == pc.calamares_defaults_patch()
        assert files[pc.CALAMARES_REGION_KEYBOARD_PATCH_NAME] == pc.calamares_region_keyboard_patch()
        assert files[pc.CALAMARES_FINISH_BUTTONS_PATCH_NAME] == pc.calamares_finish_buttons_patch()
        assert files[pc.CALAMARES_NETWORKQ_PATCH_NAME] == pc.calamares_networkq_patch()
        assert files[pc.CALAMARES_NETWORKCFG_STATIC_PATCH_NAME] == pc.calamares_networkcfg_static_patch()
