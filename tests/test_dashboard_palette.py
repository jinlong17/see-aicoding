from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CSS_PATH = ROOT / "src" / "see_aicoding" / "web_static" / "app.css"
JS_PATH = ROOT / "src" / "see_aicoding" / "web_static" / "app.js"

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


if __name__ == "__main__":
    unittest.main()
