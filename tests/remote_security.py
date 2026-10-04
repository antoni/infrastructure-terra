#!/usr/bin/env python3
"""Probes a deployed tile origin for the security properties that belong to the mountain_tile_backend role.

    .venv/bin/python tests/remote_security.py --host HOST [--insecure] [--ip IP]

Run it against a host with a release activated (see remote_rehearsal.py). It speaks plain HTTP/1.1 with
http.client, so paths reach the server unchanged (no client-side normalisation). Each check is PASS, FAIL
(the exit status is 1) or GAP, a hardening measure the role does not provide yet; gaps are listed so they
stay visible without failing the run. Findings about the host itself (a default nginx site, the firewall)
are not the role's and are reported as NOTE.

--insecure accepts a self-signed certificate. --ip connects there instead of resolving HOST.
"""

import argparse
import warnings
import http.client
import json
import socket
import ssl
import sys

results = {"PASS": 0, "FAIL": 0, "GAP": 0, "NOTE": 0}


def report(kind, name, detail=""):
    results[kind] += 1
    print(f"{kind:5} {name}" + (f" - {detail}" if detail else ""))


def check(ok, name, detail=""):
    report("PASS" if ok else "FAIL", name, "" if ok else detail)


def gap(present, name, detail=""):
    report("PASS" if present else "GAP", name, "" if present else detail)


class Origin:
    def __init__(self, host, ip, insecure):
        self.host, self.ip = host, ip or host
        self.context = ssl.create_default_context()
        if insecure:
            self.context.check_hostname = False
            self.context.verify_mode = ssl.CERT_NONE

    def request(self, path, method="GET", https=True, headers=None, host=None, body=None):
        headers = {"Host": host or self.host, **(headers or {})}
        conn = (
            http.client.HTTPSConnection(self.ip, 443, context=self.context, timeout=15)
            if https
            else http.client.HTTPConnection(self.ip, 80, timeout=15)
        )
        try:
            conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            for key, value in headers.items():
                conn.putheader(key, value)
            conn.endheaders(body)
            response = conn.getresponse()
            return response.status, {k.lower(): v for k, v in response.getheaders()}, response.read(4096)
        finally:
            conn.close()


def first_tile_path(origin):
    status, _, body = origin.request("/api/capabilities")
    capabilities = json.loads(body.decode() or "{}") if status == 200 else {}
    # Capabilities can be larger than the 4 KiB read above; fetch TileJSON through the release URL.
    for dataset in capabilities.get("datasets", []):
        _, _, tj = origin.request(dataset["tilejson"])
        try:
            template = json.loads(tj.decode())["tiles"][0]
        except (ValueError, KeyError, IndexError):
            continue
        return template.replace("{z}", "0").replace("{x}", "0").replace("{y}", "0")
    return None


def main():
    warnings.simplefilter("ignore", DeprecationWarning)  # TLS 1.0 and 1.1 are tried on purpose
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True)
    parser.add_argument("--ip")
    parser.add_argument("--insecure", action="store_true")
    args = parser.parse_args()
    o = Origin(args.host, args.ip, args.insecure)

    # --- transport ---
    status, headers, _ = o.request("/healthz?x=1", https=False)
    check(status == 301 and headers.get("location") == f"https://{args.host}/healthz?x=1", "http redirects to https, keeping the path and query", f"{status} {headers.get('location')}")
    for name, version in (("TLS 1.0", ssl.TLSVersion.TLSv1), ("TLS 1.1", ssl.TLSVersion.TLSv1_1)):
        try:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
            ctx.minimum_version = ctx.maximum_version = version
            ctx.set_ciphers("ALL:@SECLEVEL=0")
            with socket.create_connection((o.ip, 443), timeout=10) as raw, ctx.wrap_socket(raw, server_hostname=args.host):
                report("FAIL", f"{name} is refused", "the handshake succeeded")
        except ssl.SSLError:
            report("PASS", f"{name} is refused")
        except (ValueError, OSError) as exc:
            report("NOTE", f"{name} could not be tried from this client", str(exc))
    status, headers, _ = o.request("/healthz")
    check(status == 204, "https health check answers 204", str(status))
    check("nginx/" not in headers.get("server", ""), "the server version is not disclosed", headers.get("server", ""))
    gap("strict-transport-security" in headers, "HSTS on https responses", "no Strict-Transport-Security header")
    gap(headers.get("x-content-type-options") == "nosniff", "X-Content-Type-Options: nosniff", "header missing")

    # --- what is served ---
    status, _, body = o.request("/")
    check(status == 200 and b"mountain_tile_backend" in body, "the root answers with the service name only", str(status))
    for path in ("/releases/", "/releases/current/", "/releases/current/capabilities.json", "/pmtiles/", "/__pmtiles/synthetic/x.pmtiles", "/tile-store/", "/.git/config", "/docker-compose.yml"):
        status, _, body = o.request(path)
        check(status in (403, 404) and b"root:" not in body, f"{path} is not served", str(status))

    # --- path traversal, sent unnormalised ---
    for path in ("/releases/../../../../etc/passwd", "/releases/%2e%2e/%2e%2e/etc/passwd", "/releases/..%2f..%2fetc/passwd",
                 "/pmtiles/../../etc/passwd", "/styles/..%2f..%2f..%2fetc/passwd", "/tiles/../../etc/passwd",
                 "/releases/synthetic-a/../../../etc/passwd", "/releases/%00", "/tiles/synthetic/%2e%2e/%2e%2e/etc/passwd"):
        status, _, body = o.request(path)
        check(status in (400, 403, 404) and b"root:" not in body, f"traversal {path}", str(status))

    # --- tiles ---
    tile = first_tile_path(o)
    check(tile is not None, "a tile URL can be found from the capabilities")
    if tile:
        status, headers, body = o.request(tile)
        check(status == 200, "a real tile is served", str(status))
        check("immutable" in headers.get("cache-control", ""), "versioned tiles are cached as immutable", headers.get("cache-control", ""))
        check("set-cookie" not in headers, "no cookies are set", headers.get("set-cookie", ""))
        status, headers, _ = o.request(tile, method="OPTIONS", headers={"Origin": "https://example.org", "Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "Range"})
        check(status == 204 and "GET" in headers.get("access-control-allow-methods", ""), "CORS preflight on a tile", f"{status}")
        status, headers, body = o.request(tile, headers={"Range": "bytes=0-9"})
        check(status in (200, 206), "a range request on a tile is answered", str(status))
        for method in ("POST", "PUT", "DELETE", "PATCH"):
            status, _, _ = o.request(tile, method=method, body=b"x")
            check(status in (400, 403, 404, 405, 501), f"{method} on a tile is refused", str(status))
        base = tile.rsplit("/", 3)[0]
        for name, path in (("an unknown content hash", "/tiles/synthetic/000000000000/0/0/0.png"), ("a tile outside the pyramid", f"{base}/99/0/0.png"),
                           ("a huge coordinate", f"{base}/0/{10**30}/0.png"), ("a negative coordinate", f"{base}/0/-1/0.png"),
                           ("an unknown dataset", "/tiles/nope/000000000000/0/0/0.png"), ("a wrong extension", f"{base}/0/0/0.exe")):
            status, _, _ = o.request(path)
            check(status in (400, 404) and status < 500, f"{name} gives a client error, not a server error", str(status))
    status, _, _ = o.request("/tiles/synthetic/" + "A" * 9000)
    check(status in (400, 404, 414), "a very long URL is rejected cleanly", str(status))
    status, _, _ = o.request("/healthz", headers={"X-Pad": "a" * 20000})
    check(status in (400, 431, 494), "oversized headers are rejected", str(status))

    # --- virtual hosts and caching of moving aliases ---
    status, _, body = o.request("/healthz", host="evil.example")
    gap(status != 204, "an unknown Host header is not served by the role's vhost",
        "https has no catch-all default_server, so the role's vhost answers any name; a shared host proxy should add one")
    status, _, body = o.request("/", host="evil.example")
    if b"Welcome to nginx" in body:
        report("NOTE", "the host's default nginx site answers other names", "host level, outside the role")
    status, headers, _ = o.request("/api/capabilities")
    check(int(headers.get("cache-control", "max-age=0").split("max-age=")[1].split(",")[0]) <= 3600, "the moving capabilities alias is cached briefly", headers.get("cache-control", ""))
    gap(False, "request rate limiting", "no limit_req or limit_conn anywhere in the role")

    print(f"\n{results['PASS']} passed, {results['FAIL']} failed, {results['GAP']} gaps, {results['NOTE']} notes")
    return 1 if results["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main())
