"""Config loading, validation and its error messages. No Redis needed."""
import os
import re

import pytest
from helpers import ROOT, config_text

from pipeline import config

BASE = config_text("cfgtest", 6380, 5099)


def load(tmp_path, text, environ=None):
    p = tmp_path / "c.toml"
    p.write_text(text)
    return config.load(str(p), environ=environ or {})


def test_the_shipped_configs_load():
    for path in ("config/pipeline.toml", "config/examples/support_tickets.toml", "demo/pipeline.demo.toml"):
        assert len(config.load(os.path.join(ROOT, path), environ={}).options) >= 2


def test_derived_values():
    cfg = config.load(os.path.join(ROOT, "config", "pipeline.toml"), environ={})
    assert cfg.health_url.endswith("/api/health") and cfg.reclaim_idle_ms == int(cfg.reclaim_idle_s * 1000)
    assert cfg.decision_streams[-1] == cfg.review_stream
    assert cfg.group_for(cfg.ingestion) == cfg.ingestion_group and cfg.group_for("x") == "x-workers"


def test_environment_overrides_the_redis_address(tmp_path):
    cfg = load(tmp_path, BASE, {"REDIS_HOST": "redis.example", "REDIS_PORT": "7000"})
    assert (cfg.host, cfg.port) == ("redis.example", 7000)


@pytest.mark.parametrize("old,new,message", [
    ('question = "Which one?"', "", "[decision] question"),
    ("theta = 0.85", "theta = 1.5", "theta"),
    ("theta = 0.85", 'theta = "high"', "theta"),
    ('name = "beta"', 'name = "alpha"', "unique"),
    ('stream = "cfgtest:beta"', 'stream = "cfgtest:alpha"', "unique"),
    ('stream = "cfgtest:gamma"', 'stream = "cfgtest:in"', "differ"),
    ("reclaim_idle_s = 60", "reclaim_idle_s = 3", "reclaim_idle_s"),
    ('url = "http', 'url = "ftp', "http(s)"),
    ("[redis]", "[redis", "TOML"),
    ("port = 6380", 'port = "abc"', "port"),
    ('name = "gamma"\n', "", "text `name`"),
    ("batch_size = 1", "batch_size = 0", "batch_size"),
    ('context_field = "payload"', 'context_field = " "', "context_field"),
])
def test_bad_config_is_rejected_with_a_message_naming_the_problem(tmp_path, old, new, message):
    assert old in BASE
    with pytest.raises(config.ConfigError, match=re.escape(message)):
        load(tmp_path, BASE.replace(old, new, 1))


def test_fewer_than_two_options_is_rejected(tmp_path):
    one = BASE.split("[[options]]")[0] + '[[options]]\nname = "alpha"\nstream = "cfgtest:alpha"\n'
    with pytest.raises(config.ConfigError, match="2 and 26"):
        load(tmp_path, one)


def test_bad_redis_port_in_the_environment_is_named(tmp_path):
    with pytest.raises(config.ConfigError, match="REDIS_PORT"):
        load(tmp_path, BASE, {"REDIS_PORT": "abc"})


def test_missing_file_is_reported():
    with pytest.raises(config.ConfigError, match="not found"):
        config.load("nope.toml", environ={})


MINIMAL = """[decision]
question = "Which one?"
[[options]]
name = "alpha"
[[options]]
name = "beta"
"""


def test_a_minimal_config_needs_only_a_question_and_options(tmp_path):
    cfg = load(tmp_path, MINIMAL)
    assert (cfg.ingestion, cfg.dlq, cfg.port, cfg.deciders, cfg.workers) == ("ingestion", "ingestion:dlq", 6379, 1, False)
    assert cfg.option_streams == {"alpha": "decision:alpha", "beta": "decision:beta"} and cfg.review_stream == "decision:review"
    assert cfg.service_start and not cfg.has_placeholders


def test_the_shipped_template_is_flagged_as_unfinished():
    assert config.load(os.path.join(ROOT, "config", "pipeline.toml"), environ={}).has_placeholders


def test_a_relative_service_dir_is_relative_to_the_config_file(tmp_path):
    cfg = load(tmp_path, MINIMAL + '[service]\ndir = "svc"\n')
    assert cfg.service_dir == str(tmp_path / "svc")


def test_redis_password_comes_from_the_environment(tmp_path):
    assert load(tmp_path, MINIMAL, {"REDIS_PASSWORD": "s3cret"}).password == "s3cret"


def test_run_settings_are_validated(tmp_path):
    with pytest.raises(config.ConfigError, match="deciders"):
        load(tmp_path, MINIMAL + "[run]\ndeciders = 0\n")
    with pytest.raises(config.ConfigError, match="workers"):
        load(tmp_path, MINIMAL + '[run]\nworkers = "yes"\n')
