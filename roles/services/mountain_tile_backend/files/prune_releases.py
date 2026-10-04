#!/usr/bin/env python3
"""Retention policy: keep the active release and the N most recent others, drop the rest.

Old releases and the content-addressed archives only they reference are deleted. An archive in the shared tile
store stays as long as any kept release lists it, because tile URLs carry the archive's content hash and
clients on an older release keep requesting them. Releases are ordered by the modification time of their
directory (when they were copied to the host); `current` and anything that is not a plain release directory
is never touched.

    prune_releases.py --release-root R --tile-store S --keep N [--dry-run]
"""
import argparse
import os
from pathlib import Path
import re
import shutil
import sys

RELEASE_NAME = re.compile(r"[A-Za-z0-9._-]+")


def releases(release_root):
    """Plain release directories (not the `current` link, not hidden), newest first."""
    found = [
        entry for entry in release_root.iterdir()
        if entry.is_dir() and not entry.is_symlink() and entry.name != "current"
        and not entry.name.startswith(".") and RELEASE_NAME.fullmatch(entry.name)
    ]
    return sorted(found, key=lambda entry: (entry.stat().st_mtime, entry.name), reverse=True)


def referenced(kept):
    """{(dataset, archive file name)} listed by the kept releases."""
    return {(a.parent.name, a.name) for release in kept for a in (release / "pmtiles").glob("*/*.pmtiles")}


def size(path):
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file() and not f.is_symlink())


def plan(release_root, tile_store, keep):
    """Returns (releases to keep, releases to remove, store archives to remove)."""
    if keep < 1:
        raise ValueError("--keep must be at least 1")
    ordered = releases(release_root)
    current = release_root / "current"
    active = current.resolve().name if current.is_symlink() else None
    if active is not None and active not in {r.name for r in ordered}:
        raise ValueError(f"current points to {active}, which is not a release directory below {release_root}")
    others = [r for r in ordered if r.name != active]
    kept = [r for r in ordered if r.name == active] + others[:keep]
    removed = [r for r in ordered if r not in kept]
    keep_archives = referenced(kept)
    stale = []
    if tile_store.is_dir():
        stale = [a for a in sorted(tile_store.glob("*/*.pmtiles")) if (a.parent.name, a.name) not in keep_archives]
    return kept, removed, stale


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--release-root", type=Path, required=True)
    parser.add_argument("--tile-store", type=Path, required=True)
    parser.add_argument("--keep", type=int, required=True, help="releases to keep besides the active one")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        kept, removed, stale = plan(args.release_root, args.tile_store, args.keep)
    except ValueError as error:
        print(f"refusing: {error}", file=sys.stderr)
        return 2
    freed = sum(size(r) for r in removed) + sum(a.stat().st_size for a in stale if a.stat().st_nlink == 1)
    verb = "would prune" if args.dry_run else "pruned"
    print(f"kept releases: {', '.join(r.name for r in kept) or 'none'}")
    if not removed and not stale:
        print("nothing to prune")
        return 0
    if not args.dry_run:
        for release in removed:
            shutil.rmtree(release)
        for archive in stale:
            os.unlink(archive)
        for directory in sorted(args.tile_store.glob("*")):
            if directory.is_dir() and not any(directory.iterdir()):
                directory.rmdir()
    print(f"{verb} releases: {', '.join(r.name for r in removed) or 'none'}")
    print(f"{verb} archives: {', '.join(f'{a.parent.name}/{a.name}' for a in stale) or 'none'}")
    print(f"{verb} about {freed / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
