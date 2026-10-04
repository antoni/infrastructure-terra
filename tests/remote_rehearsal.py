#!/usr/bin/env python3
"""Exercise the role's release lifecycle on a real remote host with generated PNG tiles.

The remote counterpart of rehearsal.py: the same fixtures and checks (first deployment, repeat run with
zero changes, rejection of an invalid release before activation, switching releases with the old tile
URLs still served, rollback), but against the host in an inventory. The origin is reached through an SSH
tunnel, so it works with the default loopback-only binding. Needs the prerequisites on the host (Docker,
Compose, Python); the role does not install them.

    VPS_PASSWORD=... SSHPASS=... .venv/bin/python tests/remote_rehearsal.py \\
        --inventory inventory/hosts.local.yml --host root@HOST

SSHPASS (optional) makes ssh and rsync use sshpass, for hosts without keys. Releases are copied to
/opt/mountain_tile_backend/releases (override with --release-root) as synthetic-a/-b/-bad and stay there.
"""

import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).parent))
from rehearsal import ROOT, fixture, run, write_json  # noqa: E402

PLAYBOOK = ["playbooks/mountain_tile_backend.yml"]


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory", required=True, type=Path)
    parser.add_argument("--host", required=True, help="user@host for ssh and rsync")
    parser.add_argument("--release-root", default="/opt/mountain_tile_backend/releases")
    parser.add_argument("--origin-port", type=int, default=8090, help="the role's mountain_tile_backend_host_port")
    args = parser.parse_args()
    prefix = ["sshpass", "-e"] if os.environ.get("SSHPASS") else []
    playbook = str(ROOT / ".venv/bin/ansible-playbook")

    work = Path(tempfile.mkdtemp(prefix="remote-rehearsal-"))
    tunnel = None
    try:
        urls = {
            "synthetic-a": fixture(work, "synthetic-a", "orange"),
            "synthetic-b": fixture(work, "synthetic-b", "blue"),
        }
        fixture(work, "synthetic-bad", "red")
        write_json(work / "synthetic-bad/tilejson/synthetic.json", {"tiles": ["/tiles/synthetic/000000000000/{z}/{x}/{y}.png"]})
        run(*prefix, "ssh", args.host, f"mkdir -p {args.release_root}")
        for release in urls | {"synthetic-bad": None}:
            run(*prefix, "rsync", "-a", "--chown=root:root", f"{work / release}", f"{args.host}:{args.release_root}/")

        port = free_port()
        tunnel = subprocess.Popen([*prefix, "ssh", "-N", "-L", f"{port}:127.0.0.1:{args.origin_port}",
                                   "-o", "ExitOnForwardFailure=yes", args.host])
        time.sleep(3)
        base = f"http://127.0.0.1:{port}"

        def deploy(release, expect_unchanged=False, must_fail=False, extra=()):
            out = subprocess.run(
                [playbook, "-i", str(args.inventory), *PLAYBOOK, "-e", f"mountain_tile_backend_activate_release={release}",
                 "-e", "mountain_tile_backend_require_publishable=false", *extra],
                cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if must_fail:
                assert out.returncode != 0, f"{release} was accepted"
                return out.stdout
            if out.returncode:
                print(out.stdout)
                raise AssertionError(f"deploying {release} failed")
            if expect_unchanged and "changed=0 " not in out.stdout:
                print(out.stdout)
                raise AssertionError("the repeat deployment changed the host")
            return out.stdout

        def check(release, retained):
            with urlopen(f"{base}/api/capabilities") as r:
                assert json.load(r)["release"] == release, "capabilities name another release"
            with urlopen(f"{base}/releases/{release}/styles/synthetic.json") as r:
                assert json.load(r)["version"] == 8
            for path in retained:
                with urlopen(base + path) as r:
                    assert r.read().startswith(b"\x89PNG"), path
                    assert "immutable" in r.headers["Cache-Control"], path

        a, b = urls["synthetic-a"], urls["synthetic-b"]
        deploy("synthetic-a")
        check("synthetic-a", [a])
        print("PASS: first deployment")
        deploy("synthetic-a", expect_unchanged=True)
        print("PASS: repeat deployment changed nothing")
        rejected = deploy("synthetic-bad", must_fail=True)
        check("synthetic-a", [a])
        print("PASS: invalid release rejected before activation;", "current still synthetic-a")
        assert "synthetic-bad" in rejected
        deploy("synthetic-b")
        check("synthetic-b", [a, b])
        print("PASS: switched to synthetic-b; the tile URLs of synthetic-a are still served")
        deploy("synthetic-a")
        check("synthetic-a", [a, b])
        print("PASS: rolled back to synthetic-a; both tile versions are still served")

        # Retention: release order is the directory's modification time. Make it explicit on the host.
        for name, day in (("synthetic-a", "01"), ("synthetic-b", "02"), ("synthetic-bad", "03")):
            run(*prefix, "ssh", args.host, f"touch -d 2026-01-{day} {args.release_root}/{name}")
        dry = subprocess.run([playbook, "-i", str(args.inventory), *PLAYBOOK, "--check", "-e", "mountain_tile_backend_activate_release=synthetic-a",
                              "-e", "mountain_tile_backend_require_publishable=false", "-e", "mountain_tile_backend_keep_releases=1"],
                             cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)  # fmt: skip
        assert dry.returncode == 0, dry.stdout
        check(a_release := "synthetic-a", [a, b])
        print("PASS: a check-mode run with retention on changes nothing")
        deploy("synthetic-a", extra=("-e", "mountain_tile_backend_keep_releases=1"))
        listing = run(*prefix, "ssh", args.host, f"ls {args.release_root}").split()
        assert "synthetic-b" not in listing and "synthetic-a" in listing and "synthetic-bad" in listing, listing
        check(a_release, [a])
        try:
            urlopen(base + b)
            raise AssertionError("the pruned release's archive is still served")
        except HTTPError as error:
            assert error.code == 404, error.code
        print("PASS: retention kept the active release and the newest other, removed synthetic-b and its archive")
    finally:
        if tunnel:
            tunnel.terminate()
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
