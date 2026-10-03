# Changelog

## Unreleased

- add the repository playbook, empty default inventory, private inventory example,
  controller setup and CI;
- match terrain-platform's `/tiles/<dataset>/<sha12>/...` URLs and nested archives;
- retain archives in a shared content-addressed tile store for activation and rollback;
- verify nested PMTiles before activation and read only the fixed archive header for smoke tests;
- require publishable manifests and immutable asset URLs by default, with an explicit
  private-rehearsal override;
- disable legacy vhost cleanup by default and correct its optional path expansion;
- use Compose project names to isolate stacks;
- add a generated-data rehearsal for deployment, idempotence, rejection, retention and rollback.

## 2.0.0

Architecture update to match the Mountain Map technical plan:

- add a dedicated `pmtiles serve` container;
- proxy versioned Z/X/Y tile requests through Nginx to PMTiles;
- expose `/api/capabilities`;
- remove Ansible-generated styles, TileJSON, metadata, bootstrap map content and fixed dataset lists;
- adopt `capabilities.json`, root `manifest.json`, `styles/`, `tilejson/`, `pmtiles/`, and `metadata/` as the release contract;
- mount all retained releases so immutable versioned URLs survive `current` changes;
- validate release structure and run `pmtiles verify` before activation;
- switch `current` atomically;
- smoke-test capabilities, every TileJSON, and one real tile per PMTiles archive;
- add mode-0600 Compose `.env`;
- keep Martin, premium authorization, and R2/Worker out of the MVP role.
