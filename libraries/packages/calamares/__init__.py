"""calamares - the Calamares 3.4.2 installer configuration, authored as
configuration-as-Python.

Calamares is Azzio's SOLE installer: the live OpenBox session auto-launches it and
it installs Azzio Linux to disk (OFFLINE -- the target root is unpacked from the live
SquashFS by unpackfs, not pacstrapped over the network). Because Azzio ships a
CRITICALLY MODIFIED calamares build (compiled from source in the makepkg stage; see
libraries/makepkg.py and packages/pkgbuild.py), the whole configuration tree lives here
under libraries/packages/ as one of OUR packages, not under packages/.

Modules:
    calamares               the settings.conf + branding + every modules/*.conf builder,
                            plus emit_map() -> {relative path under /etc/calamares -> content}
    calamares_shellprocess  the post-unpackfs target fixups (drop the live `main` account so
                            the users module can recreate it; de-sparsify /boot; reset the
                            archiso mkinitcpio preset) -- the most intricate part of the install
    locale                  the static English-only locale data (en_US.UTF-8, us keyboard,
                            Asia/Jerusalem) + the country->(locale, layout, keymap, english)
                            RESOLVER_COUNTRY_TABLE that the guest resolver is built from

The public surface stays flat: calamares.py re-exports the shellprocess builders, so
callers use calamares.shellprocess_conf(), calamares.LIVE_USER, etc.
"""
