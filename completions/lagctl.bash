_lagctl_completion() {
    local current previous subcommand separator word
    current="${COMP_WORDS[COMP_CWORD]}"
    previous="${COMP_WORDS[COMP_CWORD-1]}"
    subcommand="${COMP_WORDS[1]}"

    local commands="add list ls status show network start stop restart run logs remove rm doctor tui completion"
    if (( COMP_CWORD == 1 )); then
        COMPREPLY=( $(compgen -W "$commands --help --version" -- "$current") )
        return
    fi

    case "$subcommand" in
        add)
            separator=0
            for ((i=2; i<COMP_CWORD; i++)); do
                [[ "${COMP_WORDS[i]}" == "--" ]] && separator=$i
            done
            if (( separator > 0 && COMP_CWORD > separator )); then
                COMPREPLY=( $(compgen -c -f -- "$current") )
                return
            fi
            case "$previous" in
                --cwd)
                    COMPREPLY=( $(compgen -d -- "$current") )
                    return
                    ;;
                --mode)
                    COMPREPLY=( $(compgen -W "keep-alive on-failure once" -- "$current") )
                    return
                    ;;
            esac
            if [[ "$current" == -* ]]; then
                COMPREPLY=( $(compgen -W "--help --cwd --env --mode --interval --no-run-at-load --throttle --clean-path --no-start --force --allow-background-children --" -- "$current") )
            fi
            ;;
        status|show|network|start|stop|restart|run)
            COMPREPLY=( $(compgen -W "$(command lagctl completion jobs 2>/dev/null)" -- "$current") )
            ;;
        logs)
            case "$previous" in
                --stream)
                    COMPREPLY=( $(compgen -W "all stdout stderr" -- "$current") )
                    return
                    ;;
            esac
            if [[ "$current" == -* ]]; then
                COMPREPLY=( $(compgen -W "--help -f --follow -n --lines --stream" -- "$current") )
            else
                COMPREPLY=( $(compgen -W "$(command lagctl completion jobs 2>/dev/null)" -- "$current") )
            fi
            ;;
        remove|rm)
            if [[ "$current" == -* ]]; then
                COMPREPLY=( $(compgen -W "--help --purge-logs" -- "$current") )
            else
                COMPREPLY=( $(compgen -W "$(command lagctl completion jobs 2>/dev/null)" -- "$current") )
            fi
            ;;
        completion)
            COMPREPLY=( $(compgen -W "zsh bash" -- "$current") )
            ;;
        *)
            COMPREPLY=( $(compgen -W "--help" -- "$current") )
            ;;
    esac
}

complete -F _lagctl_completion lagctl
