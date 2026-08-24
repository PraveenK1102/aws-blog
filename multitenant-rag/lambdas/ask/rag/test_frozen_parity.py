"""Frozen-prompt / frozen-contract parity (§5).

Productionization must not silently alter model instructions. Two layers:

  1. HASH LOCK — the recorded sha256 of each frozen prompt must match the string
     loaded in this process. A reworded prompt fails here.
  2. SOURCE-OF-TRUTH CROSS-CHECK — when the frozen eval modules are present in
     the checkout, the production strings are re-derived from them and compared
     BYTE FOR BYTE. This is what catches a drift introduced on either side.

Run: PYTHONPATH=multitenant-rag/lambdas:multitenant-rag/lambdas/ask \
     python -m unittest rag.test_frozen_parity -v
"""
import ast
import hashlib
import os
import unittest

from rag import prompts

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
EVALS = os.path.join(REPO, "evals")


def _frozen_from_evals():
    """Re-derive the frozen strings from the eval modules WITHOUT importing them
    (importing would pull nvidia_provider/boto3 and defeat the isolation test)."""
    def module_assigns(path):
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        ns = {}
        for node in ast.parse(src).body:
            if isinstance(node, ast.Import) and all(
                    a.name in ("os", "re", "json", "time") for a in node.names):
                exec(compile(ast.Module(body=[node], type_ignores=[]), "<x>", "exec"), ns)
            elif isinstance(node, ast.Assign):
                try:
                    exec(compile(ast.Module(body=[node], type_ignores=[]), "<x>", "exec"), ns)
                except Exception:
                    pass
        return ns
    r = module_assigns(os.path.join(EVALS, "router_v2.py"))
    d = module_assigns(os.path.join(EVALS, "decomp_graph.py"))
    return {"ROUTER_SYS": r.get("ROUTER_SYS"),
            "REASON_CODES": r.get("REASON_CODES"),
            "ANALYZER_SYS": d.get("ANALYZER_SYS"),
            "GEN_SYS_COMPOUND": d.get("GEN_SYS_COMPOUND")}


class HashLockTests(unittest.TestCase):
    def test_recorded_hashes_match_loaded_strings(self):
        self.assertEqual(prompts.live_hashes(), prompts.FROZEN_HASHES)

    def test_assert_frozen_passes(self):
        prompts.assert_frozen()          # must not raise

    def test_router_prompt_sha_is_the_validated_one(self):
        # The identity the holdout-passing router ran under.
        self.assertEqual(prompts.prompt_sha(prompts.ROUTER_SYS), "763d12cd82245285")

    def test_assert_frozen_detects_drift(self):
        original = prompts.ROUTER_SYS
        try:
            prompts.ROUTER_SYS = original + " "        # one trailing space
            with self.assertRaises(RuntimeError):
                prompts.assert_frozen()
        finally:
            prompts.ROUTER_SYS = original
        prompts.assert_frozen()

    def test_hashes_are_16_hex_chars(self):
        for name, h in prompts.FROZEN_HASHES.items():
            self.assertRegex(h, r"^[0-9a-f]{16}$", name)


class ReasonCodeContractTests(unittest.TestCase):
    def test_exactly_five_reason_codes(self):
        self.assertEqual(len(prompts.REASON_CODES), 5)

    def test_compound_code_is_the_only_true_code(self):
        self.assertEqual(prompts.COMPOUND_CODE,
                         "multiple_independent_retrieval_needs")
        self.assertNotIn(prompts.COMPOUND_CODE, prompts.SIMPLE_CODES)

    def test_simple_codes_exact_set(self):
        self.assertEqual(set(prompts.SIMPLE_CODES), {
            "single_retrieval_need", "single_entity_multi_attribute",
            "single_event_multi_attribute", "negative_or_scope_check"})

    def test_schema_records_the_enum(self):
        self.assertIn("needs_decomposition", prompts.ROUTER_SCHEMA)
        self.assertIn("information_needs", prompts.ROUTER_SCHEMA)
        self.assertIn("is_compound", prompts.DECOMPOSITION_SCHEMA)
        self.assertIn("subquestions", prompts.DECOMPOSITION_SCHEMA)


@unittest.skipUnless(os.path.isdir(EVALS), "frozen eval modules not in checkout")
class CrossCheckAgainstFrozenEvalsTests(unittest.TestCase):
    """The production copies must be byte-identical to the frozen originals."""

    @classmethod
    def setUpClass(cls):
        cls.frozen = _frozen_from_evals()

    def test_router_sys_byte_identical(self):
        self.assertIsNotNone(self.frozen["ROUTER_SYS"])
        self.assertEqual(prompts.ROUTER_SYS, self.frozen["ROUTER_SYS"])

    def test_analyzer_sys_byte_identical(self):
        self.assertIsNotNone(self.frozen["ANALYZER_SYS"])
        self.assertEqual(prompts.ANALYZER_SYS, self.frozen["ANALYZER_SYS"])

    def test_gen_sys_compound_byte_identical(self):
        self.assertIsNotNone(self.frozen["GEN_SYS_COMPOUND"])
        self.assertEqual(prompts.GEN_SYS_COMPOUND, self.frozen["GEN_SYS_COMPOUND"])

    def test_reason_codes_identical(self):
        self.assertEqual(tuple(prompts.REASON_CODES),
                         tuple(self.frozen["REASON_CODES"]))

    def test_hash_of_frozen_source_matches_recorded(self):
        for name in ("ROUTER_SYS", "ANALYZER_SYS", "GEN_SYS_COMPOUND"):
            h = hashlib.sha256(self.frozen[name].encode()).hexdigest()[:16]
            self.assertEqual(h, prompts.FROZEN_HASHES[name], name)


if __name__ == "__main__":
    unittest.main()
