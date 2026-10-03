#!/usr/bin/env python3
"""Retain content-addressed archives independently of the current release.

Hard links avoid duplicating archive bytes on the same filesystem. Across
filesystems, copy once. Never overwrite an existing immutable version.
"""
import argparse
import errno
import hashlib
import os
from pathlib import Path
import re
import shutil
import tempfile


def digest(path):
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(chunk)
    return checksum.hexdigest()


def sync(release_dir, tile_store):
    changed = 0
    for archive in sorted((release_dir / "pmtiles").glob("*/*.pmtiles")):
        dataset, version = archive.parent.name, archive.stem
        if not re.fullmatch(r"[A-Za-z0-9._@+-]+", dataset) or not re.fullmatch(r"[a-f0-9]{12}", version):
            raise ValueError(f"invalid archive path: {archive}")
        checksum = digest(archive)
        if not checksum.startswith(version):
            raise ValueError(f"archive is not named for its content: {archive}")
        target = tile_store / dataset / archive.name
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if digest(target) != checksum:
                raise ValueError(f"immutable archive collision: {target}")
            continue
        with tempfile.TemporaryDirectory(dir=target.parent) as staging:
            staged = Path(staging) / archive.name
            try:
                os.link(archive, staged)
            except OSError as exc:
                if exc.errno != errno.EXDEV:
                    raise
                shutil.copyfile(archive, staged)
            staged.chmod(0o644)
            os.replace(staged, target)
        changed += 1
    return changed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-dir", type=Path, required=True)
    parser.add_argument("--tile-store", type=Path, required=True)
    args = parser.parse_args()
    count = sync(args.release_dir, args.tile_store)
    print(f"indexed {count} archives" if count else "tile store unchanged")


if __name__ == "__main__":
    main()
