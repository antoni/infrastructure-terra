.PHONY: setup deps check lint test rehearse

# Controller: a virtualenv with Ansible and the test tools.
setup:
	python3 -m venv .venv
	.venv/bin/pip install -r requirements-dev.txt

# The roles and collections from requirements.yml. The shared roles are a private repository, fetched over
# SSH, so this needs your SSH key (CI does not run it).
deps:
	.venv/bin/ansible-galaxy role install -r requirements.yml -p .galaxy/roles --force
	.venv/bin/ansible-galaxy collection install -r requirements.yml -p .galaxy/collections --force

check:
	.venv/bin/ansible-playbook --syntax-check playbooks/mountain_tile_backend.yml
	@if [ -d .galaxy/collections/ansible_collections ]; then \
		.venv/bin/ansible-playbook --syntax-check playbooks/tile_backend_host.yml; \
	else echo "skipping playbooks/tile_backend_host.yml: run 'make deps' for the shared roles"; fi
	git diff --check

lint:
	.venv/bin/yamllint .
	.venv/bin/ansible-lint

test:
	.venv/bin/python -m unittest discover -s tests -v

rehearse:
	.venv/bin/python -u tests/rehearsal.py
