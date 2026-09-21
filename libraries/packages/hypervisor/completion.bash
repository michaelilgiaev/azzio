# Bash completion for the `hypervisor` command.
#
# SOURCE OF TRUTH: this file lives in the azzio hypervisor package next to the app
# sources (libraries/packages/hypervisor/completion.bash). packaging.py's emit_plan()
# ships it VERBATIM into the built rootfs at
# /usr/share/bash-completion/completions/hypervisor (root-owned; the OFFLINE Calamares
# install rsyncs the live rootfs, so it carries onto every installed system). The
# bash-completion lazy loader picks it up there on first TAB of `hypervisor`; that
# loader ships in the `bash-completion` package, which /etc/bash.bashrc sources when
# present. This is a DATA file, not a .py, so _shipped_module_names() does not sweep it
# into LIB_DIR -- it travels only via its explicit emit_plan() entry. Edit THIS file to
# change the completion; there is no generated copy to keep in sync.
#
# The ask (PROMPT.md): typing `hypervisor view <TAB>` (and `stop`) must complete the
# running VM's NAME or PID. Both subcommands accept either -- see the hypervisor
# package's _resolve_target: an all-digit arg is a PID, otherwise a VM name. So we feed
# the completion BOTH the names and the pids of every running VM, parsed from
# `hypervisor ls`, whose rows are:
#
#     VM                       PID      SSH  DIRECTORY      <- header (skipped)
#     codelis-worktest       12345    49156  /home/.../venv/codelis
#
# i.e. field 1 = name, field 2 = pid. The header line (starts with "VM") and the
# empty-state line ("No hypervisor VMs are running.") are skipped.
#
# Position 1 completes the subcommand list. `view`/`stop` complete VM names + pids.
# Everything else falls back to filename completion (e.g. `install <file.iso>`).

_hypervisor_running_names_and_pids() {
    # Print each running VM's name and pid, one token per line. Best-effort: if
    # `hypervisor` is missing or errors, print nothing (completion just offers nothing).
    command -v hypervisor >/dev/null 2>&1 || return 0
    hypervisor ls 2>/dev/null | awk '
        NR == 1 && $1 == "VM" { next }         # header
        $1 == "No" { next }                     # "No hypervisor VMs are running."
        NF >= 2 { print $1; print $2 }          # name, pid
    '
}

_hypervisor_complete() {
    local cur prev words cword
    # _get_comp_words_by_ref is provided by bash-completion; fall back to the raw
    # arrays if it is not loaded, so the completion still works on a bare system.
    if declare -F _get_comp_words_by_ref >/dev/null 2>&1; then
        _get_comp_words_by_ref -n : cur prev words cword
    else
        cur="${COMP_WORDS[COMP_CWORD]}"
        prev="${COMP_WORDS[COMP_CWORD-1]}"
        words=("${COMP_WORDS[@]}")
        cword="$COMP_CWORD"
    fi

    local subcommands="install run ls view share status stop help --configure"

    # Position 1: the subcommand itself.
    if [ "$cword" -eq 1 ]; then
        COMPREPLY=( $(compgen -W "$subcommands" -- "$cur") )
        return 0
    fi

    # The subcommand is words[1]; its argument is what we are completing now.
    local sub="${words[1]}"
    case "$sub" in
        view|stop)
            # Complete a running VM name OR pid. Only the FIRST positional takes a
            # target; after that, offer nothing (view/stop take a single arg).
            if [ "$cword" -eq 2 ]; then
                local candidates
                candidates="$(_hypervisor_running_names_and_pids)"
                COMPREPLY=( $(compgen -W "$candidates" -- "$cur") )
                # VM names can contain ':' (rare) -- keep completion sane if so.
                if declare -F __ltrim_colon_completions >/dev/null 2>&1; then
                    __ltrim_colon_completions "$cur"
                fi
            fi
            return 0
            ;;
        install|run)
            # These take a file (ISO / qcow2). Fall back to filename completion.
            COMPREPLY=( $(compgen -f -- "$cur") )
            return 0
            ;;
        *)
            # ls / status / help / --configure take no positional we can complete.
            return 0
            ;;
    esac
}

complete -F _hypervisor_complete hypervisor
