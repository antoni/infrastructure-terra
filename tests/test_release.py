import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / ".galaxy/collections/ansible_collections/yourorg/shared_roles/roles/mountain_tile_backend/files"))
from sync_tile_store import sync
from validate_release import ValidationError, validate


class DeploymentContractTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.release = self.root / "releases/test-a"
        for directory in ("styles", "tilejson", "metadata", "pmtiles/synthetic"):
            (self.release / directory).mkdir(parents=True, exist_ok=True)
        self.version = hashlib.sha256(b"synthetic archive").hexdigest()[:12]
        self.archive = self.release / f"pmtiles/synthetic/{self.version}.pmtiles"
        self.archive.write_bytes(b"synthetic archive")
        self.write("manifest.json", {"release": "test-a", "publishable": True})
        self.write("capabilities.json", {
            "release": "test-a", "styles": [{"url": "/releases/test-a/styles/test.json"}],
            "datasets": [{"tilejson": "/releases/test-a/tilejson/synthetic.json"}],
        })
        self.write("styles/test.json", {"sources": {"synthetic": {
            "url": "/releases/test-a/tilejson/synthetic.json"}}})
        self.write("tilejson/synthetic.json", {"tiles": [
            f"/tiles/synthetic/{self.version}/{{z}}/{{x}}/{{y}}.png"]})
        self.args = argparse.Namespace(release_dir=str(self.release), release_id="test-a",
                                       public_base_url="https://tiles.example", require_publishable="true")

    def write(self, name, value):
        (self.release / name).write_text(json.dumps(value))

    def test_accepts_content_versioned_release(self):
        validate(self.args)

    def test_rejects_unpublishable_unless_private_rehearsal_selected(self):
        self.write("manifest.json", {"release": "test-a", "publishable": False})
        with self.assertRaisesRegex(ValidationError, "not publishable"):
            validate(self.args)
        self.args.require_publishable = "false"
        validate(self.args)

    def test_rejects_development_asset_prefix(self):
        self.write("styles/test.json", {"sources": {"synthetic": {
            "url": "/release/tilejson/synthetic.json"}}})
        with self.assertRaisesRegex(ValidationError, "immutable"):
            validate(self.args)

    def test_rejects_wrong_tile_origin(self):
        self.write("tilejson/synthetic.json", {"tiles": [
            f"https://other.example/tiles/synthetic/{self.version}/{{z}}/{{x}}/{{y}}.png"]})
        with self.assertRaisesRegex(ValidationError, "configured public origin"):
            validate(self.args)

    def test_rejects_missing_content_version(self):
        self.write("tilejson/synthetic.json", {"tiles": [
            "/tiles/synthetic/000000000000/{z}/{x}/{y}.png"]})
        with self.assertRaisesRegex(ValidationError, "does not exist"):
            validate(self.args)

    def test_retains_archive_after_release_removal_and_repeat_is_unchanged(self):
        store = self.root / "tile-store"
        self.assertEqual(sync(self.release, store), 1)
        self.assertEqual(sync(self.release, store), 0)
        self.archive.unlink()
        self.assertEqual((store / f"synthetic/{self.version}.pmtiles").read_bytes(), b"synthetic archive")

    def test_refuses_archive_named_for_different_content(self):
        self.archive.write_bytes(b"changed content")
        with self.assertRaisesRegex(ValueError, "not named for its content"):
            sync(self.release, self.root / "tile-store")

    def test_refuses_overwrite_of_existing_immutable_archive(self):
        store = self.root / "tile-store"
        target = store / f"synthetic/{self.version}.pmtiles"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"conflicting bytes")
        with self.assertRaisesRegex(ValueError, "collision"):
            sync(self.release, store)
        self.assertEqual(target.read_bytes(), b"conflicting bytes")


if __name__ == "__main__":
    unittest.main()
