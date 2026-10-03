.PHONY: setup check test rehearse

setup:
	python3 -m venv .venv
	.venv/bin/pip install -r requirements-dev.txt

check:
	.venv/bin/ansible-playbook --syntax-check playbooks/mountain_tile_backend.yml
	git diff --check

test:
	.venv/bin/python -m unittest discover -s tests -v

rehearse:
	.venv/bin/python -u tests/rehearsal.py
