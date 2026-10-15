# ==============================================================================
# WhatsApp Gemini Agent Makefile
# ==============================================================================

.PHONY: install install-dev run test lint clean

install:
	pip install -r requirements.txt

install-dev: install
	pip install -r requirements-dev.txt

run:
	python main.py

test:
	pytest tests -v

lint:
	ruff check .

format:
	ruff check --fix .

clean:
	rm -rf __pycache__ */__pycache__ .pytest_cache .ruff_cache logs/*.log
