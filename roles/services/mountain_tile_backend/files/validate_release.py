#!/usr/bin/env python3
"""Deployment-contract validation for a Mountain Map immutable release.

This is deliberately narrower than terrain-platform's full data validator.
It checks the filesystem/API-serving contract that must be true before the
infrastructure role flips the `current` symlink.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

SAFE_RELEASE = re.compile(r"^[A-Za-z0-9._-]+$")
SAFE_DATASET = re.compile(r"^[A-Za-z0-9._@+-]+$")
TILE_PATH = re.compile(
    r"^/tiles/(?P<release>[A-Za-z0-9._-]+)/"
    r"(?P<dataset>[A-Za-z0-9._@+-]+)/"
    r"\{z\}/\{x\}/\{y\}\.(?:mvt|pbf|mlt|png|jpg|webp|avif)$"
)


class ValidationError(RuntimeError):
    pass


def load_json_object(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f"{path}: invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValidationError(f"{path}: top-level JSON value must be an object")
    return value


def validate(args: argparse.Namespace) -> None:
    release_dir = Path(args.release_dir).resolve()
    release_id = args.release_id

    if not SAFE_RELEASE.fullmatch(release_id):
        raise ValidationError(f"unsafe release id: {release_id!r}")
    if release_dir.name != release_id:
        raise ValidationError(
            f"release directory basename {release_dir.name!r} does not match {release_id!r}"
        )
    if not release_dir.is_dir():
        raise ValidationError(f"release directory does not exist: {release_dir}")

    required_files = ["capabilities.json", "manifest.json"]
    required_dirs = ["styles", "tilejson", "pmtiles", "metadata"]

    for name in required_files:
        path = release_dir / name
        if not path.is_file():
            raise ValidationError(f"missing required release file: {path}")

    for name in required_dirs:
        path = release_dir / name
        if not path.is_dir():
            raise ValidationError(f"missing required release directory: {path}")

    load_json_object(release_dir / "capabilities.json")
    load_json_object(release_dir / "manifest.json")

    style_files = sorted((release_dir / "styles").glob("*.json"))
    if not style_files:
        raise ValidationError("styles/ must contain at least one JSON style")
    for path in style_files:
        load_json_object(path)

    pmtiles_files = sorted((release_dir / "pmtiles").glob("*.pmtiles"))
    if not pmtiles_files:
        raise ValidationError("pmtiles/ must contain at least one .pmtiles archive")

    pmtiles_by_stem = {}
    for path in pmtiles_files:
        if not SAFE_DATASET.fullmatch(path.stem):
            raise ValidationError(
                f"PMTiles archive name must be URL-safe and stable: {path.name}"
            )
        pmtiles_by_stem[path.stem] = path

    tilejson_files = sorted((release_dir / "tilejson").glob("*.json"))
    if not tilejson_files:
        raise ValidationError("tilejson/ must contain at least one TileJSON document")

    referenced_archives: set[str] = set()
    public = urlsplit(args.public_base_url)
    for path in tilejson_files:
        doc = load_json_object(path)
        tiles = doc.get("tiles")
        if not isinstance(tiles, list) or not tiles or not all(
            isinstance(item, str) and item for item in tiles
        ):
            raise ValidationError(f"{path}: TileJSON must contain a non-empty tiles[]")

        for tile_url in tiles:
            parsed = urlsplit(tile_url)
            if parsed.scheme or parsed.netloc:
                if parsed.scheme != public.scheme or parsed.netloc != public.netloc:
                    raise ValidationError(
                        f"{path}: tile URL must use the configured public origin: {tile_url}"
                    )

            match = TILE_PATH.fullmatch(parsed.path)
            if not match:
                raise ValidationError(
                    f"{path}: tile URL does not match "
                    f"/tiles/<release>/<dataset>/{{z}}/{{x}}/{{y}}.<ext>: {tile_url}"
                )
            if match.group("release") != release_id:
                raise ValidationError(
                    f"{path}: tile URL is not versioned with active release {release_id}: "
                    f"{tile_url}"
                )

            dataset = match.group("dataset")
            referenced_archives.add(dataset)
            if dataset not in pmtiles_by_stem:
                raise ValidationError(
                    f"{path}: tile URL references dataset {dataset!r}, but "
                    f"pmtiles/{dataset}.pmtiles does not exist"
                )

    unreferenced = sorted(set(pmtiles_by_stem) - referenced_archives)
    if unreferenced:
        raise ValidationError(
            "PMTiles archives without any TileJSON tile URL: " + ", ".join(unreferenced)
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-dir", required=True)
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--public-base-url", required=True)
    args = parser.parse_args()

    try:
        validate(args)
    except ValidationError as exc:
        print(f"release validation failed: {exc}", file=sys.stderr)
        return 1

    print(f"release validation passed: {args.release_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
