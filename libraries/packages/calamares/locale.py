"""Locale setup: a STATIC English-only, Asia/Jerusalem configuration plus the
country->locale map kept as data for the (deferred) dynamic resolver.

Both consumers -- setup-locale.sh (runs on the LIVE ISO) and the locale portion
of chroot-setup.sh (runs in the INSTALL chroot) -- share
``_detect_and_apply_locale_block()`` below. As of the "no auto-resolve" change it
does NOT query the network: the display language is English (en_US.UTF-8), the
keyboard is English-only ("us"), and the timezone is Asia/Jerusalem, all fixed.

The old behaviour -- IP-geolocating the timezone/country (curl ipapi.co) and
switching the display locale from LANGUAGE_MAP -- is intentionally gone. It is
being reimplemented as separate, user-invoked guest commands (`azzio
timedate --resolve` / `azzio language --resolve`) tracked in
issue #46; that work is deliberately NOT done here. LANGUAGE_MAP is retained as
the single source of truth those future commands will consume (adding a language
is still a one-line Python edit), but it is no longer embedded in the shipped
setup scripts.
"""

from __future__ import annotations

# country code -> (language name, locale). Order preserved (matches the original).
# NOTE: this is now DATA ONLY -- retained for the deferred `azzio --resolve-*`
# commands (issue #46). The static locale block below no longer reads it, so the
# live/installed systems ship English-only regardless of what is added here.
LANGUAGE_MAP: dict[str, tuple[str, str]] = {
    "US": ("English", "en_US.UTF-8"),
    "GB": ("English", "en_GB.UTF-8"),
    "FR": ("French", "fr_FR.UTF-8"),
    "DE": ("German", "de_DE.UTF-8"),
    "ES": ("Spanish", "es_ES.UTF-8"),
    "IT": ("Italian", "it_IT.UTF-8"),
    "UA": ("Ukrainian", "uk_UA.UTF-8"),
    "RU": ("Russian", "ru_RU.UTF-8"),
    "CN": ("Chinese", "zh_CN.UTF-8"),
    "JP": ("Japanese", "ja_JP.UTF-8"),
    "KR": ("Korean", "ko_KR.UTF-8"),
    "BR": ("Portuguese", "pt_BR.UTF-8"),
    "IN": ("Hindi", "hi_IN.UTF-8"),
    "IL": ("Hebrew", "he_IL.UTF-8"),
    "AR": ("Arabic", "ar_SA.UTF-8"),
    "TR": ("Turkish", "tr_TR.UTF-8"),
    "NL": ("Dutch", "nl_NL.UTF-8"),
    "PL": ("Polish", "pl_PL.UTF-8"),
    "SE": ("Swedish", "sv_SE.UTF-8"),
    "NO": ("Norwegian", "nb_NO.UTF-8"),
    "DK": ("Danish", "da_DK.UTF-8"),
    "FI": ("Finnish", "fi_FI.UTF-8"),
    "CZ": ("Czech", "cs_CZ.UTF-8"),
    "HU": ("Hungarian", "hu_HU.UTF-8"),
    "GR": ("Greek", "el_GR.UTF-8"),
    "TH": ("Thai", "th_TH.UTF-8"),
    "VN": ("Vietnamese", "vi_VN.UTF-8"),
}


def _language_map_heredoc() -> str:
    """Render LANGUAGE_MAP as the ``CC|Language|locale`` lines the (deferred)
    resolver will grep. Retained as a helper for issue #46; NOT used by the
    static setup block below."""
    return "\n".join(f"{cc}|{name}|{loc}" for cc, (name, loc) in LANGUAGE_MAP.items())


# --- Resolver country table (the `azzio --resolve-*` guest commands) --------
# The SINGLE SOURCE OF TRUTH for the guest-side resolver: ISO-3166 country code ->
# (locale, xkb layout, console keymap, english?). This is a richer superset of
# LANGUAGE_MAP -- it carries the xkb LAYOUT + console KEYMAP the resolver needs to
# actually configure a second keyboard layout, and an explicit english flag so
# English-speaking countries resolve to English ONLY (no second layout/locale),
# matching PROMPT: "if the selected region is English speaking, then the machine
# should only have English".
#
# It is kept CONSISTENT with the Calamares region-keyboard patch's own C++ table
# (packages/pkgbuild.calamares_region_keyboard_patch): the layout codes are
# real /usr/share/X11/xkb/rules/base.lst identifiers (Hebrew is "il" NOT "he",
# generic Arabic is "ara", Latin-American Spanish is "latam"). The resolver embeds
# this as a Python literal (resolver_country_table_py) into the `azzio` command line interface's
# COUNTRY_TABLE, so adding a country here is a one-line edit that both the installer
# patch table and the guest resolver should mirror.
#
# Fields: cc -> (locale, xkb_layout, vconsole_keymap, is_english)
RESOLVER_COUNTRY_TABLE: dict[str, tuple[str, str, str, bool]] = {
    # English-speaking -> English only (layout/keymap "us", is_english True).
    "US": ("en_US.UTF-8", "us", "us", True),
    "GB": ("en_GB.UTF-8", "us", "us", True),
    "AU": ("en_AU.UTF-8", "us", "us", True),
    "NZ": ("en_NZ.UTF-8", "us", "us", True),
    "IE": ("en_IE.UTF-8", "us", "us", True),
    "ZA": ("en_ZA.UTF-8", "us", "us", True),
    "CA": ("en_CA.UTF-8", "us", "us", True),
    # Spanish (Latin America uses the "latam" layout; Spain uses "es").
    "SV": ("es_SV.UTF-8", "latam", "la-latin1", False),
    "MX": ("es_MX.UTF-8", "latam", "la-latin1", False),
    "AR": ("es_AR.UTF-8", "latam", "la-latin1", False),
    "CO": ("es_CO.UTF-8", "latam", "la-latin1", False),
    "CL": ("es_CL.UTF-8", "latam", "la-latin1", False),
    "PE": ("es_PE.UTF-8", "latam", "la-latin1", False),
    "VE": ("es_VE.UTF-8", "latam", "la-latin1", False),
    "EC": ("es_EC.UTF-8", "latam", "la-latin1", False),
    "GT": ("es_GT.UTF-8", "latam", "la-latin1", False),
    "BO": ("es_BO.UTF-8", "latam", "la-latin1", False),
    "CR": ("es_CR.UTF-8", "latam", "la-latin1", False),
    "PY": ("es_PY.UTF-8", "latam", "la-latin1", False),
    "PA": ("es_PA.UTF-8", "latam", "la-latin1", False),
    "UY": ("es_UY.UTF-8", "latam", "la-latin1", False),
    "HN": ("es_HN.UTF-8", "latam", "la-latin1", False),
    "NI": ("es_NI.UTF-8", "latam", "la-latin1", False),
    "DO": ("es_DO.UTF-8", "latam", "la-latin1", False),
    "CU": ("es_CU.UTF-8", "latam", "la-latin1", False),
    "ES": ("es_ES.UTF-8", "es", "es", False),
    # Other Latin-script languages.
    "FR": ("fr_FR.UTF-8", "fr", "fr", False),
    "DE": ("de_DE.UTF-8", "de", "de", False),
    "AT": ("de_AT.UTF-8", "de", "de", False),
    "CH": ("de_CH.UTF-8", "ch", "de_CH-latin1", False),
    "IT": ("it_IT.UTF-8", "it", "it", False),
    "PT": ("pt_PT.UTF-8", "pt", "pt-latin1", False),
    "BR": ("pt_BR.UTF-8", "br", "br-abnt2", False),
    "NL": ("nl_NL.UTF-8", "nl", "nl", False),
    "PL": ("pl_PL.UTF-8", "pl", "pl", False),
    "SE": ("sv_SE.UTF-8", "se", "sv-latin1", False),
    "NO": ("nb_NO.UTF-8", "no", "no-latin1", False),
    "DK": ("da_DK.UTF-8", "dk", "dk-latin1", False),
    "FI": ("fi_FI.UTF-8", "fi", "fi", False),
    "CZ": ("cs_CZ.UTF-8", "cz", "cz-lat2", False),
    "HU": ("hu_HU.UTF-8", "hu", "hu", False),
    "TR": ("tr_TR.UTF-8", "tr", "trq", False),
    "RO": ("ro_RO.UTF-8", "ro", "ro", False),
    "HR": ("hr_HR.UTF-8", "hr", "croat", False),
    "SK": ("sk_SK.UTF-8", "sk", "sk-qwerty", False),
    "SI": ("sl_SI.UTF-8", "si", "slovene", False),
    "EE": ("et_EE.UTF-8", "ee", "et", False),
    "LV": ("lv_LV.UTF-8", "lv", "lv", False),
    "LT": ("lt_LT.UTF-8", "lt", "lt", False),
    "IS": ("is_IS.UTF-8", "is", "is-latin1", False),
    # Vietnamese: the xkb "vn" layout is valid, but the kbd package ships NO "vn"
    # console keymap, so the raw-TTY keymap falls back to "us" (Vietnamese input at
    # the bare console needs an IME anyway; the X11/GUI layout stays "vn").
    "VN": ("vi_VN.UTF-8", "vn", "us", False),
    # Non-Latin scripts (the classic English-fallback cases). The xkb LAYOUT is the
    # region's; the console KEYMAP is the region's ONLY when the kbd package actually
    # ships one (il/ua/by/bg/rs/mk/gr/ge/jp exist), else it falls back to "us" -- a
    # raw VT cannot render most of these scripts without a graphical IME, and an
    # absent keymap would make systemd-vconsole-setup's `loadkeys` fail. (VERIFIED
    # against the kbd package's /usr/share/kbd/keymaps.)
    "IL": ("he_IL.UTF-8", "il", "il", False),
    "RU": ("ru_RU.UTF-8", "ru", "ruwin_alt_sh-UTF-8", False),
    "UA": ("uk_UA.UTF-8", "ua", "ua-utf", False),
    "BY": ("be_BY.UTF-8", "by", "by", False),
    "BG": ("bg_BG.UTF-8", "bg", "bg_bds-utf8", False),
    "RS": ("sr_RS.UTF-8", "rs", "sr-cy", False),
    "MK": ("mk_MK.UTF-8", "mk", "mk-utf", False),
    "GR": ("el_GR.UTF-8", "gr", "gr", False),
    "GE": ("ka_GE.UTF-8", "ge", "ge", False),
    "AM": ("hy_AM.UTF-8", "am", "us", False),   # no "am" console keymap in kbd
    "IR": ("fa_IR.UTF-8", "ir", "us", False),   # no "ir" console keymap in kbd
    "PK": ("ur_PK.UTF-8", "pk", "us", False),   # no "pk" console keymap in kbd
    "IN": ("hi_IN.UTF-8", "in", "us", False),   # no "in" console keymap in kbd
    "TH": ("th_TH.UTF-8", "th", "us", False),   # no "th" console keymap in kbd
    "KH": ("km_KH.UTF-8", "kh", "us", False),   # no "kh" console keymap in kbd
    "LA": ("lo_LA.UTF-8", "la", "us", False),   # no bare "la" console keymap in kbd
    "MM": ("my_MM.UTF-8", "mm", "us", False),   # no "mm" console keymap in kbd
    "LK": ("si_LK.UTF-8", "lk", "us", False),   # no "lk" console keymap in kbd
    "JP": ("ja_JP.UTF-8", "jp", "jp106", False),
    "KR": ("ko_KR.UTF-8", "kr", "us", False),   # no "kr" console keymap in kbd
    "CN": ("zh_CN.UTF-8", "cn", "us", False),   # no "cn" console keymap in kbd
    "TW": ("zh_TW.UTF-8", "tw", "us", False),   # no "tw" console keymap in kbd
    "MN": ("mn_MN.UTF-8", "mn", "us", False),   # no "mn" console keymap in kbd
    # Arabic-script (generic Arabic keyboard "ara" for all Arab states). The kbd
    # package ships NO Arabic console keymap, so the raw-TTY keymap is "us" (the X11
    # "ara" layout is unaffected -- Arabic at the bare console needs a graphical IME).
    "SA": ("ar_SA.UTF-8", "ara", "us", False),
    "AE": ("ar_AE.UTF-8", "ara", "us", False),
    "EG": ("ar_EG.UTF-8", "ara", "us", False),
    "IQ": ("ar_IQ.UTF-8", "ara", "us", False),
    "JO": ("ar_JO.UTF-8", "ara", "us", False),
    "KW": ("ar_KW.UTF-8", "ara", "us", False),
    "LB": ("ar_LB.UTF-8", "ara", "us", False),
    "LY": ("ar_LY.UTF-8", "ara", "us", False),
    "OM": ("ar_OM.UTF-8", "ara", "us", False),
    "QA": ("ar_QA.UTF-8", "ara", "us", False),
    "SY": ("ar_SY.UTF-8", "ara", "us", False),
    "YE": ("ar_YE.UTF-8", "ara", "us", False),
    "BH": ("ar_BH.UTF-8", "ara", "us", False),
    "DZ": ("ar_DZ.UTF-8", "ara", "us", False),
    "MA": ("ar_MA.UTF-8", "ara", "us", False),
    "TN": ("ar_TN.UTF-8", "ara", "us", False),
    "SD": ("ar_SD.UTF-8", "ara", "us", False),
}


def resolver_country_table_sh() -> str:
    """Render RESOLVER_COUNTRY_TABLE as ``CC|locale|layout|keymap|english`` lines.
    `english` is the literal 1/0. This is the data the guest resolver
    (`azzio language --resolve`) maps an IP-geolocated country
    code onto. Kept as the canonical pipe-delimited rendering the tests pin the
    table's contents against; the command line interface itself embeds the Python form below."""
    out = []
    for cc, (loc, layout, keymap, english) in RESOLVER_COUNTRY_TABLE.items():
        out.append(f"{cc}|{loc}|{layout}|{keymap}|{1 if english else 0}")
    return "\n".join(out)


def resolver_country_table_py() -> str:
    """Render RESOLVER_COUNTRY_TABLE as the body of the `azzio` command line interface's COUNTRY_TABLE
    dict literal: one ``    'CC': ('locale', 'layout', 'keymap', english),`` line per
    country (english as the literal int 1/0). The compiler substitutes this between the
    AZZIO_CC markers in the azzio command line interface (country_table.py, bundled into the shipped
    script) so the guest resolver's table stays in lock-step with this single source of
    truth."""
    out = []
    for cc, (loc, layout, keymap, english) in RESOLVER_COUNTRY_TABLE.items():
        out.append(f"    {cc!r}: ({loc!r}, {layout!r}, {keymap!r}, {1 if english else 0}),")
    return "\n".join(out)


# Azzio default/only display language and keyboard. English everywhere; the
# keymap is always "us" with no second layout and no group-toggle.
DEFAULT_LANG = "en_US.UTF-8"
DEFAULT_KEYMAP = "us"

# Date/time formatting locale (LC_TIME), kept SEPARATE from the display language so
# the UI stays US English while DATES read day/month/year. en_US.UTF-8 formats dates
# month/day/year (e.g. 08/03/2026); en_GB.UTF-8 is English but formats them
# day/month/year (e.g. 03/08/2026). Setting LC_TIME to en_GB.UTF-8 flips the whole
# system's date order to d/m/y (the user's "modify timedate from m/d/y to d/m/y"
# request) without changing the language of anything else. This LC_TIME governs the
# whole system; the OpenBox desktop has no separate clock config to align.
DEFAULT_TIME_LOCALE = "en_GB.UTF-8"

# Azzio default (and, since auto-resolve was removed, ONLY) timezone. Dynamic
# geo detection is deferred to `azzio timedate --resolve` (issue #46).
DEFAULT_TIMEZONE = "Asia/Jerusalem"


# Shared bash block: apply the STATIC locale -- English display language, an
# English-only ("us") keyboard, and the Asia/Jerusalem timezone. NO network call.
# Both the live-ISO script and the installer chroot script start from this.
#
# Language policy: ENGLISH-ONLY. LANG is en_US.UTF-8, unconditionally.
# Keyboard policy: ENGLISH-ONLY. The layout is always "us" with no second layout
# and no group-toggle.
# Timezone policy: Asia/Jerusalem, unconditionally. (No IP geolocation -- that is
# reimplemented as the user-invoked `azzio --resolve-*` commands, issue #46.)
def _detect_and_apply_locale_block() -> str:
    return f"""\
# Static locale: English display language, English-only ("us") keyboard, the
# Asia/Jerusalem timezone, and a day/month/year date format (LC_TIME=en_GB.UTF-8).
# Nothing here is auto-resolved from the network -- the dynamic resolver lives in
# the `azzio --resolve-*` commands (issue #46).
PRIMARY_LANG="{DEFAULT_LANG}"
PRIMARY_KB="{DEFAULT_KEYMAP}"
# Date/time locale: English but d/m/y instead of the m/d/y en_US default.
TIME_LANG="{DEFAULT_TIME_LOCALE}"

# Enable the display locale AND the d/m/y date locale in locale.gen so BOTH are
# built by locale-gen (LC_TIME=en_GB.UTF-8 is inert unless en_GB.UTF-8 is generated).
sed -i "s/^#\\?\\s*$PRIMARY_LANG/$PRIMARY_LANG/" /etc/locale.gen
sed -i "s/^#\\?\\s*$TIME_LANG/$TIME_LANG/" /etc/locale.gen

# Generate locales
locale-gen

# Set system locale: US English UI, but dates in day/month/year (LC_TIME).
echo "LANG=$PRIMARY_LANG" > /etc/locale.conf
echo "LC_TIME=$TIME_LANG" >> /etc/locale.conf

# Set console keyboard (English-only)
echo "KEYMAP=$PRIMARY_KB" > /etc/vconsole.conf
echo "FONT=lat2-16" >> /etc/vconsole.conf

# Set X11 keyboard layout (English-only: single "us" layout, no toggle)
mkdir -p /etc/X11/xorg.conf.d
cat <<EOF > /etc/X11/xorg.conf.d/00-keyboard.conf
Section "InputClass"
    Identifier "system-keyboard"
    MatchIsKeyboard "on"
    Option "XkbLayout" "$PRIMARY_KB"
EndSection
EOF

# Set timezone: Asia/Jerusalem (fixed; no geo detection).
ln -sf "/usr/share/zoneinfo/{DEFAULT_TIMEZONE}" /etc/localtime

hwclock --systohc"""


def setup_locale_sh() -> str:
    """The live-ISO oneshot: apply the static live-session locale, then mark complete.

    LIVE-ISO ONLY (the load-bearing guard): this script sets the LIVE session's
    English/us/Jerusalem baseline before Calamares runs. It is enabled via
    locale-setup.service in multi-user.target.wants, and the OFFLINE Calamares
    install rsyncs the live rootfs VERBATIM (unpackfs) -- so BOTH the service
    enable-symlink AND this script land on the installed target unchanged. Without
    the guard below it would therefore re-run on EVERY boot of the INSTALLED system
    and overwrite /etc/locale.conf, /etc/vconsole.conf and
    /etc/X11/xorg.conf.d/00-keyboard.conf back to the static English-only "us" +
    Asia/Jerusalem values -- clobbering exactly the locale/keyboard/timezone the user
    chose in the Calamares Location/Keyboard pages (verified: a Russian install came
    up English-only "us" post-boot, with a stray ru_RU.UTF-8 left in locale.gen and
    /var/log/.locale_set freshly timestamped). PROMPT: the installed system MUST keep
    exactly what the installer set, and only `azzio --resolve-*` may ever change it.

    The archiso live medium mounts a tmpfs at /run/archiso (its boot/cow overlay);
    an installed system has no such path. So `[ -d /run/archiso ]` is the definitive
    "am I the live ISO?" test: TRUE on the live medium (apply the baseline), FALSE on
    the installed disk (no-op, leave Calamares' choices untouched). This is a hard
    guard, not belt-and-suspenders -- it is the whole fix for the post-install
    locale/timezone clobber. (The installer.py archinstall path does NOT use this
    script; it calls _detect_and_apply_locale_block() directly inside the target
    chroot, where /run/archiso is intentionally absent -- so this guard must live
    HERE, in the live oneshot, and NOT in the shared block.)"""
    return f"""\
#!/bin/bash

# LIVE-ISO ONLY: no-op on an installed system so we never overwrite the
# locale/keyboard/timezone Calamares persisted (see the docstring). /run/archiso
# exists only on the live archiso medium.
if [ ! -d /run/archiso ]; then
    exit 0
fi

{_detect_and_apply_locale_block()}

# Mark setup complete
touch /var/log/.locale_set
"""
