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

    def test_provider_change_never_reuses_other_provider_quota(self):
        from quota import get_quota
        from state import save_state

        t0 = 3_000_000.0
        save_state({"quota": {
            "provider": "openai-codex", "available": True, "identity": "codex:old:sub",
            "windows": [{"label": "Week", "used_percent": 95.0}], "cache_fetched_at": t0,
        }})
        calls = []

        def fetch(provider):
            calls.append(provider)
            return make_snapshot(20.0, provider=provider)

        usage = get_quota(provider="anthropic", fetch=fetch, now=t0 + 5)
        self.assertEqual(calls, ["anthropic"])
        self.assertEqual(usage["provider"], "anthropic")
        self.assertEqual(usage["windows"][0]["used_percent"], 20.0)

    def test_future_dated_snapshot_is_never_considered_fresh(self):
        from quota import get_quota
        from state import save_state

        save_state({"quota": {
            "provider": "openai-codex", "available": True,
            "windows": [{"label": "Week", "used_percent": 95.0}], "cache_fetched_at": 5_000.0,
        }})
        calls = []

        def fetch(provider):
            calls.append(provider)
            return make_snapshot(25.0, provider=provider)

        usage = get_quota(provider="openai-codex", fetch=fetch, now=1_000.0)
        self.assertEqual(calls, ["openai-codex"])
        self.assertFalse(usage.get("from_cache"))
        self.assertEqual(usage["windows"][0]["used_percent"], 25.0)

    def test_unknown_active_provider_does_not_fall_back_to_cached_provider(self):
        from unittest import mock
        from quota import get_quota
        from state import save_state

        now = 9_000.0
        save_state({"quota": {
            "provider": "openai-codex", "available": True,
            "windows": [{"label": "Week", "used_percent": 95.0}], "cache_fetched_at": now - 5,
        }})
        calls = []

        def fetch(provider):
            calls.append(provider)
            return None

        with mock.patch("quota.active_provider_slug", return_value=None):
            usage = get_quota(fetch=fetch, now=now)
        self.assertEqual(calls, [])
        self.assertFalse(usage.get("available"))
        self.assertFalse(usage.get("from_cache"))

    def test_raw_provider_response_is_not_returned_or_persisted(self):
        from quota import get_quota
        from state import load_state

        snapshot = make_snapshot(25.0)
        snapshot.raw = {"diagnostic_marker": "PRIVATE_PROVIDER_BODY_DO_NOT_STORE"}
        usage = get_quota(provider="openai-codex", fetch=lambda _provider: snapshot, force=True)
        self.assertNotIn("raw", usage)
        self.assertNotIn("PRIVATE_PROVIDER_BODY_DO_NOT_STORE", str(load_state()))

    def test_identity_is_not_persisted_and_legacy_identity_is_removed_on_read(self):
        from quota import get_quota
        from state import load_state, save_state

        snapshot = make_snapshot(25.0)
        snapshot.identity = "PRIVATE_IDENTITY"
        get_quota(provider="openai-codex", fetch=lambda _provider: snapshot, force=True)
        self.assertNotIn("PRIVATE_IDENTITY", str(load_state()))
        self.assertNotIn("identity", load_state()["quota"])

        save_state({"quota": {
            "provider": "openai-codex", "available": True,
            "identity": "PRIVATE_IDENTITY",
            "windows": [{"label": "Week", "used_percent": 25.0}],
            "cache_fetched_at": 1000.0,
        }})
        cached = get_quota(provider="openai-codex", fetch=lambda _provider: self.fail("fresh cache expected"), now=1001.0)
        self.assertTrue(cached.get("from_cache"))
        self.assertNotIn("identity", load_state()["quota"])
