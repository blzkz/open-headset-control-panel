"""Virtual 7.1 graph: bass management around the HRTF spatializer."""

from __future__ import annotations

import unittest
from pathlib import Path

from openheadset.audio.surround import CHANNELS, CROSSOVER_HZ, build_surround_conf, surround_args
from openheadset.config import SurroundSettings


class SurroundGraph(unittest.TestCase):
    def setUp(self) -> None:
        args = surround_args(SurroundSettings(enabled=True), "alsa_output.test", Path("/x/kemar.sofa"), "test")
        self.graph = args["filter.graph"]
        self.nodes = {n["name"]: n for n in self.graph["nodes"]}
        self.links = {(link["output"], link["input"]) for link in self.graph["links"]}

    def _downstream(self, port: str) -> set[str]:
        """Node names reachable from a node port."""
        seen: set[str] = set()
        todo = [port.split(":")[0]]
        while todo:
            node = todo.pop()
            if node in seen:
                continue
            seen.add(node)
            todo += [i.split(":")[0] for o, i in self.links if o.split(":")[0] == node]
        return seen

    def test_lfe_bypasses_the_hrtf_and_reaches_both_ears(self) -> None:
        lfe = self.graph["inputs"][[c for c, _a, _e in CHANNELS].index("LFE")]
        reach = self._downstream(lfe)
        self.assertFalse(any(self.nodes[n]["label"] == "spatializer" for n in reach))
        self.assertTrue({"mixL", "mixR"} <= reach)

    def test_every_other_channel_is_split_by_a_linkwitz_riley_crossover(self) -> None:
        for ch, _az, _el in CHANNELS:
            if ch == "LFE":
                continue
            reach = self._downstream(f"in{ch}:In")
            self.assertIn(f"sp{ch}", reach, ch)
            for name in (f"hp{ch}a", f"hp{ch}b", f"lp{ch}a", f"lp{ch}b"):
                self.assertEqual(self.nodes[name]["control"]["Freq"], CROSSOVER_HZ)
            self.assertIn((f"hp{ch}b:Out", f"sp{ch}:In"), self.links)
            self.assertIn((f"lp{ch}b:Out", f"bass:In {[c for c, _a, _e in CHANNELS].index(ch) + 1}"), self.links)

    def test_eight_inputs_and_a_valid_config(self) -> None:
        self.assertEqual(len(self.graph["inputs"]), 8)
        self.assertEqual(self.graph["outputs"], ["mixL:Out", "mixR:Out"])
        mixers = [n for n in self.graph["nodes"] if n["label"] == "mixer"]
        self.assertTrue(all(len(n["control"]) <= 8 for n in mixers))  # the builtin mixer has 8 inputs
        conf = build_surround_conf(SurroundSettings(enabled=True), "alsa_output.test", Path("/x/kemar.sofa"), "t")
        self.assertIn("libpipewire-module-filter-chain", conf)


if __name__ == "__main__":
    unittest.main()
