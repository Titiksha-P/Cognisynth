"""Headless browser smoke test for the Cognisynth HMI render contract.

Run with: python -m unittest tests.test_headless_render -v
Requires Playwright and an installed Chromium browser:
    pip install playwright
    playwright install chromium

The 16.67 ms check verifies the declared frame-budget contract and measures this
small mock DOM update. It is not a worst-case execution-time proof or a guarantee
about every device, browser, frame, or production rendering path.
"""
from __future__ import annotations

import json
import unittest
from typing import Any

from safety.formal_validator import (
    FRAME_BUDGET_MS,
    SAFE_FALLBACK,
    _nodes_reach_home,
    validate_proposal,
)


def _registry_from_validated_blueprint() -> dict[str, Any]:
    """Build a minimal registry only from a blueprint accepted by the safety gate."""
    result = validate_proposal(SAFE_FALLBACK, expected_panic_index=0.0)
    if not result.accepted:
        raise RuntimeError("The canonical fallback did not pass the deterministic gate.")

    blueprint = result.blueprint
    graph_ok, graph_reason = _nodes_reach_home(blueprint.navigation_graph)
    if not graph_ok:
        raise RuntimeError(f"Fallback navigation graph failed reachability: {graph_reason}")

    return {
        "controls": list(blueprint.visible_controls),
        "streams": list(blueprint.visible_streams),
        "navigationGraph": blueprint.navigation_graph,
        "renderBudgetMs": blueprint.render_budget_ms,
        "criticalControls": [
            "master_emergency_shutoff",
            "alarm_acknowledge",
            "manual_override",
        ],
    }


class HeadlessRenderTests(unittest.TestCase):
    def test_mock_jspsych_interface_renders_within_declared_frame_budget(self) -> None:
        try:
            from playwright.sync_api import Error as PlaywrightError
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            self.fail(
                "Playwright is required. Install it with "
                "'pip install playwright' and 'playwright install chromium'. "
                f"Import detail: {exc}"
            )

        registry = _registry_from_validated_blueprint()
        self.assertLessEqual(registry["renderBudgetMs"], FRAME_BUDGET_MS)

        # JSON serialization is the only bridge from Python to the page. Registry
        # labels are inserted as textContent by JavaScript, never evaluated as code.
        registry_json = json.dumps(registry, separators=(",", ":"), ensure_ascii=True)
        html = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Cognisynth Headless HMI Smoke Test</title>
  <style>
    body { font-family: sans-serif; margin: 0; }
    #hmi { display: grid; grid-template-columns: repeat(2, minmax(180px, 1fr)); gap: 8px; }
    .control { min-height: 40px; padding: 8px; }
    #master_emergency_shutoff { outline: 3px solid #a00; }
  </style>
</head>
<body>
  <main id="hmi" aria-label="Cognisynth mock HMI"></main>
  <output id="status" aria-live="polite">initializing</output>
  <script>
    // Minimal local jsPsych-compatible setup. No external scripts or network calls.
    window.jsPsych = {
      initialized: false,
      init: function(config) {
        this.initialized = true;
        this.config = config || {};
        return Promise.resolve();
      }
    };

    window.cognisynthRegistry = __REGISTRY_JSON__;

    window.renderCognisynthMock = async function(registry) {
      const startedAt = performance.now();
      const root = document.getElementById("hmi");
      const allowed = new Set(registry.controls);
      const required = registry.criticalControls;

      if (required.some(function(id) { return !allowed.has(id); })) {
        throw new Error("Critical control absent from validated registry");
      }

      root.replaceChildren();
      registry.controls.forEach(function(id) {
        const button = document.createElement("button");
        button.type = "button";
        button.id = id;
        button.className = "control";
        button.textContent = id;
        root.appendChild(button);
      });

      registry.streams.forEach(function(id) {
        const section = document.createElement("section");
        section.id = "stream-" + id;
        section.textContent = id;
        root.appendChild(section);
      });

      await window.jsPsych.init({ timeline: [] });
      // Force layout so this smoke test measures the synchronous DOM/layout work.
      const layoutHeight = root.getBoundingClientRect().height;
      const renderDurationMs = performance.now() - startedAt;
      document.getElementById("status").textContent = "rendered";
      return {
        renderDurationMs: renderDurationMs,
        layoutHeight: layoutHeight,
        frameBudgetMs: registry.renderBudgetMs,
        jsPsychInitialized: window.jsPsych.initialized,
        controlCount: root.querySelectorAll("button").length
      };
    };
  </script>
</body>
</html>""".replace("__REGISTRY_JSON__", registry_json)

        browser = None
        console_errors: list[str] = []
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1280, "height": 800})
                page.on("pageerror", lambda error: console_errors.append(str(error)))
                page.set_content(html, wait_until="load")

                result = page.evaluate(
                    """async () => await window.renderCognisynthMock(window.cognisynthRegistry)"""
                )

                self.assertTrue(result["jsPsychInitialized"])
                self.assertGreater(result["layoutHeight"], 0)
                self.assertEqual(result["controlCount"], len(registry["controls"]))
                self.assertLessEqual(result["frameBudgetMs"], FRAME_BUDGET_MS)
                self.assertLessEqual(
                    result["renderDurationMs"],
                    FRAME_BUDGET_MS,
                    "Mock DOM/layout work exceeded the 16.67 ms frame budget on this run.",
                )

                for control_id in registry["criticalControls"]:
                    self.assertEqual(page.locator(f"#{control_id}").count(), 1)

                graph_ok, graph_reason = _nodes_reach_home(registry["navigationGraph"])
                self.assertTrue(graph_ok, graph_reason)
                self.assertEqual(page.locator("#status").inner_text(), "rendered")
                self.assertEqual(console_errors, [])
        except PlaywrightError as exc:
            self.fail(
                "Headless Chromium could not run. Install the browser with "
                "'playwright install chromium'. Browser detail: " + str(exc)
            )
        finally:
            if browser is not None:
                try:
                    browser.close()
                except Exception:
                    # The Playwright context manager may already have closed it.
                    pass

    def test_invalid_frame_budget_is_rejected_by_safety_gate(self) -> None:
        proposal = SAFE_FALLBACK.model_copy(deep=True)
        proposal.render_budget_ms = FRAME_BUDGET_MS + 1.0

        result = validate_proposal(proposal, expected_panic_index=0.0)

        self.assertFalse(result.accepted)
        self.assertEqual(result.blueprint, SAFE_FALLBACK)
        self.assertIn("FRAME_BUDGET_EXCEEDED", {item.code for item in result.violations})

    def test_registry_requires_home_reachability(self) -> None:
        graph = dict(SAFE_FALLBACK.navigation_graph)
        graph["trapped_operator_view"] = ["isolated_dead_end"]
        graph["isolated_dead_end"] = ["isolated_dead_end"]

        graph_ok, reason = _nodes_reach_home(graph)

        self.assertFalse(graph_ok)
        self.assertIn("isolated_dead_end", reason)
        self.assertIn("trapped_operator_view", reason)


if __name__ == "__main__":
    unittest.main(verbosity=2)
