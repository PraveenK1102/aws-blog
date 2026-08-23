"""Tests for the ground-truth audit and zero-cost rescore tooling."""
import json, os, re, sys, hashlib, unicodedata, subprocess, unittest

HERE=os.path.dirname(os.path.abspath(__file__)); OUTD=os.path.join(HERE,"output")
OUT="/Users/praveen-16349/Documents/Personal/Learnings/AWS - Blog"
import router_gt_annotations as ANN

def norm(q):
    return re.sub(r"\s+"," ",unicodedata.normalize("NFKC",q or "").strip())

def load_manifest():
    return [json.loads(l) for l in open(f"{OUT}/compound-router-groundtruth-v2.jsonl",encoding="utf-8")]


class DatasetScopeTests(unittest.TestCase):
    def setUp(self): self.recs=load_manifest()

    def test_52_generative_cases_loaded(self):
        self.assertEqual(len(self.recs), 52)
        self.assertEqual(len(ANN.A), 52)

    def test_8_global_search_cases_excluded(self):
        meta=json.load(open(os.path.join(OUTD,"router_gt_v2_meta.json")))
        self.assertEqual(len(meta["excluded_global_search"]), 8)
        ids={r["case_id"] for r in self.recs}
        for cid in meta["excluded_global_search"]: self.assertNotIn(cid, ids)

    def test_all_case_ids_unique(self):
        ids=[r["case_id"] for r in self.recs]
        self.assertEqual(len(ids), len(set(ids)))

    def test_every_annotated_case_is_in_the_manifest(self):
        self.assertEqual({r["case_id"] for r in self.recs}, set(ANN.A))


class LabelSchemaTests(unittest.TestCase):
    def setUp(self): self.recs=load_manifest()

    def test_label_enum_is_valid(self):
        for r in self.recs:
            self.assertIn(r["ground_truth"], ("simple","compound","ambiguous"), r["case_id"])

    def test_compound_need_count_at_least_two(self):
        for r in self.recs:
            if r["ground_truth"]=="compound":
                self.assertGreaterEqual(r["independent_retrieval_need_count"], 2, r["case_id"])

    def test_simple_need_count_is_one(self):
        for r in self.recs:
            if r["ground_truth"]=="simple":
                self.assertEqual(r["independent_retrieval_need_count"], 1, r["case_id"])

    def test_compound_lists_at_least_two_atomic_needs(self):
        for r in self.recs:
            if r["ground_truth"]=="compound":
                self.assertGreaterEqual(len(r["atomic_information_needs"]), 2, r["case_id"])

    def test_every_case_has_a_concise_reason_and_rule(self):
        for r in self.recs:
            self.assertTrue(r["label_reason"].strip(), r["case_id"])
            self.assertLess(len(r["label_reason"]), 200, r["case_id"])   # concise, no chain-of-thought
            self.assertTrue(r["decision_rule"].strip(), r["case_id"])

    def test_ambiguous_used_sparingly(self):
        amb=[r for r in self.recs if r["ground_truth"]=="ambiguous"]
        self.assertLessEqual(len(amb), 6, "AMBIGUOUS should be genuine disagreement, not convenience")


class DuplicateConsistencyTests(unittest.TestCase):
    def setUp(self): self.recs=load_manifest()

    def test_normalization_collapses_whitespace_and_unicode(self):
        self.assertEqual(norm("  a   b\t\nc "), "a b c")
        self.assertEqual(norm("café"), norm("café"))     # NFKC
        self.assertEqual(norm("x  y"), norm(" x y "))

    def test_duplicate_labels_cannot_disagree(self):
        by={}
        for r in self.recs: by.setdefault(r["normalized_question_hash"],[]).append(r)
        for h,grp in by.items():
            if len(grp)>1:
                self.assertEqual(len({g["ground_truth"] for g in grp}), 1,
                    f"conflicting labels in {[g['case_id'] for g in grp]}")
                self.assertEqual(len({g["independent_retrieval_need_count"] for g in grp}), 1)

    def test_case_022_and_023_share_normalized_hash(self):
        m={r["case_id"]:r for r in self.recs}
        self.assertEqual(m["case-022"]["normalized_question_hash"],
                         m["case-023"]["normalized_question_hash"])

    def test_case_022_and_023_share_the_label(self):
        m={r["case_id"]:r for r in self.recs}
        self.assertEqual(m["case-022"]["ground_truth"], m["case-023"]["ground_truth"])
        self.assertEqual(m["case-022"]["ground_truth"], "compound")

    def test_all_duplicate_groups_recorded_in_meta(self):
        meta=json.load(open(os.path.join(OUTD,"router_gt_v2_meta.json")))
        by={}
        for r in self.recs: by.setdefault(r["normalized_question_hash"],[]).append(r["case_id"])
        found={tuple(sorted(v)) for v in by.values() if len(v)>1}
        recorded={tuple(sorted(v)) for v in meta["duplicate_question_groups"].values()}
        self.assertEqual(found, recorded)

    def test_every_duplicate_member_has_a_group_id(self):
        by={}
        for r in self.recs: by.setdefault(r["normalized_question_hash"],[]).append(r)
        for grp in by.values():
            if len(grp)>1:
                for g in grp: self.assertTrue(g.get("duplicate_group_id"), g["case_id"])

    def test_route_does_not_influence_label(self):
        """case-022 (group) and case-023 (multi) differ only in route, same label."""
        corpus={c["case_id"]:c for c in json.load(open(os.path.join(HERE,"corpus60.json")))["cases"]}
        self.assertNotEqual(corpus["case-022"]["route"], corpus["case-023"]["route"])
        m={r["case_id"]:r for r in self.recs}
        self.assertEqual(m["case-022"]["ground_truth"], m["case-023"]["ground_truth"])
        meta=json.load(open(os.path.join(OUTD,"router_gt_v2_meta.json")))
        self.assertFalse(meta["route_used_for_labels"])


class ManifestFingerprintTests(unittest.TestCase):
    def test_manifest_fingerprint_is_stable_and_deterministic(self):
        recs=load_manifest()
        a=hashlib.sha256(json.dumps(recs,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        b=hashlib.sha256(json.dumps(load_manifest(),sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        self.assertEqual(a,b)

    def test_meta_records_policy_and_counts(self):
        meta=json.load(open(os.path.join(OUTD,"router_gt_v2_meta.json")))
        self.assertEqual(meta["annotation_policy_version"], "independent-retrieval-needs-v1")
        self.assertEqual(len(meta["manifest_sha256"]), 64)
        recs=load_manifest()
        for lab,key in [("simple","simple_count"),("compound","compound_count"),
                        ("ambiguous","ambiguous_count")]:
            self.assertEqual(meta[key], sum(1 for r in recs if r["ground_truth"]==lab), key)
        self.assertEqual(meta["unique_question_count"],
                         len({r["normalized_question_hash"] for r in recs}))

    def test_meta_declares_what_supersedes(self):
        meta=json.load(open(os.path.join(OUTD,"router_gt_v2_meta.json")))
        self.assertIn("supersedes", meta)
        self.assertIn("manifest_fingerprint", meta["supersedes"])


class RescoreTests(unittest.TestCase):
    def setUp(self):
        self.M=json.load(open(os.path.join(OUTD,"router_v2_rescored_metrics.json")))
        self.recs=load_manifest()

    def test_rescore_invokes_no_provider(self):
        src=open(os.path.join(HERE,"run_gt_audit.py")).read()
        import ast
        mods=set()
        for n in ast.walk(ast.parse(src)):
            if isinstance(n,ast.Import): mods|={a.name.split(".")[0] for a in n.names}
            elif isinstance(n,ast.ImportFrom) and n.module: mods.add(n.module.split(".")[0])
        for banned in ["nvidia_provider","nvidia_harness","router_v2","decomp_graph","app","boto3"]:
            self.assertNotIn(banned, mods, banned)
        self.assertEqual(self.M["provider_calls"],
            {"nvidia_20b":0,"nvidia_120b":0,"groq":0,"retrieval":0,"langgraph":0})

    def test_rescore_joins_every_non_ambiguous_case_exactly_once(self):
        n=self.M["TP"]+self.M["FP"]+self.M["TN"]+self.M["FN"]
        self.assertEqual(n, self.M["scored_cases"])
        self.assertEqual(n, sum(1 for r in self.recs if r["ground_truth"]!="ambiguous"))

    def test_ambiguous_excluded_from_denominator(self):
        amb=sum(1 for r in self.recs if r["ground_truth"]=="ambiguous")
        self.assertEqual(self.M["ambiguous_excluded"], amb)
        self.assertEqual(self.M["scored_cases"], len(self.recs)-amb)
        for cid in ["case-005","case-025"]:
            self.assertNotIn(cid, self.M["TP_cases"]+self.M["FP_cases"]+self.M["FN_cases"])

    def test_metrics_recompute_from_the_confusion_matrix(self):
        TP,FP_,TN,FN=self.M["TP"],self.M["FP"],self.M["TN"],self.M["FN"]
        self.assertAlmostEqual(self.M["compound_precision"], TP/(TP+FP_), places=4)
        self.assertAlmostEqual(self.M["compound_recall"], TP/(TP+FN), places=4)
        self.assertAlmostEqual(self.M["simple_specificity"], TN/(TN+FP_), places=4)
        self.assertAlmostEqual(self.M["routing_accuracy"], (TP+TN)/(TP+TN+FP_+FN), places=4)

    def test_existing_router_predictions_are_not_mutated(self):
        """The rescore must read predictions, never write them."""
        src=open(os.path.join(HERE,"run_gt_audit.py")).read()
        self.assertNotIn("router_v2_results.jsonl\",\"w", src.replace("'",'"'))
        self.assertNotIn("router_v2_results.jsonl\",\"a", src.replace("'",'"'))
        raw=[json.loads(l) for l in open(os.path.join(OUTD,"router_v2_results.jsonl"))]
        fp=json.load(open(os.path.join(OUTD,"router_v2_fingerprint.json")))["fingerprint_hash"]
        self.assertEqual(len([r for r in raw if r["fingerprint_hash"]==fp]), 52)

    def test_router_v2_source_and_prompt_untouched(self):
        """Frozen: router_v2.py must be untracked-only (never modified in-place)."""
        repo=subprocess.run(["git","rev-parse","--show-toplevel"],capture_output=True,text=True).stdout.strip()
        out=subprocess.run(["git","status","--porcelain","multitenant-rag/evals/router_v2.py"],
                           cwd=repo,capture_output=True,text=True).stdout.strip()
        if out: self.assertTrue(out.startswith("??"), out)

    def test_original_scoring_preserved_not_overwritten(self):
        old=self.M["original_scoring_superseded"]
        self.assertEqual(old["TP"],3); self.assertEqual(old["FP"],15)
        self.assertEqual(old["positives"],3); self.assertEqual(old["negatives"],49)
        self.assertTrue(os.path.exists(os.path.join(OUTD,"router_v2_metrics.json")))

    def test_original_15_false_positives_fully_dispositioned(self):
        d=self.M["original_15_false_positive_disposition"]
        self.assertEqual(sum(v["count"] for v in d.values()), 15)
        allc=[c for v in d.values() for c in v["cases"]]
        self.assertEqual(len(allc), len(set(allc)))

    def test_verdict_matches_thresholds(self):
        t=self.M["threshold_results"]
        expect=("PROMISING — REQUIRES UNSEEN HOLDOUT VALIDATION" if all(t.values()) else "NOT ACCEPTED")
        self.assertEqual(self.M["verdict"], expect)


class ValidationGateTests(unittest.TestCase):
    def test_audit_stops_on_duplicate_label_conflict(self):
        src=open(os.path.join(HERE,"run_gt_audit.py")).read()
        self.assertIn("DUPLICATE LABEL CONFLICT", src)
        self.assertIn("VALIDATION FAILED", src)
        self.assertIn("sys.exit(1)", src)

    def test_audit_asserts_52_and_no_global_cases(self):
        src=open(os.path.join(HERE,"run_gt_audit.py")).read()
        self.assertIn("expected 52 generative cases", src)
        self.assertIn("global-search case present", src)


if __name__=="__main__":
    unittest.main(verbosity=2)
