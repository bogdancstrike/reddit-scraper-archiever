import textwrap

from reddit_archiver.config import DateRangeConfig, load_config


def test_env_expansion_and_output_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("OUTPUT_DIR", "/data/output")
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        textwrap.dedent(
            """
            subreddits: [python]
            backends: [arctic_shift]
            output:
              dir: "${OUTPUT_DIR}"
            """
        )
    )
    config = load_config(cfg_file)
    assert config.subreddits == ["python"]
    assert str(config.output.resolved_dir()) == "/data/output"


def test_output_dir_defaults_when_unset():
    from reddit_archiver.config import OutputConfig

    assert str(OutputConfig(dir="").resolved_dir()) == "data/output"
    assert str(OutputConfig().resolved_dir()) == "data/output"


def test_date_range_blank_env_falls_back_to_default():
    # Unset ${VAR} placeholders expand to "" and must be treated as unset.
    dr = DateRangeConfig(last_days="", start="", end="")
    after, before = dr.resolve()
    assert before - after == 365 * 86400


def test_date_range_env_driven_absolute_wins():
    dr = DateRangeConfig(
        last_days="", start="2024-01-01T00:00:00Z", end="2024-01-08T00:00:00Z"
    )
    after, before = dr.resolve()
    assert before - after == 7 * 86400


def test_date_range_relative_and_absolute():
    after, before = DateRangeConfig(last_days=10).resolve()
    assert before - after == 10 * 86400

    a, b = DateRangeConfig(
        start="2024-01-01T00:00:00Z", end="2024-01-02T00:00:00Z"
    ).resolve()
    assert b - a == 86400
