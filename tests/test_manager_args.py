import json

import pytest

from reddit_archiver.exceptions import InvalidScrapingParameters, ManagerEnvError
from reddit_archiver.manager.args import parse_scraping_args
from reddit_archiver.manager.env import load_manager_env

FULL_ARG = {
    "target": "https://www.reddit.com/r/Romania/",
    "startDate": "01.01.2025",
    "endDate": "31.01.2025",
    "allWords": ["securitate", "cibernetica"],
    "noneWords": ["curs", "recrutare"],
    "anyWords": ["ransomware", "malware"],
    "exactPhrase": "atac cibernetic",
    "language": "ro",
    "fetchComments": True,
}


def test_parses_the_manager_payload():
    args = parse_scraping_args(FULL_ARG)
    assert args.subreddits == ["romania"]
    assert args.all_words == ["securitate", "cibernetica"]
    assert args.any_words == ["ransomware", "malware"]
    assert args.none_words == ["curs", "recrutare"]
    assert args.exact_phrase == "atac cibernetic"
    assert args.fetch_posts and args.fetch_comments
    assert args.has_keyword_filter


def test_end_date_is_inclusive_of_the_whole_day():
    start, end = parse_scraping_args(FULL_ARG).window()
    assert start.strftime("%Y-%m-%d %H:%M:%S") == "2025-01-01 00:00:00"
    assert end.strftime("%Y-%m-%d %H:%M:%S") == "2025-01-31 23:59:59"


@pytest.mark.parametrize(
    "target,expected",
    [
        ("romania", ["romania"]),
        ("r/Romania", ["romania"]),
        ("/r/romania/", ["romania"]),
        ("https://reddit.com/r/Programare/comments/abc", ["programare"]),
        ("romania, cluj", ["romania", "cluj"]),
        (["Romania", "r/cluj", "romania"], ["romania", "cluj"]),
    ],
)
def test_target_forms_normalize_to_subreddit_names(target, expected):
    assert parse_scraping_args({"target": target}).subreddits == expected


def test_words_accept_a_string_or_a_list():
    args = parse_scraping_args({"target": "romania", "allWords": "unu doi", "anyWords": ["trei"]})
    assert args.all_words == ["unu", "doi"]
    assert args.any_words == ["trei"]


def test_window_is_none_without_dates():
    args = parse_scraping_args({"target": "romania"})
    assert args.window() is None
    assert not args.has_keyword_filter


def test_language_is_accepted_but_dropped(caplog):
    args = parse_scraping_args(FULL_ARG)
    assert args.language == "ro"
    assert "language" in caplog.text.lower()


@pytest.mark.parametrize(
    "arg",
    [
        {},                                              # no target
        {"target": ""},                                  # blank target
        {"target": "romania", "startDate": "01.01.2025"},  # start without end
        {"target": "romania", "endDate": "01.01.2025"},    # end without start
        {"target": "romania", "startDate": "31.01.2025", "endDate": "01.01.2025"},
        {"target": "romania", "startDate": "2025/01/01", "endDate": "01.02.2025"},
        {"target": "https://example.com/nothing"},       # URL without /r/
    ],
)
def test_invalid_payloads_are_rejected(arg):
    with pytest.raises(InvalidScrapingParameters):
        parse_scraping_args(arg)


def test_env_contract(monkeypatch):
    monkeypatch.setenv("id", "container-1")
    monkeypatch.setenv(
        "scraping_args",
        json.dumps([{"scraping_arg_id": "7", "scraping_arg": FULL_ARG}]),
    )
    env = load_manager_env()
    assert env.container_entity_id == "container-1"
    assert [e.scraping_arg_id for e in env.entries] == ["7"]
    assert env.entries[0].scraping_arg["target"].endswith("/r/Romania/")


def test_env_accepts_a_bare_args_object_and_indexes_it(monkeypatch):
    monkeypatch.setenv("id", "container-1")
    monkeypatch.setenv("scraping_args", json.dumps({"target": "romania"}))
    env = load_manager_env()
    assert [e.scraping_arg_id for e in env.entries] == ["1"]
    assert env.entries[0].scraping_arg == {"target": "romania"}


@pytest.mark.parametrize(
    "env",
    [
        {},                                        # nothing set
        {"id": "1"},                               # scraping_args missing
        {"id": "1", "scraping_args": "not json"},
        {"id": "1", "scraping_args": "[]"},        # no entries
        {"id": "1", "scraping_args": '["nope"]'},  # entry is not an object
    ],
)
def test_broken_env_is_fatal(monkeypatch, env):
    monkeypatch.delenv("id", raising=False)
    monkeypatch.delenv("scraping_args", raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(ManagerEnvError):
        load_manager_env()
