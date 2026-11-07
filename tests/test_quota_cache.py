"""Quota cache rate limit and exhausted marking."""
from __future__ import annotations

from support import IsolatedHomeTest, make_snapshot


class QuotaCacheTests(IsolatedHomeTest):
    def test_fetch_at_most_once_per_180s(self):
        from quota import CACHE_TTL_SECONDS, get_quota

        calls = []

        def fetch(provider):
            calls.append(provider)
            return make_snapshot(40.0)

        t0 = 1_000_000.0
        a = get_quota(provider="openai-codex", fetch=fetch, now=t0, force=True)
        self.assertFalse(a.get("from_cache"))
        b = get_quota(provider="openai-codex", fetch=fetch, now=t0 + 10)
        self.assertTrue(b.get("from_cache"))
        c = get_quota(provider="openai-codex", fetch=fetch, now=t0 + CACHE_TTL_SECONDS + 1)
        self.assertFalse(c.get("from_cache"))
        self.assertEqual(len(calls), 2)

    def test_mark_exhausted_observe_only(self):
        from quota import get_quota, mark_exhausted_until_reset

        def fetch(provider):
            return make_snapshot(95.0)

        usage = get_quota(provider="openai-codex", fetch=fetch, force=True)
        mark_exhausted_until_reset()
        again = get_quota(provider="openai-codex", fetch=fetch)
        self.assertIn("exhausted_until", again)
        self.assertTrue(again["exhausted_until"])
        # windows still present — we did not mutate core retry fields
        self.assertTrue(usage.get("windows"))
