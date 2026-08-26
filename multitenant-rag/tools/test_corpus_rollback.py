"""Safety tests for the curated-corpus rollback (§34). Offline — no AWS."""
import json
import os
import unittest
from unittest import mock

import cleanup_curated_corpus as CC

HERE = os.path.dirname(os.path.abspath(__file__))
MANIFEST = json.load(open(os.path.join(HERE, "corpus_25_manifest.json"), encoding="utf-8"))
RETAINED = ["pk@gmail.com", "pk1@gmail.com", "snehasattai@gmail.com",
            "naresh_nagarjuna@gmail.com", "realuser@example.com"]


class DefaultsTests(unittest.TestCase):
    def test_defaults_to_dry_run(self):
        a = CC.parse_args([])
        self.assertFalse(a.apply)

    def test_apply_requires_explicit_flag(self):
        self.assertTrue(CC.parse_args(["--apply"]).apply)

    def test_plan_and_verify_are_non_destructive(self):
        self.assertFalse(CC.parse_args(["--plan"]).apply)
        self.assertFalse(CC.parse_args(["--verify"]).apply)


class ManifestGateTests(unittest.TestCase):
    def test_manifest_must_describe_25_users_and_268_posts(self):
        self.assertEqual(CC.EXPECTED_USERS, 25)
        self.assertEqual(CC.EXPECTED_POSTS, 268)
        m = CC.load_manifest()
        self.assertEqual(len(m["users"]), 25)
        self.assertEqual(len(m["posts"]), 268)

    def test_wrong_fingerprint_refuses_to_run(self):
        bad = {**MANIFEST, "source_sha256": "0" * 64}
        with mock.patch.object(CC.json, "load", return_value=bad):
            with self.assertRaises(SystemExit):
                CC.load_manifest()

    def test_short_manifest_refuses_to_run(self):
        bad = {**MANIFEST, "users": MANIFEST["users"][:5]}
        with mock.patch.object(CC.json, "load", return_value=bad):
            with self.assertRaises(SystemExit):
                CC.load_manifest()

    def test_manifest_containing_a_retained_account_refuses_to_run(self):
        bad = {**MANIFEST,
               "users": [{**MANIFEST["users"][0], "email": "pk@gmail.com"}]
                        + MANIFEST["users"][1:]}
        with mock.patch.object(CC.json, "load", return_value=bad):
            with self.assertRaises(SystemExit):
                CC.load_manifest()


class DeletionSetTests(unittest.TestCase):
    def test_every_manifest_persona_is_deletable(self):
        for u in MANIFEST["users"]:
            self.assertTrue(CC.is_deletable_email(u["email"], MANIFEST), u["email"])

    def test_retained_accounts_can_never_enter_the_deletion_set(self):
        for e in RETAINED:
            self.assertFalse(CC.is_deletable_email(e, MANIFEST), e)

    def test_a_retained_account_is_rejected_even_if_injected_into_the_manifest(self):
        forged = {"users": [{"email": "pk@gmail.com"}], "posts": []}
        self.assertFalse(CC.is_deletable_email("pk@gmail.com", forged))

    def test_non_manifest_users_cannot_enter_the_deletion_set(self):
        for e in ["stranger@example.com", "someone@gmail.com", "", None,
                  "kavin.raj25@evil.com", "KAVIN.RAJ25@example.com"]:
            self.assertFalse(CC.is_deletable_email(e, MANIFEST), repr(e))

    def test_example_com_alone_is_not_sufficient_evidence(self):
        self.assertFalse(CC.is_deletable_email("newperson@example.com", MANIFEST))

    def test_no_inverse_allowlist_logic_exists(self):
        """The tool must never contain 'delete everything not in the keep list'."""
        import ast
        src = open(os.path.join(HERE, "cleanup_curated_corpus.py"), encoding="utf-8").read()
        names = {n.id.lower() for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Name)}
        for banned in ("delete_all", "not_in_keep", "delete_others", "purge_all"):
            self.assertNotIn(banned, names)


class _Rec(unittest.TestCase):
    """Drive reconcile() against a faked DynamoDB scan."""

    def _reconcile(self, users, posts):
        ddb = mock.Mock()
        def scan(**kw):
            t = kw["TableName"]
            return {"Items": users if "users" in t else posts}
        ddb.scan.side_effect = scan
        with mock.patch.object(CC, "_clients", return_value=(ddb, None, None)):
            return CC.reconcile(MANIFEST)

    @staticmethod
    def user(email, uid, tid):
        return {"user_id": {"S": uid}, "email": {"S": email}, "tenant_id": {"S": tid}}

    @staticmethod
    def post(tid, pid, h):
        return {"tenant_id": {"S": tid}, "post_id": {"S": pid},
                "content_hash": {"S": h}, "s3_key": {"S": f"tenants/{tid}/posts/{pid}.md"}}


class ExtraPostGuardTests(_Rec):
    def test_target_with_an_extra_non_manifest_post_is_skipped(self):
        """§7 — an account used beyond the seed must NOT be rolled back."""
        mu = MANIFEST["users"][0]
        hashes = [p["body_sha256"] for p in MANIFEST["posts"]
                  if p["username"] == mu["username"]]
        users = [self.user(mu["email"], "u1", "t1")]
        posts = [self.post("t1", f"p{i}", h) for i, h in enumerate(hashes)]
        posts.append(self.post("t1", "p_new", "deadbeef" * 8))     # later usage
        rec = self._reconcile(users, posts)
        self.assertEqual(rec["targets"], [])
        self.assertTrue(any("NOT in the corpus manifest" in b for b in rec["blocked"]))

    def test_target_with_a_missing_post_count_is_skipped(self):
        mu = MANIFEST["users"][0]
        hashes = [p["body_sha256"] for p in MANIFEST["posts"]
                  if p["username"] == mu["username"]][:3]
        users = [self.user(mu["email"], "u1", "t1")]
        posts = [self.post("t1", f"p{i}", h) for i, h in enumerate(hashes)]
        rec = self._reconcile(users, posts)
        self.assertEqual(rec["targets"], [])

    def test_exact_match_becomes_a_target(self):
        mu = MANIFEST["users"][0]
        hashes = [p["body_sha256"] for p in MANIFEST["posts"]
                  if p["username"] == mu["username"]]
        users = [self.user(mu["email"], "u1", "t1")]
        posts = [self.post("t1", f"p{i}", h) for i, h in enumerate(hashes)]
        rec = self._reconcile(users, posts)
        self.assertEqual(len(rec["targets"]), 1)
        self.assertEqual(len(rec["targets"][0]["posts"]), mu["expected_post_count"])

    def test_retained_user_present_in_production_is_never_targeted(self):
        mu = MANIFEST["users"][0]
        hashes = [p["body_sha256"] for p in MANIFEST["posts"]
                  if p["username"] == mu["username"]]
        users = [self.user(mu["email"], "u1", "t1"),
                 self.user("pk@gmail.com", "u_keep", "t_keep")]
        posts = [self.post("t1", f"p{i}", h) for i, h in enumerate(hashes)]
        posts.append(self.post("t_keep", "p_keep", "aa" * 32))
        rec = self._reconcile(users, posts)
        self.assertEqual([t["email"] for t in rec["targets"]], [mu["email"]])
        self.assertNotIn("t_keep", {t["tenant_id"] for t in rec["targets"]})

    def test_already_absent_target_is_reported_not_failed(self):
        rec = self._reconcile([], [])
        self.assertEqual(rec["targets"], [])
        self.assertTrue(all("already absent" in b for b in rec["blocked"]))


class ScopingTests(unittest.TestCase):
    """Deletion must be scoped to exact identifiers, never broad."""

    def setUp(self):
        self.src = open(os.path.join(HERE, "cleanup_curated_corpus.py"),
                        encoding="utf-8").read()

    def test_qdrant_delete_is_filtered_by_tenant_and_exact_post_ids(self):
        self.assertIn('FieldCondition(key="post_id", match=MatchAny(any=pids))', self.src)
        self.assertIn('FieldCondition(key="tenant_id", match=MatchValue(value=tid))', self.src)

    def test_qdrant_deletion_is_verified_to_zero(self):
        self.assertIn("Qdrant points remain", self.src)

    def test_s3_deletes_exact_keys_not_a_prefix(self):
        self.assertIn("s3.delete_object(Bucket=bucket, Key=key)", self.src)
        self.assertNotIn("list_objects_v2", self.src)
        self.assertNotIn("delete_objects(", self.src)

    def test_semantic_cache_is_tenant_scoped_not_global(self):
        self.assertIn("semcache.invalidate_tenant(tid)", self.src)

    def test_group_itself_is_never_deleted(self):
        self.assertNotIn('tables["groups"]', self.src)

    def test_tenant_deleted_only_after_posts_are_gone(self):
        self.assertIn("posts still under the tenant", self.src)


class ResumeTests(unittest.TestCase):
    def test_state_round_trips(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(CC, "STATE", os.path.join(d, "s.json")):
                self.assertEqual(CC.load_state(), {"completed": [], "partial": None})
                CC.save_state({"completed": ["kavin.raj25"], "partial": None})
                self.assertEqual(CC.load_state()["completed"], ["kavin.raj25"])

    def test_completed_users_are_skipped_on_rerun(self):
        src = open(os.path.join(HERE, "cleanup_curated_corpus.py"), encoding="utf-8").read()
        self.assertIn('if t["username"] in done:', src)
        self.assertIn("already completed", src)

    def test_failure_stops_rather_than_continuing(self):
        src = open(os.path.join(HERE, "cleanup_curated_corpus.py"), encoding="utf-8").read()
        self.assertIn("*** STOPPED at", src)
        self.assertIn("return 1", src)


class NoProviderCallTests(unittest.TestCase):
    def test_tool_imports_no_llm_or_embedding_provider(self):
        import ast
        src = open(os.path.join(HERE, "cleanup_curated_corpus.py"), encoding="utf-8").read()
        mods = set()
        for n in ast.walk(ast.parse(src)):
            if isinstance(n, ast.Import):
                mods |= {a.name.split(".")[0] for a in n.names}
            elif isinstance(n, ast.ImportFrom) and n.module and not n.level:
                mods.add(n.module.split(".")[0])
        for banned in ("groq", "openai", "requests", "ragas", "deepeval", "fastembed"):
            self.assertNotIn(banned, mods, banned)

    def test_no_embedding_call_is_made_to_confirm_deletion(self):
        src = open(os.path.join(HERE, "cleanup_curated_corpus.py"), encoding="utf-8").read()
        self.assertNotIn("invoke_model", src)
        self.assertNotIn("titan", src.lower())


if __name__ == "__main__":
    unittest.main()
