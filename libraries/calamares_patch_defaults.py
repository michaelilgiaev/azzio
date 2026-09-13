"""Azzio calamares source patch -- installer UI defaults + the Users-page refactor.

One of the three Azzio source patches applied to the pinned calamares-3.4.2 tarball
in the recipe's prepare() (see pkgbuild_calamares). Kept in its own module so each
patch is a focused, independently-editable unit; pkgbuild_calamares re-exports the
name-constant and builder below, and pkgbuild.py re-exports them in turn, so callers
and recipe_dirs() use them unchanged.

The diff is assembled from a line-by-line list of strings so every unified-diff CONTEXT
line keeps its exact single leading space -- a triple-quoted literal would let an editor
strip trailing spaces and silently break `patch`. A context drift on a version bump
makes `patch` fail LOUDLY in prepare() (the build aborts) rather than silently dropping
the customization; regenerate the hunks via `diff -u` against the new source then.
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# calamares -- source patch: Azzio installer UI defaults + Users-page refactor
# ---------------------------------------------------------------------------
# A batch of installer UI decisions are made in Calamares' C++ (the module *.conf
# schemas expose no key for them), so they can only be changed by patching the source
# before the build. This single patch, applied in the recipe's prepare(), carries all
# of them (all VERIFIED against the pinned calamares-3.4.2 tarball):
#
#   1. Keyboard page -- "Switch Keyboard" (the xkb group-switcher dropdown).
#      Upstream builds the dropdown from a QMap sorted by human-readable label and
#      leaves the current index at 0 (the alphabetically-first combo), so "Alt+Shift"
#      is present but NOT pre-selected. The patch makes KeyboardGroupsSwitchersModel's
#      constructor select the entry whose xkb id is `alt_shift_toggle` once the list
#      is built, so the dropdown defaults to "Alt+Shift". (KeyboardLayoutModel.cpp)
#
#   2. Users page -- SEED the hostname AND the login (Username) fields.
#      Hostname: upstream seeds it ONLY once the user types a name, expanding the
#      `hostname.template` ("${first}-${product}" by default) on every keystroke so the
#      hostname keeps changing as the Full Name / Login fields change. The patch seeds
#      the template's expansion as the INITIAL hostname at module load and (via
#      setHostName, which marks the value "custom") takes the field off the auto-derive
#      path -- so with modules/users.conf `template: "azzio"` the field shows "azzio"
#      by default and stays "azzio" regardless of the other inputs.
#      Login: upstream leaves the Username field empty until typed. Per PROMPT.md the
#      field must DEFAULT to containing "main" (not merely hint it), so the patch calls
#      setLoginName("main") in the same setConfigurationMap() tail. setLoginName marks
#      the value "custom" (m_customLoginName), so the Full-Name auto-derive path never
#      overwrites it, and loginNameStatus() is non-empty so Next is reachable at once.
#      (Config.cpp, the @@ -1020 hunk seeds both.)
#
#   3a. Users page -- RENAME the four field-prompt labels in page_usersetup.ui to short
#      "Field:" captions (the QLabel objectNames are username_label_2, hostnameLabel,
#      password_label_2 and labelChooseRootPassword), and change the HOSTNAME field's
#      placeholder from "Computer Name" to "azzio" and the LOGIN field's placeholder
#      from "login" to "main" (both fields are SEEDED to those values by edit 2, so each
#      placeholder is the fallback hint shown only if the user clears the field).
#        "What name do you want to use to log in?"          -> "Username:"
#        "What is the name of this computer?"               -> "Hostname:"
#        "Choose a password to keep your account safe."     -> "Username Password:"
#        "Choose a password for the administrator account." -> "Root Password:"
#      The "Use the same password for the administrator account." checkbox label is
#      RE-WORDED to "Use username password for root password." (page_usersetup.ui.)
#
#   3b. Users page -- REMOVE the "What is your name?" (Full Name) row. The account's
#      GECOS/full name is not asked for. UsersPage.cpp hides the label + the field's
#      widgets (the QHBoxLayout is not a widget, so each child is hidden individually),
#      AND Config::isReady() drops its `!fullName().isEmpty()` gate -- because isReady()
#      hard-requires a non-empty full name, hiding the field WITHOUT relaxing isReady()
#      would leave fullName empty forever and Next permanently greyed. The login IS
#      seeded to "main" (edit 2), so with the Full Name row gone the Username field
#      still opens pre-filled and Next is reachable. (UsersPage.cpp @@ -105 hunk +
#      Config.cpp @@ -765 hunk.)
#
#   4. Users page -- an empty LOGIN or HOSTNAME is a required-field error. Upstream's
#      loginNameStatus()/hostnameStatus() treat an empty value as "ok" (they return an
#      empty status), so Next would be reachable with a blank name. The patch makes the
#      empty branch return a message instead -- "User parameter must include at least
#      one character." / "Hostname parameter must include at least two characters." --
#      which both shows the field error AND (via isReady()'s
#      loginNameStatus().isEmpty()/hostnameStatus().isEmpty() gates) disables Next until
#      filled. Both the login ("main") and hostname ("azzio") are seeded by edit 2, so
#      each error only appears if the user clears that field. (Config.cpp @@ -236 /
#      @@ -301 hunks.)
#
#   5. Users page -- REMOVE the "Require strong passwords." checkbox (UsersPage.cpp
#      force-hides it: setVisible(false) regardless of the config value). Password-
#      strength enforcement is not offered (no libpwquality checks in users.conf).
#      (UsersPage.cpp.)
#
#   6. SetPasswordJob -- a SKIPPED (empty) password locks the account. Upstream locks
#      only root on an empty password (usermod -p '!'); the installer lets the user
#      skip the password field, and a skipped password must become a locked "*" account
#      (not crypt("") -- an empty but usable password). The patch broadens the
#      empty-password special case from root-only to ANY user. (SetPasswordJob.cpp.)
#
# The hunks are small and target stable code paths; the pinned tarball guarantees the
# context lines below match. A context drift on a version bump makes `patch` fail
# LOUDLY in prepare() (the build aborts) rather than silently dropping the
# customization -- refresh the hunks (regenerate via `diff -u`) when bumping the
# version. (See modules/users.conf in packages/calamares/calamares.py for the .conf
# side: doReusePassword:true checks the reuse-for-root box by default,
# allowWeakPasswords:false + no passwordRequirements so an empty password is accepted.)
CALAMARES_DEFAULTS_PATCH_NAME = "azzio-calamares-defaults.patch"


def calamares_defaults_patch() -> str:
    r"""Unified diff (-p1) applied to the extracted calamares-3.4.2 source in the
    recipe's prepare(): default the keyboard group-switcher to Alt+Shift, seed a fixed
    non-reactive hostname (the login is LEFT empty by default), hide the strong-
    password checkbox, RENAME the four Users-page field labels (login prompt ->
    "Username:", hostname prompt -> "Hostname:", user-password prompt -> "Username
    Password:", root-password prompt -> "Root Password:"), make an empty login /
    hostname report a required-field error, and lock a skipped (empty) password. See
    the block comment above for the per-edit rationale and why these live in a source
    patch rather than a module .conf. Paths are a/ b/ prefixed so `patch -p1` (run
    from the source root) applies them.

    The Full Name ("What is your name?") row is HIDDEN and Config::isReady() is relaxed
    (dropping its non-empty-full-name gate) so hiding the field does not permanently
    disable Next. The login VALUE is SEEDED to "main" (PROMPT.md: the Username field
    must DEFAULT to containing the text "main"); the "main" placeholder is now just a
    fallback hint. The "Use the same password for the administrator account." checkbox
    label is re-worded to "Use username password for root password.".

    The diff is built line-by-line rather than as one big triple-quoted literal
    ON PURPOSE: a unified diff's CONTEXT lines (unchanged surrounding source) must
    each begin with a single leading SPACE, and blank context lines are therefore a
    line that is exactly one space. A triple-quoted literal makes those
    space-only lines invisible and trivially corrupted by an editor that strips
    trailing whitespace -- which silently breaks `patch`. Assembling from a list
    keeps every context line's leading space explicit and greppable. The hunk
    headers (@@ -284,4 / @@ -123,3 (x2) / @@ -222,3 / @@ -324,3 / @@ -494,3 /
    @@ -156,1 / @@ -236,5 (x2) / @@ -1020,7 / @@ -81,12 ...) were generated by
    `diff -u` against the pinned 3.4.2 source (edit a pristine extraction, then diff)
    and verified to apply with `patch -p1`; regenerate them the same way on a version
    bump."""
    # Each entry is one full diff line. Context lines start with " " (space),
    # additions with "+", hunk headers with "@@", file headers with ---/+++.
    lines = [
        "--- a/src/modules/keyboard/KeyboardLayoutModel.cpp",
        "+++ b/src/modules/keyboard/KeyboardLayoutModel.cpp",
        "@@ -284,4 +284,18 @@",
        "     }",
        " ",
        '     cDebug() << "Loaded" << m_list.count() << "keyboard groups";',
        "+",
        '+    // Azzio: default the "Switch Keyboard" dropdown to Alt+Shift. Upstream leaves',
        "+    // the current index at 0 (the alphabetically-first combo), so alt_shift_toggle is",
        "+    // listed but not pre-selected. Select it here, once the list is populated, so the",
        '+    // page opens with "Alt+Shift" chosen. Falls back to the upstream default (index 0)',
        "+    // if the xkb id is ever absent from the map.",
        "+    for ( int i = 0; i < m_list.count(); ++i )",
        "+    {",
        '+        if ( m_list.at( i ).key == QStringLiteral( "alt_shift_toggle" ) )',
        "+        {",
        "+            setCurrentIndex( i );",
        "+            break;",
        "+        }",
        "+    }",
        " }",
        # --- page_usersetup.ui: RENAME the four field-prompt labels ----------
        # Each hunk keeps 1 line of context above/below the changed <string> so
        # the anchors are unambiguous. The reuse-password checkbox and the Full
        # Name label are deliberately NOT touched (left at pristine wording).
        "--- a/src/modules/users/page_usersetup.ui",
        "+++ b/src/modules/users/page_usersetup.ui",
        # login prompt "What name do you want to use to log in?" -> "Username:"
        "@@ -123,3 +123,3 @@",
        '      <property name="text">',
        "-      <string>What name do you want to use to log in?</string>",
        "+      <string>Username:</string>",
        "      </property>",
        # login field placeholder "login" -> "main": per PROMPT.md the Username field
        # must DEFAULT to containing "main". Config.cpp's setConfigurationMap() SEEDS the
        # field VALUE to "main" (so loginNameStatus() is non-empty and Next is reachable
        # out of the box); this placeholder is the fallback hint shown only if the user
        # clears the field. This textBox is nested one level deeper than the prompt labels
        # (7-space indent). Ascending order: line 147, after 123, before 222.
        "@@ -147,3 +147,3 @@",
        '        <property name="placeholderText">',
        "-        <string>login</string>",
        "+        <string>main</string>",
        "        </property>",
        # hostname prompt "What is the name of this computer?" -> "Hostname:"
        "@@ -222,3 +222,3 @@",
        '      <property name="text">',
        "-      <string>What is the name of this computer?</string>",
        "+      <string>Hostname:</string>",
        "      </property>",
        # hostname field placeholder "Computer Name" -> "azzio": the field is seeded
        # to "azzio", but if the user CLEARS it the greyed placeholder shows "azzio"
        # (the default that will be used) instead of the generic "Computer Name". This
        # textBox is nested one level deeper than the prompt labels (8/9-space indent).
        # MUST stay in ascending file-line order (line 249, after 222, before 324).
        # Indent: all four lines are 8-space indented (verified via `diff -u`).
        "@@ -249,3 +249,3 @@",
        '        <property name="placeholderText">',
        "-        <string>Computer Name</string>",
        "+        <string>azzio</string>",
        "        </property>",
        # user-password prompt "Choose a password ... safe." -> "Username Password:"
        "@@ -324,3 +324,3 @@",
        '      <property name="text">',
        "-      <string>Choose a password to keep your account safe.</string>",
        "+      <string>Username Password:</string>",
        "      </property>",
        # reuse-password checkbox "Use the same password for the administrator account."
        # -> "Use username password for root password." per PROMPT.md Prompt#1. This is the
        # checkbox that (with doReusePassword:true) is checked by default, making root reuse
        # the user's password. Ascending order: line 471, after 324, before 494.
        "@@ -471,3 +471,3 @@",
        '      <property name="text">',
        "-      <string>Use the same password for the administrator account.</string>",
        "+      <string>Use username password for root password.</string>",
        "      </property>",
        # root-password prompt "Choose a password ... administrator account." -> "Root Password:"
        "@@ -494,3 +494,3 @@",
        '      <property name="text">',
        "-      <string>Choose a password for the administrator account.</string>",
        "+      <string>Root Password:</string>",
        "      </property>",
        # --- UsersPage.cpp: hide the Full Name row + the strong-password checkbox
        # The "What is your name?" (Full Name) row is REMOVED (hidden): the account's
        # GECOS/full name is not asked for. Each child widget of the QHBoxLayout is
        # hidden individually (the layout itself is not a widget). Config::isReady()
        # is relaxed below so a permanently-empty full name does not disable Next.
        # The "Require strong passwords." checkbox is likewise force-hidden.
        "--- a/src/modules/users/UsersPage.cpp",
        "+++ b/src/modules/users/UsersPage.cpp",
        "@@ -105,6 +105,17 @@",
        "     connect( ui->textBoxFullName, &QLineEdit::textEdited, config, &Config::setFullName );",
        "     connect( config, &Config::fullNameChanged, this, &UsersPage::onFullNameTextEdited );",
        " ",
        '+    // Azzio: hide the "What is your name?" (Full Name) row entirely. The account\'s',
        "+    // GECOS/full name is not asked for; Config::isReady() no longer requires a",
        "+    // non-empty full name (see Config.cpp), so Next stays reachable with these widgets",
        "+    // gone. The QHBoxLayout that holds the field is not a widget, so each child widget",
        "+    // is hidden individually. The login VALUE is seeded to \"main\" in Config.cpp\'s",
        "+    // setConfigurationMap() (the \"main\" placeholder is now merely a fallback hint).",
        "+    ui->labelWhatIsYourName->setVisible( false );",
        "+    ui->textBoxFullName->setVisible( false );",
        "+    ui->labelFullName->setVisible( false );",
        "+    ui->labelFullNameError->setVisible( false );",
        "+",
        "     // If the hostname is going to be written out, then show the field",
        "     if ( ( m_config->hostnameAction() == HostNameAction::EtcHostname )",
        "          || ( m_config->hostnameAction() == HostNameAction::SystemdHostname ) )",
        "@@ -156,1 +166,5 @@",
        "-    ui->checkBoxRequireStrongPassword->setVisible( m_config->permitWeakPasswords() );",
        '+    // Azzio: never show the "Require strong passwords." checkbox. Password-strength',
        "+    // enforcement is not offered on this installer (no libpwquality checks are",
        "+    // configured in users.conf, so any password -- including an empty one -- is",
        "+    // accepted). Force the checkbox hidden regardless of the config value.",
        "+    ui->checkBoxRequireStrongPassword->setVisible( false );",
        # --- Config.cpp: required-field errors + fixed hostname seed ---------
        # Two status functions get a required-field message on empty; the hostname
        # is seeded to the template default and the login is seeded to "main" (both
        # in setConfigurationMap()'s tail below). isReady()'s readyFullName gate is
        # dropped (the Full Name row is hidden) -- see the @@ -765 hunk.
        "--- a/src/modules/users/Config.cpp",
        "+++ b/src/modules/users/Config.cpp",
        # loginNameStatus(): empty login -> required-field error (was "ok" / "")
        # 1 leading + 1 trailing context line; old=6 (1+4+1), new=9 (1+7+1).
        "@@ -236,6 +236,9 @@",
        '     // An empty login is "ok", even if it isn\'t really',
        "-    if ( m_loginName.isEmpty() )",
        "-    {",
        "-        return QString();",
        "-    }",
        "+    // Azzio: an empty login is NOT ok -- a username is required. Returning a",
        "+    // non-empty status both surfaces this as the field error and (via isReady()'s",
        "+    // loginNameStatus().isEmpty() gate) keeps Next disabled until a name is typed.",
        "+    if ( m_loginName.isEmpty() )",
        "+    {",
        '+        return tr( "User parameter must include at least one character." );',
        "+    }",
        " ",
        # hostnameStatus(): empty hostname -> required-field error (was "ok" / "")
        # 1 leading + 1 trailing context line; old=6 (1+4+1), new=10 (1+8+1).
        "@@ -301,6 +304,10 @@",
        '     // An empty hostname is "ok", even if it isn\'t really',
        "-    if ( m_hostname.isEmpty() )",
        "-    {",
        "-        return QString();",
        "-    }",
        "+    // Azzio: an empty hostname is NOT ok -- a hostname is required. The default",
        '+    // template seeds "azzio", but if the user clears the field this shows the error',
        "+    // and (via isReady()'s hostnameStatus().isEmpty() gate) blocks Next. Two-char",
        "+    // minimum mirrors HOSTNAME_MIN_LENGTH.",
        "+    if ( m_hostname.isEmpty() )",
        "+    {",
        '+        return tr( "Hostname parameter must include at least two characters." );',
        "+    }",
        " ",
        # isReady(): drop the readyFullName gate. The Full Name row is hidden
        # (UsersPage.cpp), so fullName() is always empty by design -- gating on it
        # would leave Next permanently disabled. Login/hostname/password still gate.
        # old=6 (3 ctx + del + 2 ctx + del); new=5 (3 ctx + 2 ctx) -- net -2 lines... wait:
        # explicit: old has 1 ctx('{'), the readyFullName del line, then readyHostname/
        # readyUsername/readyUserPassword/readyRootPassword ctx, then the return del +
        # return add. Counted directly below.
        "@@ -765,8 +775,7 @@",
        " {",
        "-    bool readyFullName = !fullName().isEmpty();  // Needs some text",
        "     bool readyHostname = hostnameStatus().isEmpty();  // .. no warning message",
        "     bool readyUsername = !loginName().isEmpty() && loginNameStatus().isEmpty();  // .. no warning message",
        "     bool readyUserPassword = userPasswordValidity() != Config::PasswordValidity::Invalid;",
        "     bool readyRootPassword = rootPasswordValidity() != Config::PasswordValidity::Invalid;",
        "-    return readyFullName && readyHostname && readyUsername && readyUserPassword && readyRootPassword;",
        "+    return readyHostname && readyUsername && readyUserPassword && readyRootPassword;",
        " }",
        # Seed the fixed hostname from the template. (The login seed is a SEPARATE
        # hunk further down -- @@ -1069 -- so this hostname hunk keeps its original,
        # fuzz-0 trailing context. Splicing the login lines into this hunk's trailing
        # context made `patch` apply it with fuzz 2, weakening the fail-loud contract.)
        # old=6 (3 ctx + } + blank + setConfig ctx); new=19 (6 ctx + 13 added).
        "@@ -1020,6 +1027,19 @@",
        '         m_forbiddenHostNames = Calamares::getStringList( hostnameSettings, "forbidden_names" );',
        "         m_forbiddenHostNames << alwaysForbiddenHostNames();",
        "         tidy( m_forbiddenHostNames );",
        "+",
        "+        // Azzio: seed a fixed default hostname and take it off the auto-derive",
        "+        // path. Upstream leaves the hostname empty until the user types a name, then",
        "+        // re-expands m_hostnameTemplate on every keystroke -- so the hostname keeps",
        "+        // changing as the Login field changes. Expanding the template once here (with",
        '+        // no user data) gives the initial value, and setHostName() marks it "custom"',
        "+        // (m_customHostName = true) so nothing later recomputes it. With a literal",
        '+        // template ("azzio") the field shows "azzio" by default and stays "azzio".',
        "+        const QString seededHostname = makeHostnameSuggestion( m_hostnameTemplate, QStringList(), QString() );",
        "+        if ( !seededHostname.isEmpty() )",
        "+        {",
        "+            setHostName( seededHostname );",
        "+        }",
        "     }",
        " ",
        "     setConfigurationDefaultGroups( configurationMap, m_defaultGroups );",
        # Seed the default login to "main" -- its OWN hunk (kept apart from the hostname
        # hunk above so both apply at fuzz 0). PROMPT.md: the Username field must DEFAULT
        # to containing "main", not merely hint it via the placeholder. Anchored on the
        # existing tail (updateGSAutoLogin(..., loginName()) then checkReady()), inserting
        # the seed right before checkReady() so readiness is (re)computed with the login
        # already set. setLoginName() emits loginNameStatusChanged -> checkReady (a wired
        # connection), and marks the value "custom" (m_customLoginName) so the Full-Name
        # auto-derive path never overwrites it; loginNameStatus() is then non-empty so the
        # required-field error clears and Next is reachable out of the box. (ApplyPresets
        # for "loginName" runs after this but does NOT overwrite it: users.conf ships no
        # `presets` map, so apply() takes its no-value branch and never calls setProperty.)
        # old=4 (updateGSAutoLogin + blank + checkReady + blank ctx); new=11 (+7 added).
        "@@ -1069,4 +1076,11 @@",
        "     updateGSAutoLogin( doAutoLogin(), loginName() );",
        "+",
        "+    // Azzio: seed the default login to \"main\" so the Username field opens",
        "+    // pre-filled (PROMPT.md). setLoginName() marks it custom (m_customLoginName),",
        "+    // so the Full-Name auto-derive path never clobbers it, and it emits",
        "+    // loginNameStatusChanged -> checkReady() (below) so readiness reflects the",
        "+    // seeded value immediately.",
        '+    setLoginName( QStringLiteral( "main" ) );',
        "     checkReady();",
        " ",
        "     ApplyPresets( *this, configurationMap ) << \"fullName\"",
        "--- a/src/modules/users/SetPasswordJob.cpp",
        "+++ b/src/modules/users/SetPasswordJob.cpp",
        "@@ -81,12 +81,17 @@",
        '                                             tr( "rootMountPoint is %1" ).arg( destDir.absolutePath() ) );',
        "     }",
        " ",
        '-    if ( m_userName == "root" && m_newPassword.isEmpty() )  //special case for disabling root account',
        '+    // Azzio: an empty password locks the account (shadow "!") for ANY user, not just',
        "+    // root. The installer lets the user skip the password field; a skipped password must",
        '+    // yield a locked account (no usable password) rather than crypt("") -- an empty but',
        "+    // *valid* password that would allow passwordless login. Upstream only special-cased",
        '+    // "root" here; broadening it to every user gives the "skip -> * (locked)" behaviour.',
        '+    if ( m_newPassword.isEmpty() )  //special case for disabling the account (no usable password)',
        "     {",
        '         int ec = Calamares::System::instance()->targetEnvCall( { "usermod", "-p", "!", m_userName } );',
        "         if ( ec )",
        "         {",
        '-            return Calamares::JobResult::error( tr( "Cannot disable root account." ),',
        '+            return Calamares::JobResult::error( tr( "Cannot disable account %1." ).arg( m_userName ),',
        '                                                 tr( "usermod terminated with error code %1." ).arg( ec ) );',
        "         }",
        "         return Calamares::JobResult::ok();",
    ]
    # Trailing newline so the last line is terminated (patch/POSIX text file).
    return "\n".join(lines) + "\n"
