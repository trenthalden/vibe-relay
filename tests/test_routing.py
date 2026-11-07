"""Routing decision by quota + refusal without allowed/gate."""
from __future__ import annotations

import unittest
from unittest import mock

from support import IsolatedHomeTest, make_snapshot


class RoutingDecisionTests(IsolatedHomeTest):
    def test_auto_self_when_remaining_above_threshold(self):
        from routing import decide_lane
        # 40% used → 60% remaining > 51
        d = decide_lane(lane="auto", remaining_percent=60.0, used_percent=40.0)
        self.assertEqual(d["lane"], "self")

    def test_auto_executor_when_remaining_at_or_below_threshold(self):
        from routing import decide_lane
        d = decide_lane(lane="auto", remaining_percent=51.0, used_percent=49.0)
        self.assertEqual(d["lane"], "executor")
        d2 = decide_lane(lane="auto", remaining_percent=50.0, used_percent=50.0)
        self.assertEqual(d2["lane"], "executor")

    def test_executor_always_policy(self):
        from routing import decide_lane
        d = decide_lane(lane="auto", policy="executor-always", remaining_percent=90.0, used_percent=10.0)
        self.assertEqual(d["lane"], "executor")
        self.assertIn("executor-always", d["reason"])

    def test_park_when_stop_threshold(self):
        from routing import decide_lane
        d = decide_lane(lane="auto", remaining_percent=5.0, used_percent=91.0, stop_used_percent=90.0)
        self.assertTrue(d["should_park"])


class ProviderRefuseTests(IsolatedHomeTest):
    def test_refuse_without_allowed_or_gate(self):
        from agent.delegation_provider import DelegationRouteError
        from provider import VibeRelayProvider

        p = VibeRelayProvider()
        cfg = {"backends": {"vibe-relay": {
            "executor_provider": "openrouter",
            "executor_model": "google/gemini-2.5-flash",
        }}}
        with self.assertRaises(DelegationRouteError):
            p.route_task({"goal": "x", "lane": "auto"}, cfg)
        with self.assertRaises(DelegationRouteError):
            p.route_task({"goal": "x", "allowed": "src/**"}, cfg)

    def test_routes_by_stub_quota(self):
        from provider import VibeRelayProvider
        from quota import get_quota

        p = VibeRelayProvider()
        cfg = {"backends": {"vibe-relay": {
            "self_remaining_threshold": 51,
            "executor_provider": "openrouter",
            "executor_model": "google/gemini-2.5-flash",
            "quota_provider": "openai-codex",
        }}}

        def low(_p):
            return make_snapshot(70.0)  # 30% remaining → executor

        get_quota(provider="openai-codex", fetch=low, force=True)
        route = p.route_task(
            {"goal": "x", "lane": "auto", "allowed": "src/**", "gate": "pytest -q"},
            cfg,
        )
        self.assertEqual(route.get("provider"), "openrouter")
        self.assertIn("gemini", route.get("model", ""))

        def high(_p):
            return make_snapshot(20.0)  # 80% remaining → self

        get_quota(provider="openai-codex", fetch=high, force=True)
        route2 = p.route_task(
            {"goal": "x", "lane": "auto", "allowed": "src/**", "gate": "pytest -q"},
            cfg,
        )
        self.assertEqual(route2.get("label", "")[:4], "self")
        self.assertNotIn("provider", route2)


if __name__ == "__main__":
    unittest.main()
