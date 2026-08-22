"""PHASE 32 — tests for the NVIDIA evaluation tooling. No provider calls (all mocked).

    AWS_REGION=ap-south-1 python -m unittest test_nvidia_evals -v
"""
import json, os, tempfile, time, unittest
from unittest import mock

os.environ.setdefault("TENANTS_TABLE", "t")
os.environ.setdefault("USAGE_TABLE", "u")
os.environ.setdefault("AWS_REGION", "ap-south-1")

import nvidia_provider as nv
import nvidia_harness as H


def _reset():
    nv._consecutive_429[0] = 0
    nv._last_call[0] = 0.0
    for k in nv.STATS:
        nv.STATS[k] = 0 if not isinstance(nv.STATS[k], float) else 0.0


class Resp:
    def __init__(self, status=200, content="ok", headers=None, usage=True):
        self.status_code = status; self.headers = headers or {}
        self._c = content; self._u = usage
    def json(self):
        return {"choices": [{"message": {"content": self._c, "reasoning": "hidden"},
                             "finish_reason": "stop"}],
                "usage": ({"prompt_tokens": 10, "completion_tokens": 5} if self._u else {})}


class GroqZeroCallTests(unittest.TestCase):
    def test_guard_makes_groq_fail_fast(self):
        H.install_groq_guard()
        import app as prod
        with self.assertRaises(H.GroqCallForbidden):
            prod.stream_answer("sys", "q")

    def test_guard_also_blocks_llm_module(self):
        H.install_groq_guard()
        import llm
        with self.assertRaises(H.GroqCallForbidden):
            llm.stream_answer("sys", "q")


class FingerprintTests(unittest.TestCase):
    def test_fingerprint_fields_and_determinism(self):
        a = H.fingerprint("ds-1", "v1"); b = H.fingerprint("ds-1", "v1")
        self.assertEqual(a["fingerprint_hash"], b["fingerprint_hash"])
        self.assertEqual(a["application_provider"], "nvidia")
        self.assertEqual(a["application_model"], "openai/gpt-oss-20b")
        self.assertEqual(a["judge_model"], "openai/gpt-oss-120b")
        self.assertEqual(a["top_k"], 5)
        self.assertEqual(a["retrieval_floor"], 0.15)

    def test_fingerprint_changes_with_dataset(self):
        self.assertNotEqual(H.fingerprint("ds-1", "v1")["fingerprint_hash"],
                            H.fingerprint("ds-2", "v1")["fingerprint_hash"])


GOOD = json.dumps({"correctness": {"score": 1.0, "reason": "matches"},
                   "completeness": {"score": 0.5, "reason": "partial"},
                   "groundedness": {"score": 0.0, "reason": "unsupported"}})


class JudgeParsingTests(unittest.TestCase):
    def test_parses_three_dimensions(self):
        d = H.parse_judge(GOOD)
        self.assertEqual(d["correctness"]["score"], 1.0)
        self.assertEqual(d["completeness"]["score"], 0.5)
        self.assertEqual(d["groundedness"]["score"], 0.0)

    def test_parses_with_surrounding_prose(self):
        self.assertEqual(H.parse_judge("Result:\n" + GOOD + "\ndone")["correctness"]["score"], 1.0)

    def test_rejects_out_of_range_rather_than_rescaling(self):
        bad = GOOD.replace('"score": 1.0', '"score": 0.87', 1)
        with self.assertRaises(ValueError):
            H.parse_judge(bad)

    def test_rejects_missing_dimension(self):
        d = json.loads(GOOD); d.pop("groundedness")
        with self.assertRaises(Exception):
            H.parse_judge(json.dumps(d))

    def test_reason_is_truncated_and_no_cot_dump(self):
        d = json.loads(GOOD); d["correctness"]["reason"] = "x" * 5000
        self.assertLessEqual(len(H.parse_judge(json.dumps(d))["correctness"]["reason"]), 300)


class SchedulerAndRetryTests(unittest.TestCase):
    def setUp(self): _reset()

    def test_min_interval_enforced_between_requests(self):
        slept = []
        with mock.patch.object(nv.requests, "post", return_value=Resp()), \
             mock.patch.object(nv, "_key", lambda: "k"), \
             mock.patch.object(nv.time, "sleep", lambda s: slept.append(s)):
            nv._last_call[0] = time.time()          # pretend a call just happened
            nv.chat("m", [{"role": "user", "content": "x"}])
        self.assertTrue(slept and slept[0] > 0, "scheduler must pace the next request")

    def test_success_returns_usage_and_excludes_pacing_from_latency(self):
        with mock.patch.object(nv.requests, "post", return_value=Resp()), \
             mock.patch.object(nv, "_key", lambda: "k"), \
             mock.patch.object(nv.time, "sleep", lambda *_: None):
            r = nv.chat("m", [{"role": "user", "content": "x"}])
        self.assertEqual(r["input_tokens"], 10)
        self.assertEqual(r["output_tokens"], 5)
        self.assertEqual(r["content"], "ok")
        self.assertLess(r["latency_ms"], 5000)

    def test_429_retry_is_bounded_then_errors(self):
        _reset()
        nv.CIRCUIT_THRESHOLD_BACKUP = nv.CIRCUIT_THRESHOLD
        with mock.patch.object(nv, "CIRCUIT_THRESHOLD", 99), \
             mock.patch.object(nv.requests, "post", return_value=Resp(429, headers={"retry-after": "0"})), \
             mock.patch.object(nv, "_key", lambda: "k"), \
             mock.patch.object(nv.time, "sleep", lambda *_: None):
            with self.assertRaises(nv.NvidiaError):
                nv.chat("m", [{"role": "user", "content": "x"}])
        self.assertLessEqual(nv.STATS["requests"], nv.MAX_ATTEMPTS)

    def test_circuit_breaker_opens_after_three_consecutive_429(self):
        _reset()
        with mock.patch.object(nv.requests, "post", return_value=Resp(429)), \
             mock.patch.object(nv, "_key", lambda: "k"), \
             mock.patch.object(nv.time, "sleep", lambda *_: None):
            with self.assertRaises(nv.CircuitOpen):
                nv.chat("m", [{"role": "user", "content": "x"}])
        self.assertEqual(nv.STATS["circuit_breaker_events"], 1)

    def test_auth_error_stops_immediately(self):
        _reset()
        with mock.patch.object(nv.requests, "post", return_value=Resp(401)), \
             mock.patch.object(nv, "_key", lambda: "k"), \
             mock.patch.object(nv.time, "sleep", lambda *_: None):
            with self.assertRaises(nv.NvidiaAuthError):
                nv.chat("m", [{"role": "user", "content": "x"}])
        self.assertEqual(nv.STATS["requests"], 1, "must not retry an auth failure")

    def test_success_resets_consecutive_429_counter(self):
        _reset(); nv._consecutive_429[0] = 2
        with mock.patch.object(nv.requests, "post", return_value=Resp()), \
             mock.patch.object(nv, "_key", lambda: "k"), \
             mock.patch.object(nv.time, "sleep", lambda *_: None):
            nv.chat("m", [{"role": "user", "content": "x"}])
        self.assertEqual(nv._consecutive_429[0], 0)


class CheckpointTests(unittest.TestCase):
    def test_append_then_resume_skips_completed(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "sub", "ckpt.jsonl")
            H.append_checkpoint(p, {"case_id": "case-001", "fingerprint_hash": "fp1",
                                    "result": {"status": "completed"}})
            H.append_checkpoint(p, {"case_id": "case-002", "fingerprint_hash": "fp1",
                                    "result": {"status": "completed"}})
            done = H.load_checkpoint(p, "fp1")
            self.assertEqual(sorted(done), ["case-001", "case-002"])

    def test_other_fingerprint_is_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "c.jsonl")
            H.append_checkpoint(p, {"case_id": "case-001", "fingerprint_hash": "OTHER",
                                    "result": {}})
            self.assertEqual(H.load_checkpoint(p, "fp1"), {})

    def test_torn_last_line_is_tolerated(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "c.jsonl")
            H.append_checkpoint(p, {"case_id": "case-001", "fingerprint_hash": "fp1", "result": {}})
            with open(p, "a") as f:
                f.write('{"case_id": "case-002", "fingerp')      # simulated kill mid-write
            self.assertEqual(sorted(H.load_checkpoint(p, "fp1")), ["case-001"])

    def test_missing_file_is_empty_not_error(self):
        self.assertEqual(H.load_checkpoint("/nonexistent/x.jsonl", "fp"), {})


class EvaluatorSemanticsTests(unittest.TestCase):
    def test_request_success_rules(self):
        self.assertEqual(H.request_success({"generated_answer": "a", "status": "completed"}), 1)
        self.assertEqual(H.request_success({"generated_answer": H.FALLBACK_TEXT,
                                            "status": "generation_error"}), 0)
        self.assertEqual(H.request_success({"generated_answer": " ", "status": "completed"}), 0)
        self.assertEqual(H.request_success({"generated_answer": "a",
                                            "status": "application_rate_limit"}), 0)

    def test_llm_free_route_is_not_judged(self):
        r = H.judge({"llm_used": False, "status": "completed"}, "q", "e")
        self.assertEqual(r["status"], "not_scored")
        self.assertIn("LLM-free", r["reason"])

    def test_provider_failure_is_not_scored_zero(self):
        r = H.judge({"llm_used": True, "status": "application_rate_limit"}, "q", "e")
        self.assertEqual(r["status"], "not_scored")
        self.assertNotIn("scores", r)

    def test_judge_parse_error_after_bounded_retries(self):
        with mock.patch.object(nv, "chat", return_value={"content": "not json", "input_tokens": 1,
                                                         "output_tokens": 1, "latency_ms": 1,
                                                         "retry_count": 0, "rate_limited": False}) as m:
            r = H.judge({"llm_used": True, "status": "completed",
                         "generated_answer": "a", "retrieved_contexts": ["[T] c"]}, "q", "e")
        self.assertEqual(r["status"], "judge_parse_error")
        self.assertEqual(m.call_count, 3, "bounded retry on malformed output")

    def test_one_judge_call_yields_all_three_dimensions(self):
        with mock.patch.object(nv, "chat", return_value={"content": GOOD, "input_tokens": 9,
                                                         "output_tokens": 4, "latency_ms": 12,
                                                         "retry_count": 0, "rate_limited": False}) as m:
            r = H.judge({"llm_used": True, "status": "completed",
                         "generated_answer": "a", "retrieved_contexts": ["[T] c"]}, "q", "e")
        self.assertEqual(m.call_count, 1, "must be ONE structured call for all metrics")
        self.assertEqual(set(r["scores"]), {"correctness", "completeness", "groundedness"})
        self.assertEqual(r["judge_input_tokens"], 9)

    def test_groundedness_prompt_uses_contexts_not_reference(self):
        captured = {}
        def fake(model, messages, **k):
            captured["user"] = messages[1]["content"]; captured["sys"] = messages[0]["content"]
            return {"content": GOOD, "input_tokens": 1, "output_tokens": 1,
                    "latency_ms": 1, "retry_count": 0, "rate_limited": False}
        with mock.patch.object(nv, "chat", side_effect=fake):
            H.judge({"llm_used": True, "status": "completed", "generated_answer": "ans",
                     "retrieved_contexts": ["[T1] evidence text"]}, "the question", "the reference")
        self.assertIn("RETRIEVED CONTEXT", captured["user"])
        self.assertIn("evidence text", captured["user"])
        self.assertIn("do not use the reference for this score", captured["sys"])


class RedactionTests(unittest.TestCase):
    def test_stats_and_fingerprint_contain_no_credentials(self):
        blob = json.dumps({"stats": nv.STATS, "fp": H.fingerprint("d", "v")})
        for pat in ("nvapi-", "gsk_", "lsv2_", "Bearer ", "eyJ", "api_key"):
            self.assertNotIn(pat, blob)

    def test_feedback_key_names(self):
        self.assertEqual(
            ["request_success", "answer_correctness", "answer_completeness", "answer_groundedness"],
            ["request_success", "answer_correctness", "answer_completeness", "answer_groundedness"])


if __name__ == "__main__":
    unittest.main()
