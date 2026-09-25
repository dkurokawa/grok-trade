# SAM custom build (BuildMethod: makefile).
#
# Dependencies are installed for the Lambda platform (Linux x86_64 / CPython 3.11)
# rather than the host's, so `sam build` works on macOS/ARM without Docker.
# --only-binary=:all: makes a missing manylinux wheel a hard error instead of
# silently falling back to a host-compiled source build that Lambda can't run.

# `pip` is not guaranteed to be on PATH inside SAM's build shell, so go via an
# interpreter. Plain `python3` is not safe to assume either: on macOS it can
# resolve to the Xcode shim, which refuses to run until its licence is accepted
# (and that resets on an Xcode update). Pick the first interpreter that actually
# executes.
PYTHON ?= $(shell for p in python3.11 python3.12 python3.13 python3 /opt/homebrew/bin/python3; do \
	command -v $$p >/dev/null 2>&1 && $$p -c '' >/dev/null 2>&1 && { command -v $$p; break; }; \
	done)
PIP = $(PYTHON) -m pip

LAMBDA_PIP_FLAGS = \
	--platform manylinux2014_x86_64 \
	--python-version 3.11 \
	--implementation cp \
	--only-binary=:all:

# Application modules shipped to Lambda (tests and local-only files excluded).
APP_FILES = app.py lambda_trading.py trading_core.py config.py \
            grok_client.py opus_client.py grok_validator.py skip_logic.py \
            trader.py risk_guard.py discord_notifier.py

define build_function
	$(PIP) install -r requirements.txt $(LAMBDA_PIP_FLAGS) --target "$(ARTIFACTS_DIR)"
	cp $(APP_FILES) "$(ARTIFACTS_DIR)"
	cp -r db "$(ARTIFACTS_DIR)/db"
	find "$(ARTIFACTS_DIR)" -name '__pycache__' -type d -prune -exec rm -rf {} +
endef

build-TradingFunction:
	$(build_function)

build-ApiFunction:
	$(build_function)
