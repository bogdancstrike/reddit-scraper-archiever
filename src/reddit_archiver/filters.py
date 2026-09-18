"""Keyword filtering applied while archiving.

Used by scraper-manager jobs, whose allWords / anyWords / noneWords /
exactPhrase mirror the X scraper's search-query operators. The Reddit archive
endpoints this project uses page by subreddit + time window and expose no
equivalent boolean query, so the same semantics are applied locally: a record is
kept only if it satisfies every clause. Filtering happens before persistence, so
a filtered job's output holds matching records only.

This is deliberately separate from phase 2, which annotates an already-archived
corpus in place instead of narrowing what gets stored.

Words match on word boundaries (``curs`` does not match ``cursuri``), which is
what a query-operator user expects; ``exactPhrase`` matches as a substring so
punctuation and spacing inside the phrase behave predictably.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(slots=True)
class ContentFilter:
    """Compiled all/any/none/phrase clauses. ``matches`` ANDs them together."""

    all_words: list[re.Pattern]
    any_words: list[re.Pattern]
    none_words: list[re.Pattern]
    exact_phrase: str | None

    @classmethod
    def build(
        cls,
        all_words: list[str] | None = None,
        any_words: list[str] | None = None,
        none_words: list[str] | None = None,
        exact_phrase: str | None = None,
    ) -> "ContentFilter | None":
        """Compile the clauses, or return None when there is nothing to filter."""
        if not (all_words or any_words or none_words or exact_phrase):
            return None
        return cls(
            all_words=[_word(w) for w in all_words or []],
            any_words=[_word(w) for w in any_words or []],
            none_words=[_word(w) for w in none_words or []],
            exact_phrase=exact_phrase.lower() if exact_phrase else None,
        )

    def matches(self, *parts: str | None) -> bool:
        """True if the joined, non-empty text satisfies every clause."""
        text = " \n ".join(p for p in parts if p)
        if not text:
            return False
        if any(not p.search(text) for p in self.all_words):
            return False
        if self.any_words and not any(p.search(text) for p in self.any_words):
            return False
        if any(p.search(text) for p in self.none_words):
            return False
        if self.exact_phrase and self.exact_phrase not in text.lower():
            return False
        return True


def _word(word: str) -> re.Pattern:
    return re.compile(rf"(?<!\w){re.escape(word)}(?!\w)", re.IGNORECASE)
