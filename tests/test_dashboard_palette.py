from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CSS_PATH = ROOT / "src" / "see_aicoding" / "web_static" / "app.css"
JS_PATH = ROOT / "src" / "see_aicoding" / "web_static" / "app.js"
HTML_PATH = ROOT / "src" / "see_aicoding" / "web_static" / "index.html"

PALETTE_TOKENS = (
    "resource-cpu",
    "resource-gpu",
    "resource-memory",
    "resource-storage",
    "resource-network",
    "resource-process",
    "io-read",
    "io-write",
    "io-iops",
    "io-latency",
    "network-download",
    "network-upload",
    "runtime-service",
    "runtime-container",
    "state-live",
    "state-warning",
    "state-critical",
    "codex",
    "claude",
    "cursor",
)


class DashboardPaletteTests(unittest.TestCase):
    def test_semantic_palette_tokens_have_unique_hex_values(self) -> None:
        css = CSS_PATH.read_text(encoding="utf-8")
        values = {}
        for token in PALETTE_TOKENS:
            match = re.search(rf"--{re.escape(token)}:\s*(#[0-9a-fA-F]{{6}})\s*;", css)
            self.assertIsNotNone(match, f"missing dashboard color token --{token}")
            values[token] = match.group(1).lower()

        self.assertEqual(
            len(values),
            len(set(values.values())),
            f"semantic dashboard colors must not overlap: {values}",
        )

    def test_charts_reference_css_palette_instead_of_duplicate_hex_colors(self) -> None:
        javascript = JS_PATH.read_text(encoding="utf-8")

        for token in ("cpu", "gpu", "memory", "storage", "network", "process"):
            self.assertIn(f'{token}: "var(--resource-{token})"', javascript)
        self.assertIn('read: "var(--io-read)"', javascript)
        self.assertIn('write: "var(--io-write)"', javascript)

    def test_all_theme_and_language_controls_are_shipped(self) -> None:
        css = CSS_PATH.read_text(encoding="utf-8")
        javascript = JS_PATH.read_text(encoding="utf-8")
        html = HTML_PATH.read_text(encoding="utf-8")

        for theme in ("light", "warm", "mint", "dark", "deep"):
            self.assertIn(f'[data-theme="{theme}"]', css)
            self.assertIn(f'<option value="{theme}">', html)
        self.assertIn('<option value="zh-CN">简体中文</option>', html)
        self.assertIn('const ZH_TEXT = {', javascript)
        self.assertIn('document.documentElement.lang = state.preferences.language', javascript)
        self.assertIn('PERFORMANCE_INTERVALS = { realtime: 1.5, balanced: 3, efficient: 5 }', javascript)

    def test_frontend_uses_compact_stream_and_lazy_runtime_polling(self) -> None:
        javascript = JS_PATH.read_text(encoding="utf-8")

        self.assertIn('new EventSource(`/events?interval=${encodeURIComponent(interval)}`)', javascript)
        self.assertIn('new IntersectionObserver', javascript)
        self.assertIn('snapshot.stream_compact', javascript)
        self.assertNotIn('setInterval(fetchNetworkAttribution, 4000)', javascript)
        self.assertNotIn('setInterval(fetchContainers, 8000)', javascript)

    def test_language_switch_rerenders_dynamic_quota_labels(self) -> None:
        javascript = JS_PATH.read_text(encoding="utf-8")

        self.assertIn(
            "previousLanguage !== state.preferences.language && state.providerUsage",
            javascript,
        )
        self.assertIn("renderProviderUsage(state.providerUsage)", javascript)

    def test_quota_cards_have_persistent_visibility_and_collection_controls(self) -> None:
        css = CSS_PATH.read_text(encoding="utf-8")
        javascript = JS_PATH.read_text(encoding="utf-8")
        html = HTML_PATH.read_text(encoding="utf-8")

        self.assertIn('id="showQuotaCardsSetting"', html)
        self.assertIn('id="toggleQuotaVisibility"', html)
        self.assertIn('aria-controls="quotaGrid"', html)
        self.assertIn('aria-expanded="true"', html)
        self.assertIn(".quota-gauge", css)
        self.assertIn("conic-gradient", css)
        self.assertIn(".section-actions { width: 100%; flex-wrap: wrap;", css)
        self.assertIn("show_quota_cards: true", javascript)
        self.assertIn("if (!state.preferences.show_quota_cards) return;", javascript)
        self.assertIn("el.quotaGrid.hidden = !state.preferences.show_quota_cards", javascript)
        self.assertIn("state.providerUsage.next_refresh_at", javascript)
        self.assertIn("state.quotaFetchFailures += 1", javascript)
        self.assertIn("next_refresh_at: Date.now() / 1000 + retrySeconds", javascript)
        self.assertIn('aria-valuetext="${quotaLocale("Unavailable", "不可用")}"', javascript)


if __name__ == "__main__":
    unittest.main()
