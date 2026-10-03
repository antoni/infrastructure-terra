# infrastructure-terra

Ansible deployment for Terra's tile origin. `terrain-platform` builds releases;
this repository deploys and serves them with Nginx and `pmtiles serve`.

## Controller and target

Run `make setup` with Python 3.12 on the controller. The target needs Python 3.9+
and working Docker Engine with Docker Compose v2. The SSH user needs become/sudo
access; the role does not install Docker or issue TLS certificates.

The checked-in `inventory/hosts.yml` has an empty `tile_backend` group, so it
selects no target. Create your private inventory from the example:

```sh
cp inventory/hosts.example.yml inventory/hosts.local.yml
# Fill in host/IP, SSH user, domain and reverse-proxy settings.
.venv/bin/ansible-inventory -i inventory/hosts.local.yml --graph
.venv/bin/ansible tile_backend -i inventory/hosts.local.yml -m ping
```

`hosts.local.yml` is ignored by Git. Keep secrets in Ansible Vault or your SSH
agent, rather than a committed inventory. Host-key checking remains enabled.

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

See [role settings and architecture](roles/services/mountain_tile_backend/README.md).
Remote SSH, TLS, certificates and Cloudflare remain to be checked on the real host;
the local rehearsal does not claim to validate them.
