import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / ".galaxy/collections/ansible_collections/yourorg/shared_roles/roles/mountain_tile_backend/files/prune_releases.py"


def make_release(root: Path, store: Path, name: str, archives: dict[str, str], age: int):
    """A release with archives {dataset: 12-hex version}; the store holds hard links, as sync_tile_store makes them."""
    release = root / name
    for dataset, version in archives.items():
        archive = release / "pmtiles" / dataset / f"{version}.pmtiles"
        archive.parent.mkdir(parents=True)
        archive.write_bytes(f"{dataset}-{version}".encode() * 100)
        target = store / dataset / archive.name
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            os.link(archive, target)
    os.utime(release, (1_000_000 + age, 1_000_000 + age))
    return release


def run(root, store, *extra):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--release-root", str(root), "--tile-store", str(store), *extra],
        capture_output=True, text=True, check=False,
    )


class PruneReleases(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "releases"
        self.store = Path(self.tmp.name) / "tile-store"
        self.root.mkdir()
        # r1 oldest ... r4 newest; "elevation" never changes, "slope" changes with every release except r2 -> r3
        make_release(self.root, self.store, "r1", {"elevation": "a" * 12, "slope": "1" * 12}, age=1)
        make_release(self.root, self.store, "r2", {"elevation": "a" * 12, "slope": "2" * 12}, age=2)
        make_release(self.root, self.store, "r3", {"elevation": "a" * 12, "slope": "2" * 12}, age=3)
        make_release(self.root, self.store, "r4", {"elevation": "a" * 12, "slope": "4" * 12}, age=4)

    def tearDown(self):
        self.tmp.cleanup()

    def names(self):
        return sorted(p.name for p in self.root.iterdir())

    def versions(self, dataset):
        return sorted(p.stem[:1] for p in (self.store / dataset).glob("*.pmtiles"))

    def test_keeps_the_active_release_and_the_newest_others(self):
        (self.root / "current").symlink_to("r2")
        result = run(self.root, self.store, "--keep", "1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.names(), ["current", "r2", "r4"])  # r2 is active, r4 the newest other
        self.assertEqual(self.versions("slope"), ["2", "4"])  # r1's and nothing else of r3's is gone
        self.assertEqual(self.versions("elevation"), ["a"])
        self.assertEqual((self.root / "current").resolve().name, "r2")

    def test_an_archive_shared_by_a_kept_release_stays(self):
        (self.root / "current").symlink_to("r4")
        run(self.root, self.store, "--keep", "1")  # keeps r4 and r3
        self.assertEqual(self.names(), ["current", "r3", "r4"])
        self.assertEqual(self.versions("slope"), ["2", "4"], "r3 still lists slope 2")

    def test_a_dry_run_changes_nothing(self):
        (self.root / "current").symlink_to("r4")
        before = (self.names(), self.versions("slope"))
        result = run(self.root, self.store, "--keep", "1", "--dry-run")
        self.assertEqual((self.names(), self.versions("slope")), before)
        self.assertIn("would prune releases: r2, r1", result.stdout)

    def test_nothing_to_prune(self):
        (self.root / "current").symlink_to("r4")
        result = run(self.root, self.store, "--keep", "10")
        self.assertIn("nothing to prune", result.stdout)
        self.assertEqual(self.names(), ["current", "r1", "r2", "r3", "r4"])

    def test_keep_below_one_is_refused(self):
        result = run(self.root, self.store, "--keep", "0")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.names(), ["r1", "r2", "r3", "r4"])

    def test_a_current_link_to_something_else_is_refused(self):
        (self.root / "current").symlink_to("elsewhere")
        result = run(self.root, self.store, "--keep", "1")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.names(), ["current", "r1", "r2", "r3", "r4"])

    def test_hidden_and_oddly_named_entries_are_left_alone(self):
        (self.root / ".current.next").symlink_to("r4")
        (self.root / "not a release").mkdir()
        (self.root / "current").symlink_to("r4")
        run(self.root, self.store, "--keep", "1")
        self.assertIn(".current.next", self.names())
        self.assertIn("not a release", self.names())


if __name__ == "__main__":
    unittest.main()
