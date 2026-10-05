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

# mountain_tile_backend comes from the shared roles (make deps), so everything below needs them.
SHARED_ROLES := .galaxy/collections/ansible_collections/yourorg/shared_roles

$(SHARED_ROLES):
	@echo "the shared roles are missing: run 'make deps' (needs access to antoni/roles)"; exit 1

check: $(SHARED_ROLES)
	.venv/bin/ansible-playbook --syntax-check playbooks/mountain_tile_backend.yml
	.venv/bin/ansible-playbook --syntax-check playbooks/tile_backend_host.yml
	git diff --check

lint:
	.venv/bin/yamllint .
	.venv/bin/ansible-lint --offline

test: $(SHARED_ROLES)
	.venv/bin/python -m unittest discover -s tests -v

rehearse: $(SHARED_ROLES)
	.venv/bin/python -u tests/rehearsal.py
