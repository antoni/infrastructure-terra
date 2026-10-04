# Roles a production tile host needs

Written 2026-10-04. The rule: generic host setup comes from dedicated roles in `antoni/roles`
(or a well-known Galaxy role); `mountain_tile_backend` does none of it. This is the list of what a production origin
needs, what exists, and in which order they would run. Nothing here has been added to the roles repository.

## Exists today

| Need | Role | Notes |
| --- | --- | --- |
| Docker Engine, Compose, `daemon.json` | `geerlingguy.docker` 8.0.0 | log rotation and `userland-proxy: false` set in `inventory/group_vars/tile_backend.yml` |
| nginx, TLS, Let's Encrypt, security headers, HSTS, real IP, rate-limit zones, GeoIP | `yourorg.shared_roles.reverse_proxy` | log format is CrowdSec- and Fail2ban-friendly |
| Packages | `common`, `common_utils` | `common` needs `common_packages` (no defaults); `common_utils` is a workstation toolbox: use a short package list on servers |
| Tailscale | `tailscale` | later; defaults to the `bookworm` repo |

## Needed, in priority order

Priority 1 is what I would not put a public host on the internet without.

| # | Role (suggested name) | What it does on this host | Why |
| --- | --- | --- | --- |
| 1 | `ssh_hardening` | key-only login, no root login, a named admin user with sudo, modern ciphers, `AllowUsers`, optional non-default port | the VPS currently allows root with a password; every internet host is scanned within minutes |
| 2 | `firewall` (nftables or ufw) | default deny in; allow 22 (from your addresses if you have them), 80, 443; later 80/443 only from Cloudflare's ranges so the origin cannot be reached around the CDN | Docker publishes only on 127.0.0.1 here, but a firewall is the second lock and protects whatever gets installed later |
| 3 | `unattended_upgrades` | automatic security updates, optional automatic reboot window, `needrestart` set to restart services | the proxy and Docker are exposed software; unpatched hosts are how breaches start |
| 4 | `time_sync` (chrony) | NTP | certificate validation, log correlation, Let's Encrypt |
| 5 | `intrusion_ban` (fail2ban **or** CrowdSec, not both) | bans repeat offenders on ssh and nginx using the proxy's logs | `reverse_proxy` already writes both log formats; pick one engine |
| 6 | `monitoring` (node_exporter, plus a way to alert) | disk space (releases are GBs), memory, container health, certificate expiry, `/healthz` | retention is opt-in, so a full disk is the likeliest outage |
| 7 | `log_limits` (journald size, logrotate for `/var/log/nginx`) | bounded logs | disk exhaustion; Docker logs are already bounded |
| 8 | `sysctl_baseline` | `net.ipv4.tcp_syncookies`, `rp_filter`, `ip_local_port_range` sized for the proxy, `somaxconn`, no source routing | cheap hardening; the port range matters under load |
| 9 | `cloudflare_real_ip` | keeps `reverse_proxy_real_ip_trusted_proxies` current with Cloudflare's published ranges and reloads nginx | without it, per-client limits and logs see only Cloudflare addresses |
| 10 | `base` (hostname, timezone, locale, apt sources, `/etc/hosts`) | the boring first step | repeatable hosts |

Later, when they apply: `backups` (restic; this host holds only releases that `terrain-platform` can rebuild, so low value
today), `auditd`, and `swap` sizing.

## Order in the playbook

`base` → `ssh_hardening` → `firewall` → `unattended_upgrades` → `time_sync` → `sysctl_baseline` → `geerlingguy.docker` →
`reverse_proxy` → `cloudflare_real_ip` → `mountain_tile_backend` → `intrusion_ban` → `monitoring`. Hardening goes first so
that everything installed later is already behind it; ban rules and monitoring go last because they read what the
earlier roles produced.

## Things that change when these exist

- The throwaway VPS is reached as root with a password. `ssh_hardening` would remove that; give it a key and an admin user
  first, then update `inventory/hosts.local.yml`.
- `firewall` plus Cloudflare: allow ports 80/443 only from Cloudflare's published ranges, otherwise the origin can be hit
  directly and bypass the CDN's caching and protection.
- `reverse_proxy_real_ip_*` must list Cloudflare before rate limiting is enabled for the tile vhost
  (`mountain_tile_backend_rate_limit_enabled`).
