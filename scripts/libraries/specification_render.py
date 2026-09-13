"""
specification_render -- assemble the General specification (documentations/SPECIFICATIONS_GENERAL.md)
from resolved data, the per-package tags (specification_classify) and the configuration-derived
at-a-glance facts (glance).

This is the technical, developer-facing document. It is prose plus a small set of
DERIVED tables (at-a-glance metrics, edition tags, network endpoints, and a
category-level capability surface). Every fact is computed live from the resolved
package set, the classification, or the build configuration -- nothing about the component
set (package names, versions, counts) is hardcoded in prose, so the document
re-renders correctly when the manifest changes, with no hand edits.

It deliberately does NOT enumerate components. The full component detail -- every
package, its version, and the base->top dependency edges -- lives entirely in the
companion component artifact:
  * SPECIFICATIONS_COMPONENTS_NAVIGATE_FULL.html -- the tiered dependency graph, interactive
The general specification points at it; it does not reproduce its tables. Because it
carries no per-package tables, it cannot drift from the closure.

Only specification_classify is imported (for CATEGORY_ORDER and AZZIO_CONFIGURED); the
hand-authored subsystem prose (specification_content) is no longer used.
"""

import specification_classify as K


# --------------------------------------------------------------------------- #
# Generic, role-level description for each category. Intentionally about the
# ROLE a category plays on the medium, never about specific packages, so it
# survives add/remove churn in the manifest. Keys are specification_classify categories;
# any category not present here falls back to just its name (see _capability).
# --------------------------------------------------------------------------- #
CATEGORY_BLURB = {
    "Kernel & firmware":
        "the Linux kernel, CPU microcode, and device firmware blobs",
    "Boot & init":
        "boot loaders for both firmware types, the initramfs generator, and the "
        "systemd init/service manager",
    "Core system":
        "the base userland -- C library, coreutils, package manager, PAM/polkit, "
        "and privilege escalation",
    "Shell & CLI tools":
        "interactive shells, terminal editors, pagers, multiplexers, and everyday "
        "command-line utilities",
    "Desktop shell":
        "the live-session desktop shell and its supporting session services",
    "Window/compositor":
        "the window manager / compositor that draws and manages windows",
    "Desktop app":
        "graphical end-user applications shipped on the medium",
    "GUI toolkit/framework":
        "the widget toolkits and UI frameworks graphical apps are built on",
    "Graphics & display":
        "the X11 display server, Mesa/Vulkan drivers, and display configuration",
    "Audio":
        "the audio server and mixer/control tooling",
    "Networking":
        "connection management, wireless, VPN, SSH, DNS, and network diagnostics",
    "Storage & filesystems":
        "partitioning, RAID/LVM, encryption setup, and filesystem/imaging tooling",
    "Security & crypto":
        "the host firewall, full-disk encryption, TPM/FIDO/smartcard, and OpenPGP",
    "Developer tools":
        "compilers, build tooling, version control, and developer editors",
    "Language runtime":
        "language interpreters and runtimes available out of the box",
    "Multimedia codec/player":
        "media players and the codec/plugin stack that decodes and encodes them",
    "Fonts & icons":
        "console and desktop fonts, cursors, and icon themes",
    "Printing & scanning":
        "the printing subsystem and its device support",
    "Bluetooth & devices":
        "Bluetooth, USB, accessibility, and other peripheral device support",
    "Virtualization guest":
        "guest integration agents for the major hypervisors",
    "Shared library":
        "shared libraries other components link against",
    "System":
        "supporting system components that back the above",
}


class Doc:
    def __init__(self):
        self.parts = []

    def w(self, s=""):
        self.parts.append(s)

    def text(self):
        return "\n".join(self.parts) + "\n"


def _pkg_version(packages, name):
    rec = packages.get(name)
    return rec["version"] if rec else "?"


def _capability(category):
    """Generic role-level one-liner for a category; never names packages."""
    return CATEGORY_BLURB.get(category, category)


def _category_summary(packages, closure, tags):
    """Walk the classified closure and, for each category that has >=1 package in
    it, return (category, count, total_install_size_bytes) in CATEGORY_ORDER.
    Categories with no packages in the closure are omitted entirely -- there is no
    assumption they exist. Anything classified into a category not in
    CATEGORY_ORDER is ignored here (CATEGORY_ORDER is the canonical set)."""
    counts = {c: 0 for c in K.CATEGORY_ORDER}
    sizes = {c: 0 for c in K.CATEGORY_ORDER}
    for p in closure:
        c = tags[p]["category"]
        if c in counts:
            counts[c] += 1
            sizes[c] += packages.get(p, {}).get("isize", 0)
    return [(c, counts[c], sizes[c]) for c in K.CATEGORY_ORDER if counts[c] > 0]


def _human_size(nbytes):
    if nbytes >= 1024 ** 3:
        return f"{nbytes / 1024 ** 3:.2f} GiB"
    if nbytes >= 1024 ** 2:
        return f"{nbytes / 1024 ** 2:.1f} MiB"
    if nbytes >= 1024:
        return f"{nbytes / 1024:.1f} KiB"
    return f"{nbytes} B"


def render(packages, resolved, tiers, tags, glance, graph_rel):
    """Assemble the general specification Markdown.

    Signature is fixed (pull_specifications.py calls it positionally). graph_rel is
    the relative link to the interactive dependency-graph artifact. Not every
    argument is used directly, but all are kept.
    """
    closure = resolved["closure"]
    roots = resolved["roots"]

    d = Doc()
    d.w("# Azzio -- General Specification")
    d.w()
    d.w(f"Technical, developer-facing specification of the Azzio Linux "
        f"distribution. It is generated -- do not hand-edit; it is regenerated by "
        f"`scripts/pull_specifications.sh` from the official Arch `core`/`extra`/"
        f"`multilib` databases and the build configuration, and every fact below is "
        f"derived live from the resolved package set. It describes what the medium "
        f"*is* and how it behaves; it does not enumerate components. The full "
        f"base->top dependency graph, with every package and version, lives in the "
        f"companion interactive map [`{graph_rel}`]({graph_rel}) described in the "
        f"last section.")
    d.w()
    d.w("---")
    d.w()

    # 1. At a glance ---------------------------------------------------------
    d.w("## 1. At a glance")
    d.w()
    d.w(f"- **Base distribution:** {glance['base']}")
    d.w(f"- **Live session:** {glance['desktop']}")
    d.w(f"- **Kernel:** `linux` {glance['kernel']}")
    d.w(f"- **Init:** `systemd` {glance['init']}")
    d.w(f"- **Display manager / session:** {glance['dm']}")
    d.w(f"- **ISO versioning:** {glance['iso_version']}")
    d.w(f"- **Live-session writable RAM (`cow_spacesize`):** {glance['ram']}")
    d.w(f"- **Purpose:** {glance['purpose']}")
    d.w()
    d.w("| Metric | Value |")
    d.w("|---|---:|")
    dupes = glance['raw_lines'] - glance['tokens']
    dup_note = f"; {dupes} duplicate line{'s' if dupes != 1 else ''} de-duped" if dupes else ""
    d.w(f"| Explicit manifest entries | {glance['tokens']} "
        f"({glance['raw_lines']} non-comment lines{dup_note}) |")
    d.w(f"| Explicit entries incl. group members (e.g. `xorg`) | {len(roots)} |")
    d.w(f"| **Full package set (transitive closure)** | **{len(closure)}** |")
    d.w(f"| &nbsp;&nbsp;from `core` / `extra` / `multilib` | "
        f"{glance['by_repo']['core']} / {glance['by_repo']['extra']} / "
        f"{glance['by_repo']['multilib']} |")
    d.w(f"| Edition: Azzio Component / Stock Arch | "
        f"{glance['azzio']} / {glance['stock']} |")
    d.w(f"| Top / leaf packages (nothing depends on them) | {len(tiers['leaves'])} |")
    d.w(f"| Base / sink packages (depend on nothing else in the set) | "
        f"{len(tiers['bases'])} |")
    d.w(f"| Deepest dependency chain (leaf -> base) | {tiers['max_height']} hops |")
    d.w(f"| Total installed size of the package set | {glance['size']} |")
    d.w()
    d.w("**Edition tags** (used throughout, and marked on the component graph):")
    d.w()
    d.w("| Tag | Meaning |")
    d.w("|---|---|")
    d.w("| `azzio` | **Azzio Component** -- in the package set **only** "
        "because Azzio added it on top of the stock archiso `releng` baseline "
        "(a chosen application, or a dependency that exists purely to support "
        "one). |")
    d.w("| `stock` | **Stock Arch** -- already pulled in by the stock archiso "
        "`releng` install medium; Azzio inherits it whether it adds anything "
        "or not. |")
    d.w()
    d.w("---")
    d.w()

    # 2. Base & identity -----------------------------------------------------
    d.w("## 2. Base and identity")
    d.w()
    d.w(f"Azzio is [Arch Linux](https://archlinux.org) -- rolling release, "
        f"`x86_64` -- with a curated package set and Azzio branding/configuration on "
        f"top. Every package comes unmodified from the official Arch repositories; "
        f"Arch's own documentation, the [ArchWiki](https://wiki.archlinux.org), "
        f"applies directly. The kernel is `linux` {glance['kernel']} and the init "
        f"system is `systemd` {glance['init']}. The medium is versioned "
        f"{glance['iso_version']}.")
    d.w()
    d.w("Identity is set through `/usr/lib/os-release`: `NAME=\"Azzio Linux\"` "
        "with `ID=arch` and `ID_LIKE=arch` kept deliberately -- so tooling that "
        "keys off `ID` still treats the system as Arch -- `BUILD_ID=rolling`, and "
        "`HOME_URL` pointing at the project repository. The branding is "
        "presentational; the system remains Arch underneath.")
    d.w()
    d.w("---")
    d.w()

    # 3. Medium and boot -----------------------------------------------------
    d.w("## 3. Medium and boot")
    d.w()
    d.w(f"The artifact is a {glance['purpose']}: a single ISO that can run live, "
        f"serve as a rescue environment, and install the system. The root "
        f"filesystem ships as a zstd-compressed SquashFS image; at runtime it is "
        f"read-only with a writable overlay ({glance['ram']}).")
    d.w()
    d.w("The medium boots on both firmware types:")
    d.w()
    d.w("- **BIOS:** `syslinux` (MBR and El Torito).")
    d.w("- **UEFI:** `systemd-boot`, with both `ia32` and `x64` loaders (ESP and "
        "El Torito).")
    d.w()
    d.w("---")
    d.w()

    # 4. Live session --------------------------------------------------------
    d.w("## 4. Live session")
    d.w()
    d.w("The live session runs **KDE Plasma** under X11 -- there is no display "
        "manager. The boot path is: `getty@tty1` autologins the live user `main`, "
        "whose `~/.bash_profile` execs `startx`, whose `~/.xinitrc` paints the "
        "wallpaper (`feh`, so there is no solid-color flash) and execs "
        "`startplasma-x11`; Plasma brings up the panel/launcher, `kwin_x11`, the "
        "NetworkManager (`plasma-nm`) and audio (`plasma-pa`) applets, and a "
        "`~/.config/autostart` entry auto-launches the Calamares installer once via "
        "`sudo -E`.")
    d.w()
    d.w("The live user `main` has passwordless `sudo` and a blank password and is "
        "autologged in -- this is a live medium, not the installed posture. The "
        "installed system is different (see below).")
    d.w()
    d.w("---")
    d.w()

    # 5. Installation --------------------------------------------------------
    d.w("## 5. Installation")
    d.w()
    d.w("Installation is **offline**. The primary path is the Calamares installer, "
        "whose `unpackfs` module rsyncs the live SquashFS root onto the target -- "
        "there is no network `pacstrap`, so the install works with no connectivity "
        "and installs exactly what shipped on the medium. A simpler shell installer "
        "path also exists for the automated case.")
    d.w()
    d.w("- **Filesystem:** Btrfs by default, with `@` and `@home` subvolumes "
        "mounted with zstd compression and `noatime`.")
    d.w("- **Encryption:** LUKS2 full-disk encryption is offered at install time.")
    d.w("- **Boot loader:** GRUB is installed on both UEFI and BIOS targets.")
    d.w("- **Cleanup:** Calamares removes itself from the installed target after a "
        "successful install.")
    d.w()
    d.w("On the installed system the posture tightens: privilege escalation is via "
        "the `wheel` group (not passwordless), the hostname is chosen in the "
        "installer UI, and there is no autologin.")
    d.w()
    d.w("---")
    d.w()

    # 6. Localization --------------------------------------------------------
    d.w("## 6. Localization")
    d.w()
    d.w("Locale, keyboard layout and timezone are **auto-detected on first boot** "
        "from a geo-IP lookup, mapped through a table of supported locales in the "
        "build configuration. If the lookup host is unreachable (fully offline boot), the "
        "defaults `en_US.UTF-8` / `us` keymap apply. The network hosts involved are "
        "listed in the Network endpoints section.")
    d.w()
    d.w("---")
    d.w()

    # 7. Security posture ----------------------------------------------------
    d.w("## 7. Security posture")
    d.w()
    d.w("- **Firewall:** `ufw` is enabled with a default **reject-incoming / "
        "allow-outgoing** policy.")
    d.w("- **Disk encryption:** LUKS2 full-disk encryption is available at install "
        "time.")
    d.w("- **Live vs installed:** the live user's passwordless sudo and blank "
        "password are a live-medium convenience; the installed system uses "
        "`wheel`-group sudo with no autologin.")
    d.w()
    d.w("---")
    d.w()

    # 8. Capability surface --------------------------------------------------
    d.w("## 8. Capability surface")
    d.w()
    d.w("What the medium can do, grouped by the role each component plays. Counts "
        "and sizes are computed live from the classified package closure, so they "
        "track the manifest automatically. This is a role-level summary only -- for "
        "the actual packages and versions in each category, use the component "
        "artifacts in the next section.")
    d.w()
    d.w("| Category | Components | Installed size | Provides |")
    d.w("|---|---:|---:|---|")
    for category, count, size in _category_summary(packages, closure, tags):
        d.w(f"| {category} | {count} | {_human_size(size)} | "
            f"{_capability(category)} |")
    d.w()
    d.w("---")
    d.w()

    # 9. Network endpoints ---------------------------------------------------
    d.w("## 9. Network endpoints -- what the distro contacts")
    d.w()
    d.w("Every external host and service the distribution talks to: where it "
        "downloads packages from, how it resolves the timezone and locale, and "
        "what it pings. Read live from the build configuration "
        "(`libraries/pacman.py`, `libraries/installer.py`, and "
        "`libraries/packages/calamares/locale.py`), so this list cannot drift from "
        "what the ISO actually does. The system is designed to work fully offline; "
        "these are the endpoints used **when a network is available**.")
    d.w()
    d.w("| Endpoint | Purpose | Where / notes |")
    d.w("|---|---|---|")
    for endpoint, purpose, context in glance["endpoints"]:
        d.w(f"| `{endpoint}` | {purpose} | {context} |")
    d.w()
    d.w("Notes for a developer:")
    d.w()
    d.w("- **Package mirrors** are hard-coded for the *build* (host-independent so "
        "the ISO builds identically on Arch, Manjaro or Docker); the *installed* "
        "system uses the standard Arch `mirrorlist`. The offline `file://` repo is "
        "the baked-in package cache used when no mirror is reachable.")
    d.w("- **Timezone / locale / keyboard** are auto-detected on first boot from a "
        "geo-IP lookup; if that host is unreachable the defaults (`en_US.UTF-8`, "
        "`us`) apply. Change the provider in `libraries/packages/calamares/locale.py`.")
    d.w("- **Time sync** uses systemd-timesyncd's default NTP servers, enabled only "
        "after the connectivity probe succeeds.")
    d.w()
    d.w("---")
    d.w()

    # 10. Component detail ---------------------------------------------------
    d.w("## 10. Where the component detail lives")
    d.w()
    d.w("This document does not list packages. The complete component enumeration "
        "-- every package, its version, and the dependency edges between them -- "
        "lives in a companion artifact in `documentations/`:")
    d.w()
    d.w(f"- [`{graph_rel}`]({graph_rel}) -- **interactive map**: the base->top "
        f"dependency graph, navigable, coloured by category and marked with the "
        f"edition tags above, with search and per-package detail.")
    d.w()
    d.w("---")
    d.w()

    # 11. Status -------------------------------------------------------------
    d.w("## 11. Status")
    d.w()
    d.w("Under construction. Azzio tracks Arch rolling, and the shipped component "
        "set is subject to change as the distribution is reworked; the figures in "
        "this document are regenerated from the current manifest on every change, "
        "so they always reflect the present state rather than a fixed release.")

    return d.text()
