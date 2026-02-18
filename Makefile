.PHONY: install dev test run lint clean

install:
	uv pip install -e .

dev:
	uv pip install -e ".[dev,server]"

test:
	python -m pytest tests/ -v

run:
	python -m qa_navigator --url $(URL) --instructions "$(INSTRUCTIONS)"

run-headless:
	python -m qa_navigator --url $(URL) --headless --instructions "$(INSTRUCTIONS)"

lint:
	python -m ruff check src/ tests/

clean:
	rm -rf __pycache__ src/qa_navigator/__pycache__ .pytest_cache
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

install-browser:
	playwright install chromium
