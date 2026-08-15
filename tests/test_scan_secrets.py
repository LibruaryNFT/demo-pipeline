"""The committed-credential guard.

The scanner's whole value is its failure path, so that is what is tested.
A scanner that silently stops matching reports "clean" forever, which is
worse than having none at all.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import scan_secrets  # noqa: E402


class TestSelftest:
    def test_all_patterns_match_planted_samples(self):
        assert scan_secrets.selftest() == 0

    def test_selftest_is_reachable_from_main(self):
        assert scan_secrets.main(["--selftest"]) == 0


class TestDetection:
    @pytest.mark.parametrize(
        "sample",
        [
            "OPENAI_API_KEY=sk-proj-" + "B" * 24,
            "token = 'ghp_" + "B" * 36 + "'",
            "pat: github_pat_" + "B" * 22,
            "aws_key = AKIAIOSFODNN7EXAMPLE",
            "google = AIza" + "B" * 35,
            "slack = xoxb-" + "2" * 12,
            "-----BEGIN OPENSSH PRIVATE KEY-----",
            "jwt=eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.sig",
            "flow_key = " + "b" * 64,
        ],
    )
    def test_credential_shapes_are_caught(self, sample):
        assert scan_secrets.scan_text(sample), f"missed: {sample[:30]}"

    @pytest.mark.parametrize(
        "sample",
        [
            "OPENAI_API_KEY=",
            "export OPENAI_API_KEY=...",
            "api_key = os.getenv('OPENAI_API_KEY')",
            "a normal sentence about tokens and secrets",
            "sk-short",
            "test-key-not-real",
        ],
    )
    def test_ordinary_text_is_not_flagged(self, sample):
        # False positives train people to ignore the scanner.
        assert not scan_secrets.scan_text(sample), f"false positive: {sample}"


class TestOutputSafety:
    def test_matches_are_truncated_not_echoed_whole(self):
        # The finding goes into CI logs, so it must not reprint the secret.
        secret = "sk-proj-" + "C" * 40
        (_, excerpt), = scan_secrets.scan_text(secret)
        assert excerpt.endswith("...")
        assert len(excerpt) < len(secret)
        assert secret not in excerpt


class TestRepoIsClean:
    def test_tracked_files_have_no_secrets(self):
        assert scan_secrets.main([]) == 0

    def test_full_history_has_no_secrets(self):
        assert scan_secrets.main(["--history"]) == 0


class TestBinaryHandling:
    """Binary blobs must be skipped, not decoded and scanned.

    Regression: committing PNG screenshots turned the scan red. Compressed
    bytes are effectively random, so an image eventually contains 64
    characters matching the hex-secret pattern. CI caught it; a local run
    had passed only because the images were not committed yet.
    """

    def test_nul_bytes_mark_content_as_binary(self):
        assert scan_secrets._as_text(b"\x89PNG\r\n\x1a\n\x00\x00binary") is None

    def test_invalid_utf8_is_treated_as_binary(self):
        assert scan_secrets._as_text(b"\xff\xfe\xfd\xfc") is None

    def test_plain_text_still_decodes(self):
        assert scan_secrets._as_text(b"hello = 1\n") == "hello = 1\n"

    def test_a_real_committed_png_is_skipped(self):
        png = Path(__file__).resolve().parents[1] / "docs/assets/01-intro.png"
        assert png.exists(), "example still is missing"
        assert scan_secrets._as_text(png.read_bytes()) is None

    def test_hex_run_inside_binary_would_otherwise_have_matched(self):
        # Proves the skip is load-bearing rather than incidental.
        payload = b"\x00" + (b"a" * 64)
        assert scan_secrets.scan_text(payload.decode("latin-1"))
        assert scan_secrets._as_text(payload) is None
