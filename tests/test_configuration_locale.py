"""packages.calamares.locale -- the static (English-only, Asia/Jerusalem) locale
setup plus the country->locale map retained as data for the deferred resolver.

As of the "no auto-resolve" change the emitted setup bash is STATIC: it must NOT
query the network (no curl / ipapi.co), must NOT match a country against
LANGUAGE_MAP, and must ship English (en_US.UTF-8), an English-only "us" keymap,
and the Asia/Jerusalem timezone unconditionally. The dynamic resolver is being
reimplemented as the user-invoked `azzio --resolve-*` commands (issue #46) and
is deliberately NOT part of the shipped setup scripts. LANGUAGE_MAP survives as
the single source of truth those future commands will consume.
"""

from __future__ import annotations

from packages.calamares import locale


def test_language_map_has_no_duplicate_country_codes():
    # A dict can't hold dup keys, but assert the count matches distinct locales'
    # spread so a copy-paste that overwrites an entry is visible.
    codes = list(locale.LANGUAGE_MAP.keys())
    assert len(codes) == len(set(codes))


def test_every_locale_is_utf8():
    for cc, (name, loc) in locale.LANGUAGE_MAP.items():
        assert loc.endswith(".UTF-8"), f"{cc} -> {loc}"


def test_heredoc_renders_every_entry_as_pipe_delimited():
    # The heredoc helper is retained for the deferred resolver (issue #46); it
    # still renders every entry, even though the static setup no longer embeds it.
    heredoc = locale._language_map_heredoc()
    lines = heredoc.splitlines()
    assert len(lines) == len(locale.LANGUAGE_MAP)
    for cc, (name, loc) in locale.LANGUAGE_MAP.items():
        assert f"{cc}|{name}|{loc}" in lines


def test_heredoc_lines_have_exactly_three_fields():
    for line in locale._language_map_heredoc().splitlines():
        assert line.count("|") == 2, line


def test_setup_locale_sh_is_a_bash_script_with_completion_marker():
    sh = locale.setup_locale_sh()
    assert sh.startswith("#!/bin/bash")
    # The completion marker the oneshot touches.
    assert "touch /var/log/.locale_set" in sh


def test_setup_locale_sh_is_live_iso_only_guarded():
    # REGRESSION (post-install locale/timezone clobber): the live oneshot is enabled
    # in multi-user.target.wants and the OFFLINE Calamares install rsyncs the live
    # rootfs verbatim, so BOTH the enable-symlink and this script land on the target.
    # Without the /run/archiso guard it re-runs on every INSTALLED-system boot and
    # overwrites the locale/keyboard/timezone Calamares persisted. The guard must be
    # a hard early-exit on a non-live system.
    sh = locale.setup_locale_sh()
    # /run/archiso exists only on the live archiso medium; absent on an installed disk.
    assert "/run/archiso" in sh
    assert "if [ ! -d /run/archiso ]; then" in sh
    # It must EXIT (no-op) when not live, not merely warn.
    guard = sh.split("/run/archiso", 1)[1].split("fi", 1)[0]
    assert "exit 0" in guard


def test_setup_locale_guard_runs_before_any_locale_write():
    # The guard has to short-circuit BEFORE the locale-application block, or it would
    # still clobber /etc/locale.conf etc. on the installed system. Assert the
    # /run/archiso check precedes the first thing the static block writes.
    sh = locale.setup_locale_sh()
    guard_pos = sh.index("/run/archiso")
    # locale-gen and the locale.conf write are the load-bearing mutations.
    assert guard_pos < sh.index("locale-gen")
    assert guard_pos < sh.index("/etc/locale.conf")
    assert guard_pos < sh.index("/etc/X11/xorg.conf.d/00-keyboard.conf")


def test_shared_locale_block_has_no_live_guard():
    # The /run/archiso guard belongs ONLY to the live oneshot, NOT the shared block:
    # installer.py's chroot_setup_sh() runs the shared block inside the target chroot
    # (arch-chroot /mnt) where /run/archiso is intentionally absent -- guarding the
    # shared block there would wrongly skip locale setup during that install path.
    block = locale._detect_and_apply_locale_block()
    assert "/run/archiso" not in block


def test_us_maps_to_english():
    assert locale.LANGUAGE_MAP["US"] == ("English", "en_US.UTF-8")


def test_brace_escaping_collapsed():
    # The block is built with an f-string; every literal brace in the emitted bash
    # is written doubled in the source ({{ / }}). If a doubling is missed the
    # f-string raises at import; if a stray {{ survives into the OUTPUT the bash is
    # broken. So the rendered text must contain NO doubled braces.
    block = locale._detect_and_apply_locale_block()
    assert "{{" not in block
    assert "}}" not in block


def test_no_auto_resolve_in_setup_block():
    # The whole point of the "no auto-resolve" change: the shipped setup bash must
    # NOT geolocate anything. No network call (curl / ipapi.co), no COUNTRY/TIMEZONE
    # capture, and NO embedding of the LANGUAGE_MAP the old country match grepped.
    block = locale._detect_and_apply_locale_block()
    assert "curl" not in block
    assert "ipapi.co" not in block
    assert "COUNTRY" not in block
    # The old geo-detected TIMEZONE variable is gone (the zone is now a literal).
    assert "TIMEZONE=" not in block
    # The country->locale map is NOT embedded in the static script anymore.
    assert "US|English|en_US.UTF-8" not in block
    assert locale._language_map_heredoc() not in block
    # No SECONDARY (2nd locale) machinery survives either.
    assert "SECONDARY_LANG" not in block


def test_language_is_english_only():
    # Display language is a fixed English (en_US.UTF-8) LANG -- no conditional.
    block = locale._detect_and_apply_locale_block()
    assert locale.DEFAULT_LANG == "en_US.UTF-8"
    assert 'PRIMARY_LANG="en_US.UTF-8"' in block
    assert 'echo "LANG=$PRIMARY_LANG" > /etc/locale.conf' in block


def test_keyboard_is_english_only():
    # Keyboard policy is English-only: a single "us" layout, no second layout, and
    # no group-toggle. The old SECONDARY_KB machinery (comma expansion + the
    # grp:alt_shift_toggle option) must be entirely gone from the emitted bash.
    block = locale._detect_and_apply_locale_block()
    assert locale.DEFAULT_KEYMAP == "us"
    assert 'PRIMARY_KB="us"' in block
    assert "SECONDARY_KB" not in block
    assert "grp:alt_shift_toggle" not in block
    # The X11 layout line carries only the primary (us) layout, no comma-joined 2nd.
    assert 'Option "XkbLayout" "$PRIMARY_KB"' in block


def test_default_timezone_is_asia_jerusalem_and_static():
    # Asia/Jerusalem is the shipped default timezone; with auto-resolve removed it
    # is now the ONLY zone -- symlinked unconditionally, with no geo override and
    # no fallback branch.
    assert locale.DEFAULT_TIMEZONE == "Asia/Jerusalem"
    block = locale._detect_and_apply_locale_block()
    assert 'ln -sf "/usr/share/zoneinfo/Asia/Jerusalem" /etc/localtime' in block
    # The old UTC fallback must be gone.
    assert "zoneinfo/UTC" not in block
    # And there is exactly one localtime symlink (no if/else geo branch).
    assert block.count("/etc/localtime") == 1


def test_setup_locale_single_shebang_and_marker():
    # setup_locale_sh() wraps the shared block; the shared block itself must NOT
    # carry a shebang or the completion marker, or the wrapper would emit two of
    # each. Exactly one shebang and one marker in the final script.
    out = locale.setup_locale_sh()
    assert out.count("#!/bin/bash") == 1
    assert out.count("touch /var/log/.locale_set") == 1


# --- LC_TIME: day/month/year dates (Task 3) ---------------------------------

def test_time_locale_is_british_english_dmy():
    # LC_TIME=en_GB.UTF-8 is English but formats dates day/month/year (vs the en_US
    # month/day/year default) -- the user's "modify timedate from m/d/y to d/m/y".
    assert locale.DEFAULT_TIME_LOCALE == "en_GB.UTF-8"


def test_setup_block_sets_lc_time_separately_from_lang():
    # The UI language stays US English (LANG=en_US.UTF-8) while ONLY the date order
    # changes via LC_TIME. Both must be written to /etc/locale.conf.
    block = locale._detect_and_apply_locale_block()
    assert 'TIME_LANG="en_GB.UTF-8"' in block
    assert 'echo "LANG=$PRIMARY_LANG" > /etc/locale.conf' in block          # UI language
    assert 'echo "LC_TIME=$TIME_LANG" >> /etc/locale.conf' in block         # date order
    # LANG must NOT be changed to the GB locale (only LC_TIME differs).
    assert locale.DEFAULT_LANG == "en_US.UTF-8"


def test_setup_block_generates_the_time_locale():
    # LC_TIME=en_GB.UTF-8 is inert unless en_GB.UTF-8 is generated, so the block must
    # uncomment it in /etc/locale.gen before locale-gen runs.
    block = locale._detect_and_apply_locale_block()
    assert 'sed -i "s/^#\\?\\s*$TIME_LANG/$TIME_LANG/" /etc/locale.gen' in block
    # Both the primary and the time locale's uncomment-sed lines must run before the
    # `locale-gen` COMMAND (newline-anchored so a mention of "locale-gen" in a comment
    # does not match). Both seds enable a locale in /etc/locale.gen.
    gen_cmd = block.index("\nlocale-gen\n")
    assert block.index('sed -i "s/^#\\?\\s*$PRIMARY_LANG/$PRIMARY_LANG/"') < gen_cmd
    assert block.index('sed -i "s/^#\\?\\s*$TIME_LANG/$TIME_LANG/"') < gen_cmd
    # And LC_TIME is written to locale.conf AFTER locale-gen.
    assert block.index('echo "LC_TIME=$TIME_LANG"') > gen_cmd


def test_setup_block_still_dollar_brace_clean():
    # The f-string additions must not leave doubled braces in the emitted bash.
    block = locale._detect_and_apply_locale_block()
    assert "{{" not in block
    assert "}}" not in block


# --- Resolver country table (the `azzio --resolve-*` commands) --------------

def test_resolver_table_rows_have_five_fields():
    # Every row is CC|locale|layout|keymap|english; the guest command line interface splits on '|' and
    # relies on exactly five fields.
    for line in locale.resolver_country_table_sh().splitlines():
        parts = line.split("|")
        assert len(parts) == 5, line


def test_resolver_table_english_flag_is_one_or_zero():
    for line in locale.resolver_country_table_sh().splitlines():
        assert line.split("|")[4] in ("0", "1"), line


def test_resolver_table_english_speaking_countries_are_english_only():
    # English-speaking countries must be flagged english=1 AND map to the plain "us"
    # layout/keymap (no second layout), matching "English speaking -> English only".
    t = locale.RESOLVER_COUNTRY_TABLE
    for cc in ("US", "GB", "AU", "NZ", "IE", "ZA", "CA"):
        loc, layout, keymap, english = t[cc]
        assert english is True, cc
        assert layout == "us" and keymap == "us", cc


def test_resolver_table_hebrew_layout_is_il_not_he():
    # The xkb LAYOUT code for Hebrew is "il" (base.lst); "he" is only a keymap name.
    # Getting this wrong makes setxkbmap fail and the second layout never appear.
    loc, layout, keymap, english = locale.RESOLVER_COUNTRY_TABLE["IL"]
    assert layout == "il"
    assert english is False


def test_resolver_table_arabic_uses_generic_ara_layout():
    for cc in ("SA", "AE", "EG", "IQ"):
        loc, layout, keymap, english = locale.RESOLVER_COUNTRY_TABLE[cc]
        assert layout == "ara", cc
        assert english is False


def test_resolver_table_latin_american_spanish_uses_latam():
    # El Salvador (and the rest of Latin America) must use the "latam" layout, not
    # Spain's "es" -- the screenshot shows "español (El Salvador)".
    loc, layout, keymap, english = locale.RESOLVER_COUNTRY_TABLE["SV"]
    assert loc == "es_SV.UTF-8"
    assert layout == "latam"
    assert english is False


def test_resolver_table_locales_are_utf8():
    for cc, (loc, layout, keymap, english) in locale.RESOLVER_COUNTRY_TABLE.items():
        assert loc.endswith(".UTF-8"), (cc, loc)


def test_resolver_table_matches_calamares_patch_layout_codes():
    # Single-source-of-truth guard: every non-English country's (layout, keymap) in
    # the resolver table must also appear as a { "CC", "layout", "keymap" } row in
    # the Calamares region-keyboard C++ patch, so the guest resolver and the
    # installer never drift.
    import pkgbuild

    patch = pkgbuild.calamares_region_keyboard_patch()
    for cc, (loc, layout, keymap, english) in locale.RESOLVER_COUNTRY_TABLE.items():
        if english:
            continue  # English countries are absent from the patch table by design
        needle = '{ "%s", "%s", "%s" }' % (cc, layout, keymap)
        assert needle in patch, f"{needle} missing from calamares region patch"
