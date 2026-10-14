"""Settings loading and its error messages."""
import pytest

from decisionlm import settings


def write(tmp_path, text):
    p = tmp_path / "s.toml"
    p.write_text(text)
    return p


def test_defaults_load():
    s = settings.load(environ={})
    assert s.n_ctx == 8192 and s.temperature > 1 and s.threads == 1


def test_environment_overrides_selected_settings():
    s = settings.load(environ={"PORT": "7000", "DECIDE_THETA": "0.9", "MAX_THINK_TOKENS": "100"})
    assert (s.port, s.theta, s.max_think_tokens) == (7000, 0.9, 100)


@pytest.mark.parametrize("env", [{"PORT": "x"}, {"DECIDE_THETA": "high"}])
def test_bad_environment_value_names_the_variable(env):
    with pytest.raises(settings.SettingsError, match=next(iter(env))):
        settings.load(environ=env)


def test_missing_file_and_invalid_toml_are_reported(tmp_path):
    with pytest.raises(settings.SettingsError, match="not found"):
        settings.load(tmp_path / "nope.toml", environ={})
    with pytest.raises(settings.SettingsError, match="TOML"):
        settings.load(write(tmp_path, "[model"), environ={})


def test_missing_setting_is_named(tmp_path):
    with pytest.raises(settings.SettingsError, match=r"\[model\] path"):
        settings.load(write(tmp_path, "[model]\nlabel='x'\n"), environ={})


def test_use_and_get_round_trip():
    s = settings.load(environ={"PORT": "1234"})
    settings.use(s)
    assert settings.get() is s
