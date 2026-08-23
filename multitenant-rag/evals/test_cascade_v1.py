"""Tests for compound-router-cascade-v1 (frozen V2 -> strict verifier). No network."""
import ast, json, os, re, subprocess, hashlib, unicodedata, unittest
import verifier_v1 as V

HERE=os.path.dirname(os.path.abspath(__file__)); OUTD=os.path.join(HERE,"output")
OUT="/Users/praveen-16349/Documents/Personal/Learnings/AWS - Blog"
HOLDOUT_SHA="0957b7bf8db137bad6e35f1495f5953e636e87969b5173b0fd9101a1dfd233b8"
def J(c,code,rat=None):
    d={"confirm_compound":c,"reason_code":code}
    if rat is not None: d["rationale"]=rat
    return json.dumps(d)
def imports_of(p):
    m=set()
    for n in ast.walk(ast.parse(open(p).read())):
        if isinstance(n,ast.Import): m|={a.name.split(".")[0] for a in n.names}
        elif isinstance(n,ast.ImportFrom) and n.module: m.add(n.module.split(".")[0])
    return m
RUNNER=os.path.join(HERE,"run_cascade_v1.py")
src=lambda: open(RUNNER).read()


class VerifierParserTests(unittest.TestCase):
    def test_confirm_parses(self):
        o=V.parse_verifier_output(J(True,V.CONFIRM_CODE,"two domains"))
        self.assertTrue(o["parse_ok"]); self.assertTrue(o["confirm_compound"])

    def test_reject_parses(self):
        o=V.parse_verifier_output(J(False,"same_entity_state","one state"))
        self.assertTrue(o["parse_ok"]); self.assertFalse(o["confirm_compound"])

    def test_json_in_prose_parses(self):
        o=V.parse_verifier_output('ok: {"confirm_compound": false, "reason_code": "same_entity_state"} done')
        self.assertTrue(o["parse_ok"])

    def test_no_json_rejected(self):
        self.assertEqual(V.parse_verifier_output("they are separate")["parse_error"],"no_json_object")

    def test_non_bool_flag_rejected(self):
        self.assertEqual(V.parse_verifier_output(J("yes","same_entity_state"))["parse_error"],
                         "confirm_compound_not_bool")

    def test_parse_never_raises(self):
        for bad in ["",None,"{}","[]","{"*40,"\x00",J(True,None)]: V.parse_verifier_output(bad)

    def test_rationale_optional_and_capped(self):
        o=V.parse_verifier_output(J(False,"same_entity_state","x"*400))
        self.assertTrue(o["parse_ok"]); self.assertLessEqual(len(o["rationale"]),200)
        self.assertIsNone(V.parse_verifier_output(J(False,"same_entity_state"))["rationale"])

    def test_truncation_labelled(self):
        import nvidia_provider as nv
        orig=nv.chat
        nv.chat=lambda *a,**k:{"content":'{"confirm_comp',"finish_reason":"length",
                               "latency_ms":1,"input_tokens":5,"output_tokens":V.VERIFIER_MAX_TOKENS}
        try: r=V.verify("q",["a","b"])
        finally: nv.chat=orig
        self.assertEqual(r["parse_error"],"truncated_output_token_limit")

    def test_schema_is_small(self):
        """v4's lesson: no nested arrays/objects in the verifier contract."""
        for banned in ["retrieval_query","anchor","facts_needed","evidence_units"]:
            self.assertNotIn(banned, V.VERIFIER_SYS, banned)


class ReasonCodeEnumTests(unittest.TestCase):
    def test_enum_small_and_closed(self):
        self.assertEqual(len(V.REASON_CODES),6)
        self.assertIn(V.CONFIRM_CODE,V.REASON_CODES)
        for c in V.REJECT_CODES: self.assertIn(c,V.REASON_CODES)

    def test_unknown_code_rejected(self):
        self.assertEqual(V.parse_verifier_output(J(False,"vibes"))["parse_error"],
                         "reason_code_not_in_enum")

    def test_confirm_code_exclusive_to_true(self):
        self.assertEqual(V.parse_verifier_output(J(False,V.CONFIRM_CODE))["parse_error"],
                         "confirm_false_with_confirm_reason_code")

    def test_true_requires_confirm_code(self):
        self.assertEqual(V.parse_verifier_output(J(True,"same_entity_state"))["parse_error"],
                         "confirm_true_with_reject_reason_code")

    def test_every_reject_code_accepted(self):
        for c in V.REJECT_CODES:
            self.assertTrue(V.parse_verifier_output(J(False,c))["parse_ok"],c)


class VerifierSemanticContractTests(unittest.TestCase):
    def test_prompt_frames_a_confirmation_not_a_fresh_plan(self):
        s=V.VERIFIER_SYS.lower()
        self.assertIn("confirm or reject that split", s)
        self.assertIn("retrieval neighbourhood", s)

    def test_same_object_multi_attribute_maps_to_reject(self):
        self.assertIn("one object's measurements and their consequences", V.VERIFIER_SYS.lower())
        self.assertTrue(V.parse_verifier_output(J(False,"same_entity_state"))["parse_ok"])

    def test_same_subject_verification_maps_to_reject(self):
        self.assertIn("what that subject actually does", V.VERIFIER_SYS.lower())
        self.assertTrue(V.parse_verifier_output(J(False,"same_subject_verification"))["parse_ok"])

    def test_same_subject_temporal_maps_to_reject(self):
        self.assertIn("older and current values", V.VERIFIER_SYS.lower())
        self.assertTrue(V.parse_verifier_output(J(False,"same_subject_temporal_comparison"))["parse_ok"])

    def test_series_synthesis_maps_to_reject(self):
        self.assertIn("related series of observations", V.VERIFIER_SYS.lower())
        self.assertTrue(V.parse_verifier_output(J(False,"same_series_synthesis"))["parse_ok"])

    def test_cross_domain_and_sibling_map_to_confirm(self):
        s=V.VERIFIER_SYS.lower()
        self.assertIn("different unrelated systems", s)
        self.assertIn("their own independent rules", s)
        self.assertIn("independently sourced value is required from each", s)
        self.assertTrue(V.parse_verifier_output(J(True,V.CONFIRM_CODE))["parse_ok"])

    def test_shared_noun_is_not_sufficient_for_reject(self):
        self.assertIn("does not prove they share one", V.VERIFIER_SYS.lower())

    def test_different_outputs_not_sufficient_for_confirm(self):
        self.assertIn("does not prove separate neighbourhoods", V.VERIFIER_SYS.lower())

    def test_no_chain_of_thought(self):
        s=V.VERIFIER_SYS.lower()
        self.assertIn("only json", s); self.assertIn("no description of your reasoning", s)


class NoLeakageTests(unittest.TestCase):
    FORBIDDEN=["ground_truth","ground truth","expected_answer","expected answer","reference answer",
               "judge score","judge_score","judged","scenario","case_id","case-0","hold-0",
               "decision_rule","was correct","v2 was","route"]

    def test_input_contains_only_question_needs_and_code(self):
        m=V.build_messages("Q?",["n1","n2"],"multiple_independent_retrieval_needs")
        self.assertEqual(len(m),2)
        body=m[1]["content"]
        self.assertIn("Question: Q?",body); self.assertIn("1. n1",body); self.assertIn("2. n2",body)
        self.assertIn("First-pass reason code:",body)

    def test_signature_takes_only_permitted_inputs(self):
        import inspect
        self.assertEqual(list(inspect.signature(V.build_messages).parameters),
                         ["question","proposed_needs","v2_reason_code"])

    def test_no_forbidden_token_in_user_message(self):
        blob=V.build_messages("Some question?",["a"],None)[1]["content"].lower()
        for t in self.FORBIDDEN: self.assertNotIn(t,blob,t)

    def test_no_forbidden_token_in_system_prompt(self):
        """Targets score/label leakage specifically. Bare 'judge' would false-positive on
        the instruction 'judge retrieval locality', which is the verb, not a judge score."""
        s=V.VERIFIER_SYS.lower()
        for t in ["ground truth","expected_answer","expected answer","judge score","judge_score",
                  "judged","scenario","case_id","case-0","hold-0","was correct","baseline"]:
            self.assertNotIn(t,s,t)

    def test_labels_never_passed_at_call_site(self):
        """The runner must pass only question, a_needs, a_code into the verifier."""
        tree=ast.parse(src())
        found=False
        for n in ast.walk(tree):
            if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=="verify":
                found=True
                args=[ast.unparse(a) for a in n.args]
                self.assertEqual(args,["it['question']","it['a_needs']","it['a_code']"])
        self.assertTrue(found,"verifier call site not found")

    def test_no_route_derived_label_in_runner_input(self):
        self.assertNotIn('"route"',src())

    def test_no_diagnostic_case_ids_in_verifier_logic(self):
        body=open(os.path.abspath(V.__file__)).read().split('"""',2)[-1]
        for cid in ["case-002","case-003","case-004","case-030","case-041","case-056","case-059"]:
            self.assertNotIn(cid,body,cid)
            self.assertNotIn(cid,V.VERIFIER_SYS,cid)


class CascadeLogicTests(unittest.TestCase):
    def test_stage_b_runs_only_on_stage_a_compound(self):
        self.assertIn('candidates = [it for it in items if it.get("a_compound") is True]',src())

    def test_stage_a_simple_bypasses_verifier_and_is_final_simple(self):
        s=src()
        self.assertIn('final = False; path = "A only"',s)

    def test_confirmed_candidate_becomes_compound_rejected_becomes_simple(self):
        self.assertIn('final = None if (b is None or b["confirm_compound"] is None) else b["confirm_compound"]',src())

    def test_stage_b_cannot_convert_simple_to_compound(self):
        """Structural: the only path to final_compound=True is via a Stage A candidate."""
        s=src()
        self.assertNotIn("a_compound is False and", s)
        # simulate the cascade rule
        for a,b,expected in [(False,None,False),(True,True,True),(True,False,False)]:
            final = b if a else False
            self.assertEqual(bool(final), expected)

    def test_verifier_invoked_at_most_once_per_case(self):
        s=src()
        self.assertIn("todo = [it for it in candidates if it[\"id\"] not in bdone]",s)
        self.assertEqual(s.count("V.verify("),1)

    def test_dev_reuses_persisted_stage_a_zero_v2_calls(self):
        s=src()
        self.assertIn('FP["stage_a_source"] = "persisted"',s)
        self.assertIn("router_v2_results.jsonl",s)
        self.assertIn("expected 52 persisted V2 predictions",s)

    def test_holdout_runs_frozen_stage_a_live(self):
        s=src()
        self.assertIn("import router_v2 as R2",s)
        self.assertIn("R2.classify(",s)

    def test_ambiguous_path_executed_but_excluded_from_metrics(self):
        s=src()
        self.assertIn('scored = [r for r in rows if r["gt"] != "ambiguous"]',s)

    def test_concurrency_one_and_checkpointed(self):
        s=src()
        for b in ["ThreadPool","as_completed","Semaphore","asyncio"]: self.assertNotIn(b,s,b)
        self.assertIn('"concurrency": 1',s); self.assertIn("os.fsync",s)

    def test_groq_guard_installed_and_unreferenced(self):
        self.assertIn("H.install_groq_guard()",src())
        self.assertNotIn("groq",open(os.path.abspath(V.__file__)).read().lower())

    def test_uses_20b_not_120b(self):
        import nvidia_harness as H
        self.assertEqual(V.VERIFIER_MODEL,H.APP_MODEL)
        self.assertNotEqual(V.VERIFIER_MODEL,H.JUDGE_MODEL)

    def test_no_retrieval_generation_or_judging(self):
        called=set()
        for n in ast.walk(ast.parse(open(os.path.abspath(V.__file__)).read())):
            if isinstance(n,ast.Call):
                f=n.func
                if isinstance(f,ast.Attribute): called.add(f.attr)
                elif isinstance(f,ast.Name): called.add(f.id)
        for b in ["hybrid_search","query_points","embed","stream_answer","retrieve","judge"]:
            self.assertNotIn(b,called,b)
        self.assertIn("chat",called)
        for k in ['"retrieval_performed": False','"generation_performed": False',
                  '"judging_performed": False','"langgraph_used": False']:
            self.assertIn(k,src())

    def test_verifier_module_isolated_from_routers_and_graph(self):
        mods=imports_of(os.path.abspath(V.__file__))
        for b in ["router_v2","router_v3","router_v4","decomp_graph"]: self.assertNotIn(b,mods,b)


class GateAndHoldoutTests(unittest.TestCase):
    def test_dev_gate_thresholds_unrelaxed(self):
        s=src()
        for t in ['"recall": 0.90','"specificity": 0.90','"precision": 0.80']: self.assertIn(t,s,t)
        self.assertIn("CASCADE V1 DEVELOPMENT FAILURE",s)

    def test_holdout_not_callable_without_dev_pass(self):
        s=src()
        self.assertIn("REFUSING to run the holdout",s)
        self.assertIn('json.load(open(GATE)).get("passed")',s)

    def test_holdout_hash_verified_before_execution(self):
        s=src()
        self.assertIn("HOLDOUT_SHA",s); self.assertIn("frozen holdout hash mismatch",s)
        self.assertEqual(HOLDOUT_SHA, re.search(r'HOLDOUT_SHA = "([0-9a-f]{64})"',s).group(1))

    def test_frozen_holdout_bytes_unchanged(self):
        recs=[json.loads(l) for l in open(f"{OUT}/compound-router-holdout-v1.jsonl",encoding="utf-8")]
        live=hashlib.sha256(json.dumps(recs,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        self.assertEqual(live,HOLDOUT_SHA)
        self.assertEqual(len(recs),40)
        self.assertEqual(sum(1 for r in recs if r["ground_truth"]=="simple"),20)
        self.assertEqual(sum(1 for r in recs if r["ground_truth"]=="compound"),20)

    def test_runner_never_writes_frozen_manifests(self):
        tree=ast.parse(src())
        for n in ast.walk(tree):
            if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=="open":
                mode=n.args[1].value if len(n.args)>1 and isinstance(n.args[1],ast.Constant) else "r"
                path=ast.unparse(n.args[0]) if n.args else ""
                if any(k in path for k in ("holdout-v1","groundtruth-v2","router_v2_results")):
                    self.assertEqual(mode,"r",f"frozen artifact opened mode {mode!r}")


class FrozenPriorWorkTests(unittest.TestCase):
    FROZEN=["multitenant-rag/evals/router_v2.py","multitenant-rag/evals/router_v3.py",
            "multitenant-rag/evals/router_v4.py","multitenant-rag/evals/decomp_graph.py"]

    def test_prior_router_sources_unmodified(self):
        repo=subprocess.run(["git","rev-parse","--show-toplevel"],capture_output=True,text=True).stdout.strip()
        out=subprocess.run(["git","status","--porcelain"]+self.FROZEN,cwd=repo,
                           capture_output=True,text=True).stdout
        for line in out.strip().split("\n"):
            if line.strip(): self.assertTrue(line.startswith("??"),f"frozen file modified: {line}")

    def test_frozen_v2_prompt_sha_matches_its_recorded_fingerprint(self):
        import router_v2 as R2
        live=hashlib.sha256(R2.ROUTER_SYS.encode()).hexdigest()[:16]
        stored=json.load(open(os.path.join(OUTD,"router_v2_fingerprint.json")))["prompt_sha"]
        self.assertEqual(live,stored)

    def test_prior_results_intact(self):
        for f,n in [("router_v2_results.jsonl",58),("router_v3_dev_results.jsonl",52),
                    ("router_v4_dev_results.jsonl",52),("decomp_cases.jsonl",6)]:
            self.assertEqual(len([l for l in open(os.path.join(OUTD,f))]),n,f)

    def test_v3_and_v4_never_ran_the_holdout(self):
        for f in ["router_v3_holdout_results.jsonl","router_v4_holdout_results.jsonl"]:
            self.assertFalse(os.path.exists(os.path.join(OUTD,f)),f)

    def test_adjudicated_labels_untouched(self):
        recs=[json.loads(l) for l in open(f"{OUT}/compound-router-groundtruth-v2.jsonl",encoding="utf-8")]
        self.assertEqual(len(recs),52)
        c=lambda l: sum(1 for r in recs if r["ground_truth"]==l)
        self.assertEqual((c("simple"),c("compound"),c("ambiguous")),(39,11,2))


if __name__=="__main__":
    unittest.main(verbosity=2)
