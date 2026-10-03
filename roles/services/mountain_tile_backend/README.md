# mountain_tile_backend

Thin deployment/serving role for the Mountain Map static release origin.

The role does **not** build terrain, generate styles, generate TileJSON, generate metadata, or know dataset names. `terrain-platform` owns those artifacts. This role validates a finished release, verifies PMTiles archives, atomically activates it, and serves it through Nginx + `pmtiles serve`.

## Architecture

```text
Cloudflare / host reverse proxy
            |
            v
      127.0.0.1:8090
            |
       container Nginx
        /          \
 static release   /tiles/*
      |              |
      v              v
 releases/*      pmtiles serve
                    |
                    v
       tile-store/<dataset>/<hash>.pmtiles
```

Nginx mounts the entire release root. PMTiles mounts a shared content-addressed tile store populated before activation. Retained release assets and previously deployed tile versions remain available after `current` moves.

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

This matches terrain-platform’s existing URL contract. Build release assets with `--public-base https://<domain>/releases/<release>` (or the root-relative equivalent). Local-development `/release/...` URLs are rejected. The indexer verifies content hashes and retains archives with hard links, falling back to copying across filesystems. Archive pruning is deferred until a retention policy exists.

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

The role writes a mode `0600` `.env` for Compose runtime configuration:

- Compose project name;
- loopback host port;
- Nginx image;
- PMTiles image;
- PMTiles header/directory cache size;
- public base URL.

There are no premium/R2 secrets in the MVP. The file provides the intended place for later runtime credentials/configuration without embedding them in Compose templates.

## Cloudflare caching

The origin sends:

- short cache headers for mutable `current` aliases such as `/api/capabilities` and `/styles/...`;
- `Cache-Control: public, max-age=31536000, immutable` for `/releases/<release>/...`;
- the same immutable cache header for `/tiles/<dataset>/<sha12>/...`.

Configure Cloudflare Cache Rules so the versioned tile/release namespaces are cache-eligible. Do not apply immutable caching to a URL whose bytes can change.

## Host reverse proxy

By default:

```yaml
mountain_tile_backend_manage_vhost: false
```

The container only binds to `127.0.0.1:8090`. The existing host reverse-proxy layer can continue to own TLS, HSTS, rate limits, GeoIP, Cloudflare real-IP handling, and other shared policy.

Optional vhost management is retained for hosts that want this role to create the public proxy.

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

See the [repository README](../../../README.md) for inventory setup, playbook commands, synthetic release generation and the local Docker rehearsal. The role’s loopback serving, activation and rollback have local integration coverage. Remote SSH, TLS, certificates and Cloudflare must still be tested on the actual deployment host. Legacy vhost cleanup is disabled by default.
