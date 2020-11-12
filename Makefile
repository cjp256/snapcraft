.PHONY: autoformat
autoformat:
	black .

.PHONY: static-test-black
static-test-black:
	black --check --diff .

.PHONY: static-test-codespell
static-test-codespell:
	codespell --quiet-level 4 --ignore-words-list keyserver --skip '*.tar,*.xz,*.zip,*.bz2,*.7z,*.gz,*.deb,*.rpm,*.snap,*.gpg,*.pyc,*.png,*.ico,*.jar,changelog,.git,.hg,.mypy_cache,.tox,.venv,_build,buck-out,__pycache__,build,dist,.vscode,parts,stage,prime,test_appstream.py,./snapcraft.spec,./.direnv'

.PHONY: static-test-flake8
static-test-flake8:
	python3 -m flake8 .

.PHONY: static-test-mypy
static-test-mypy:
	mypy .

.PHONY: static-test-shellcheck
static-test-shellcheck:
# Skip third-party gradlew script.
	find . \( -name .git -o -name gradlew \) -prune -o -print0 | xargs -0 file -N | grep shell.script | cut -f1 -d: | xargs shellcheck
	./tools/spread-shellcheck.py spread.yaml tests/spread/

.PHONY: static-tests
static-tests: static-test-black static-test-codespell static-test-flake8 static-test-mypy static-test-shellcheck

.PHONY: unit-tests
unit-tests:
	pytest --cov-report=xml --cov=snapcraft tests/unit
