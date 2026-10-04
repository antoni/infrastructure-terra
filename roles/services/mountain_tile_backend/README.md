# mountain_tile_backend

Thin deployment/serving role for the Mountain Map static release origin.

The role does **not** build terrain, generate styles, generate TileJSON, generate metadata, or know dataset names. `terrain-platform` owns those artifacts. This role validates a finished release, verifies PMTiles archives, atomically activates it, and serves it through Nginx + `pmtiles serve`.

## Architecture

```text
client -> Cloudflare (later)
            |
            v
   shared reverse proxy on the host        <- yourorg.shared_roles.reverse_proxy: TLS, certificates,
   (nginx, :80/:443)                          security headers, real IP, rate-limit zones
            |  this role's vhost: <domain>.conf
            v
      127.0.0.1:8090  (loopback only, upstream keep-alive)
            |
       container nginx
        /          \
 static release   /tiles/*
      |              |
      v              v
 releases/*      pmtiles serve (read-only, runs as nobody)
                    |
                    v
       tile-store/<dataset>/<hash>.pmtiles
```

Nginx mounts the entire release root. PMTiles mounts a shared content-addressed tile store populated before activation. Retained release assets and previously deployed tile versions remain available after `current` moves.

## What belongs where

| Concern | Owner |
| --- | --- |
| Docker Engine, Compose plugin, `daemon.json` (log rotation) | `geerlingguy.docker`, settings in `inventory/group_vars/tile_backend.yml` |
| nginx, TLS policy, certificates (Let's Encrypt, Tailscale or manual), security headers, HSTS, real client IP, rate-limit zones, GeoIP | `yourorg.shared_roles.reverse_proxy` |
| The vhost for this domain, the two containers, release validation and activation, tile store, retention, smoke tests | this role |

The role does not install Docker, nginx or certbot, does not issue certificates and does not change any shared setting. It checks that what it needs is there (Docker, Compose, the proxy's `ssl.conf`, `security-headers.conf` and `proxy.conf` snippets, the certificate files) and fails with a message naming the role to run first.

## Playbooks

```sh
make deps                        # geerlingguy.docker and the shared roles, pinned in requirements.yml
# a new host: Docker, the shared proxy, then this role
.venv/bin/ansible-playbook -i inventory/hosts.local.yml playbooks/tile_backend_host.yml \
  -e mountain_tile_backend_activate_release=<release id>
# a host that already has both: only this role (tags: docker, proxy, app select a layer of the host playbook)
.venv/bin/ansible-playbook -i inventory/hosts.local.yml playbooks/mountain_tile_backend.yml \
  -e mountain_tile_backend_activate_release=<release id>
```

Required per host (private inventory): `mountain_tile_backend_domain`, and for Let's Encrypt `reverse_proxy_email`. DNS for the domain must point at the host before the first run.

## Release contract

`terrain-platform` must copy a complete immutable release before this role is run:

```text
releases/2026.10.0/
├── capabilities.json
├── manifest.json
├── styles/
│   └── ...
├── tilejson/
│   └── ...
├── pmtiles/
│   ├── elevation/<sha12>.pmtiles
│   └── base/<sha12>.pmtiles
└── metadata/
    └── ...
```

Generated data does not live in the infrastructure repository.

The deployment validator checks:

- `capabilities.json` and root `manifest.json` exist, identify the release, and are JSON objects;
- `manifest.publishable` is true unless `mountain_tile_backend_require_publishable: false` is explicitly set for a private rehearsal;
- capabilities and style source URLs reference existing immutable release assets;
- `styles/`, `tilejson/`, `pmtiles/`, and `metadata/` exist;
- at least one style JSON, TileJSON, and PMTiles archive exists;
- each TileJSON has a non-empty `tiles` array;
- each tile URL follows the versioned route contract below;
- each TileJSON dataset resolves to a PMTiles archive;
- every PMTiles archive is referenced by TileJSON;
- `pmtiles verify` succeeds for every archive.

The deeper data-release checks from the technical plan (value ranges, seams, licence flags, tile counts, JSON Schema, etc.) remain `terrain-platform`/CI responsibilities.

## URL contract

The one stable discovery URL is:

```text
GET /api/capabilities
```

It serves `releases/current/capabilities.json` with a short cache lifetime.

Versioned release artifacts are available as:

```text
/releases/<release>/styles/...
/releases/<release>/tilejson/...
/releases/<release>/metadata/...
/releases/<release>/pmtiles/...
```

They are sent with one-year immutable caching.

Online tiles use:

```text
/tiles/<dataset>/<sha12>/<z>/<x>/<y>.<ext>
```

and map to:

```text
tile-store/<dataset>/<sha12>.pmtiles
```

Example:

```text
/tiles/elevation/5a8ddd56ff08/12/2201/1375.webp
```

The role does not hardcode dataset names. `<sha12>` is the first 12 hex characters of the archive SHA-256.

For MVT archives, native PMTiles serving uses `.mvt`. Nginx also accepts a public `.pbf` URL and translates it to `.mvt` internally for compatibility.

This matches terrain-platform’s existing URL contract. Build release assets with `--public-base https://<domain>/releases/<release>` (or the root-relative equivalent). Local-development `/release/...` URLs are rejected. The indexer verifies content hashes and retains archives with hard links, falling back to copying across filesystems. Old releases and archives are kept until you opt in to [retention](#retention).

## Activation

Set:

```yaml
mountain_tile_backend_activate_release: "2026.10.0"
```

The role validates the release first and then switches `releases/current` with a same-filesystem temporary symlink + rename:

```text
.current.next -> 2026.10.0
mv -T .current.next current
```

That makes the visible `current` pointer change atomic. Rollback is the same operation with the previous release ID.

If `mountain_tile_backend_activate_release` is empty, an existing valid `current` symlink is kept.

## Smoke tests

After Compose is up, the role checks:

1. `GET /healthz` -> 204;
2. `GET /api/capabilities` -> JSON object;
3. every TileJSON through its versioned `/releases/<release>/...` URL;
4. one real tile from every PMTiles archive through the Nginx -> PMTiles path.

The tile smoke checker reads the PMTiles v3 header and probes the declared center tile plus nearby candidates. `terrain-platform` should set each archive's center to a representative populated location; a sparse archive with an empty center can fail the deploy smoke check even when its structure is valid.

## `.env`

The role writes a mode `0600` `.env` for Compose runtime configuration: project name, bind address and loopback port, the two images, the PMTiles cache size, the memory and process limits, the log rotation settings and the public base URL. There are no secrets in it today; it is the intended place for later runtime credentials.

## Container hardening

Both containers run with a read-only root filesystem, `no-new-privileges`, all capabilities dropped (nginx keeps only the four it needs to start), `init: true`, memory and process limits, `restart: unless-stopped` and size-bounded `local` log rotation. `pmtiles serve` runs as `nobody` and sees the tile store read-only. The published port is bound to `127.0.0.1`, so the only way in is the shared proxy. Pin images by digest (`nginx:1.27-alpine@sha256:...`) in the inventory when a deployment must be reproducible.

## Cloudflare caching

The origin sends:

- short cache headers for mutable `current` aliases such as `/api/capabilities` and `/styles/...`;
- `Cache-Control: public, max-age=31536000, immutable` for `/releases/<release>/...`;
- the same immutable cache header for `/tiles/<dataset>/<sha12>/...`.

Configure Cloudflare Cache Rules so the versioned tile/release namespaces are cache-eligible. Do not apply immutable caching to a URL whose bytes can change.

## The vhost on the shared reverse proxy

`mountain_tile_backend_manage_vhost: true` (the default) writes `/etc/nginx/sites-available/<domain>.conf`, enables it, runs `nginx -t` and, if nginx rejects it, takes the vhost out again before anything can restart the shared proxy. The reload happens after the containers are up. The file contains:

- port 80: a redirect to HTTPS (certbot answers HTTP-01 challenges from this block on renewal);
- port 443: the certificate named by the proxy role's own variables, then `ssl.conf` and `security-headers.conf` (HSTS, nosniff, referrer and frame policy) from the shared role, optionally `geoblocking-enforce.conf`;
- an `upstream` with keep-alive to the loopback origin, and four locations: `/healthz` (unlogged), `/pmtiles/` and `/releases/` (streamed, never buffered to disk, `Range` passes through) and everything else.

**Forwarding snippet.** The shared `proxy.conf` sends `Connection: $connection_upgrade`, which is `close` for ordinary requests, so nginx closes the upstream connection after every request and the `keepalive` of the upstream block never applies (an empty `proxy_set_header Connection ""` in the same location cannot override it; both are sent). Measured on the test host this opened about 1,000 loopback connections per second, each leaving a TIME_WAIT socket for 60 seconds; at roughly 450 cache misses per second a host runs out of ephemeral ports and requests fail. The role therefore installs `mountain_tile_backend_proxy.conf` (same forwarding headers, no WebSocket line; a tile origin has no WebSockets). `mountain_tile_backend_upstream_keepalive: 0` uses the shared `proxy.conf` unchanged. If the shared role's map used `''` instead of `close` for non-upgrade requests, this snippet would no longer be needed.

**Does the extra hop make it slower?** Not measurably. A loopback hop costs microseconds against tile latencies of tens of milliseconds, and behind Cloudflare most requests never reach the origin (versioned URLs are immutable for a year). On the 2-vCPU test VPS, runs of the same configuration varied by 3x (a single direct connection: 0.6 ms, then 2.1 ms, then 2.3 ms), more than any proxy effect, so no number is claimed. What did matter was the connection reuse above.

**Rate limiting is off by default** (`mountain_tile_backend_rate_limit_enabled`). A map view requests dozens of tiles at once, and behind a CDN all clients share the CDN's few addresses unless the proxy restores the real client IP (`reverse_proxy_real_ip_enabled` with the CDN's ranges as trusted proxies). With the shared role's `perip` zone at 10 requests per second a single Cloudflare address would throttle the world. Enable it, with a high burst, only after real-IP handling is configured.

## Retention

`mountain_tile_backend_keep_releases: N` (default `0`: keep everything) runs, after a deployment whose smoke tests passed, `prune_releases.py`: it keeps the active release and the `N` most recent others (by when they were copied to the host), removes the rest, and removes tile-store archives that no kept release lists. Tile URLs carry the archive's hash, so older apps keep requesting old archives; choose `N` so the oldest app version in use is covered. In check mode it only reports what it would remove.

## Deferred by design

This role does not implement:

- Martin/PostGIS dynamic layers;
- premium tile authorization/tokens;
- Cloudflare R2/Worker tile serving.

Those remain later phases. The stable external discovery/TileJSON/tile contracts are intended to let the storage/serving implementation move later without rewriting the web client.

## Important migration note

The previous role generated bootstrap styles, TileJSON, metadata and fixed per-layer static XYZ directories. Those concepts are intentionally absent here.

A first deployment therefore requires a real `terrain-platform` release on disk before this role can start successfully.

## Validation and repository entry points

See the [repository README](../../../README.md) for inventory setup and commands.

- `make check test lint rehearse`: syntax, unit tests (release contract, retention), `ansible-lint` at the `production` profile, and a local Docker rehearsal of deployment, a repeat run with zero changes, rejection of an invalid release, a release switch with old tile URLs retained, and rollback.
- `tests/remote_rehearsal.py`: the same lifecycle plus check mode and retention against a real host over an SSH tunnel.
- `tests/remote_security.py`: probes the public path (redirect, TLS versions, headers, path traversal, methods, tile edge cases). Remaining `GAP` lines are host-level: a catch-all `default_server` and rate limiting.

## Production checklist

Covered by this role: idempotent runs (zero changes on repeat), check mode, hardened containers with limits and log rotation, restart on boot, health checks and smoke tests through both the loopback origin and the shared proxy, atomic activation and rollback, a vhost that cannot leave the shared proxy unable to restart, opt-in retention.

Not covered here, because they are generic host concerns that belong to dedicated roles: a firewall, SSH hardening, automatic security updates, intrusion banning (fail2ban/CrowdSec), time synchronisation, monitoring and alerting, and backups (the host holds only releases that `terrain-platform` can rebuild). Cloudflare Cache Rules and real-IP ranges are configured at the CDN and through `reverse_proxy_real_ip_*`.
