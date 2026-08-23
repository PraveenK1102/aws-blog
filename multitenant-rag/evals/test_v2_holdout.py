"""Tests for the frozen-V2 high-recall holdout evaluation. No network."""
import ast, json, os, re, hashlib, unicodedata, subprocess, unittest

HERE=os.path.dirname(os.path.abspath(__file__)); OUTD=os.path.join(HERE,"output")
OUT="/Users/praveen-16349/Documents/Personal/Learnings/AWS - Blog"
SHA="0957b7bf8db137bad6e35f1495f5953e636e87969b5173b0fd9101a1dfd233b8"
RUNNER=os.path.join(HERE,"run_v2_holdout.py")
src=lambda: open(RUNNER).read()
holdout=lambda: [json.loads(l) for l in open(f"{OUT}/compound-router-holdout-v1.jsonl",encoding="utf-8")]
def imports_of(p):
    m=set()
    for n in ast.walk(ast.parse(open(p).read())):
        if isinstance(n,ast.Import): m|={a.name.split(".")[0] for a in n.names}
        elif isinstance(n,ast.ImportFrom) and n.module: m.add(n.module.split(".")[0])
    return m


class HoldoutIntegrityTests(unittest.TestCase):
    def test_sha256_matches_expected(self):
        live=hashlib.sha256(json.dumps(holdout(),sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        self.assertEqual(live,SHA)

    def test_40_cases_20_20_zero_ambiguous(self):
        h=holdout()
        self.assertEqual(len(h),40)
        self.assertEqual(sum(1 for r in h if r["ground_truth"]=="simple"),20)
        self.assertEqual(sum(1 for r in h if r["ground_truth"]=="compound"),20)
        self.assertEqual(sum(1 for r in h if r["ground_truth"]=="ambiguous"),0)

    def test_no_duplicate_ids_or_normalized_questions(self):
        h=holdout()
        norm=lambda q: re.sub(r"\s+"," ",unicodedata.normalize("NFKC",q).strip())
        self.assertEqual(len({r["holdout_case_id"] for r in h}),40)
        self.assertEqual(len({norm(r["question"]) for r in h}),40)

    def test_thirteen_categories_preserved(self):
        self.assertEqual(len({r["category"] for r in holdout()}),13)

    def test_runner_asserts_integrity_before_any_provider_call(self):
        """The integrity gate must precede the classify loop in source order."""
        s=src()
        self.assertLess(s.index("HOLDOUT INTEGRITY FAILURE"), s.index("R2.classify("))
        self.assertIn("hash mismatch",s)

    def test_runner_never_writes_the_holdout_manifest(self):
        for n in ast.walk(ast.parse(src())):
            if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=="open":
                mode=n.args[1].value if len(n.args)>1 and isinstance(n.args[1],ast.Constant) else "r"
                path=ast.unparse(n.args[0]) if n.args else ""
                if "holdout-v1" in path: self.assertEqual(mode,"r")


class FrozenV2IdentityTests(unittest.TestCase):
    def test_prompt_sha_matches_stored_fingerprint(self):
        import router_v2 as R2
        live=hashlib.sha256(R2.ROUTER_SYS.encode()).hexdigest()[:16]
        self.assertEqual(live,json.load(open(os.path.join(OUTD,"router_v2_fingerprint.json")))["prompt_sha"])

    def test_model_temperature_and_schema_unchanged(self):
        import router_v2 as R2, inspect
        fp=json.load(open(os.path.join(OUTD,"router_v2_fingerprint.json")))
        self.assertEqual(R2.ROUTER_MODEL,fp["router_model"])
        self.assertEqual(R2.ROUTER_MAX_TOKENS,fp["max_tokens"])
        self.assertEqual(list(R2.REASON_CODES),fp["reason_codes"])
        self.assertIn("temperature=0.0",inspect.getsource(R2.classify))

    def test_runner_refuses_on_identity_mismatch(self):
        s=src()
        self.assertIn("frozen V2 identity mismatch",s)
        self.assertLess(s.index("frozen V2 identity mismatch"), s.index("R2.classify("))

    def test_v2_source_unmodified_in_git(self):
        repo=subprocess.run(["git","rev-parse","--show-toplevel"],capture_output=True,text=True).stdout.strip()
        out=subprocess.run(["git","status","--porcelain","multitenant-rag/evals/router_v2.py"],
                           cwd=repo,capture_output=True,text=True).stdout.strip()
        if out: self.assertTrue(out.startswith("??"),out)


class ExecutionContractTests(unittest.TestCase):
    def test_v2_called_exactly_once_per_question(self):
        s=src()
        self.assertEqual(s.count("R2.classify("),1)
        self.assertIn("for c in todo:",s)

    def test_no_other_router_or_verifier_used(self):
        mods=imports_of(RUNNER)
        for b in ["router_v3","router_v4","verifier_v1"]: self.assertNotIn(b,mods,b)
        s=src()
        for k in ['"verifier_used": False','"v3_used": False','"v4_used": False']: self.assertIn(k,s)

    def test_no_retrieval_generation_judging_or_langgraph(self):
        mods=imports_of(RUNNER)
        for b in ["decomp_graph","langgraph","qdrant_client"]: self.assertNotIn(b,mods,b)
        s=src()
        for k in ['"retrieval_performed": False','"generation_performed": False',
                  '"judging_performed": False','"langgraph_used": False']: self.assertIn(k,s)
        for b in ["hybrid_search","query_points","stream_answer","H.judge"]: self.assertNotIn(b,s,b)

    def test_concurrency_one_and_durable_checkpoint(self):
        s=src()
        for b in ["ThreadPool","as_completed","Semaphore","asyncio"]: self.assertNotIn(b,s,b)
        self.assertIn('"concurrency": 1',s); self.assertIn("os.fsync",s)

    def test_checkpoint_resume_filters_by_fingerprint(self):
        self.assertIn('r.get("fingerprint_hash") == FP["fingerprint_hash"]',src())

    def test_groq_guard_installed(self):
        self.assertIn("H.install_groq_guard()",src())

    def test_no_120b_model_referenced(self):
        s=src()
        self.assertNotIn("JUDGE_MODEL",s); self.assertNotIn("gpt-oss-120b",s)
        self.assertIn('"nvidia_120b_calls":0',s.replace(" ",""))

    def test_no_ground_truth_or_expected_answer_in_router_input(self):
        """Only c['question'] may reach classify()."""
        for n in ast.walk(ast.parse(src())):
            if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=="classify":
                self.assertEqual([ast.unparse(a) for a in n.args],["c['question']"])

    def test_router_input_carries_only_the_question(self):
        import router_v2 as R2
        m=R2.build_messages("Some question?")
        self.assertEqual(m[1]["content"],"Question: Some question?")
        blob=json.dumps(m).lower()
        for t in ["ground_truth","expected","category","reference_retrieval","hold-0"]:
            self.assertNotIn(t,blob,t)


class AcceptancePolicyTests(unittest.TestCase):
    def test_thresholds_fixed_as_specified(self):
        s=src()
        self.assertIn('THRESH = {"recall": 0.95, "specificity": 0.80, "precision": 0.80}',s)
        self.assertIn('"compound_without_and": 0.75',s)
        self.assertIn('"contrast_verification": 0.75',s)

    def test_thresholds_recorded_in_fingerprint_before_run(self):
        s=src()
        self.assertIn('"acceptance_thresholds": THRESH',s)
        self.assertLess(s.index('"acceptance_thresholds": THRESH'), s.index("R2.classify("))

    def test_verdict_requires_core_and_guards(self):
        self.assertIn('verdict = "PASS" if (all(core.values()) and all(guards.values())) else "FAIL"',src())

    def test_metrics_recompute_from_confusion_matrix(self):
        p=os.path.join(OUTD,"v2_holdout_metrics.json")
        if not os.path.exists(p): self.skipTest("holdout not yet run")
        M=json.load(open(p))
        TP,FP,TN,FN=M["TP"],M["FP"],M["TN"],M["FN"]
        self.assertEqual(TP+FP+TN+FN,40)
        self.assertAlmostEqual(M["compound_precision"],TP/(TP+FP),places=4)
        self.assertAlmostEqual(M["compound_recall"],TP/(TP+FN),places=4)
        self.assertAlmostEqual(M["simple_specificity"],TN/(TN+FP),places=4)
        self.assertAlmostEqual(M["routing_accuracy"],(TP+TN)/40,places=4)

    def test_category_metrics_cover_all_forty(self):
        p=os.path.join(OUTD,"v2_holdout_metrics.json")
        if not os.path.exists(p): self.skipTest("holdout not yet run")
        M=json.load(open(p))
        self.assertEqual(sum(o["n"] for o in M["by_category"].values()),40)
        self.assertEqual(len(M["by_category"]),13)


class PriorWorkFrozenTests(unittest.TestCase):
    FROZEN=["multitenant-rag/evals/router_v2.py","multitenant-rag/evals/router_v3.py",
            "multitenant-rag/evals/router_v4.py","multitenant-rag/evals/verifier_v1.py",
            "multitenant-rag/evals/decomp_graph.py"]

    def test_prior_experiment_sources_unmodified(self):
        repo=subprocess.run(["git","rev-parse","--show-toplevel"],capture_output=True,text=True).stdout.strip()
        out=subprocess.run(["git","status","--porcelain"]+self.FROZEN,cwd=repo,
                           capture_output=True,text=True).stdout
        for line in out.strip().split("\n"):
            if line.strip(): self.assertTrue(line.startswith("??"),f"frozen file modified: {line}")

    def test_prior_results_intact(self):
        for f,n in [("router_v2_results.jsonl",58),("router_v3_dev_results.jsonl",52),
                    ("router_v4_dev_results.jsonl",52),("decomp_cases.jsonl",6),
                    ("cascade_v1_dev_verifier.jsonl",18)]:
            self.assertEqual(len([l for l in open(os.path.join(OUTD,f))]),n,f)

    def test_adjudicated_dev_labels_untouched(self):
        r=[json.loads(l) for l in open(f"{OUT}/compound-router-groundtruth-v2.jsonl",encoding="utf-8")]
        c=lambda l: sum(1 for x in r if x["ground_truth"]==l)
        self.assertEqual((len(r),c("simple"),c("compound"),c("ambiguous")),(52,39,11,2))

    def test_no_other_router_ever_ran_the_holdout(self):
        for f in ["router_v3_holdout_results.jsonl","router_v4_holdout_results.jsonl",
                  "cascade_v1_holdout_stageA.jsonl"]:
            self.assertFalse(os.path.exists(os.path.join(OUTD,f)),f)


if __name__=="__main__":
    unittest.main(verbosity=2)
