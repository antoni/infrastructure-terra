#!/usr/bin/env python3
"""Exercise the real Ansible role and Docker stack using generated PNG tiles.

Runs without sudo in .rehearsal/, binds a free loopback port, and removes the
Compose stack in finally. No production dataset or remote host is used.
"""
import argparse
import getpass
import grp
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
from urllib.error import HTTPError
from urllib.request import urlopen

from PIL import Image
from pmtiles.tile import Compression, TileType, zxy_to_tileid
from pmtiles.writer import Writer

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / ".rehearsal"


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2))


def fixture(release_root, release_id, colour):
    release = release_root / release_id
    if release.exists():
        raise RuntimeError(f"Release already exists: {release}")
    archive_dir = release / "pmtiles" / "synthetic"
    archive_dir.mkdir(parents=True, exist_ok=True)
    image = io.BytesIO()
    Image.new("RGB", (256, 256), colour).save(image, "PNG")
    archive = archive_dir / "staging.pmtiles"
    with archive.open("wb") as stream:
        writer = Writer(stream)
        writer.write_tile(zxy_to_tileid(0, 0, 0), image.getvalue())
        writer.finalize({
            "tile_type": TileType.PNG,
            "tile_compression": Compression.NONE,
            "min_zoom": 0, "max_zoom": 0,
            "min_lon_e7": -1800000000, "max_lon_e7": 1800000000,
            "min_lat_e7": -850000000, "max_lat_e7": 850000000,
            "center_zoom": 0, "center_lon_e7": 0, "center_lat_e7": 0,
        }, {})
    version = hashlib.sha256(archive.read_bytes()).hexdigest()[:12]
    archive.rename(archive_dir / f"{version}.pmtiles")
    url = f"/tiles/synthetic/{version}/{{z}}/{{x}}/{{y}}.png"
    tilejson = f"/releases/{release_id}/tilejson/synthetic.json"
    style = f"/releases/{release_id}/styles/synthetic.json"
    write_json(release / "tilejson/synthetic.json", {
        "tilejson": "3.0.0", "tiles": [url], "minzoom": 0, "maxzoom": 0,
        "bounds": [-180, -85, 180, 85], "attribution": "Generated deployment test tile",
    })
    write_json(release / "styles/synthetic.json", {
        "version": 8, "sources": {"synthetic": {"type": "raster", "url": tilejson}},
        "layers": [{"id": "synthetic", "type": "raster", "source": "synthetic"}],
    })
    write_json(release / "capabilities.json", {"release": release_id,
        "styles": [{"id": "synthetic", "locale": "en", "url": style}],
        "datasets": [{"id": "synthetic", "kind": "raster", "tilejson": tilejson}]})
    write_json(release / "manifest.json", {"release": release_id, "publishable": False,
                                          "note": "Synthetic local deployment rehearsal"})
    write_json(release / "metadata/sources.json", {"sources": []})
    return url.replace("{z}", "0").replace("{x}", "0").replace("{y}", "0")


def run(*args):
    result = subprocess.run(args, cwd=ROOT, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT)
    if result.returncode:
        print(result.stdout)
        result.check_returncode()
    return result.stdout


def main():
    if WORK.exists():
        raise RuntimeError(f"{WORK} already exists; inspect/remove it before another rehearsal")
    WORK.mkdir()
    first = fixture(WORK / "releases", "synthetic-a", "orange")
    second = fixture(WORK / "releases", "synthetic-b", "blue")
    fixture(WORK / "releases", "synthetic-bad", "red")
    write_json(WORK / "releases/synthetic-bad/tilejson/synthetic.json",
               {"tiles": ["/tiles/synthetic/000000000000/{z}/{x}/{y}.png"]})
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    inventory = WORK / "inventory.json"
    write_json(inventory, {"all": {"children": {"tile_backend": {"hosts": {
        "localhost": {"ansible_connection": "local", "ansible_python_interpreter": sys.executable}
    }}}}})
    variables = {
        "ansible_become": False,
        "mountain_tile_backend_project_dir": str(WORK),
        "mountain_tile_backend_owner": getpass.getuser(),
        "mountain_tile_backend_group": grp.getgrgid(os.getgid()).gr_name,
        "mountain_tile_backend_compose_project": "mountain_tile_backend_rehearsal",
        "mountain_tile_backend_domain": "localhost",
        "mountain_tile_backend_public_scheme": "http",
        "mountain_tile_backend_host_port": port,
        "mountain_tile_backend_manage_vhost": False,
        "mountain_tile_backend_require_publishable": False,
    }
    variables_file = WORK / "vars.json"
    playbook = ROOT / ".venv/bin/ansible-playbook"
    retained = [first]
    try:
        for release, expect_unchanged in (("synthetic-a", False), ("synthetic-a", True),
                                           ("synthetic-b", False), ("synthetic-a", False)):
            variables["mountain_tile_backend_activate_release"] = release
            write_json(variables_file, variables)
            output = run(str(playbook), "-i", str(inventory), "playbooks/mountain_tile_backend.yml",
                         "-e", f"@{variables_file}")
            print(output)
            if expect_unchanged and "changed=0 " not in output:
                raise AssertionError("Repeat deployment changed the host")
            if expect_unchanged:
                variables["mountain_tile_backend_activate_release"] = "synthetic-bad"
                write_json(variables_file, variables)
                rejected = subprocess.run([
                    str(playbook), "-i", str(inventory), "playbooks/mountain_tile_backend.yml",
                    "-e", f"@{variables_file}"], cwd=ROOT, text=True,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                assert rejected.returncode != 0, "Invalid release was accepted"
                assert "does not exist" in rejected.stdout, rejected.stdout
                assert (WORK / "releases/current").resolve().name == "synthetic-a"
                print("PASS: invalid release rejected before activation")
            base = f"http://127.0.0.1:{port}"
            with urlopen(f"{base}/api/capabilities") as response:
                assert json.load(response)["release"] == release
            if release == "synthetic-b":
                retained = [first, second]
            for path in retained:
                with urlopen(base + path) as response:
                    assert response.read().startswith(b"\x89PNG")
                    assert "immutable" in response.headers["Cache-Control"]
            # go-pmtiles ignores x and y at zoom 0; the role must not pass such URLs on.
            try:
                urlopen(base + first.replace("/0/0/0.png", "/0/1/0.png"))
                raise AssertionError("zoom-0 tile with x=1 was served")
            except HTTPError as error:
                assert error.code == 404, error.code
            with urlopen(f"{base}/releases/{release}/styles/synthetic.json") as response:
                assert json.load(response)["version"] == 8
        print("PASS: deployment, repeat run, invalid-release rejection, release switch, retained tiles, rollback")
    finally:
        if (WORK / "docker-compose.yml").exists():
            print(run("docker", "compose", "--project-directory", str(WORK), "down"))
        shutil.rmtree(WORK)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture-root", type=Path, help="Only generate a release below this directory")
    parser.add_argument("--release", default="synthetic-rehearsal")
    args = parser.parse_args()
    if args.fixture_root:
        if not args.release or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for c in args.release) or args.release in (".", "..", "current"):
            parser.error("--release must be a safe immutable directory name")
        fixture(args.fixture_root.resolve(), args.release, "orange")
        print(args.fixture_root.resolve() / args.release)
    else:
        main()
