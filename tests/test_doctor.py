"""The environment diagnostic.

A doctor that reports green when a dependency is missing is worse than no
doctor, so these tests focus on the failure paths.
"""

from demotape import doctor


class TestReport:
    def test_starts_clean(self):
        assert not doctor.Report().failed

    def test_a_warn_is_not_a_failure(self):
        r = doctor.Report()
        r.add(doctor.WARN, "thing", "heads up")
        assert not r.failed

    def test_a_fail_is_a_failure(self):
        r = doctor.Report()
        r.add(doctor.OK, "fine", "")
        r.add(doctor.FAIL, "broken", "")
        assert r.failed


class TestFfmpegCheck:
    def test_missing_tools_fail(self, monkeypatch):
        monkeypatch.setattr(doctor, "which", lambda _: None)
        r = doctor.Report()
        doctor.check_ffmpeg(r)
        names = [n for s, n, _ in r.rows if s == doctor.FAIL]
        assert "ffmpeg" in names and "ffprobe" in names

    def test_present_tools_pass(self, monkeypatch):
        monkeypatch.setattr(doctor, "which", lambda n: f"/usr/bin/{n}")
        monkeypatch.setattr(doctor, "_version", lambda _: "v1")
        r = doctor.Report()
        doctor.check_ffmpeg(r)
        assert not r.failed


class TestApiKeyCheck:
    def test_exported_key_passes(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "test-not-real")
        r = doctor.Report()
        doctor.check_openai_key(r)
        assert r.rows[0][0] == doctor.OK

    def test_no_key_and_no_dotenv_warns_rather_than_fails(self, monkeypatch, tmp_path):
        """OpenAI is the last of four narration sources. A project whose
        scenes all carry their own audio never reads the key, so failing on
        it tells a correctly-configured demo it is broken.

        Found by running the diagnostic inside the Docker image, which has
        no key and no .env and never will."""
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.chdir(tmp_path)
        r = doctor.Report()
        doctor.check_openai_key(r)
        assert r.rows[0][0] == doctor.WARN
        assert not r.failed

    def test_the_warning_names_the_alternatives(self, monkeypatch, tmp_path):
        """A bare "not set" sends people hunting for a key they may not
        need. The row has to say what to do instead."""
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.chdir(tmp_path)
        r = doctor.Report()
        doctor.check_openai_key(r)
        detail = r.rows[0][2]
        assert "audio" in detail and "tts" in detail

    def test_dotenv_present_warns_rather_than_fails(self, monkeypatch, tmp_path):
        # The key may well load at runtime, so this is not a hard failure.
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        (tmp_path / ".env").write_text("OPENAI_API_KEY=test-not-real\n")
        monkeypatch.chdir(tmp_path)
        r = doctor.Report()
        doctor.check_openai_key(r)
        assert r.rows[0][0] == doctor.WARN
        assert not r.failed


class TestFontCheck:
    def test_missing_font_fails(self, monkeypatch):
        monkeypatch.setattr(doctor, "default_font", lambda: "/no/such/font.ttf")
        monkeypatch.setattr(doctor.sys, "platform", "linux")
        r = doctor.Report()
        doctor.check_font(r)
        # A missing font renders blank title cards rather than erroring, so
        # it has to be caught here or not at all.
        assert r.failed


class TestExitCode:
    def test_returns_nonzero_when_anything_failed(self, monkeypatch, tmp_path):
        monkeypatch.setattr(doctor, "which", lambda _: None)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(doctor, "check_playwright", lambda r: None)
        assert doctor.main([]) == 1

    def test_returns_zero_when_all_clear(self, monkeypatch):
        monkeypatch.setattr(doctor, "which", lambda n: f"/usr/bin/{n}")
        monkeypatch.setattr(doctor, "_version", lambda _: "v1")
        monkeypatch.setenv("OPENAI_API_KEY", "test-not-real")
        monkeypatch.setattr(doctor, "check_playwright", lambda r: None)
        monkeypatch.setattr(doctor, "check_font", lambda r: None)
        assert doctor.main([]) == 0
