#!/usr/bin/env python3
"""Request one real HTTP tile from every PMTiles archive in a release.

The script reads only the fixed PMTiles v3 header. It tries the archive's
declared center tile and nearby tiles at a small set of zooms, through the
public Nginx -> pmtiles serve route. terrain-platform should set each archive's
center to a representative populated location.
"""

from __future__ import annotations

import argparse
import math
import struct
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

TILE_EXTENSIONS = {
    1: "mvt",
    2: "png",
    3: "jpg",
    4: "webp",
    5: "avif",
    6: "mlt",
}


class SmokeError(RuntimeError):
    pass


def read_header(path: Path) -> dict:
    with path.open("rb") as stream:
        data = stream.read(127)
    if len(data) < 127 or data[:7] != b"PMTiles" or data[7] != 3:
        raise SmokeError(f"{path}: not a PMTiles v3 archive")

    tile_type = data[99]
    ext = TILE_EXTENSIONS.get(tile_type)
    if not ext:
        raise SmokeError(f"{path}: unsupported/unknown tile type {tile_type}")

    return {
        "min_zoom": data[100],
        "max_zoom": data[101],
        "center_zoom": data[118],
        "center_lon": struct.unpack_from("<i", data, 119)[0] / 10_000_000,
        "center_lat": struct.unpack_from("<i", data, 123)[0] / 10_000_000,
        "ext": ext,
    }


def lonlat_to_xyz(lon: float, lat: float, zoom: int) -> tuple[int, int]:
    lat = max(-85.05112878, min(85.05112878, lat))
    n = 1 << zoom
    x = int((lon + 180.0) / 360.0 * n)
    lat_rad = math.radians(lat)
    y = int(
        (1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n
    )
    return max(0, min(n - 1, x)), max(0, min(n - 1, y))


def candidate_tiles(header: dict):
    zooms = []
    for z in (
        header["center_zoom"],
        header["min_zoom"],
        min(header["max_zoom"], header["center_zoom"] + 1),
        max(header["min_zoom"], header["center_zoom"] - 1),
    ):
        if z not in zooms:
            zooms.append(z)

    for z in zooms:
        x0, y0 = lonlat_to_xyz(header["center_lon"], header["center_lat"], z)
        n = 1 << z
        for radius in (0, 1, 2):
            for dy in range(-radius, radius + 1):
                for dx in range(-radius, radius + 1):
                    if radius and abs(dx) != radius and abs(dy) != radius:
                        continue
                    x = x0 + dx
                    y = y0 + dy
                    if 0 <= x < n and 0 <= y < n:
                        yield z, x, y


def request_one(base_url: str, archive: Path, timeout: int) -> str:
    header = read_header(archive)
    version = archive.stem
    dataset = archive.parent.name
    attempts = []

    for z, x, y in candidate_tiles(header):
        url = (
            f"{base_url.rstrip('/')}/tiles/"
            f"{quote(dataset, safe='._@+-')}/"
            f"{quote(version, safe='')}/"
            f"{z}/{x}/{y}.{header['ext']}"
        )
        req = Request(url, headers={"User-Agent": "mountain_tile_backend-smoke/1"})
        try:
            with urlopen(req, timeout=timeout) as response:
                status = response.getcode()
                body = response.read(1)
            attempts.append(f"{status} {url}")
            if status == 200 and body:
                return url
        except HTTPError as exc:
            attempts.append(f"{exc.code} {url}")
        except URLError as exc:
            attempts.append(f"ERROR {url}: {exc.reason}")

    raise SmokeError(
        f"{archive.name}: could not fetch a populated tile through Nginx/PMTiles.\n"
        + "\n".join(attempts[-20:])
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-root", required=True)
    parser.add_argument("--release", required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--timeout", type=int, default=10)
    args = parser.parse_args()

    pmtiles_dir = Path(args.release_root) / args.release / "pmtiles"
    archives = sorted(pmtiles_dir.glob("*/*.pmtiles"))
    if not archives:
        print(f"no PMTiles archives found in {pmtiles_dir}", file=sys.stderr)
        return 1

    try:
        for archive in archives:
            url = request_one(args.base_url, archive, args.timeout)
            print(f"ok {archive.parent.name}/{archive.name}: {url}")
    except SmokeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
