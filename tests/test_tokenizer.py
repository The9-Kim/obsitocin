import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from obsitocin.tokenizer import UnicodeTokenizer, get_tokenizer


def _kiwi_available() -> bool:
    try:
        import kiwipiepy  # noqa: F401
        return True
    except ImportError:
        return False


class TestUnicodeTokenizer(unittest.TestCase):
    def test_korean_text_tokenized(self):
        t = UnicodeTokenizer()
        tokens = t.tokenize("Docker 컨테이너를 빌드한다")
        self.assertIn("docker", tokens)
        # Korean words should be present (exact form depends on regex)
        korean_found = any("컨테이너" in tok for tok in tokens)
        self.assertTrue(korean_found)

    def test_single_char_dropped(self):
        t = UnicodeTokenizer()
        tokens = t.tokenize("a b cd ef")
        self.assertNotIn("a", tokens)
        self.assertNotIn("b", tokens)
        self.assertIn("cd", tokens)
        self.assertIn("ef", tokens)

    def test_empty_input(self):
        t = UnicodeTokenizer()
        self.assertEqual(t.tokenize(""), [])

    def test_name_property(self):
        t = UnicodeTokenizer()
        self.assertEqual(t.name, "unicode")

    def test_mixed_language(self):
        # "Python으로 REST API 개발" → ['python으로', 'rest', 'api', '개발']
        t = UnicodeTokenizer()
        tokens = t.tokenize("Python으로 REST API 개발")
        self.assertIn("python으로", tokens)
        self.assertIn("rest", tokens)
        self.assertIn("api", tokens)
        self.assertIn("개발", tokens)


class TestKiwiTokenizer(unittest.TestCase):
    @unittest.skipUnless(_kiwi_available(), "kiwipiepy not installed")
    def test_morpheme_extraction(self):
        from obsitocin.tokenizer import KiwiTokenizer
        t = KiwiTokenizer()
        tokens = t.tokenize("Docker 컨테이너를 빌드한다")
        self.assertIn("컨테이너", tokens)

    @unittest.skipUnless(_kiwi_available(), "kiwipiepy not installed")
    def test_particles_dropped(self):
        from obsitocin.tokenizer import KiwiTokenizer
        t = KiwiTokenizer()
        tokens = t.tokenize("컨테이너를 이미지가 빌드된다")
        # Particles like 를, 가 should not appear
        self.assertNotIn("를", tokens)
        self.assertNotIn("가", tokens)

    @unittest.skipUnless(_kiwi_available(), "kiwipiepy not installed")
    def test_name_property(self):
        from obsitocin.tokenizer import KiwiTokenizer
        t = KiwiTokenizer()
        self.assertEqual(t.name, "kiwi")


class TestGetTokenizer(unittest.TestCase):
    def test_default_is_unicode(self):
        t = get_tokenizer()
        self.assertEqual(t.name, "unicode")

    def test_explicit_unicode(self):
        t = get_tokenizer("unicode")
        self.assertEqual(t.name, "unicode")

    def test_env_var_selects_tokenizer(self):
        with mock.patch.dict(os.environ, {"OBS_TOKENIZER": "unicode"}):
            t = get_tokenizer()
            self.assertEqual(t.name, "unicode")

    def test_kiwi_fallback_when_not_installed(self):
        """Requesting kiwi without kiwipiepy should fallback gracefully."""
        with mock.patch("obsitocin.tokenizer.KiwiTokenizer", side_effect=ImportError):
            import warnings
            with warnings.catch_warnings(record=True):
                warnings.simplefilter("always")
                t = get_tokenizer("kiwi")
                self.assertEqual(t.name, "unicode")


if __name__ == "__main__":
    unittest.main()
