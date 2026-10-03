# Changelog

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
