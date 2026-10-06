# infrastructure-terra

Completed work, CI evidence and the next deployment step: [handoff — 2026-10-03](handoff-2026-10-03.md).

Ansible deployment for Terra's tile origin. `terrain-platform` builds releases;
this repository deploys and serves them with Nginx and `pmtiles serve`.

## Controller and target

Run `make setup` with Python 3.12 on the controller, then `make deps` for the roles this repository composes (`requirements.yml`):

- `geerlingguy.docker` (Docker Engine, Compose plugin, `daemon.json`);
- `yourorg.shared_roles` from the private `antoni/roles` repository, pinned to a commit; `reverse_proxy` there provides nginx and the certificates, and `mountain_tile_backend` (moved there for now) writes its own self-contained vhost on it. `make deps` clones it over SSH, so it needs your key. (The collection's namespace is still the template placeholder `yourorg` in its `galaxy.yml`.)

The target needs only Python 3.9+ and SSH access with sudo; everything else is installed by those roles. The checked-in `inventory/hosts.yml` has an empty `tile_backend` group, so it selects no target. Create your private inventory from the example:

```sh
cp inventory/hosts.example.yml inventory/hosts.local.yml
# Fill in host/IP, SSH user, domain and the Let's Encrypt contact address.
.venv/bin/ansible-inventory -i inventory/hosts.local.yml --graph
.venv/bin/ansible tile_backend -i inventory/hosts.local.yml -m ping
```

`hosts.local.yml` is ignored by Git. Keep secrets in Ansible Vault or your SSH agent, rather than a committed inventory (for password-based SSH on a test host, read it from the environment: `ansible_password: "{{ lookup('env', 'VPS_PASSWORD') }}"`). Host-key checking remains enabled. Settings shared by all hosts (the Docker daemon options, the proxy's certificate mode) are in `inventory/group_vars/tile_backend.yml`.

A complete host, from a fresh Debian or Ubuntu install:

```sh
.venv/bin/ansible-playbook -i inventory/hosts.local.yml playbooks/tile_backend_host.yml \
  -e mountain_tile_backend_activate_release=<release id>
```

Make a release available on the host first (below). The first run installs Docker and the proxy, obtains the certificate and starts the origin; later runs change nothing unless something changed.

## Checks

`make check test lint rehearse` is what CI would run (CI runs `setup check test` and the rehearsal; it cannot fetch the private roles, so the host playbook's syntax check is skipped there). `make lint` is `yamllint` and `ansible-lint` at the `production` profile and needs `make deps`. To test a real host, `tests/remote_rehearsal.py` and `tests/remote_security.py` (see their docstrings).

## Private deployment rehearsal

`make check test rehearse` exercises the real role locally against generated PNG
PMTiles, including a repeat run with zero changes, a second release, retained
old tile URLs and rollback. It binds a free loopback port, uses a separate Compose
project, needs no sudo, and removes its stack afterwards. CI runs the same checks.

For the first remote rehearsal, generate the same synthetic serving fixture:

```sh
.venv/bin/python tests/rehearsal.py --fixture-root /tmp/terra-test-releases \
  --release synthetic-rehearsal
```

Create `/opt/mountain_tile_backend/releases` on the target, then copy the entire
`synthetic-rehearsal` directory there. Use your configured project directory if
you override the default. The SSH user may need sudo-assisted copying for `/opt`.
Activate it:

```sh
.venv/bin/ansible-playbook -i inventory/hosts.local.yml \
  playbooks/mountain_tile_backend.yml \
  -e mountain_tile_backend_activate_release=synthetic-rehearsal \
  -e mountain_tile_backend_require_publishable=false
```

This fixture tests the serving contract; it is not a terrain-platform product
release. Keep the rehearsal private. With the default vhost setting, use an SSH
tunnel to reach the loopback-bound origin:

```sh
ssh -L 18090:127.0.0.1:8090 USER@HOST
curl -i http://127.0.0.1:18090/healthz
curl http://127.0.0.1:18090/api/capabilities
```

The role checks capabilities, versioned TileJSON and a real tile from every
archive. Also test the same paths through the configured host reverse proxy
before declaring the remote deployment verified.

## Remote rehearsal and security probes

With the prerequisites installed on a host (Docker, Compose, Python; installed by hand, the role does not do it)
and a private inventory, `tests/remote_rehearsal.py` runs the release lifecycle there (first deployment, a repeat
with zero changes, rejection of an invalid release, a switch with the old tile URLs still served, rollback), and
`tests/remote_security.py` probes the public path (redirects, TLS versions, path traversal, methods, tile edge cases,
cache headers). Both need `--host`/`--inventory`; see their docstrings. The probes list hardening gaps that are not
the role's to close (HSTS, rate limiting, a catch-all `default_server`) as GAP without failing the run.

## Terrain releases

Build a release with immutable asset URLs, using the actual origin and release ID:

```sh
# In ../terrain-platform:
make release VERSION=2026.10.0 PUBLIC_BASE=https://tiles.example.com/releases/2026.10.0
```

Copy the finished directory to the target's release root and run the playbook
with `-e mountain_tile_backend_activate_release=2026.10.0`. The default validator
requires `manifest.publishable: true`, so licence-blocked development releases
cannot be activated accidentally. The `/release/...` default used in local app
development is rejected for deployment.

The role verifies nested `pmtiles/<dataset>/<hash>.pmtiles`, then indexes them in
a shared `tile-store/<dataset>/<hash>.pmtiles`. Hard links avoid duplicate bytes
on the same filesystem; a cross-filesystem store copies once. Existing versions
are never overwritten and survive release removal. Do not edit deployed release
archives in place. Automatic pruning is deferred until a retention policy exists.

Rollback uses the same playbook with the previous release ID. Clearing the
activation variable preserves the existing `current` symlink.

The role itself lives in the shared roles repository for now (`antoni/roles`, `roles/mountain_tile_backend`, used here as
`yourorg.shared_roles.mountain_tile_backend`); `make deps` installs it. See its README there for settings and architecture.
The remote lifecycle and security probes have been run against a throwaway Debian 13 host with a self-signed
certificate. Let's Encrypt issuance, a real domain and Cloudflare remain to be checked.
