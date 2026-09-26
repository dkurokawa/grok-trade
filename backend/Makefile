# SAM custom build (BuildMethod: makefile).
#
# Dependencies are installed for the Lambda platform (Linux x86_64 / CPython 3.11)
# rather than the host's, so `sam build` works on macOS/ARM without Docker.
# --only-binary=:all: makes a missing manylinux wheel a hard error instead of
# silently falling back to a host-compiled source build that Lambda can't run.
#
# Installs from requirements.lock (pinned, generated from requirements.txt via
# `uv pip compile requirements.txt -o requirements.lock --python 3.11 --python-platform linux`)
# rather than requirements.txt directly, so the exact same dependency versions
# CI tested against are the ones that ship. Regenerate the lock whenever
# requirements.txt changes.

# `pip` is not guaranteed to be on PATH inside SAM's build shell; go via python3.
PIP = python3 -m pip

LAMBDA_PIP_FLAGS = \
	--platform manylinux2014_x86_64 \
	--python-version 3.11 \
	--implementation cp \
	--only-binary=:all:

# Application modules shipped to Lambda (tests and local-only files excluded).
APP_FILES = app.py lambda_trading.py trading_core.py config.py \
            grok_client.py opus_client.py grok_validator.py skip_logic.py \
            trader.py risk_guard.py discord_notifier.py decision_schema.py

define build_function
	$(PIP) install -r requirements.lock $(LAMBDA_PIP_FLAGS) --target "$(ARTIFACTS_DIR)"
	cp $(APP_FILES) "$(ARTIFACTS_DIR)"
	cp -r db "$(ARTIFACTS_DIR)/db"
	find "$(ARTIFACTS_DIR)" -name '__pycache__' -type d -prune -exec rm -rf {} +
endef

build-TradingFunction:
	$(build_function)

build-ApiFunction:
	$(build_function)
