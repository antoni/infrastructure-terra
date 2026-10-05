# Roles a production tile host needs

Written 2026-10-04. The rule: generic host setup comes from dedicated roles in `antoni/roles`
(or a well-known Galaxy role); `mountain_tile_backend` does none of it. This is the list of what a production origin
needs, what exists, and in which order they would run. Nothing here has been added to the roles repository.

## Exists today

| Need | Role | Notes |
| --- | --- | --- |
| Docker Engine, Compose, `daemon.json` | `geerlingguy.docker` 8.0.0 | log rotation and `userland-proxy: false` set in `inventory/group_vars/tile_backend.yml` |
| nginx, Let's Encrypt certificates, real IP, GeoIP (the tile vhost sets its own TLS settings, security headers, HSTS and rate-limit zones) | `yourorg.shared_roles.reverse_proxy` | log format is CrowdSec- and Fail2ban-friendly |
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

## Scope of each role

**`base`.** The identity of the machine and nothing else: hostname and `/etc/hosts`, timezone, locale, the apt sources
(components, mirrors, the `non-free-firmware` decision) and the `apt` housekeeping every later role relies on (an
up-to-date cache, `apt-transport-https`, `ca-certificates`). It does not install tools for people to use (that is `common`
or `common_utils` with a short list), it does not create users (`ssh_hardening` does), and it makes no security decisions.
It is the first role because a repeatable host starts with a name, a clock zone and working package sources.

**`ssh_hardening`.** Everything about how administrators get in. It creates the named admin user(s) with sudo and their
authorised public keys, turns off root login and password authentication, restricts logins with `AllowUsers`, selects
modern key-exchange, cipher and MAC algorithms, sets login grace time and retry limits, and can move the port. It validates
the new `sshd_config` with `sshd -t` and only then reloads, and it must not lock the operator out: the role refuses to
disable password login unless at least one key is installed for an allowed user. It does not decide which addresses may
connect (that is the firewall) and does not ban abusers (`intrusion_ban`).

**`firewall`.** The network policy of the host: default deny for inbound traffic, allow established connections and loopback,
and open exactly the ports the host's role set declares (22, 80 and 443 here), each optionally limited to source ranges,
with IPv6 treated the same as IPv4. For the tile origin it also holds the Cloudflare rule: ports 80 and 443 only from
Cloudflare's published ranges once the CDN is in front, so the origin cannot be reached around it. It cooperates with Docker
(which writes its own iptables/nftables rules) instead of fighting it, and it applies rules atomically so a mistake cannot
leave the host open or unreachable. It does not configure services and does not ban individual addresses.

**`unattended_upgrades`.** Automatic security patching: it enables `unattended-upgrades` for the security origins
(optionally updates from the Docker repository), sets how often the package lists refresh, whether the host reboots by
itself and in which window when a kernel or libc update needs it, whether a mail or a notification is sent, and configures
`needrestart` so services that use updated libraries are restarted automatically. It deliberately does not upgrade
everything (no major or feature changes), and it does not touch the application releases or container images, which are
pinned and updated by deployments.

**`time_sync`.** One accurate clock: install and configure `chrony` with a set of NTP servers (the provider's, or pool
servers), make it the only time service on the host (it disables `systemd-timesyncd` to avoid two daemons), and check that
the clock is synchronised. It matters because certificate validity, Let's Encrypt, log correlation and token expiry all
break with a wrong clock. Nothing else is in scope: no timezone (that is `base`), no hardware clock tricks.

**`intrusion_ban`.** Detects hostile behaviour in the logs and bans the sources. One engine, chosen by a variable
(`fail2ban` or `crowdsec`, never both), with jails or scenarios for ssh and for the nginx logs that `reverse_proxy` already
writes in both formats; ban durations, an allow-list of your own addresses, and the firewall integration (bans are applied
through the host firewall, not a separate mechanism). It does not block countries (that is `reverse_proxy`'s GeoIP rules)
and does not rate-limit tile requests (also the proxy); it reacts to patterns such as repeated failures and scanners.

**`monitoring`.** Makes the host observable: install and configure `node_exporter` (CPU, memory, disk, network, systemd
unit states) on a private address or behind the firewall, plus the checks that matter for this host, namely free disk space
on the release volume, container health and restarts, nginx up, and certificate expiry for the served domains. It provides
alert rules or hands the metrics to whatever you run (Prometheus, Grafana Cloud, a simple script that mails), and says
which of those it needs. It does not run the monitoring server and does not collect application logs (that is log shipping,
a separate decision).

**`log_limits`.** Keeps logs from filling the disk: journald size and retention (`SystemMaxUse`, `MaxRetentionSec`), a
logrotate policy for `/var/log/nginx` (including the second log written for Fail2ban) with compression and a retention
period, and sane limits for any other service that writes under `/var/log`. Docker's own container logs are already bounded
in `daemon.json`. It does not ship logs anywhere and does not decide what is logged; it only bounds how much stays.

**`sysctl_baseline`.** A short, reviewed set of kernel settings applied through `/etc/sysctl.d`: SYN cookies and reverse-path
filtering on, no source routing or ICMP redirects, a larger `somaxconn` and a wider `ip_local_port_range` for a proxy under
load, sensible `vm` and file-descriptor limits. Each setting is a variable with a comment saying why, and the role checks
the value after applying it. It does not tune individual applications (nginx worker counts, container limits) and does not
change anything outside `sysctl`.

**`cloudflare_real_ip`.** Keeps nginx honest about who the client is once Cloudflare is in front. It fetches Cloudflare's
published IPv4 and IPv6 ranges, writes them as the trusted proxies for `reverse_proxy` (`set_real_ip_from`) with the right
header (`CF-Connecting-IP`), and refreshes them on a timer, reloading nginx only when the list changed; the same list can
feed the firewall rule that limits ports 80 and 443 to Cloudflare. It must fail safe: if the download fails it keeps the last
good list. It does not terminate TLS, cache anything or configure Cloudflare itself.
