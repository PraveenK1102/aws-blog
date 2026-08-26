"""Tests for curated-corpus seeding + legacy cleanup safety.

Offline: no AWS, no network. Run:
  PYTHONPATH=multitenant-rag/tools python -m unittest test_corpus_seed -v
"""
import json
import os
import unittest

import corpus_identity as CI
import corpus_parser as CP
from corpus_dates import corpus_date_to_epoch, epoch_to_corpus_date

HERE = os.path.dirname(os.path.abspath(__file__))
MANIFEST = os.path.join(HERE, "corpus_25_manifest.json")
def _read(p):
    with open(p, encoding="utf-8") as fh:
        return fh.read()


def _load(p):
    return json.loads(_read(p))


PROTECTED = {
    "kavin.raj25", "vignesh.k25", "gokul.krishnan25", "janani.raman25",
    "aishwarya.selvam25", "naveen.kumar25", "divya.rajan25", "nithin.k25",
    "karthik.raj25", "saikiran.reddy25", "nandhini.k25", "pavithra.selvan25",
    "madhan.kumar25", "deepika.chandran25", "anusha.reddy25", "vishnu.priyan25",
    "priyadharshini.m25", "swathi.raj25", "ashwin.raj25", "dinesh.k25",
    "yogesh.k25", "meena.lakshmi25", "dharani.vel25", "aravind.k25", "abinaya.raj25",
}
UNKNOWN_REVIEW = {"pk@gmail.com", "pk1@gmail.com", "snehasattai@gmail.com",
                  "naresh_nagarjuna@gmail.com", "realuser@example.com"}


class EmailDerivationTests(unittest.TestCase):
    def test_local_part_is_the_exact_corpus_username(self):
        self.assertEqual(CI.derive_email("kavin.raj25"), "kavin.raj25@example.com")
        self.assertEqual(CI.derive_email("priyadharshini.m25"),
                         "priyadharshini.m25@example.com")

    def test_username_is_not_normalised(self):
        """§4 forbids normalising usernames — dots and digits survive verbatim."""
        for u in ("saikiran.reddy25", "meena.lakshmi25", "abinaya.raj25"):
            self.assertTrue(CI.derive_email(u).startswith(u + "@"), u)

    def test_domain_is_the_synthetic_non_deliverable_one(self):
        self.assertEqual(CI.DOMAIN, "example.com")

    def test_empty_username_rejected(self):
        for bad in ("", "   ", None):
            with self.assertRaises(ValueError):
                CI.derive_email(bad)

    def test_every_protected_username_yields_a_unique_email(self):
        emails = {CI.derive_email(u) for u in PROTECTED}
        self.assertEqual(len(emails), 25)


class PasswordDerivationTests(unittest.TestCase):
    M = "test-master-secret-not-real"

    def test_deterministic_for_same_master_and_username(self):
        self.assertEqual(CI.derive_password("kavin.raj25", self.M),
                         CI.derive_password("kavin.raj25", self.M))

    def test_different_usernames_derive_different_passwords(self):
        pws = {CI.derive_password(u, self.M) for u in PROTECTED}
        self.assertEqual(len(pws), 25, "password collision across personas")

    def test_different_master_derives_different_password(self):
        self.assertNotEqual(CI.derive_password("kavin.raj25", self.M),
                            CI.derive_password("kavin.raj25", self.M + "x"))

    def test_meets_application_length_requirement(self):
        for u in list(PROTECTED)[:5]:
            self.assertGreaterEqual(len(CI.derive_password(u, self.M)), 8)

    def test_password_does_not_contain_the_master_secret(self):
        self.assertNotIn(self.M, CI.derive_password("kavin.raj25", self.M))

    def test_missing_master_secret_raises_rather_than_defaulting(self):
        saved = os.environ.pop(CI.MASTER_ENV, None)
        try:
            with self.assertRaises(CI.MasterSecretMissing):
                CI.derive_password("kavin.raj25")
        finally:
            if saved is not None:
                os.environ[CI.MASTER_ENV] = saved


class SecretLeakTests(unittest.TestCase):
    """A password must never be reachable from any artifact we write."""

    def test_manifest_contains_no_secret_material(self):
        blob = _read(MANIFEST).lower()
        for token in ("password", "secret", "token", "api_key", "master"):
            self.assertNotIn(token, blob, token)

    def test_manifest_has_no_derived_password_values(self):
        man = _load(MANIFEST)
        blob = json.dumps(man)
        for u in list(PROTECTED)[:8]:
            self.assertNotIn(CI.derive_password(u, "test-master-secret-not-real"), blob)

    def test_manifest_user_fields_are_the_expected_safe_set(self):
        man = _load(MANIFEST)
        allowed = {"username", "display_name", "email", "expected_post_count",
                   "corpus_age", "corpus_origin", "corpus_gender",
                   "corpus_content_type"}
        for u in man["users"]:
            self.assertTrue(set(u).issubset(allowed), set(u) - allowed)


class DateConversionTests(unittest.TestCase):
    def test_converts_to_midnight_utc(self):
        self.assertEqual(corpus_date_to_epoch("2026-04-01"), 1775001600)
        self.assertEqual(epoch_to_corpus_date(1775001600), "2026-04-01")

    def test_roundtrip_for_every_manifest_date(self):
        man = _load(MANIFEST)
        for p in man["posts"]:
            e = corpus_date_to_epoch(p["date"])
            self.assertEqual(epoch_to_corpus_date(e), p["date"])
            self.assertEqual(e % 86400, 0, "not midnight UTC")

    def test_deterministic_regardless_of_local_timezone(self):
        saved = os.environ.get("TZ")
        try:
            import time as _t
            for tz in ("UTC", "Asia/Kolkata", "America/Los_Angeles"):
                os.environ["TZ"] = tz
                if hasattr(_t, "tzset"):
                    _t.tzset()
                self.assertEqual(corpus_date_to_epoch("2026-04-01"), 1775001600, tz)
        finally:
            if saved is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = saved
            import time as _t
            if hasattr(_t, "tzset"):
                _t.tzset()

    def test_malformed_date_rejected(self):
        for bad in ("2026-4-1", "01-04-2026", "", "yesterday", None):
            with self.assertRaises(ValueError):
                corpus_date_to_epoch(bad)


class BodyAndTagFidelityTests(unittest.TestCase):
    """DECISION 2: the S3 body is the EXACT corpus body — no Date/Tags injected."""

    @classmethod
    def setUpClass(cls):
        cls.man = _load(MANIFEST)
        src = os.environ.get(
            "CORPUS_PATH",
            "/Users/praveen-16349/Downloads/"
            "friends_realistic_blog_corpus_25_users_268_posts.md")
        cls.have_src = os.path.exists(src)
        if cls.have_src:
            cls.parsed = CP.parse(_read(src))

    def test_manifest_records_268_posts(self):
        self.assertEqual(len(self.man["posts"]), 268)

    def test_tags_present_and_ordered_for_every_post(self):
        for p in self.man["posts"]:
            self.assertTrue(p["tags"], p["title"])
            self.assertEqual(p["tags"], [t.strip() for t in p["tags"]])

    def test_body_hash_matches_parsed_body_exactly(self):
        if not self.have_src:
            self.skipTest("corpus source not present")
        import hashlib
        idx = {(u["fields"]["Username"], q["title"], q["date"]): q
               for u in self.parsed["users"] for q in u["posts"]}
        for p in self.man["posts"]:
            body = idx[(p["username"], p["title"], p["date"])]["body"]
            self.assertEqual(hashlib.sha256(body.encode()).hexdigest(),
                             p["body_sha256"])

    def test_body_never_contains_injected_date_or_tag_lines(self):
        if not self.have_src:
            self.skipTest("corpus source not present")
        for u in self.parsed["users"]:
            for q in u["posts"]:
                self.assertNotIn("**Date:**", q["body"], q["title"])
                self.assertNotIn("**Tags:**", q["body"], q["title"])

    def test_identity_key_is_unique_across_the_corpus(self):
        keys = {(p["username"], p["title"], p["date"]) for p in self.man["posts"]}
        self.assertEqual(len(keys), 268)

    def test_body_hash_is_unique_within_each_owner(self):
        """Per-tenant content_hash dedup must not silently drop a corpus post."""
        seen = {}
        for p in self.man["posts"]:
            k = (p["username"], p["body_sha256"])
            self.assertNotIn(k, seen, f"{p['username']} duplicate body would be deduped")
            seen[k] = True


class CleanupSafetyTests(unittest.TestCase):
    def setUp(self):
        import cleanup_legacy_seed as CL
        self.CL = CL

    def test_cleanup_defaults_to_dry_run(self):
        self.assertFalse(self.CL.parse_args([]).apply)

    def test_apply_requires_an_explicit_flag(self):
        self.assertTrue(self.CL.parse_args(["--apply"]).apply)

    def test_delete_set_is_exactly_the_six_seed_users(self):
        allow = self.CL.load_allowlist()
        self.assertEqual(len(allow["user_ids"]), 6)
        self.assertEqual(len(allow["tenant_ids"]), 6)
        self.assertEqual(len(allow["post_ids"]), 50)
        self.assertEqual(allow["seed_prefix"], "seed-20260822")

    def test_protected_corpus_usernames_cannot_enter_the_delete_set(self):
        allow = self.CL.load_allowlist()
        for u in PROTECTED:
            self.assertFalse(self.CL.is_deletable_email(f"{u}@example.com", allow), u)

    def test_unknown_review_accounts_cannot_enter_the_delete_set(self):
        allow = self.CL.load_allowlist()
        for e in UNKNOWN_REVIEW:
            self.assertFalse(self.CL.is_deletable_email(e, allow), e)

    def test_only_manifest_identities_are_deletable(self):
        allow = self.CL.load_allowlist()
        self.assertTrue(all(uid.startswith("user_") for uid in allow["user_ids"]))
        self.assertFalse(self.CL.is_deletable_user("user_doesnotexist", allow))

    def test_no_delete_everything_not_in_corpus_logic(self):
        """The tool must never contain an inverse-allowlist deletion rule."""
        src = _read(os.path.join(HERE, "cleanup_legacy_seed.py"))
        import ast
        tree = ast.parse(src)
        names = {n.id.lower() for n in ast.walk(tree) if isinstance(n, ast.Name)}
        self.assertNotIn("not_in_corpus", names)
        self.assertNotIn("delete_all", names)


if __name__ == "__main__":
    unittest.main()
