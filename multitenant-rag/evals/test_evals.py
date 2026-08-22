"""PHASE 32 — focused tests for the evaluation tooling.

Run (from multitenant-rag/evals):
    AWS_REGION=ap-south-1 python -m unittest test_evals -v

No provider calls: the judge transport is mocked. Stdlib unittest only.
"""
import csv
import io
import json
import os
import statistics as st
import unittest
from unittest import mock

os.environ.setdefault("TENANTS_TABLE", "t")
os.environ.setdefault("USAGE_TABLE", "u")
os.environ.setdefault("AWS_REGION", "ap-south-1")

import harness
from harness import _parse_judge, request_success, combined_judge, FALLBACK_TEXT


CORPUS = os.path.join(os.path.dirname(__file__), "corpus60.json")


class CorpusValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.path.exists(CORPUS):
            raise unittest.SkipTest("corpus60.json not present next to the harness")
        cls.cases = json.load(open(CORPUS))["cases"]

    def test_sixty_cases_stable_ids_no_gaps(self):
        ids = [c["case_id"] for c in self.cases]
        self.assertEqual(len(ids), 60)
        self.assertEqual(len(set(ids)), 60, "duplicate case IDs")
        self.assertEqual(ids, [f"case-{i:03d}" for i in range(1, 61)])

    def test_every_case_has_question_and_expected(self):
        for c in self.cases:
            self.assertTrue((c.get("question") or "").strip(), c["case_id"])
            self.assertTrue((c.get("expected_answer") or "").strip(), c["case_id"])

    def test_routes_are_known(self):
        for c in self.cases:
            self.assertIn(c["route"], {"single", "multi", "group", "global"}, c["case_id"])


class JudgeParsingTests(unittest.TestCase):
    GOOD = ('{"correctness_score":1.0,"correctness_reason":"ok",'
            '"completeness_score":0.5,"completeness_reason":"partial"}')

    def test_parses_clean_json(self):
        d = _parse_judge(self.GOOD)
        self.assertEqual(d["correctness_score"], 1.0)
        self.assertEqual(d["completeness_score"], 0.5)

    def test_parses_json_wrapped_in_prose(self):
        d = _parse_judge("Here you go:\n" + self.GOOD + "\nThanks!")
        self.assertEqual(d["correctness_score"], 1.0)

    def test_rejects_out_of_range_score_instead_of_rescaling(self):
        bad = self.GOOD.replace('"correctness_score":1.0', '"correctness_score":0.83')
        with self.assertRaises(ValueError):      # must NOT silently snap to 1.0/0.5
            _parse_judge(bad)

    def test_rejects_missing_json(self):
        with self.assertRaises(ValueError):
            _parse_judge("I think the answer is good.")

    def test_only_allowed_score_values(self):
        for v in ("1.0", "0.5", "0.0"):
            d = _parse_judge(self.GOOD.replace('"correctness_score":1.0', f'"correctness_score":{v}'))
            self.assertIn(d["correctness_score"], {1.0, 0.5, 0.0})


class RequestSuccessTests(unittest.TestCase):
    def test_valid_answer_is_success(self):
        r = request_success(outputs={"generated_answer": "a real answer", "status": "completed"})
        self.assertEqual(r["score"], 1)
        self.assertEqual(r["key"], "request_success")

    def test_fallback_text_is_not_success(self):
        r = request_success(outputs={"generated_answer": f"\n\n{FALLBACK_TEXT}",
                                     "status": "generation_error"})
        self.assertEqual(r["score"], 0)

    def test_empty_answer_is_not_success(self):
        self.assertEqual(request_success(outputs={"generated_answer": "  ",
                                                  "status": "completed"})["score"], 0)

    def test_provider_failure_is_not_success(self):
        self.assertEqual(request_success(outputs={"generated_answer": "partial",
                                                  "status": "provider_rate_limit"})["score"], 0)


class FailureClassificationTests(unittest.TestCase):
    """Provider failure must never be scored as a quality failure (score 0.0)."""

    def test_non_completed_status_is_not_scored(self):
        fb = combined_judge(inputs={"question": "q"},
                            outputs={"generated_answer": FALLBACK_TEXT,
                                     "status": "provider_rate_limit"},
                            reference_outputs={"expected_answer": "x"})
        keys = {f["key"]: f for f in fb}
        self.assertEqual(set(keys), {"answer_correctness", "answer_completeness"})
        for f in fb:
            self.assertIsNone(f["score"], "provider failure must be None, not 0.0")
            self.assertIn("provider_rate_limit", f["comment"])

    def test_malformed_judge_output_becomes_evaluator_error_not_zero(self):
        with mock.patch.object(harness, "judge_call", return_value="not json"):
            fb = combined_judge(inputs={"question": "q"},
                                outputs={"generated_answer": "a", "status": "completed"},
                                reference_outputs={"expected_answer": "x"})
        for f in fb:
            self.assertIsNone(f["score"])
            self.assertIn("custom_evaluator_error", f["comment"])

    def test_feedback_key_names_are_exact(self):
        with mock.patch.object(harness, "judge_call", return_value=JudgeParsingTests.GOOD):
            fb = combined_judge(inputs={"question": "q"},
                                outputs={"generated_answer": "a", "status": "completed"},
                                reference_outputs={"expected_answer": "x"})
        self.assertEqual([f["key"] for f in fb], ["answer_correctness", "answer_completeness"])
        self.assertEqual(fb[0]["score"], 1.0)
        self.assertEqual(fb[1]["score"], 0.5)

    def test_one_judge_call_produces_both_scores(self):
        with mock.patch.object(harness, "judge_call",
                               return_value=JudgeParsingTests.GOOD) as jc:
            combined_judge(inputs={"question": "q"},
                           outputs={"generated_answer": "a", "status": "completed"},
                           reference_outputs={"expected_answer": "x"})
        self.assertEqual(jc.call_count, 1, "must be ONE combined call, not two")


class RateLimitBoundsTests(unittest.TestCase):
    def test_retry_is_bounded_and_raises_ratelimited(self):
        calls = {"n": 0}

        class R:
            status_code = 429
            headers = {"retry-after": "0"}
            def json(self): return {}
        def fake_post(*a, **k):
            calls["n"] += 1
            return R()
        with mock.patch.object(harness.requests, "post", side_effect=fake_post), \
             mock.patch.object(harness, "_pace", lambda: None), \
             mock.patch.object(harness.time, "sleep", lambda *_: None), \
             mock.patch.object(harness, "get_groq_key", lambda: "x"):
            with self.assertRaises(harness.RateLimited):
                harness.judge_call("s", "u")
        self.assertLessEqual(calls["n"], harness.MAX_RETRIES, "retries must be bounded")

    def test_quota_headers_captured_without_auth_values(self):
        class R:
            headers = {"x-ratelimit-remaining-requests": "900",
                       "x-ratelimit-remaining-tokens": "7000",
                       "authorization": "Bearer gsk_secret"}
        harness.QUOTA.clear()
        harness._capture_quota(R())
        self.assertEqual(harness.QUOTA.get("x-ratelimit-remaining-requests"), "900")
        blob = json.dumps(harness.QUOTA)
        self.assertNotIn("gsk_", blob)
        self.assertNotIn("Bearer", blob)


class ContextShapeTests(unittest.TestCase):
    def test_contexts_are_real_strings_from_payload(self):
        pt = type("P", (), {"payload": {"title": "T1", "chunk_text": "body text"}})()
        ctx = harness._contexts_from([pt])
        self.assertEqual(ctx, ["[T1] body text"])
        self.assertNotIn("expected", ctx[0].lower())   # never the reference answer


class PercentileTests(unittest.TestCase):
    """The percentile helper used for latency reporting."""

    @staticmethod
    def pct(vals, p):
        s = sorted(vals)
        if len(s) == 1: return s[0]
        k = (len(s) - 1) * p / 100
        lo, hi = int(k), min(int(k) + 1, len(s) - 1)
        return s[lo] + (s[hi] - s[lo]) * (k - lo)

    def test_known_values(self):
        v = list(range(1, 101))                 # 1..100
        self.assertEqual(self.pct(v, 50), 50.5)
        self.assertAlmostEqual(self.pct(v, 95), 95.05, places=2)
        self.assertEqual(self.pct(v, 100), 100)

    def test_single_and_median_consistency(self):
        self.assertEqual(self.pct([42], 99), 42)
        v = [3, 1, 2]
        self.assertEqual(self.pct(v, 50), st.median(v))


class ExportRedactionTests(unittest.TestCase):
    FORBIDDEN = ("gsk_", "lsv2_", "Bearer ", "eyJ", "AKIA", "password")

    def test_export_row_carries_no_credentials(self):
        row = {"case_id": "case-001", "question": "q", "expected_answer": "e",
               "generated_answer": "a", "retrieved_contexts": "[T] c",
               "citations": "T", "status": "completed"}
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=list(row)); w.writeheader(); w.writerow(row)
        blob = buf.getvalue()
        for pat in self.FORBIDDEN:
            self.assertNotIn(pat, blob)

    def test_real_exports_are_clean_if_present(self):
        out = os.path.join(os.path.dirname(__file__), "output")
        found = False
        for name in ("rag-baseline-v1-full.csv", "rag-baseline-v1-full.jsonl",
                     "rag-baseline-v1-metrics.csv"):
            p = os.path.join(out, name)
            if os.path.exists(p):
                found = True
                blob = open(p, encoding="utf-8").read()
                for pat in self.FORBIDDEN:
                    self.assertNotIn(pat, blob, f"{name} contains {pat}")
        if not found:
            self.skipTest("exports not generated yet")


if __name__ == "__main__":
    unittest.main()
