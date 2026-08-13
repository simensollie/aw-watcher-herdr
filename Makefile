.PHONY: install test verify verify-cli clean

install:
	python3 -m venv .venv
	.venv/bin/pip install -q --upgrade pip
	.venv/bin/pip install -q -e ".[dev]"

test:
	.venv/bin/pytest -q

verify:
	PY=.venv/bin/python scripts/verify.sh

# Same end-to-end check through the CLI source, which is the Windows transport.
verify-cli:
	PY=.venv/bin/python SOURCE=cli scripts/verify.sh

clean:
	rm -rf .venv build dist *.egg-info .pytest_cache
	find . -name __pycache__ -type d -exec rm -rf {} +
