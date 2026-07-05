"""BYOK: per-request X-Provider-Key overrides the central env key.

Runs offline — monkeypatches providers._retrying_post to capture the
Authorization/x-goog-api-key header instead of making a network call.
    python3 -m unittest test_provider_key
"""
import base64
import os
import unittest

import providers


class _FakeResp:
    """Minimal stand-in returning one 1x1 PNG so generate_* succeeds."""
    _PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n").decode()

    def __init__(self, kind):
        self._kind = kind

    def json(self):
        if self._kind == "openrouter":
            return {"choices": [{"message": {"images": [
                {"image_url": {"url": f"data:image/png;base64,{self._PNG}"}}]}}]}
        return {"candidates": [{"content": {"parts": [
            {"inlineData": {"data": self._PNG}}]}}]}


class ProviderKeyTest(unittest.TestCase):
    def setUp(self):
        self.captured = {}
        self._orig = providers._retrying_post

        def fake_post(url, *, headers, json_payload, timeout, attempts=3):
            self.captured["headers"] = headers
            self.captured["url"] = url
            kind = "gemini" if "generativelanguage" in url else "openrouter"
            return _FakeResp(kind)

        providers._retrying_post = fake_post

    def tearDown(self):
        providers._retrying_post = self._orig

    def test_openrouter_prefers_provider_key(self):
        os.environ["OPENROUTER_API_KEY"] = "central-key"
        providers.generate_openrouter("p", b"img", "image/jpeg", "16:9", "1K",
                                      provider_key="byok-key")
        self.assertEqual(self.captured["headers"]["Authorization"], "Bearer byok-key")

    def test_openrouter_falls_back_to_env(self):
        os.environ["OPENROUTER_API_KEY"] = "central-key"
        providers.generate_openrouter("p", b"img", "image/jpeg", "16:9", "1K")
        self.assertEqual(self.captured["headers"]["Authorization"], "Bearer central-key")

    def test_gemini_prefers_provider_key(self):
        os.environ["GEMINI_API_KEY"] = "central-gem"
        providers.generate_gemini("p", b"img", "image/jpeg", "16:9", "1K",
                                  provider_key="byok-gem")
        self.assertEqual(self.captured["headers"]["x-goog-api-key"], "byok-gem")

    def test_missing_key_raises(self):
        os.environ.pop("OPENROUTER_API_KEY", None)
        with self.assertRaises(providers.ProviderError):
            providers.generate_openrouter("p", b"img", "image/jpeg", "16:9", "1K")


if __name__ == "__main__":
    unittest.main()
