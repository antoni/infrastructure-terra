# Migration from the previous role

Delete the old product-generation templates/tasks rather than carrying them forward:

- `map_style.json.j2`
- `metadata-*.json.j2`
- `tilejson-*.json.j2`
- vector/raster/terrain layer lists in defaults
- bootstrap release generation
- Nginx locations that expect expanded `{z}/{x}/{y}` directories

Retain the repository's existing `LICENSE` file unchanged when applying this role in-place. It is not reproduced in this generated ZIP because the original license text was not available as a file in this conversation.

# Migration to the shared reverse proxy (2026-10-04)

A host that used the earlier `manage_vhost` option, its own certificate variables or legacy vhost cleanup:

1. Add `docker` and `reverse_proxy` (see `playbooks/tile_backend_host.yml`) before this role, and list the domain in
   `reverse_proxy_certificates`.
2. Remove `mountain_tile_backend_certificate_mode`, `_manual_certificate*`, `_cleanup_legacy_vhosts`, `_legacy_vhosts`,
   `_remove_vhost_when_disabled` and `_host_nginx_includes` from the inventory; certificate mode and paths now come from
   `reverse_proxy_certificate_mode`, `reverse_proxy_ssl_certificate` and `reverse_proxy_ssl_certificate_key`.
3. Set `mountain_tile_backend_domain` (it has no default any more).
4. The vhost file is now `<domain>.conf` (it was `mountain_tile_backend.conf`). Remove the old file and its link from
   `sites-available` and `sites-enabled` once, by hand; the role no longer cleans up after earlier versions.
