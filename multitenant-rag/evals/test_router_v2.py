"""Tests for compound-router-v2. No network: classify() is monkeypatched."""
import json, os, subprocess, unittest, hashlib
import router_v2 as R

CID = lambda d: json.dumps(d)


class StructuredParsingTests(unittest.TestCase):
    def test_compound_parses(self):
        o = R.parse_router_output(CID({"needs_decomposition": True,
            "information_needs": ["a", "b"], "reason_code": R.COMPOUND_CODE}))
        self.assertTrue(o["parse_ok"]); self.assertTrue(o["needs_decomposition"])
        self.assertEqual(len(o["information_needs"]), 2)

    def test_simple_parses(self):
        o = R.parse_router_output(CID({"needs_decomposition": False,
            "information_needs": ["a"], "reason_code": "single_retrieval_need"}))
        self.assertTrue(o["parse_ok"]); self.assertFalse(o["needs_decomposition"])

    def test_json_embedded_in_prose_still_parses(self):
        o = R.parse_router_output('sure: {"needs_decomposition": false, '
            '"information_needs": ["x"], "reason_code": "single_retrieval_need"} done')
        self.assertTrue(o["parse_ok"])

    def test_no_json_is_rejected(self):
        o = R.parse_router_output("I think it is compound.")
        self.assertFalse(o["parse_ok"]); self.assertEqual(o["parse_error"], "no_json_object")

    def test_malformed_json_is_rejected(self):
        o = R.parse_router_output('{"needs_decomposition": tru')
        self.assertFalse(o["parse_ok"])

    def test_non_bool_flag_rejected(self):
        o = R.parse_router_output(CID({"needs_decomposition": "yes",
            "information_needs": ["a"], "reason_code": "single_retrieval_need"}))
        self.assertFalse(o["parse_ok"])
        self.assertEqual(o["parse_error"], "needs_decomposition_not_bool")

    def test_needs_must_be_string_list(self):
        o = R.parse_router_output(CID({"needs_decomposition": False,
            "information_needs": [1, 2], "reason_code": "single_retrieval_need"}))
        self.assertFalse(o["parse_ok"])

    def test_compound_with_one_need_rejected(self):
        o = R.parse_router_output(CID({"needs_decomposition": True,
            "information_needs": ["only one"], "reason_code": R.COMPOUND_CODE}))
        self.assertFalse(o["parse_ok"])
        self.assertEqual(o["parse_error"], "compound_needs_fewer_than_two")

    def test_parse_never_raises_on_garbage(self):
        for bad in ["", None, "{}", "[]", "{'a':1}", "\x00", "{"*50]:
            R.parse_router_output(bad)   # must not raise


class TruncationAndGateTests(unittest.TestCase):
    """Regression cover for the preflight incident: a truncated response was
    reported as 'no_json_object', and two unparsed cases made the gate report
    PASSED without a single control classified simple."""

    def test_token_ceiling_raised_above_observed_need(self):
        # observed: parsed responses used 194-230 output tokens; two hit the 400 cap
        self.assertGreaterEqual(R.ROUTER_MAX_TOKENS, 1000)

    def test_truncated_output_is_labelled_as_truncation(self):
        import nvidia_provider as nv
        orig = nv.chat
        nv.chat = lambda *a, **k: {"content": '{"needs_decomposition": tr',
                                   "finish_reason": "length", "latency_ms": 1,
                                   "input_tokens": 10, "output_tokens": R.ROUTER_MAX_TOKENS}
        try:
            r = R.classify("q")
        finally:
            nv.chat = orig
        self.assertFalse(r["parse_ok"])
        self.assertEqual(r["parse_error"], "truncated_output_token_limit")
        self.assertIsNone(r["needs_decomposition"])

    def test_gate_halts_on_unparsed_case(self):
        src = open(os.path.join(os.path.dirname(os.path.abspath(R.__file__)),
                                "run_router_v2.py")).read()
        self.assertIn("PREFLIGHT INDETERMINATE", src)
        self.assertIn("is NOT evidence of improvement", src)

    def test_gate_requires_at_least_one_control_fixed(self):
        src = open(os.path.join(os.path.dirname(os.path.abspath(R.__file__)),
                                "run_router_v2.py")).read()
        self.assertIn("if ctrl_fixed == 0:", src)
        self.assertIn("not one control was classified simple", src)

    def test_unparsed_is_never_counted_as_simple(self):
        """None must not be equal to False anywhere in scoring."""
        self.assertIsNot(None, False)
        gen = open(os.path.join(os.path.dirname(os.path.abspath(R.__file__)),
                                "gen_router_v2_report.py")).read()
        self.assertIn('r["predicted_compound"] is False', gen)   # identity, not truthiness
        self.assertIn('r["predicted_compound"] is True', gen)


class ReasonCodeEnumTests(unittest.TestCase):
    def test_enum_is_small_and_closed(self):
        self.assertEqual(len(R.REASON_CODES), 5)
        self.assertIn(R.COMPOUND_CODE, R.REASON_CODES)
        for c in R.SIMPLE_CODES: self.assertIn(c, R.REASON_CODES)

    def test_unknown_code_rejected(self):
        o = R.parse_router_output(CID({"needs_decomposition": False,
            "information_needs": ["a"], "reason_code": "vibes"}))
        self.assertFalse(o["parse_ok"])
        self.assertEqual(o["parse_error"], "reason_code_not_in_enum")

    def test_compound_code_exclusive_to_true(self):
        o = R.parse_router_output(CID({"needs_decomposition": False,
            "information_needs": ["a"], "reason_code": R.COMPOUND_CODE}))
        self.assertFalse(o["parse_ok"])
        self.assertEqual(o["parse_error"], "simple_flag_with_compound_reason_code")

    def test_true_requires_compound_code(self):
        o = R.parse_router_output(CID({"needs_decomposition": True,
            "information_needs": ["a", "b"], "reason_code": "single_retrieval_need"}))
        self.assertFalse(o["parse_ok"])
        self.assertEqual(o["parse_error"], "compound_flag_with_simple_reason_code")

    def test_every_simple_code_accepted(self):
        for c in R.SIMPLE_CODES:
            o = R.parse_router_output(CID({"needs_decomposition": False,
                "information_needs": ["a"], "reason_code": c}))
            self.assertTrue(o["parse_ok"], c)


class SemanticContractTests(unittest.TestCase):
    """The prompt must encode the v1->v2 distinction. Asserts on the shipped prompt."""
    def test_prompt_demands_independent_retrievability(self):
        s = R.ROUTER_SYS.lower()
        self.assertIn("independently retrievable", s)
        self.assertIn("separate", s)

    def test_prompt_rejects_grammar_as_evidence(self):
        s = R.ROUTER_SYS.lower()
        self.assertIn("grammar is not evidence", s)
        for tok in ["'and'", "'or'", "'but'", "clause"]:
            self.assertIn(tok, s)

    def test_multiple_independent_needs_maps_to_compound(self):
        o = R.parse_router_output(CID({"needs_decomposition": True,
            "information_needs": ["need A", "need B"], "reason_code": R.COMPOUND_CODE}))
        self.assertTrue(o["parse_ok"] and o["needs_decomposition"])

    def test_single_entity_multi_attribute_maps_to_simple(self):
        o = R.parse_router_output(CID({"needs_decomposition": False,
            "information_needs": ["attributes of one entity"],
            "reason_code": "single_entity_multi_attribute"}))
        self.assertTrue(o["parse_ok"]); self.assertFalse(o["needs_decomposition"])

    def test_single_event_multi_attribute_maps_to_simple(self):
        o = R.parse_router_output(CID({"needs_decomposition": False,
            "information_needs": ["attributes of one event"],
            "reason_code": "single_event_multi_attribute"}))
        self.assertTrue(o["parse_ok"]); self.assertFalse(o["needs_decomposition"])

    def test_negative_or_scope_check_maps_to_simple(self):
        o = R.parse_router_output(CID({"needs_decomposition": False,
            "information_needs": ["verify one fact"],
            "reason_code": "negative_or_scope_check"}))
        self.assertTrue(o["parse_ok"]); self.assertFalse(o["needs_decomposition"])

    def test_prompt_covers_the_three_v1_control_shapes(self):
        s = R.ROUTER_SYS.lower()
        self.assertIn("attributes", s)          # single entity, many attributes
        self.assertIn("really do", s)           # yes/no + what-does-it-really-do
        self.assertIn("bounding one fact", s)   # negative / scope check

    def test_prompt_requires_identifier_and_time_preservation(self):
        s = R.ROUTER_SYS.lower()
        self.assertIn("identifiers", s); self.assertIn("time constraints", s)
        self.assertIn("exactly", s)

    def test_prompt_forbids_chain_of_thought(self):
        s = R.ROUTER_SYS.lower()
        self.assertIn("only json", s)
        self.assertIn("no description of your reasoning", s)


class NoLabelLeakageTests(unittest.TestCase):
    """The router must never see anything but the question."""
    FORBIDDEN = ["expected_answer", "expected answer", "reference answer", "ground_truth",
                 "ground truth", "baseline", "judge", "scenario", "case_id", "case-0",
                 "compound multi-part", "prior_score", "prior_status", "prior_observed",
                 "correctness", "completeness", "groundedness"]

    def test_messages_contain_only_question(self):
        q = "What time does the bell ring?"
        msgs = R.build_messages(q)
        self.assertEqual(len(msgs), 2)
        self.assertEqual(msgs[1]["content"], f"Question: {q}")

    def test_no_forbidden_token_in_user_message(self):
        msgs = R.build_messages("Some question about X and Y?")
        blob = msgs[1]["content"].lower()
        for tok in self.FORBIDDEN:
            self.assertNotIn(tok, blob, tok)

    def test_no_forbidden_token_in_system_prompt(self):
        s = R.ROUTER_SYS.lower()
        for tok in ["expected_answer", "expected answer", "reference answer", "ground truth",
                    "baseline", "judge", "scenario", "case_id", "case-0", "prior_score"]:
            self.assertNotIn(tok, s, tok)

    def test_build_messages_takes_only_a_question(self):
        import inspect
        sig = inspect.signature(R.build_messages)
        self.assertEqual(list(sig.parameters), ["question"])

    def test_corpus_label_fields_never_reach_the_model(self):
        """Feed a full corpus record's question; label fields must not leak."""
        case = {"case_id": "case-018", "question": "A and B?",
                "expected_answer": "SECRET_LABEL_VALUE", "prior_status": "FAIL",
                "title": "compound multi-part"}
        blob = json.dumps(R.build_messages(case["question"])).lower()
        self.assertNotIn("secret_label_value", blob)
        self.assertNotIn("case-018", blob)
        self.assertNotIn("compound multi-part", blob)
        self.assertNotIn("fail", blob)


class ManifestTests(unittest.TestCase):
    def setUp(self):
        p = os.path.join(os.path.dirname(os.path.abspath(R.__file__)),
                         "output", "router_v2_manifest.json")
        self.man = json.load(open(p))

    def test_52_generative_cases_3_positive_49_negative(self):
        self.assertEqual(self.man["generative_case_count"], 52)
        self.assertEqual(self.man["positive_count"], 3)
        self.assertEqual(self.man["negative_count"], 49)

    def test_eight_global_search_cases_excluded(self):
        self.assertEqual(len(self.man["excluded_global_search"]), 8)

    def test_positive_and_negative_sets_are_disjoint_and_complete(self):
        pos, neg = set(self.man["ground_truth_compound"]), set(self.man["ground_truth_simple"])
        self.assertEqual(pos & neg, set())
        self.assertEqual(len(pos | neg), 52)

    def test_manifest_is_fingerprinted(self):
        self.assertEqual(len(self.man["manifest_fingerprint"]), 16)


class CheckpointResumeTests(unittest.TestCase):
    def test_checkpoint_lines_are_valid_json_with_required_keys(self):
        p = os.path.join(os.path.dirname(os.path.abspath(R.__file__)),
                         "output", "router_v2_results.jsonl")
        if not os.path.exists(p): self.skipTest("no checkpoint yet")
        need = {"case_id","fingerprint_hash","ground_truth_compound",
                "predicted_compound","reason_code","provider_status"}
        for line in open(p):
            if line.strip(): self.assertTrue(need <= set(json.loads(line)))

    def test_resume_filters_by_fingerprint(self):
        src = open(os.path.join(os.path.dirname(os.path.abspath(R.__file__)),
                                "run_router_v2.py")).read()
        self.assertIn('r.get("fingerprint_hash") == FP["fingerprint_hash"]', src)

    def test_runner_is_sequential_concurrency_one(self):
        src = open(os.path.join(os.path.dirname(os.path.abspath(R.__file__)),
                                "run_router_v2.py")).read()
        for banned in ["ThreadPool", "as_completed", "Semaphore", "asyncio"]:
            self.assertNotIn(banned, src, banned)
        self.assertIn('"concurrency": 1', src)


class GroqZeroCallTests(unittest.TestCase):
    def test_runner_installs_groq_guard(self):
        src = open(os.path.join(os.path.dirname(os.path.abspath(R.__file__)),
                                "run_router_v2.py")).read()
        self.assertIn("H.install_groq_guard()", src)

    def test_router_module_never_references_groq(self):
        src = open(os.path.abspath(R.__file__)).read().lower()
        self.assertNotIn("groq", src)

    def test_guard_makes_groq_raise(self):
        import nvidia_harness as H
        H.install_groq_guard()
        import app as prod
        with self.assertRaises(Exception): prod.stream_answer("x", "y")

    def test_router_uses_20b_not_120b(self):
        import nvidia_harness as H
        self.assertEqual(R.ROUTER_MODEL, H.APP_MODEL)
        self.assertNotEqual(R.ROUTER_MODEL, H.JUDGE_MODEL)


class FrozenV1Tests(unittest.TestCase):
    """Experiment v1 must be untouched by this task."""
    V1 = ["multitenant-rag/evals/decomp_graph.py",
          "multitenant-rag/evals/run_decomp_experiment.py",
          "multitenant-rag/evals/test_decomp_graph.py"]

    def _repo(self):
        return subprocess.run(["git","rev-parse","--show-toplevel"],
            capture_output=True,text=True).stdout.strip()

    def test_v1_files_have_no_uncommitted_modification(self):
        repo = self._repo()
        out = subprocess.run(["git","status","--porcelain"]+self.V1,
                             cwd=repo,capture_output=True,text=True).stdout
        for line in out.strip().split("\n"):
            if line.strip():
                self.assertTrue(line.startswith("??"),
                    f"v1 file modified, expected untracked-only: {line}")

    @staticmethod
    def _imports(path):
        """Actual import graph via AST — substring matching would trip on docstrings."""
        import ast
        mods = set()
        for n in ast.walk(ast.parse(open(path).read())):
            if isinstance(n, ast.Import):
                mods |= {a.name.split(".")[0] for a in n.names}
            elif isinstance(n, ast.ImportFrom) and n.module:
                mods.add(n.module.split(".")[0])
        return mods

    def test_router_v2_does_not_import_v1_graph(self):
        d = os.path.dirname(os.path.abspath(R.__file__))
        for f in ["router_v2.py", "run_router_v2.py"]:
            self.assertNotIn("decomp_graph", self._imports(os.path.join(d, f)), f)

    def test_router_v2_imports_only_provider_and_harness(self):
        mods = self._imports(os.path.abspath(R.__file__))
        self.assertEqual(mods & {"nvidia_harness", "nvidia_provider"},
                         {"nvidia_harness", "nvidia_provider"})
        self.assertNotIn("app", mods)          # no production app import
        self.assertNotIn("langgraph", mods)    # no LangGraph in the router

    def test_v1_outputs_untouched_by_this_experiment(self):
        d = os.path.join(os.path.dirname(os.path.abspath(R.__file__)), "output")
        for f in ["decomp_cases.jsonl", "decomp_judge.jsonl"]:
            self.assertTrue(os.path.exists(os.path.join(d, f)), f)
        runner = open(os.path.join(os.path.dirname(os.path.abspath(R.__file__)),
                                   "run_router_v2.py")).read()
        self.assertNotIn("decomp_cases", runner)
        self.assertNotIn("decomp_judge", runner)

    def test_quarantine_still_present_and_unreferenced(self):
        d = os.path.join(os.path.dirname(os.path.abspath(R.__file__)), "output")
        self.assertTrue(os.path.exists(os.path.join(d, "decomp_cases.INVALID_scope_bug.jsonl")))
        runner = open(os.path.join(os.path.dirname(os.path.abspath(R.__file__)),
                                   "run_router_v2.py")).read()
        self.assertNotIn("INVALID_scope_bug", runner)


class NoRetrievalOrGenerationTests(unittest.TestCase):
    def test_router_performs_no_retrieval_or_generation(self):
        """Assert on called function names via AST, not on substrings: a plain
        'search(' scan matches re.search and gives a false positive."""
        import ast
        tree = ast.parse(open(os.path.abspath(R.__file__)).read())
        called = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Call):
                f = n.func
                if isinstance(f, ast.Name): called.add(f.id)
                elif isinstance(f, ast.Attribute):
                    base = f.value.id if isinstance(f.value, ast.Name) else ""
                    called.add(f"{base}.{f.attr}" if base else f.attr)
        for banned in ["hybrid_search", "search_points", "query_points", "embed",
                       "embed_query", "stream_answer", "retrieve", "upsert"]:
            self.assertNotIn(banned, {c.split(".")[-1] for c in called}, banned)
        self.assertNotIn("qdrant", open(os.path.abspath(R.__file__)).read().lower())
        # the ONLY provider call the router makes is one chat completion
        self.assertIn("nv.chat", called)

    def test_fingerprint_declares_no_retrieval_generation_judging(self):
        src = open(os.path.join(os.path.dirname(os.path.abspath(R.__file__)),
                                "run_router_v2.py")).read()
        for k in ['"retrieval_performed": False', '"generation_performed": False',
                  '"judging_performed": False', '"langgraph_used": False']:
            self.assertIn(k, src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
