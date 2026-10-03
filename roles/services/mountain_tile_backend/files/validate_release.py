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
from urllib.parse import unquote, urlsplit

SAFE_RELEASE = re.compile(r"^[A-Za-z0-9._-]+$")
SAFE_DATASET = re.compile(r"^[A-Za-z0-9._@+-]+$")
TILE_PATH = re.compile(
    r"^/tiles/(?P<dataset>[A-Za-z0-9._@+-]+)/"
    r"(?P<version>[A-Fa-f0-9]{12})/"
    r"\{z\}/\{x\}/\{y\}\.(?:mvt|pbf|mlt|png|jpg|webp|avif)$"
)


class ValidationError(RuntimeError):
    pass


def asset_path(url: str, release_dir: Path, release_id: str, public_base: str, *, template=False) -> Path:
    if not isinstance(url, str):
        raise ValidationError(f"asset URL must be a string: {url!r}")
    parsed, public = urlsplit(url), urlsplit(public_base)
    if (parsed.scheme or parsed.netloc) and (parsed.scheme, parsed.netloc) != (public.scheme, public.netloc):
        raise ValidationError(f"asset URL must use the configured public origin: {url}")
    prefix = f"/releases/{release_id}/"
    if not parsed.path.startswith(prefix):
        raise ValidationError(f"asset URL must use immutable {prefix} URLs: {url}")
    path = (release_dir / unquote(parsed.path[len(prefix):])).resolve()
    if not path.is_relative_to(release_dir):
        raise ValidationError(f"asset URL escapes the release: {url}")
    if not template and not path.is_file():
        raise ValidationError(f"asset URL references a missing file: {url}")
    return path


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

    if not SAFE_RELEASE.fullmatch(release_id) or release_id in (".", "..", "current"):
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

    capabilities = load_json_object(release_dir / "capabilities.json")
    manifest = load_json_object(release_dir / "manifest.json")
    if capabilities.get("release") != release_id or manifest.get("release") != release_id:
        raise ValidationError("capabilities and manifest must identify this release")
    if args.require_publishable == "true" and manifest.get("publishable") is not True:
        raise ValidationError("release is not publishable; allow only for a private rehearsal")
    for collection, key in (("styles", "url"), ("datasets", "tilejson")):
        entries = capabilities.get(collection)
        if not isinstance(entries, list) or not entries:
            raise ValidationError(f"capabilities.{collection} must be a non-empty list")
        for entry in entries:
            if not isinstance(entry, dict) or key not in entry:
                raise ValidationError(f"capabilities.{collection} contains an invalid entry")
            asset_path(entry[key], release_dir, release_id, args.public_base_url)

    style_files = sorted((release_dir / "styles").glob("*.json"))
    if not style_files:
        raise ValidationError("styles/ must contain at least one JSON style")
    for path in style_files:
        style = load_json_object(path)
        for source in style.get("sources", {}).values():
            if "url" in source:
                asset_path(source["url"], release_dir, release_id, args.public_base_url)
        if "glyphs" in style:
            asset_path(style["glyphs"], release_dir, release_id, args.public_base_url, template=True)

    pmtiles_files = sorted((release_dir / "pmtiles").glob("*/*.pmtiles"))
    if not pmtiles_files:
        raise ValidationError("pmtiles/ must contain at least one .pmtiles archive")

    pmtiles_by_id = {}
    for path in pmtiles_files:
        dataset = path.parent.name
        if not SAFE_DATASET.fullmatch(dataset) or not re.fullmatch(r"[A-Fa-f0-9]{12}", path.stem):
            raise ValidationError(
                f"PMTiles archive must be pmtiles/<dataset>/<12-hex-version>.pmtiles: {path}"
            )
        pmtiles_by_id[(dataset, path.stem)] = path

    tilejson_files = sorted((release_dir / "tilejson").glob("*.json"))
    if not tilejson_files:
        raise ValidationError("tilejson/ must contain at least one TileJSON document")

    referenced_archives: set[tuple[str, str]] = set()
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
                    f"/tiles/<dataset>/<12-hex-version>/{{z}}/{{x}}/{{y}}.<ext>: {tile_url}"
                )
            archive_id = (match.group("dataset"), match.group("version"))
            referenced_archives.add(archive_id)
            if archive_id not in pmtiles_by_id:
                raise ValidationError(
                    f"{path}: tile URL references {archive_id[0]}/{archive_id[1]}, but "
                    f"pmtiles/{archive_id[0]}/{archive_id[1]}.pmtiles does not exist"
                )

    unreferenced = sorted(set(pmtiles_by_id) - referenced_archives)
    if unreferenced:
        raise ValidationError(
            "PMTiles archives without any TileJSON tile URL: "
            + ", ".join(f"{dataset}/{version}" for dataset, version in unreferenced)
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-dir", required=True)
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--public-base-url", required=True)
    parser.add_argument("--require-publishable", choices=("true", "false"), default="true")
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
