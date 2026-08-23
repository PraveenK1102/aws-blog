"""Tests for router-v4 (atomic evidence units) and frozen-holdout integrity. No network."""
import ast, json, os, re, subprocess, hashlib, unicodedata, unittest
import router_v4 as R

HERE=os.path.dirname(os.path.abspath(__file__)); OUTD=os.path.join(HERE,"output")
OUT="/Users/praveen-16349/Documents/Personal/Learnings/AWS - Blog"
HOLDOUT_SHA="0957b7bf8db137bad6e35f1495f5953e636e87969b5173b0fd9101a1dfd233b8"
U=lambda a,f,q: {"anchor":a,"facts_needed":f,"retrieval_query":q}
def P(units, flag, code, rationale=None):
    d={"evidence_units":units,"needs_decomposition":flag,"reason_code":code}
    if rationale is not None: d["rationale"]=rationale
    return json.dumps(d)
def imports_of(p):
    m=set()
    for n in ast.walk(ast.parse(open(p).read())):
        if isinstance(n,ast.Import): m|={a.name.split(".")[0] for a in n.names}
        elif isinstance(n,ast.ImportFrom) and n.module: m.add(n.module.split(".")[0])
    return m
holdout=lambda: [json.loads(l) for l in open(f"{OUT}/compound-router-holdout-v1.jsonl",encoding="utf-8")]


class EvidenceUnitParsingTests(unittest.TestCase):
    def test_one_unit_simple_parses(self):
        o=R.parse_router_output(P([U("QL-2D enclosure",["height","lever effect"],"QL-2D enclosure height lever")],
                                  False,"one_entity_multi_attribute"))
        self.assertTrue(o["parse_ok"]); self.assertFalse(o["needs_decomposition"])
        self.assertEqual(o["evidence_unit_count"],1)

    def test_two_units_compound_parses(self):
        o=R.parse_router_output(P([U("A",["x"],"qa"),U("B",["y"],"qb")],True,R.COMPOUND_CODE))
        self.assertTrue(o["parse_ok"]); self.assertTrue(o["needs_decomposition"])
        self.assertEqual(o["evidence_unit_count"],2)

    def test_three_units_allowed(self):
        o=R.parse_router_output(P([U("A",["x"],"qa"),U("B",["y"],"qb"),U("C",["z"],"qc")],
                                  True,R.COMPOUND_CODE))
        self.assertTrue(o["parse_ok"]); self.assertEqual(o["evidence_unit_count"],3)

    def test_four_units_rejected(self):
        o=R.parse_router_output(P([U(c,["x"],"q") for c in "ABCD"],True,R.COMPOUND_CODE))
        self.assertFalse(o["parse_ok"]); self.assertEqual(o["parse_error"],"too_many_evidence_units")

    def test_zero_units_rejected(self):
        o=R.parse_router_output(P([],False,"single_evidence_neighborhood"))
        self.assertEqual(o["parse_error"],"evidence_units_empty")

    def test_unit_requires_anchor(self):
        for bad in [{"facts_needed":["x"],"retrieval_query":"q"},
                    U("",["x"],"q"), U("   ",["x"],"q")]:
            o=R.parse_router_output(P([bad],False,"single_evidence_neighborhood"))
            self.assertEqual(o["parse_error"],"unit_anchor_missing")

    def test_unit_requires_non_empty_facts(self):
        o=R.parse_router_output(P([U("a",[],"q")],False,"single_evidence_neighborhood"))
        self.assertEqual(o["parse_error"],"unit_facts_needed_empty")
        o2=R.parse_router_output(P([U("a",["",""],"q")],False,"single_evidence_neighborhood"))
        self.assertEqual(o2["parse_error"],"unit_facts_needed_invalid")

    def test_unit_requires_non_empty_query(self):
        for bad in [U("a",["x"],""), U("a",["x"],"   "), {"anchor":"a","facts_needed":["x"]}]:
            o=R.parse_router_output(P([bad],False,"single_evidence_neighborhood"))
            self.assertEqual(o["parse_error"],"unit_retrieval_query_empty")

    def test_unit_must_be_object(self):
        o=R.parse_router_output(P(["just a string"],False,"single_evidence_neighborhood"))
        self.assertEqual(o["parse_error"],"unit_not_object")

    def test_no_json_and_garbage_never_raise(self):
        self.assertEqual(R.parse_router_output("two searches needed")["parse_error"],"no_json_object")
        for bad in ["",None,"{}","[]","{"*40,"\x00"]: R.parse_router_output(bad)

    def test_rationale_optional_and_capped(self):
        o=R.parse_router_output(P([U("a",["x"],"q")],False,"single_evidence_neighborhood","y"*400))
        self.assertTrue(o["parse_ok"]); self.assertLessEqual(len(o["rationale"]),200)
        o2=R.parse_router_output(P([U("a",["x"],"q")],False,"single_evidence_neighborhood"))
        self.assertTrue(o2["parse_ok"]); self.assertIsNone(o2["rationale"])

    def test_truncation_labelled(self):
        import nvidia_provider as nv
        orig=nv.chat
        nv.chat=lambda *a,**k:{"content":'{"evidence_units": [{"anch',"finish_reason":"length",
                               "latency_ms":1,"input_tokens":5,"output_tokens":R.ROUTER_MAX_TOKENS}
        try: r=R.classify("q")
        finally: nv.chat=orig
        self.assertEqual(r["parse_error"],"truncated_output_token_limit")


class UnitCountInvariantTests(unittest.TestCase):
    def test_flag_must_equal_unit_count_ge_2(self):
        a=R.parse_router_output(P([U("a",["x"],"q")],True,R.COMPOUND_CODE))
        self.assertEqual(a["parse_error"],"unit_count_flag_invariant_violated")
        b=R.parse_router_output(P([U("a",["x"],"qa"),U("b",["y"],"qb")],False,"single_evidence_neighborhood"))
        self.assertEqual(b["parse_error"],"unit_count_flag_invariant_violated")

    def test_invariant_holds_for_all_accepted_outputs(self):
        for n,flag,code in [(1,False,"single_evidence_neighborhood"),(2,True,R.COMPOUND_CODE),
                            (3,True,R.COMPOUND_CODE)]:
            o=R.parse_router_output(P([U(f"a{i}",["x"],f"q{i}") for i in range(n)],flag,code))
            self.assertTrue(o["parse_ok"])
            self.assertEqual(o["needs_decomposition"], o["evidence_unit_count"]>=2)

    def test_compound_code_exclusive_to_true(self):
        o=R.parse_router_output(P([U("a",["x"],"q")],False,R.COMPOUND_CODE))
        self.assertEqual(o["parse_error"],"simple_flag_with_compound_reason_code")

    def test_true_requires_compound_code(self):
        o=R.parse_router_output(P([U("a",["x"],"qa"),U("b",["y"],"qb")],True,"one_entity_multi_attribute"))
        self.assertEqual(o["parse_error"],"compound_flag_with_simple_reason_code")

    def test_unknown_code_rejected(self):
        o=R.parse_router_output(P([U("a",["x"],"q")],False,"nope"))
        self.assertEqual(o["parse_error"],"reason_code_not_in_enum")


class AtomicityTests(unittest.TestCase):
    """The v3 loophole must be closed in the PARSER, not only the prompt."""
    def test_or_stuffing_rejected(self):
        o=R.parse_router_output(P([U("MS-E series",["v1","v2","v3"],
            "MS-E1 seeds per tray OR MS-E2 seeds per tray OR MS-E3 seeds per tray")],
            False,"single_evidence_neighborhood"))
        self.assertFalse(o["parse_ok"]); self.assertEqual(o["parse_error"],"unit_boolean_stuffing")

    def test_boolean_and_pipe_amp_rejected(self):
        for q in ["a AND b","a || b","a && b","x OR y"]:
            o=R.parse_router_output(P([U("a",["x"],q)],False,"single_evidence_neighborhood"))
            self.assertEqual(o["parse_error"],"unit_boolean_stuffing",q)

    def test_natural_lowercase_or_not_penalised(self):
        """Ordinary prose should not trip the boolean guard."""
        o=R.parse_router_output(P([U("bell",["time"],"Morrow Bell ringing time or schedule")],
                                  False,"single_evidence_neighborhood"))
        self.assertTrue(o["parse_ok"])

    def test_boolean_stuffing_rejected_in_any_unit(self):
        o=R.parse_router_output(P([U("a",["x"],"clean query"),U("b",["y"],"p OR q")],
                                  True,R.COMPOUND_CODE))
        self.assertEqual(o["parse_error"],"unit_boolean_stuffing")

    def test_multiple_facts_in_one_unit_is_legitimate(self):
        o=R.parse_router_output(P([U("one event",["cause","records recovered","downtime"],
            "Ternlink outage North Fen cause and recovery")],False,"one_event_multi_consequence"))
        self.assertTrue(o["parse_ok"]); self.assertEqual(o["evidence_unit_count"],1)


class PromptContractTests(unittest.TestCase):
    def test_prompt_defines_evidence_unit_by_retrieval_locality(self):
        s=R.ROUTER_SYS.lower()
        self.assertIn("evidence unit", s)
        self.assertIn("retrieval locality", s)
        self.assertIn("localized evidence neighbourhood", s)

    def test_prompt_states_the_unit_count_invariant(self):
        self.assertIn("needs_decomposition MUST equal (number of evidence units >= 2)", R.ROUTER_SYS)

    def test_prompt_forbids_boolean_stuffing_inside_a_unit(self):
        s=R.ROUTER_SYS.lower()
        self.assertIn("never combine independent targets", s)
        for t in ["or, and, ||","commas","slashes","lists"]: self.assertIn(t, s)

    def test_prompt_names_one_unit_shapes(self):
        s=R.ROUTER_SYS.lower()
        for t in ["several attributes of one localized object",
                  "several consequences of one event",
                  "same subject",
                  "same attribute compared across time",
                  "summary or synthesis over a related series",
                  "scope, applicability or did-it-happen-at-all check"]:
            self.assertIn(t, s, t)

    def test_prompt_contrast_rule_requires_shared_neighbourhood(self):
        s=R.ROUTER_SYS.lower()
        self.assertIn("a contrast is one unit only when both sides belong to the same", s)
        self.assertIn("one unit per side", s)
        self.assertIn("does not prove shared retrieval locality", s)

    def test_prompt_sibling_rule_present(self):
        s=R.ROUTER_SYS.lower()
        self.assertIn("independently sourced value from each", s)
        self.assertIn("own unit", s)

    def test_prompt_warns_against_entity_counting_both_ways(self):
        s=R.ROUTER_SYS.lower()
        self.assertIn("not counting names", s)
        self.assertIn("naming two things does not by itself require two units", s)

    def test_prompt_asks_for_minimum_units(self):
        self.assertIn("MINIMUM number of evidence units", R.ROUTER_SYS)
        self.assertIn("fewest that are legitimate", R.ROUTER_SYS.lower())

    def test_prompt_forbids_chain_of_thought_and_long_output(self):
        s=R.ROUTER_SYS.lower()
        self.assertIn("only json", s); self.assertIn("no description of your reasoning", s)
        self.assertIn("keep anchor, facts_needed and retrieval_query short", s)
        self.assertIn("one short rationale sentence", s)

    def test_prompt_requires_identifier_and_time_preservation(self):
        s=R.ROUTER_SYS.lower()
        for t in ["identifiers","time constraints","exactly"]: self.assertIn(t, s)


class NoLeakageTests(unittest.TestCase):
    FORBIDDEN=["expected_answer","expected answer","reference answer","ground_truth","ground truth",
               "baseline","judge","scenario","case_id","case-0","hold-0","prior_score","correctness",
               "reference_retrieval_queries","category","route"]

    def test_messages_contain_only_the_question(self):
        q="What is X and Y?"
        m=R.build_messages(q)
        self.assertEqual(len(m),2); self.assertEqual(m[1]["content"],f"Question: {q}")

    def test_signature_takes_only_question(self):
        import inspect
        self.assertEqual(list(inspect.signature(R.build_messages).parameters),["question"])

    def test_no_forbidden_token_in_user_message(self):
        blob=R.build_messages("Some question?")[1]["content"].lower()
        for t in self.FORBIDDEN: self.assertNotIn(t, blob, t)

    def test_no_forbidden_token_in_system_prompt(self):
        s=R.ROUTER_SYS.lower()
        for t in ["expected_answer","expected answer","ground truth","baseline","judge","scenario",
                  "case_id","case-0","hold-0","reference_retrieval_queries"]:
            self.assertNotIn(t, s, t)

    def test_no_route_label_leakage(self):
        self.assertNotIn("route", R.ROUTER_SYS.lower())
        self.assertNotIn('"route"', open(os.path.join(HERE,"run_router_v4.py")).read())

    def test_no_diagnostic_case_ids_in_prompt_or_router_module(self):
        """Diagnostic cases must not be special-cased in code or prompt."""
        src=open(os.path.abspath(R.__file__)).read()
        for cid in ["case-030","case-041","case-002","case-004","case-056","case-059","case-003"]:
            self.assertNotIn(cid, R.ROUTER_SYS, cid)
        # the module may mention them only in the explanatory docstring, never in logic
        logic=src.split('"""',2)[-1]
        for cid in ["case-030","case-041"]:
            self.assertNotIn(cid, logic, f"{cid} appears in v4 logic")

    def test_holdout_labels_never_reach_the_model(self):
        h=holdout()[0]
        blob=json.dumps(R.build_messages(h["question"])).lower()
        self.assertNotIn(h["ground_truth"], blob)
        self.assertNotIn(h["category"].lower(), blob)
        self.assertNotIn(h["holdout_case_id"].lower(), blob)


class FrozenHoldoutIntegrityTests(unittest.TestCase):
    def test_frozen_holdout_hash_unchanged(self):
        live=hashlib.sha256(json.dumps(holdout(),sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        self.assertEqual(live, HOLDOUT_SHA)
        meta=json.load(open(os.path.join(OUTD,"router_holdout_v1_meta.json")))
        self.assertEqual(meta["manifest_sha256"], HOLDOUT_SHA)

    def test_holdout_still_40_cases_20_20_zero_ambiguous(self):
        h=holdout()
        self.assertEqual(len(h),40)
        self.assertEqual(sum(1 for r in h if r["ground_truth"]=="simple"),20)
        self.assertEqual(sum(1 for r in h if r["ground_truth"]=="compound"),20)
        self.assertEqual(sum(1 for r in h if r["ground_truth"]=="ambiguous"),0)

    def test_runner_verifies_hash_before_using_holdout(self):
        src=open(os.path.join(HERE,"run_router_v4.py")).read()
        self.assertIn("HOLDOUT_SHA", src)
        self.assertIn("frozen holdout hash mismatch", src)
        self.assertIn("REFUSING to run the holdout", src)

    def test_runner_never_writes_the_holdout_manifest(self):
        tree=ast.parse(open(os.path.join(HERE,"run_router_v4.py")).read())
        for n in ast.walk(tree):
            if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=="open":
                mode=n.args[1].value if len(n.args)>1 and isinstance(n.args[1],ast.Constant) else "r"
                path=ast.unparse(n.args[0]) if n.args else ""
                if any(k in path for k in ("holdout-v1","groundtruth-v2")):
                    self.assertEqual(mode,"r", f"frozen manifest opened mode {mode!r}")

    def test_no_provider_import_in_holdout_manifest_builder(self):
        mods=imports_of(os.path.join(HERE,"build_holdout_v1.py"))
        for banned in ["nvidia_provider","nvidia_harness","router_v4","router_v3","app","boto3"]:
            self.assertNotIn(banned, mods, banned)

    def test_dev_and_holdout_sets_remain_disjoint(self):
        norm=lambda q: re.sub(r"\s+"," ",unicodedata.normalize("NFKC",q).strip())
        dev={norm(json.loads(l)["question"]) for l in open(f"{OUT}/compound-router-groundtruth-v2.jsonl",encoding="utf-8")}
        for r in holdout(): self.assertNotIn(norm(r["question"]), dev, r["holdout_case_id"])


class RunnerTests(unittest.TestCase):
    def setUp(self): self.src=open(os.path.join(HERE,"run_router_v4.py")).read()

    def test_concurrency_one(self):
        for b in ["ThreadPool","as_completed","Semaphore","asyncio"]: self.assertNotIn(b,self.src,b)
        self.assertIn('"concurrency": 1', self.src)

    def test_groq_guard_and_no_groq_reference(self):
        self.assertIn("H.install_groq_guard()", self.src)
        self.assertNotIn("groq", open(os.path.abspath(R.__file__)).read().lower())

    def test_uses_20b_not_120b(self):
        import nvidia_harness as H2
        self.assertEqual(R.ROUTER_MODEL,H2.APP_MODEL)
        self.assertNotEqual(R.ROUTER_MODEL,H2.JUDGE_MODEL)

    def test_checkpoint_resume_and_fsync(self):
        self.assertIn('r.get("fingerprint_hash") == FP["fingerprint_hash"]', self.src)
        self.assertIn("os.fsync", self.src)

    def test_dev_gate_thresholds_unrelaxed(self):
        for t in ['"recall": 0.90','"specificity": 0.90','"precision": 0.80']:
            self.assertIn(t, self.src, t)
        self.assertIn("ROUTER V4 DEVELOPMENT FAILURE", self.src)

    def test_no_retrieval_generation_or_judging(self):
        called=set()
        for n in ast.walk(ast.parse(open(os.path.abspath(R.__file__)).read())):
            if isinstance(n,ast.Call):
                f=n.func
                if isinstance(f,ast.Attribute): called.add(f.attr)
                elif isinstance(f,ast.Name): called.add(f.id)
        for b in ["hybrid_search","query_points","embed","stream_answer","retrieve","judge"]:
            self.assertNotIn(b, called, b)
        self.assertIn("chat", called)
        for k in ['"retrieval_performed": False','"generation_performed": False',
                  '"judging_performed": False','"langgraph_used": False']:
            self.assertIn(k, self.src)

    def test_v4_isolated_from_prior_routers_and_graph(self):
        for f in ["router_v4.py","run_router_v4.py"]:
            mods=imports_of(os.path.join(HERE,f))
            for banned in ["router_v2","router_v3","decomp_graph"]:
                self.assertNotIn(banned, mods, f"{f} imports {banned}")


class PriorWorkFrozenTests(unittest.TestCase):
    FROZEN=["multitenant-rag/evals/router_v2.py","multitenant-rag/evals/router_v3.py",
            "multitenant-rag/evals/run_router_v3.py","multitenant-rag/evals/decomp_graph.py",
            "multitenant-rag/evals/holdout_v1_cases.py","multitenant-rag/evals/build_holdout_v1.py"]

    def test_prior_router_sources_unchanged(self):
        repo=subprocess.run(["git","rev-parse","--show-toplevel"],capture_output=True,text=True).stdout.strip()
        out=subprocess.run(["git","status","--porcelain"]+self.FROZEN,cwd=repo,
                           capture_output=True,text=True).stdout
        for line in out.strip().split("\n"):
            if line.strip(): self.assertTrue(line.startswith("??"), f"frozen file modified: {line}")

    def test_prior_results_intact(self):
        self.assertEqual(len([l for l in open(f"{OUT}/compound-router-groundtruth-v2.jsonl")]),52)
        for f,n in [("router_v3_dev_results.jsonl",52),("decomp_cases.jsonl",6)]:
            self.assertEqual(len([l for l in open(os.path.join(OUTD,f))]),n,f)

    def test_v3_holdout_still_never_run(self):
        self.assertFalse(os.path.exists(os.path.join(OUTD,"router_v3_holdout_results.jsonl")))


if __name__=="__main__":
    unittest.main(verbosity=2)
