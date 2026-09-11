PREFIX ?= $(HOME)/.local
ZSH_CUSTOM_COMPLETION_DIR ?= $(HOME)/.oh-my-zsh/custom/completions

.PHONY: test install uninstall

test:
	PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v

install:
	install -d "$(PREFIX)/bin" "$(PREFIX)/libexec/lagctl/completions" "$(PREFIX)/libexec/lagctl/lagctl_tui"
	install -d "$(PREFIX)/share/zsh/site-functions" "$(PREFIX)/share/bash-completion/completions"
	install -m 755 bin/lagctl "$(PREFIX)/bin/lagctl"
	install -m 644 lagctl.py "$(PREFIX)/libexec/lagctl/lagctl.py"
	install -m 644 pyproject.toml "$(PREFIX)/libexec/lagctl/pyproject.toml"
	install -m 644 lagctl_tui/__init__.py "$(PREFIX)/libexec/lagctl/lagctl_tui/__init__.py"
	install -m 644 lagctl_tui/app.py "$(PREFIX)/libexec/lagctl/lagctl_tui/app.py"
	install -m 644 lagctl_tui/styles.tcss "$(PREFIX)/libexec/lagctl/lagctl_tui/styles.tcss"
	install -m 644 completions/_lagctl "$(PREFIX)/libexec/lagctl/completions/_lagctl"
	install -m 644 completions/lagctl.bash "$(PREFIX)/libexec/lagctl/completions/lagctl.bash"
	install -m 644 completions/_lagctl "$(PREFIX)/share/zsh/site-functions/_lagctl"
	install -m 644 completions/lagctl.bash "$(PREFIX)/share/bash-completion/completions/lagctl"
	@if [ -d "$(dir $(ZSH_CUSTOM_COMPLETION_DIR))" ]; then \
		install -d "$(ZSH_CUSTOM_COMPLETION_DIR)"; \
		install -m 644 completions/_lagctl "$(ZSH_CUSTOM_COMPLETION_DIR)/_lagctl"; \
		printf 'Installed zsh completion: %s\n' "$(ZSH_CUSTOM_COMPLETION_DIR)/_lagctl"; \
	fi
	@printf 'Installed: %s\n' "$(PREFIX)/bin/lagctl"

uninstall:
	rm -f "$(PREFIX)/bin/lagctl" "$(PREFIX)/libexec/lagctl/lagctl.py" "$(PREFIX)/libexec/lagctl/pyproject.toml"
	rm -f "$(PREFIX)/libexec/lagctl/lagctl_tui/__init__.py" "$(PREFIX)/libexec/lagctl/lagctl_tui/app.py" "$(PREFIX)/libexec/lagctl/lagctl_tui/styles.tcss"
	rm -f "$(PREFIX)/libexec/lagctl/completions/_lagctl" "$(PREFIX)/libexec/lagctl/completions/lagctl.bash"
	rm -f "$(PREFIX)/share/zsh/site-functions/_lagctl" "$(PREFIX)/share/bash-completion/completions/lagctl"
	-rm -f "$(ZSH_CUSTOM_COMPLETION_DIR)/_lagctl"
	-rmdir "$(PREFIX)/libexec/lagctl/lagctl_tui" 2>/dev/null
	-rmdir "$(PREFIX)/libexec/lagctl/completions" 2>/dev/null
	-rmdir "$(PREFIX)/libexec/lagctl" 2>/dev/null
	-rmdir "$(PREFIX)/share/zsh/site-functions" "$(PREFIX)/share/zsh" 2>/dev/null
	-rmdir "$(PREFIX)/share/bash-completion/completions" "$(PREFIX)/share/bash-completion" 2>/dev/null
	-rmdir "$(PREFIX)/share" "$(PREFIX)/libexec" 2>/dev/null
	@printf 'Uninstalled: %s\n' "$(PREFIX)/bin/lagctl"
