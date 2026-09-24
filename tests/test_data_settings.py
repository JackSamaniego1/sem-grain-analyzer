"""App settings: defaults, roundtrip, recent-sessions cap."""
from data.models import AppSettings
from data.settings import add_recent_session, get_settings_path, load_settings, save_settings


def test_defaults_when_missing(tmp_path):
    p = tmp_path / "settings.json"
    s = load_settings(p)
    assert s.theme == "system"
    assert s.recent_sessions == []


def test_save_and_load_roundtrip(tmp_path):
    p = tmp_path / "settings.json"
    s = AppSettings(operator="jack", theme="dark", last_export_dir="C:/exports")
    save_settings(s, p)
    loaded = load_settings(p)
    assert loaded.operator == "jack"
    assert loaded.theme == "dark"
    assert loaded.last_export_dir == "C:/exports"


def test_corrupt_settings_file_falls_back_to_defaults(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text("not json{{{", encoding="utf-8")
    s = load_settings(p)
    assert s.theme == "system"


def test_recent_sessions_dedupe_and_cap(tmp_path):
    p = tmp_path / "settings.json"
    s = AppSettings()
    for i in range(15):
        add_recent_session(s, f"session-{i}", p)
    assert len(s.recent_sessions) == 10
    assert s.recent_sessions[0] == "session-14"

    add_recent_session(s, "session-14", p)  # re-add moves to front, no dup
    assert s.recent_sessions[0] == "session-14"
    assert s.recent_sessions.count("session-14") == 1


def test_get_settings_path_under_localappdata(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    p = get_settings_path()
    assert str(tmp_path) in str(p)
    assert p.name == "settings.json"
    assert "GrainAnalyzer" in str(p)
