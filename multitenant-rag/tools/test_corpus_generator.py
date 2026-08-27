"""Invariants for stress-corpus generation, naturalization and measurement (§47)."""
import ast
import json
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
SPEC = os.path.join(REPO, "rag-stress-corpus")
GEN = os.path.join(SPEC, "generated", "cohort-a")


def read(p):
    with open(p, encoding="utf-8") as fh:
        return fh.read()


def load(p):
    return json.loads(read(p))


class AntiLeakageTests(unittest.TestCase):
    """§6 — the generator must never see the evaluation questions."""

    def setUp(self):
        self.src = read(os.path.join(HERE, "generate_rag_stress_corpus.py"))

    def test_generator_does_not_reference_the_eval_file(self):
        self.assertNotIn("rag_stress_eval", self.src)
        self.assertNotIn("naturalized", self.src)

    def test_generator_imports_no_eval_module(self):
        mods = set()
        for n in ast.walk(ast.parse(self.src)):
            if isinstance(n, ast.Import):
                mods |= {a.name.split(".")[0] for a in n.names}
            elif isinstance(n, ast.ImportFrom) and n.module:
                mods.add(n.module.split(".")[0])
        self.assertNotIn("naturalize_rag_stress_eval", mods)

    def test_generator_only_loads_the_three_structural_manifests(self):
        loaded = {m.group(0) for m in __import__("re").finditer(
            r"rag_stress_\w+_v1\.json", self.src)}
        self.assertEqual(loaded, {"rag_stress_users_v1.json",
                                  "rag_stress_posts_v1.json",
                                  "rag_stress_facts_v1.json"})

    def test_generator_calls_no_provider_or_aws_client(self):
        for banned in ("boto3", "requests", "groq", "openai", "qdrant_client",
                       "invoke_model", "put_object", "send_message"):
            self.assertNotIn(banned, self.src, banned)


class CohortShapeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.man = load(os.path.join(GEN, "manifest.json"))["posts"]
        cls.trace = load(os.path.join(GEN, "fact_trace.json"))["traces"]
        cls.users = [u for u in load(os.path.join(SPEC, "rag_stress_users_v1.json"))["users"]
                     if u["cohort"] == "A"]

    def test_25_users(self):
        self.assertEqual(len(self.users), 25)

    def test_450_posts(self):
        self.assertEqual(len(self.man), 450)

    def test_18_posts_per_user(self):
        import collections
        c = collections.Counter(p["user_id"] for p in self.man)
        self.assertEqual(set(c.values()), {18})

    def test_category_composition(self):
        import collections
        c = collections.Counter(u["category"] for u in self.users)
        self.assertEqual(dict(c), {"job_search": 10, "ai_ml_swe": 5, "travel_food": 3,
                                   "eng_notes": 3, "noise": 2, "adversarial": 2})

    def test_every_post_within_its_word_range(self):
        for p in self.man:
            lo, hi = p["target_word_range"]
            self.assertTrue(lo <= p["actual_word_count"] <= hi, p["post_id"])

    def test_all_facts_traced(self):
        facts = [f for f in load(os.path.join(SPEC, "rag_stress_facts_v1.json"))["facts"]
                 if f["user_id"].startswith("U0") and
                 f["user_id"] in {u["user_id"] for u in self.users}]
        self.assertEqual(len({t["fact_id"] for t in self.trace}), len(facts))

    def test_evidence_is_verbatim_in_its_post(self):
        paths = {p["post_id"]: p["path"] for p in self.man}
        for t in self.trace[:400]:
            body = read(os.path.join(GEN, paths[t["post_id"]]))
            self.assertIn(t["evidence_excerpt"], body, t["fact_id"])

    def test_no_fact_id_or_marker_appears_in_a_body(self):
        import re
        pat = re.compile(r"(FACT-U\d|QUESTION-ID|GROUND TRUTH|\[simple\]|\[compound\])", re.I)
        for p in self.man[:120]:
            self.assertIsNone(pat.search(read(os.path.join(GEN, p["path"]))), p["post_id"])

    def test_rare_identifiers_preserved_exactly(self):
        """Confusable siblings must not be normalised into one another."""
        facts = {f["fact_id"]: f for f in
                 load(os.path.join(SPEC, "rag_stress_facts_v1.json"))["facts"]}
        paths = {p["post_id"]: p["path"] for p in self.man}
        checked = 0
        for t in self.trace:
            f = facts[t["fact_id"]]
            for tok in f.get("rare_tokens", []):
                self.assertIn(tok, read(os.path.join(GEN, paths[t["post_id"]])), tok)
                checked += 1
        self.assertGreater(checked, 0)


class NaturalizedEvalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.orig = {c["question_id"]: c for c in
                    load(os.path.join(SPEC, "rag_stress_eval_v1.json"))["cases"]}
        cls.nat = {c["question_id"]: c for c in
                   load(os.path.join(SPEC, "rag_stress_eval_v1_naturalized.json"))["cases"]}

    def test_240_cases_retained(self):
        self.assertEqual(len(self.nat), 240)
        self.assertEqual(set(self.nat), set(self.orig))

    def test_no_category_prefix_remains(self):
        import re
        pat = re.compile(r"\[(simple|compound|scope|temporal|exact|overlap|unanswerable|compare)", re.I)
        for q, c in self.nat.items():
            self.assertIsNone(pat.search(c["question"]), q)

    def test_metadata_is_unchanged(self):
        keys = ["query_type", "scope_type", "scope_user_ids", "answerable",
                "expected_user_ids", "expected_post_ids", "expected_fact_ids",
                "should_decompose", "expected_router_class", "forbidden_user_ids",
                "forbidden_post_ids", "required_fact_count", "scale_stability",
                "minimum_cohort", "minimum_user_count"]
        for q in self.orig:
            for k in keys:
                self.assertEqual(self.orig[q][k], self.nat[q][k], f"{q}.{k}")

    def test_original_wording_is_preserved_separately(self):
        for q, c in self.nat.items():
            self.assertEqual(c["question_template"], self.orig[q]["question"])

    def test_questions_are_non_empty(self):
        for q, c in self.nat.items():
            self.assertTrue(c["question"].strip(), q)

    def test_exact_tokens_survive_naturalization(self):
        import re
        pat = re.compile(r"\b(INC-\d+|QL-\d[A-Z]|WG-\d+|SKU-[0-9A-Za-z]+|batch-\d+|"
                         r"runbook-\d+|Model R\d+|Project Blue[a-z]+)\b")
        n = 0
        for q, c in self.nat.items():
            if c["query_type"] == "exact_token_bm25":
                self.assertEqual(set(pat.findall(self.orig[q]["question"])),
                                 set(pat.findall(c["question"])), q)
                n += 1
        self.assertEqual(n, 15)

    def test_compound_questions_stay_compound(self):
        for q, c in self.nat.items():
            if c["query_type"] == "compound_decomposition":
                self.assertTrue(c["should_decompose"], q)
                self.assertGreaterEqual(c["required_fact_count"], 2, q)

    def test_unanswerable_questions_name_no_evidence(self):
        for q, c in self.nat.items():
            if not c["answerable"]:
                self.assertEqual(c["expected_fact_ids"], [], q)
                self.assertEqual(c["expected_post_ids"], [], q)


class ChunkMeasurementTests(unittest.TestCase):
    def test_measurement_imports_the_production_chunker(self):
        src = read(os.path.join(HERE, "measure_rag_stress_chunks.py"))
        self.assertIn("from chunker import chunk_markdown", src)
        self.assertIn("ingest_worker", src)

    def test_measurement_does_not_reimplement_the_algorithm(self):
        src = read(os.path.join(HERE, "measure_rag_stress_chunks.py"))
        for banned in ("def _split_by_headers", "def _chunk_section", "HEADER_RE ="):
            self.assertNotIn(banned, src, banned)

    def test_measurement_makes_no_embedding_or_qdrant_call(self):
        src = read(os.path.join(HERE, "measure_rag_stress_chunks.py"))
        for banned in ("invoke_model", "boto3", "qdrant_client", "upsert", "SparseTextEmbedding"):
            self.assertNotIn(banned, src, banned)

    def test_uses_production_parameters(self):
        stats = load(os.path.join(GEN, "chunk_stats.json"))["stats"]["chunker"]
        self.assertEqual(stats["max_tokens"], 500)
        self.assertEqual(stats["overlap_tokens"], 50)
        self.assertFalse(stats["reimplemented"])


if __name__ == "__main__":
    unittest.main()
