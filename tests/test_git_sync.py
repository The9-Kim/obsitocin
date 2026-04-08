import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from obsitocin.git_sync import (
    SyncResult,
    SyncStatus,
    get_hostname,
    is_git_repo,
    has_remote,
    _is_generated_file,
    git_stage_vault,
    git_commit,
)


class TestGitPrimitives(unittest.TestCase):
    def test_is_git_repo_true(self):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init", tmp], capture_output=True, check=True)
            self.assertTrue(is_git_repo(Path(tmp)))

    def test_is_git_repo_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(is_git_repo(Path(tmp)))

    def test_has_remote_false_no_remote(self):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init", tmp], capture_output=True, check=True)
            self.assertFalse(has_remote(Path(tmp)))

    def test_get_hostname_returns_string(self):
        hostname = get_hostname()
        self.assertIsInstance(hostname, str)
        self.assertGreater(len(hostname), 0)
        self.assertLessEqual(len(hostname), 50)

    def test_hostname_no_special_chars(self):
        hostname = get_hostname()
        import re
        self.assertRegex(hostname, r'^[a-zA-Z0-9._-]+$')


class TestGeneratedFileDetection(unittest.TestCase):
    def test_moc_is_generated(self):
        self.assertTrue(_is_generated_file("obsitocin/_MOC.md"))

    def test_index_is_generated(self):
        self.assertTrue(_is_generated_file("obsitocin/projects/test/_index.md"))

    def test_topic_not_generated(self):
        self.assertFalse(_is_generated_file("obsitocin/projects/test/topics/Docker.md"))


class TestLocalSync(unittest.TestCase):
    def test_sync_local_only_with_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            subprocess.run(["git", "init", str(vault)], capture_output=True, check=True)
            subprocess.run(["git", "-C", str(vault), "config", "user.email", "test@test.com"], capture_output=True)
            subprocess.run(["git", "-C", str(vault), "config", "user.name", "Test"], capture_output=True)
            # Create initial commit
            (vault / ".gitkeep").write_text("")
            subprocess.run(["git", "-C", str(vault), "add", "."], capture_output=True)
            subprocess.run(["git", "-C", str(vault), "commit", "-m", "init"], capture_output=True)

            # Create obsitocin directory with content
            obs = vault / "obsitocin" / "projects" / "test" / "topics"
            obs.mkdir(parents=True)
            (obs / "Docker.md").write_text("# Docker\n\n## 핵심 지식\n- test")

            from obsitocin.git_sync import sync
            with mock.patch("obsitocin.git_sync.VAULT_DIR", vault), \
                 mock.patch("obsitocin.git_sync.OBS_DIR", vault / "obsitocin"):
                result = sync(local_only=True)

            self.assertEqual(result.status, SyncStatus.SUCCESS)
            self.assertGreaterEqual(result.files_committed, 1)
            self.assertTrue(len(result.commit_sha) > 0)

    def test_sync_no_vault_dir(self):
        from obsitocin.git_sync import sync
        with mock.patch("obsitocin.git_sync.VAULT_DIR", None):
            result = sync()
        self.assertEqual(result.status, SyncStatus.ERROR)

    def test_sync_not_git_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            from obsitocin.git_sync import sync
            with mock.patch("obsitocin.git_sync.VAULT_DIR", Path(tmp)):
                result = sync()
            self.assertEqual(result.status, SyncStatus.NO_GIT)

    def test_sync_dry_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            subprocess.run(["git", "init", str(vault)], capture_output=True, check=True)
            subprocess.run(["git", "-C", str(vault), "config", "user.email", "test@test.com"], capture_output=True)
            subprocess.run(["git", "-C", str(vault), "config", "user.name", "Test"], capture_output=True)
            (vault / ".gitkeep").write_text("")
            subprocess.run(["git", "-C", str(vault), "add", "."], capture_output=True)
            subprocess.run(["git", "-C", str(vault), "commit", "-m", "init"], capture_output=True)

            obs = vault / "obsitocin"
            obs.mkdir()
            (obs / "test.md").write_text("test")

            from obsitocin.git_sync import sync
            with mock.patch("obsitocin.git_sync.VAULT_DIR", vault), \
                 mock.patch("obsitocin.git_sync.OBS_DIR", obs):
                result = sync(local_only=True, dry_run=True)

            self.assertIn("Dry run", result.message)
