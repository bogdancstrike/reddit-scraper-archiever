import json
from pathlib import Path

import pytest

from reddit_archiver.exceptions import InvalidScrapingParameters
from reddit_archiver.filters import ContentFilter
from reddit_archiver.manager import job
from reddit_archiver.manager.args import parse_scraping_args
from reddit_archiver.manager.env import ScrapingEntry
from reddit_archiver.manager.paths import generate_results_folder
from reddit_archiver.manager.results import count_by_type, export_results
from reddit_archiver.sources.base import Page, SourceBackend


class FakeBackend(SourceBackend):
    """Serves one page of posts and one of comments, then reports exhaustion."""

    name = "arctic_shift"

    def __init__(self, posts, comments):
        self._posts = posts
        self._comments = comments

    async def search_posts(self, subreddit, after, before, cursor=None):
        items, self._posts = self._posts, []
        return Page(items=items, next_cursor=None, exhausted=True)

    async def search_comments(self, subreddit, after, before, cursor=None):
        items, self._comments = self._comments, []
        return Page(items=items, next_cursor=None, exhausted=True)

    async def aclose(self):
        return None


def _post(pid, title, epoch=1735689600):
    return {"id": pid, "subreddit": "romania", "author": "u1", "title": title,
            "selftext": "", "created_utc": epoch, "num_comments": 0, "score": 1}


def _comment(cid, body, epoch=1735689700):
    return {"id": cid, "link_id": "t3_p1", "parent_id": "t3_p1", "subreddit": "romania",
            "author": "u2", "body": body, "created_utc": epoch, "score": 1}


@pytest.fixture
def fake_chain(monkeypatch):
    """Swap the real backend chain for the fake one."""
    def _install(posts, comments):
        monkeypatch.setattr(
            job, "build_backend_chain", lambda config: [FakeBackend(posts, comments)]
        )
    return _install


# --- keyword filter --------------------------------------------------------

def test_filter_requires_every_clause():
    f = ContentFilter.build(["securitate"], ["ransomware", "malware"], ["curs"],
                            "atac cibernetic")
    assert f.matches("Securitate: atac cibernetic cu ransomware")
    assert not f.matches("atac cibernetic cu ransomware")          # missing all_word
    assert not f.matches("securitate si atac cibernetic")          # no any_word
    assert not f.matches("curs de securitate, atac cibernetic, malware")  # none_word
    assert not f.matches("securitate, malware, atac informatic")   # phrase absent


def test_filter_matches_whole_words_only():
    f = ContentFilter.build(none_words=["curs"])
    assert f.matches("cursuri de securitate")
    assert not f.matches("un curs de securitate")


def test_no_clauses_means_no_filter():
    assert ContentFilter.build([], [], [], None) is None


# --- job wiring ------------------------------------------------------------

async def test_job_writes_archive_results_and_done_flag(tmp_path, fake_chain):
    fake_chain([_post("p1", "Atac cibernetic")], [_comment("c1", "raspuns")])
    entry = ScrapingEntry(
        scraping_arg_id="7",
        scraping_arg={"target": "r/Romania", "startDate": "01.01.2025",
                      "endDate": "31.01.2025"},
    )

    result = await job.run_scraping_job(None, entry, output_root=tmp_path)

    assert result.results_folder.parent == tmp_path
    assert result.results_folder.name.startswith("reddit_7_romania_")
    assert (result.results_folder / "gata.txt").is_file()
    assert (result.results_folder / "romania" / "posts.json").is_file()

    records = json.loads(result.results_file.read_text())
    assert count_by_type(records) == {"post": 1, "comment": 1}
    assert (result.posts, result.comments) == (1, 1)
    # The flat export drops `raw` but keeps the archive copy intact.
    assert "raw" not in records[0]
    assert "raw" in json.loads((result.results_folder / "romania" / "posts.json").read_text())["p1"]


async def test_job_stores_only_records_matching_the_keywords(tmp_path, fake_chain):
    fake_chain(
        [_post("p1", "Atac cibernetic cu ransomware"), _post("p2", "Curs de securitate")],
        [_comment("c1", "ransomware peste tot"), _comment("c2", "off topic")],
    )
    entry = ScrapingEntry(
        scraping_arg_id="7",
        scraping_arg={"target": "romania", "anyWords": ["ransomware"], "noneWords": ["curs"]},
    )

    result = await job.run_scraping_job(None, entry, output_root=tmp_path)

    records = json.loads(result.results_file.read_text())
    assert sorted(r["id"] for r in records) == ["c1", "p1"]


async def test_invalid_entry_does_not_create_a_folder(tmp_path, fake_chain):
    fake_chain([], [])
    entry = ScrapingEntry(scraping_arg_id="9", scraping_arg={"target": None})
    with pytest.raises(InvalidScrapingParameters):
        await job.run_scraping_job(None, entry, output_root=tmp_path)
    assert list(tmp_path.iterdir()) == []


async def test_manager_batch_continues_past_a_bad_entry(tmp_path, fake_chain, monkeypatch):
    fake_chain([_post("p1", "ok")], [])
    monkeypatch.setenv("id", "container-1")
    monkeypatch.setenv(
        "scraping_args",
        json.dumps([
            {"scraping_arg_id": "bad", "scraping_arg": {"target": ""}},
            {"scraping_arg_id": "good", "scraping_arg": {"target": "romania"}},
        ]),
    )

    exit_code = await job.run_manager(None, tmp_path)

    # Non-zero because one entry failed, but the good entry still ran.
    assert exit_code == 1
    folders = [p.name for p in tmp_path.iterdir() if p.is_dir()]
    assert len(folders) == 1 and folders[0].startswith("reddit_good_romania_")


# --- config overlay --------------------------------------------------------

def test_job_config_overlays_the_args_on_the_base_config(tmp_path):
    from reddit_archiver.config import load_config

    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        "subreddits: [ignored]\nbackends: [pullpush]\nlog_level: DEBUG\n"
    )
    base = load_config(cfg_file)
    args = parse_scraping_args({"target": ["romania", "cluj"], "startDate": "01.01.2025",
                                "endDate": "31.01.2025", "fetchComments": False})
    folder = generate_results_folder(tmp_path, "7", args)

    config = job.build_job_config(base, args, folder)

    assert config.subreddits == ["romania", "cluj"]
    assert config.backends == ["pullpush"]          # base config still applies
    assert config.log_level == "DEBUG"
    assert config.fetch.posts and not config.fetch.comments
    assert Path(config.output.resolved_dir()) == folder
    after, before = config.date_range.resolve()
    assert before - after == 31 * 86400 - 1


def test_job_config_without_a_base_uses_defaults(tmp_path):
    args = parse_scraping_args({"target": "romania"})
    config = job.build_job_config(None, args, tmp_path / "job")
    assert config.backends == ["arctic_shift", "pullpush"]
    after, before = config.date_range.resolve()
    assert before - after == 365 * 86400


def test_whole_yaml_equivalent_comes_from_the_payload(tmp_path):
    """Everything configs/scraper-*.yaml expresses, with no config file."""
    args = parse_scraping_args({
        "subreddits": ["Romania", "programming"],
        "lastDays": 30,
        "fetchPosts": True,
        "fetchComments": False,
        "backends": ["pullpush", "arctic_shift"],
        "backendSettings": {
            "pullpush": {"baseUrl": "https://example.test",
                         "rateLimit": {"minIntervalSeconds": 9.0, "pageLimit": 50}},
        },
        "maxPages": 3,
    })
    config = job.build_job_config(None, args, tmp_path / "job")

    assert config.subreddits == ["romania", "programming"]
    assert config.backends == ["pullpush", "arctic_shift"]
    assert config.fetch.posts and not config.fetch.comments
    pullpush = config.backend_config("pullpush")
    assert pullpush.base_url == "https://example.test"
    assert pullpush.rate_limit.min_interval_seconds == 9.0
    assert pullpush.rate_limit.page_limit == 50
    # Untouched fields keep their defaults, and other backends are unaffected.
    assert pullpush.rate_limit.max_retries == 5
    assert config.backend_config("arctic_shift").rate_limit.min_interval_seconds == 0.5
    after, before = config.date_range.resolve()
    assert before - after == 30 * 86400
    assert args.max_pages == 3


def test_explicit_dates_win_over_last_days(tmp_path):
    args = parse_scraping_args({"target": "romania", "lastDays": 30,
                                "startDate": "01.01.2025", "endDate": "02.01.2025"})
    config = job.build_job_config(None, args, tmp_path / "job")
    after, before = config.date_range.resolve()
    assert before - after == 2 * 86400 - 1


def test_payload_backend_settings_merge_over_the_base_config(tmp_path):
    from reddit_archiver.config import load_config

    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        "subreddits: [ignored]\n"
        "backend_settings:\n"
        "  arctic_shift:\n"
        "    base_url: https://base.test\n"
        "    rate_limit: {min_interval_seconds: 2.0, max_workers: 8}\n"
    )
    base = load_config(cfg_file)
    args = parse_scraping_args({
        "target": "romania",
        "backendSettings": {"arctic_shift": {"rateLimit": {"maxWorkers": 1}}},
    })

    settings = job.build_job_config(base, args, tmp_path / "job").backend_config("arctic_shift")

    assert settings.rate_limit.max_workers == 1        # payload override
    assert settings.rate_limit.min_interval_seconds == 2.0  # kept from the config
    assert settings.base_url == "https://base.test"


@pytest.mark.parametrize(
    "arg",
    [
        {"target": "romania", "backends": ["nope"]},
        {"target": "romania", "backendSettings": {"nope": {}}},
        {"target": "romania", "backendSettings": {"pullpush": 5}},
        {"target": "romania", "lastDays": 0},
        {"target": "romania", "lastDays": "many"},
        {"target": "romania", "maxPages": -1},
    ],
)
def test_invalid_tuning_fields_are_rejected(arg):
    with pytest.raises(InvalidScrapingParameters):
        parse_scraping_args(arg)


def test_invalid_backend_setting_value_is_reported_as_a_bad_arg(tmp_path):
    args = parse_scraping_args(
        {"target": "romania", "backendSettings": {"pullpush": {"rateLimit": {"maxWorkers": "x"}}}}
    )
    with pytest.raises(InvalidScrapingParameters):
        job.build_job_config(None, args, tmp_path / "job")


def test_unknown_payload_fields_are_flagged(caplog):
    parse_scraping_args({"target": "romania", "subReddits": ["typo"]})
    assert "subReddits" in caplog.text


async def test_max_pages_caps_the_walk(tmp_path, monkeypatch):
    pages = {"n": 0}

    class EndlessBackend(FakeBackend):
        async def search_posts(self, subreddit, after, before, cursor=None):
            pages["n"] += 1
            epoch = 1735689600 + pages["n"] * 60
            return Page(items=[_post(f"p{pages['n']}", "t", epoch)],
                        next_cursor=epoch, exhausted=False)

    monkeypatch.setattr(job, "build_backend_chain", lambda config: [EndlessBackend([], [])])
    entry = ScrapingEntry(
        scraping_arg_id="1",
        scraping_arg={"target": "romania", "maxPages": 2, "fetchComments": False},
    )

    result = await job.run_scraping_job(None, entry, output_root=tmp_path)

    assert pages["n"] == 2
    assert result.posts == 2


def test_multi_subreddit_folder_name_is_labelled_by_count(tmp_path):
    args = parse_scraping_args({"target": ["romania", "cluj"]})
    folder = generate_results_folder(tmp_path, "7", args)
    assert folder.name.startswith("reddit_7_2subs_any_any_")


def test_export_is_sorted_and_typed(tmp_path):
    sub = tmp_path / "romania"
    sub.mkdir()
    (sub / "posts.json").write_text(json.dumps({"p1": {"id": "p1", "created_epoch": 200}}))
    (sub / "comments.json").write_text(json.dumps({"c1": {"id": "c1", "created_epoch": 100}}))

    _, records = export_results(tmp_path)

    assert [(r["type"], r["id"]) for r in records] == [("comment", "c1"), ("post", "p1")]
