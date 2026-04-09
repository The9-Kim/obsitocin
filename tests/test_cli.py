import subprocess
import sys
import unittest


class TestServeCli(unittest.TestCase):
    def _run(self, *args, **kwargs):
        return subprocess.run(
            [sys.executable, "-m", "obsitocin"] + list(args),
            capture_output=True,
            text=True,
        )

    def test_serve_help_exits_zero(self):
        result = self._run("serve", "--help")
        self.assertEqual(result.returncode, 0)
        # serve --help should show usage and options
        self.assertIn("usage:", result.stdout)

    def test_serve_without_fastmcp_exits_one(self):
        # fastmcp may or may not be installed — test the graceful error path
        # If fastmcp IS installed, this test is skipped
        try:
            import fastmcp  # noqa: F401

            self.skipTest("fastmcp is installed, cannot test missing-dep error")
        except ImportError:
            pass
        result = self._run("serve")
        self.assertEqual(result.returncode, 1)
        self.assertIn("obsitocin[mcp]", result.stderr)

    def test_existing_commands_unaffected(self):
        result = self._run("status")
        # status should work (may show missing vault config but shouldn't crash)
        # Just verify it doesn't exit with code 2 (argument parsing error)
        self.assertNotEqual(result.returncode, 2)

    def test_serve_in_help_output(self):
        result = self._run("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("serve", result.stdout)


if __name__ == "__main__":
    unittest.main()
