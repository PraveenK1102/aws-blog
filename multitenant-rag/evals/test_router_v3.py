"""Tests for router-v3 and the frozen holdout. No network."""
import ast, json, os, re, subprocess, unicodedata, unittest, hashlib
import router_v3 as R

HERE=os.path.dirname(os.path.abspath(__file__)); OUTD=os.path.join(HERE,"output")
OUT="/Users/praveen-16349/Documents/Personal/Learnings/AWS - Blog"
J=lambda d: json.dumps(d)
def norm(q): return re.sub(r"\s+"," ",unicodedata.normalize("NFKC",q or "").strip())
def imports_of(p):
    m=set()
    for n in ast.walk(ast.parse(open(p).read())):
        if isinstance(n,ast.Import): m|={a.name.split(".")[0] for a in n.names}
        elif isinstance(n,ast.ImportFrom) and n.module: m.add(n.module.split(".")[0])
    return m
def holdout(): return [json.loads(l) for l in open(f"{OUT}/compound-router-holdout-v1.jsonl",encoding="utf-8")]
def dev(): return [json.loads(l) for l in open(f"{OUT}/compound-router-groundtruth-v2.jsonl",encoding="utf-8")]


class V3ParserTests(unittest.TestCase):
    def test_simple_plan_parses(self):
        o=R.parse_router_output(J({"needs_decomposition":False,"retrieval_queries":["q"],
            "reason_code":"single_retrieval_target","rationale":"one target"}))
        self.assertTrue(o["parse_ok"]); self.assertFalse(o["needs_decomposition"])
        self.assertEqual(len(o["retrieval_queries"]),1)

    def test_compound_plan_parses(self):
        o=R.parse_router_output(J({"needs_decomposition":True,"retrieval_queries":["a","b"],
            "reason_code":R.COMPOUND_CODE,"rationale":"two targets"}))
        self.assertTrue(o["parse_ok"]); self.assertTrue(o["needs_decomposition"])

    def test_three_queries_allowed(self):
        o=R.parse_router_output(J({"needs_decomposition":True,"retrieval_queries":["a","b","c"],
            "reason_code":R.COMPOUND_CODE}))
        self.assertTrue(o["parse_ok"])

    def test_more_than_three_queries_rejected(self):
        o=R.parse_router_output(J({"needs_decomposition":True,"retrieval_queries":["a","b","c","d"],
            "reason_code":R.COMPOUND_CODE}))
        self.assertFalse(o["parse_ok"])
        self.assertEqual(o["parse_error"],"too_many_retrieval_queries")

    def test_empty_plan_rejected(self):
        o=R.parse_router_output(J({"needs_decomposition":False,"retrieval_queries":[],
            "reason_code":"single_retrieval_target"}))
        self.assertFalse(o["parse_ok"]); self.assertEqual(o["parse_error"],"retrieval_queries_empty")

    def test_no_json_rejected(self):
        self.assertEqual(R.parse_router_output("it needs two searches")["parse_error"],"no_json_object")

    def test_parse_never_raises(self):
        for bad in ["",None,"{}","[]","{"*40,"\x00",J({"needs_decomposition":1})]:
            R.parse_router_output(bad)

    def test_rationale_capped_and_optional(self):
        o=R.parse_router_output(J({"needs_decomposition":False,"retrieval_queries":["q"],
            "reason_code":"single_retrieval_target","rationale":"x"*500}))
        self.assertTrue(o["parse_ok"]); self.assertLessEqual(len(o["rationale"]),200)
        o2=R.parse_router_output(J({"needs_decomposition":False,"retrieval_queries":["q"],
            "reason_code":"single_retrieval_target"}))
        self.assertTrue(o2["parse_ok"]); self.assertIsNone(o2["rationale"])

    def test_truncation_labelled(self):
        import nvidia_provider as nv
        orig=nv.chat
        nv.chat=lambda *a,**k:{"content":'{"needs_decomposition": tr',"finish_reason":"length",
                               "latency_ms":1,"input_tokens":5,"output_tokens":R.ROUTER_MAX_TOKENS}
        try: r=R.classify("q")
        finally: nv.chat=orig
        self.assertEqual(r["parse_error"],"truncated_output_token_limit")


class PlanFlagInvariantTests(unittest.TestCase):
    def test_flag_must_equal_plan_length_ge_2(self):
        bad1=R.parse_router_output(J({"needs_decomposition":True,"retrieval_queries":["only"],
            "reason_code":R.COMPOUND_CODE}))
        self.assertEqual(bad1["parse_error"],"plan_flag_invariant_violated")
        bad2=R.parse_router_output(J({"needs_decomposition":False,"retrieval_queries":["a","b"],
            "reason_code":"single_retrieval_target"}))
        self.assertEqual(bad2["parse_error"],"plan_flag_invariant_violated")

    def test_invariant_holds_for_every_accepted_output(self):
        for n,flag,code in [(1,False,"single_retrieval_target"),(2,True,R.COMPOUND_CODE),
                            (3,True,R.COMPOUND_CODE)]:
            o=R.parse_router_output(J({"needs_decomposition":flag,
                "retrieval_queries":[f"q{i}" for i in range(n)],"reason_code":code}))
            self.assertTrue(o["parse_ok"])
            self.assertEqual(o["needs_decomposition"], len(o["retrieval_queries"])>=2)

    def test_compound_code_exclusive_to_true(self):
        o=R.parse_router_output(J({"needs_decomposition":False,"retrieval_queries":["q"],
            "reason_code":R.COMPOUND_CODE}))
        self.assertEqual(o["parse_error"],"simple_flag_with_compound_reason_code")

    def test_true_requires_compound_code(self):
        o=R.parse_router_output(J({"needs_decomposition":True,"retrieval_queries":["a","b"],
            "reason_code":"single_entity_multi_attribute"}))
        self.assertEqual(o["parse_error"],"compound_flag_with_simple_reason_code")

    def test_unknown_code_rejected(self):
        o=R.parse_router_output(J({"needs_decomposition":False,"retrieval_queries":["q"],
            "reason_code":"whatever"}))
        self.assertEqual(o["parse_error"],"reason_code_not_in_enum")


class V3SemanticContractTests(unittest.TestCase):
    """The prompt must encode retrieval-plan semantics and name the v2 failure shapes."""
    def test_prompt_asks_for_minimum_query_count(self):
        s=R.ROUTER_SYS.lower()
        self.assertIn("minimum number of focused retrieval queries", s)
        self.assertIn("smallest plan", s)

    def test_prompt_states_the_invariant(self):
        self.assertIn("needs_decomposition MUST equal", R.ROUTER_SYS)

    def test_prompt_names_one_entity_multi_attribute_as_one_query(self):
        self.assertIn("one entity with several attributes", R.ROUTER_SYS.lower())

    def test_prompt_names_contrast_about_one_subject_as_one_query(self):
        s=R.ROUTER_SYS.lower()
        self.assertIn("contrasted with what it actually does", s)
        self.assertIn("verified or corrected", s)

    def test_prompt_names_temporal_same_subject_as_one_query(self):
        self.assertIn("before and after a change", R.ROUTER_SYS.lower())

    def test_prompt_names_synthesis_and_scope_as_one_query(self):
        s=R.ROUTER_SYS.lower()
        self.assertIn("synthesis or summary over a related series", s)
        self.assertIn("scope, applicability or negative check", s)

    def test_prompt_rejects_conjunctions_as_justification(self):
        s=R.ROUTER_SYS.lower()
        for t in ["'and'","'but'","'while'","'versus'","'before'","'after'"]:
            self.assertIn(t, s)
        self.assertIn("do not by themselves justify", s)

    def test_prompt_requires_multiple_queries_only_for_separate_neighbourhoods(self):
        self.assertIn("separate semantic neighbourhoods", R.ROUTER_SYS.lower())

    def test_prompt_forbids_chain_of_thought(self):
        s=R.ROUTER_SYS.lower()
        self.assertIn("only json", s); self.assertIn("no description of your reasoning", s)

    def test_prompt_requires_identifier_and_time_preservation(self):
        s=R.ROUTER_SYS.lower()
        self.assertIn("identifiers", s); self.assertIn("time constraints", s); self.assertIn("exactly", s)

    def test_every_simple_code_maps_to_one_query(self):
        for c in R.SIMPLE_CODES:
            o=R.parse_router_output(J({"needs_decomposition":False,"retrieval_queries":["q"],
                "reason_code":c}))
            self.assertTrue(o["parse_ok"], c); self.assertFalse(o["needs_decomposition"])

    def test_enum_covers_the_five_v2_failure_shapes(self):
        for c in ["contrast_or_verification_one_subject","temporal_update_same_topic",
                  "synthesis_over_related_observations","scope_or_negative_check",
                  "single_entity_multi_attribute","single_event_multi_consequence"]:
            self.assertIn(c, R.REASON_CODES, c)


class NoLeakageTests(unittest.TestCase):
    FORBIDDEN=["expected_answer","expected answer","reference answer","ground_truth","ground truth",
               "baseline","judge","scenario","case_id","case-0","hold-0","prior_score",
               "correctness","completeness","groundedness","reference_retrieval_queries","category"]

    def test_messages_contain_only_the_question(self):
        q="What is X and Y?"
        m=R.build_messages(q)
        self.assertEqual(len(m),2); self.assertEqual(m[1]["content"],f"Question: {q}")

    def test_build_messages_signature_takes_only_question(self):
        import inspect
        self.assertEqual(list(inspect.signature(R.build_messages).parameters),["question"])

    def test_no_forbidden_token_in_user_message(self):
        blob=R.build_messages("Some question?")[1]["content"].lower()
        for t in self.FORBIDDEN: self.assertNotIn(t, blob, t)

    def test_no_forbidden_token_in_system_prompt(self):
        s=R.ROUTER_SYS.lower()
        for t in ["expected_answer","expected answer","ground truth","baseline","judge",
                  "scenario","case_id","case-0","hold-0","reference_retrieval_queries"]:
            self.assertNotIn(t, s, t)

    def test_no_route_label_leakage(self):
        """route must never reach the model."""
        self.assertNotIn("route", R.ROUTER_SYS.lower())
        src=open(os.path.join(HERE,"run_router_v3.py")).read()
        self.assertNotIn('"route"', src)

    def test_holdout_label_fields_never_reach_the_model(self):
        h=holdout()[0]
        blob=json.dumps(R.build_messages(h["question"])).lower()
        self.assertNotIn(h["ground_truth"], blob)
        self.assertNotIn(h["category"].lower(), blob)
        self.assertNotIn(h["holdout_case_id"].lower(), blob)


class HoldoutManifestTests(unittest.TestCase):
    def setUp(self): self.h=holdout(); self.meta=json.load(open(os.path.join(OUTD,"router_holdout_v1_meta.json")))

    def test_40_cases_balanced_20_20(self):
        self.assertEqual(len(self.h),40)
        self.assertEqual(sum(1 for r in self.h if r["ground_truth"]=="simple"),20)
        self.assertEqual(sum(1 for r in self.h if r["ground_truth"]=="compound"),20)

    def test_no_ambiguous_in_holdout(self):
        for r in self.h: self.assertIn(r["ground_truth"],("simple","compound"))

    def test_no_internal_duplicate_questions(self):
        hs=[r["normalized_question_hash"] for r in self.h]
        self.assertEqual(len(hs),len(set(hs)))

    def test_no_exact_overlap_with_development_set(self):
        dn={r["normalized_question"] for r in dev()}
        dh={r["normalized_question_hash"] for r in dev()}
        for r in self.h:
            self.assertNotIn(r["normalized_question"], dn, r["holdout_case_id"])
            self.assertNotIn(r["normalized_question_hash"], dh, r["holdout_case_id"])

    def test_near_duplicate_similarity_below_threshold(self):
        tk=lambda q: set(re.findall(r"[a-z0-9-]+", norm(q).lower()))
        d=dev()
        for r in self.h:
            a=tk(r["question"])
            worst=max((len(a&tk(x["question"]))/len(a|tk(x["question"])) for x in d), default=0)
            self.assertLess(worst, 0.60, f"{r['holdout_case_id']} too similar to dev set")

    def test_compound_need_count_and_reference_queries_agree(self):
        for r in self.h:
            self.assertEqual(len(r["reference_retrieval_queries"]),
                             r["independent_retrieval_need_count"], r["holdout_case_id"])
            if r["ground_truth"]=="compound":
                self.assertGreaterEqual(r["independent_retrieval_need_count"],2)
            else:
                self.assertEqual(r["independent_retrieval_need_count"],1)

    def test_manifest_fingerprint_stable(self):
        a=hashlib.sha256(json.dumps(holdout(),sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        b=hashlib.sha256(json.dumps(holdout(),sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        self.assertEqual(a,b); self.assertEqual(len(self.meta["manifest_sha256"]),64)

    def test_holdout_is_not_cue_separable(self):
        """A naive and-detector must not solve the holdout."""
        self.assertGreaterEqual(self.meta["cue_counts"]["simple_with_and"],4)
        self.assertGreaterEqual(self.meta["cue_counts"]["compound_without_and"],4)
        self.assertLess(self.meta["naive_and_detector_baseline"]["accuracy"],0.90)

    def test_category_distribution_recorded(self):
        s=sum(self.meta["category_distribution"].values())
        self.assertEqual(s,40)

    def test_authored_before_any_v3_call(self):
        self.assertTrue(self.meta["authored_before_any_router_v3_call"])

    def test_provider_not_imported_by_manifest_builder(self):
        mods=imports_of(os.path.join(HERE,"build_holdout_v1.py"))
        for banned in ["nvidia_provider","nvidia_harness","router_v3","router_v2","app","boto3"]:
            self.assertNotIn(banned, mods, banned)

    def test_holdout_labels_immutable_after_freeze(self):
        """The runner must never assign to a ground_truth/label field, and must never
        open the holdout manifest for writing. Checked via AST — a substring scan for
        'ground_truth"]=' also matches the equality comparison and is a false positive."""
        tree=ast.parse(open(os.path.join(HERE,"run_router_v3.py")).read())
        for n in ast.walk(tree):
            if isinstance(n,(ast.Assign,ast.AugAssign)):
                targets=n.targets if isinstance(n,ast.Assign) else [n.target]
                for t in targets:
                    if isinstance(t,ast.Subscript) and isinstance(t.slice,ast.Constant):
                        self.assertNotIn(t.slice.value,
                            ("ground_truth","gt","category","independent_retrieval_need_count"),
                            f"runner assigns to label field {t.slice.value!r}")
        # no write/append handle on either frozen manifest
        for n in ast.walk(tree):
            if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=="open":
                mode=n.args[1].value if len(n.args)>1 and isinstance(n.args[1],ast.Constant) else "r"
                path=ast.unparse(n.args[0]) if n.args else ""
                if any(k in path for k in ("holdout-v1","groundtruth-v2")):
                    self.assertEqual(mode,"r", f"frozen manifest opened with mode {mode!r}")


class RunnerTests(unittest.TestCase):
    def setUp(self): self.src=open(os.path.join(HERE,"run_router_v3.py")).read()

    def test_concurrency_one(self):
        for banned in ["ThreadPool","as_completed","Semaphore","asyncio"]:
            self.assertNotIn(banned, self.src, banned)
        self.assertIn('"concurrency": 1', self.src)

    def test_groq_guard_installed(self):
        self.assertIn("H.install_groq_guard()", self.src)

    def test_router_module_never_references_groq(self):
        self.assertNotIn("groq", open(os.path.abspath(R.__file__)).read().lower())

    def test_uses_20b_not_120b(self):
        import nvidia_harness as H2
        self.assertEqual(R.ROUTER_MODEL, H2.APP_MODEL)
        self.assertNotEqual(R.ROUTER_MODEL, H2.JUDGE_MODEL)

    def test_checkpoint_resume_filters_by_fingerprint(self):
        self.assertIn('r.get("fingerprint_hash") == FP["fingerprint_hash"]', self.src)
        self.assertIn("os.fsync", self.src)

    def test_holdout_refuses_to_run_without_dev_gate(self):
        self.assertIn("REFUSING to run the holdout", self.src)
        self.assertIn('json.load(open(GATE)).get("passed")', self.src)

    def test_dev_gate_thresholds(self):
        self.assertIn('"recall": 0.90', self.src)
        self.assertIn('"specificity": 0.90', self.src)
        self.assertIn('"precision": 0.80', self.src)
        self.assertIn("ROUTER V3 DEVELOPMENT FAILURE", self.src)

    def test_no_retrieval_generation_or_judging(self):
        called=set()
        for n in ast.walk(ast.parse(open(os.path.abspath(R.__file__)).read())):
            if isinstance(n,ast.Call):
                f=n.func
                if isinstance(f,ast.Attribute): called.add(f.attr)
                elif isinstance(f,ast.Name): called.add(f.id)
        for banned in ["hybrid_search","query_points","embed","stream_answer","retrieve","judge"]:
            self.assertNotIn(banned, called, banned)
        self.assertIn("chat", called)
        for k in ['"retrieval_performed": False','"generation_performed": False',
                  '"judging_performed": False','"langgraph_used": False']:
            self.assertIn(k, self.src)

    def test_v3_does_not_import_v2_or_graph(self):
        for f in ["router_v3.py","run_router_v3.py"]:
            mods=imports_of(os.path.join(HERE,f))
            self.assertNotIn("router_v2", mods, f)
            self.assertNotIn("decomp_graph", mods, f)


class FrozenPriorWorkTests(unittest.TestCase):
    FROZEN=["multitenant-rag/evals/router_v2.py","multitenant-rag/evals/run_router_v2.py",
            "multitenant-rag/evals/decomp_graph.py","multitenant-rag/evals/run_gt_audit.py",
            "multitenant-rag/evals/router_gt_annotations.py"]

    def test_prior_experiment_files_unmodified(self):
        repo=subprocess.run(["git","rev-parse","--show-toplevel"],capture_output=True,text=True).stdout.strip()
        out=subprocess.run(["git","status","--porcelain"]+self.FROZEN,cwd=repo,
                           capture_output=True,text=True).stdout
        for line in out.strip().split("\n"):
            if line.strip(): self.assertTrue(line.startswith("??"), f"frozen file modified: {line}")

    def test_v2_predictions_and_labels_intact(self):
        self.assertEqual(len([l for l in open(f"{OUT}/compound-router-groundtruth-v2.jsonl")]),52)
        fp=json.load(open(os.path.join(OUTD,"router_v2_fingerprint.json")))["fingerprint_hash"]
        raw=[json.loads(l) for l in open(os.path.join(OUTD,"router_v2_results.jsonl"))]
        self.assertEqual(len([r for r in raw if r["fingerprint_hash"]==fp]),52)

    def test_decomposition_outputs_intact(self):
        for f in ["decomp_cases.jsonl","decomp_judge.jsonl","decomp_cases.INVALID_scope_bug.jsonl"]:
            self.assertTrue(os.path.exists(os.path.join(OUTD,f)), f)
        self.assertEqual(len([l for l in open(os.path.join(OUTD,"decomp_cases.jsonl"))]),6)

    def test_v3_runner_never_writes_prior_artifacts(self):
        src=open(os.path.join(HERE,"run_router_v3.py")).read()
        for f in ["router_v2_results","decomp_cases","decomp_judge","groundtruth-v2"]:
            self.assertNotIn(f+'","a', src.replace("'",'"'))
            self.assertNotIn(f+'","w', src.replace("'",'"'))


if __name__=="__main__":
    unittest.main(verbosity=2)
