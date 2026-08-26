"""Profile identity tests (§40). Offline — DynamoDB is faked, no AWS.

The core property under test: username/email are MUTABLE profile attributes, and
changing them never disturbs the stable identity (user_id, tenant_id) or any
content ownership derived from it.
"""
import unittest
from unittest import mock

from botocore.exceptions import ClientError

from common import profile as P


def _cond_fail():
    return ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "PutItem")


class FakeDDB:
    """Minimal DynamoDB with the real conditional-write semantics we depend on."""

    def __init__(self, items=None):
        self.items = dict(items or {})     # user_id -> item
        self.by_email = {}
        self.reindex()

    def reindex(self):
        self.by_email = {}
        for k, it in self.items.items():
            e = it.get("email", {}).get("S")
            if e:
                self.by_email.setdefault(e, []).append(it)

    def get_item(self, TableName, Key, ConsistentRead=False):
        it = self.items.get(Key["user_id"]["S"])
        return {"Item": it} if it else {}

    def put_item(self, TableName, Item, ConditionExpression=None):
        k = Item["user_id"]["S"]
        if ConditionExpression == "attribute_not_exists(user_id)" and k in self.items:
            raise _cond_fail()
        self.items[k] = Item
        self.reindex()

    def update_item(self, TableName, Key, UpdateExpression, ExpressionAttributeValues,
                    ConditionExpression=None):
        k = Key["user_id"]["S"]
        if k not in self.items:
            raise _cond_fail()
        if ":n" in ExpressionAttributeValues:
            self.items[k]["username"] = ExpressionAttributeValues[":n"]
        if ":e" in ExpressionAttributeValues:
            self.items[k]["email"] = ExpressionAttributeValues[":e"]
        self.reindex()

    def delete_item(self, TableName, Key, ConditionExpression=None,
                    ExpressionAttributeValues=None):
        k = Key["user_id"]["S"]
        if ConditionExpression and k in self.items:
            want = ExpressionAttributeValues[":u"]["S"]
            if self.items[k].get("owner_user_id", {}).get("S") != want:
                raise _cond_fail()
        self.items.pop(k, None)
        self.reindex()

    def query(self, TableName, IndexName=None, KeyConditionExpression=None,
              ExpressionAttributeValues=None):
        e = ExpressionAttributeValues[":e"]["S"]
        return {"Items": self.by_email.get(e, [])}


def user(uid="user_a", tid="tenant_a", email="a@example.com", username=None):
    it = {"user_id": {"S": uid}, "tenant_id": {"S": tid}, "email": {"S": email},
          "display_name": {"S": "A"}}
    if username:
        it["username"] = {"S": username}
    return it


class UsernameValidationTests(unittest.TestCase):
    def test_corpus_usernames_are_valid(self):
        for u in ("kavin.raj25", "priyadharshini.m25", "saikiran.reddy25", "dinesh.k25"):
            self.assertEqual(P.validate_username(u), u)

    def test_case_is_normalised_so_case_cannot_duplicate(self):
        self.assertEqual(P.normalize_username("Kavin.Raj25"), "kavin.raj25")
        self.assertEqual(P.validate_username("KAVIN.RAJ25"), "kavin.raj25")

    def test_invalid_usernames_rejected(self):
        for bad in ("", "   ", "ab", "a" * 31, "has space", "has-dash", "UPPER!",
                    ".leading", "trailing.", "double..dot", "emoji😀", "a@b"):
            with self.assertRaises(P.ProfileError, msg=bad):
                P.validate_username(bad)

    def test_underscore_and_digits_allowed(self):
        self.assertEqual(P.validate_username("a_b.9"), "a_b.9")


class UsernameClaimTests(unittest.TestCase):
    def setUp(self):
        self.db = FakeDDB({"user_a": user(), "user_b": user("user_b", "tenant_b", "b@example.com")})
        self.p = mock.patch.object(P, "_ddb", self.db); self.p.start()
        self.addCleanup(self.p.stop)

    def test_absent_username_can_be_set(self):
        r = P.set_username("user_a", "kavin.raj25")
        self.assertEqual(r["username"], "kavin.raj25")
        self.assertEqual(self.db.items["user_a"]["username"]["S"], "kavin.raj25")
        self.assertIn("USERNAME#kavin.raj25", self.db.items)

    def test_duplicate_username_rejected(self):
        P.set_username("user_a", "kavin.raj25")
        with self.assertRaises(P.ProfileError) as cm:
            P.set_username("user_b", "kavin.raj25")
        self.assertEqual(cm.exception.status, 409)
        self.assertNotIn("username", self.db.items["user_b"])

    def test_case_normalised_duplicate_rejected(self):
        P.set_username("user_a", "kavin.raj25")
        with self.assertRaises(P.ProfileError):
            P.set_username("user_b", "Kavin.Raj25")

    def test_rename_claims_new_before_releasing_old(self):
        P.set_username("user_a", "old.name")
        P.set_username("user_a", "new.name")
        self.assertEqual(self.db.items["user_a"]["username"]["S"], "new.name")
        self.assertIn("USERNAME#new.name", self.db.items)
        self.assertNotIn("USERNAME#old.name", self.db.items)

    def test_failed_rename_leaves_original_intact(self):
        P.set_username("user_a", "mine.name")
        P.set_username("user_b", "taken.name")
        with self.assertRaises(P.ProfileError):
            P.set_username("user_a", "taken.name")
        self.assertEqual(self.db.items["user_a"]["username"]["S"], "mine.name")
        self.assertEqual(self.db.items["USERNAME#taken.name"]["owner_user_id"]["S"], "user_b")

    def test_two_users_can_never_share_a_username(self):
        P.set_username("user_a", "shared.one")
        try:
            P.set_username("user_b", "shared.one")
        except P.ProfileError:
            pass
        owners = [v["username"]["S"] for v in self.db.items.values()
                  if v.get("username") and not v["user_id"]["S"].startswith("USERNAME#")]
        self.assertEqual(len(owners), len(set(owners)))

    def test_setting_same_username_is_idempotent(self):
        P.set_username("user_a", "same.name")
        r = P.set_username("user_a", "same.name")
        self.assertFalse(r["changed"])

    def test_stable_identity_unchanged_by_username(self):
        before = (self.db.items["user_a"]["user_id"]["S"],
                  self.db.items["user_a"]["tenant_id"]["S"])
        P.set_username("user_a", "kavin.raj25")
        P.set_username("user_a", "renamed.later")
        after = (self.db.items["user_a"]["user_id"]["S"],
                 self.db.items["user_a"]["tenant_id"]["S"])
        self.assertEqual(before, after)

    def test_availability_check(self):
        self.assertTrue(P.is_username_available("free.name"))
        P.set_username("user_a", "free.name")
        self.assertFalse(P.is_username_available("free.name", for_user_id="user_b"))
        self.assertTrue(P.is_username_available("free.name", for_user_id="user_a"))


class ReservationsAreInvisibleTests(unittest.TestCase):
    """Reservation rows live in the users table; the directory must not show them."""

    def test_reservation_row_has_no_tenant_id(self):
        db = FakeDDB({"user_a": user()})
        with mock.patch.object(P, "_ddb", db):
            P.set_username("user_a", "kavin.raj25")
        row = db.items["USERNAME#kavin.raj25"]
        self.assertNotIn("tenant_id", row)
        self.assertEqual(row["reservation_kind"]["S"], "username")

    def test_directory_filter_skips_rows_without_tenant(self):
        """Mirrors app.list_profiles: `tid = it.get('tenant_id',{}).get('S','')`
        then `if not tenant: continue`."""
        db = FakeDDB({"user_a": user()})
        with mock.patch.object(P, "_ddb", db):
            P.set_username("user_a", "kavin.raj25")
        visible = [it for it in db.items.values()
                   if it.get("tenant_id", {}).get("S", "")]
        self.assertEqual(len(visible), 1)


class EmailChangeTests(unittest.TestCase):
    def setUp(self):
        self.db = FakeDDB({"user_a": user(), "user_b": user("user_b", "tenant_b", "b@example.com")})
        self.p = mock.patch.object(P, "_ddb", self.db); self.p.start()
        self.addCleanup(self.p.stop)

    def test_email_can_be_changed(self):
        r = P.set_email("user_a", "new@example.com")
        self.assertEqual(r["email"], "new@example.com")
        self.assertEqual(self.db.items["user_a"]["email"]["S"], "new@example.com")

    def test_duplicate_email_rejected(self):
        with self.assertRaises(P.ProfileError) as cm:
            P.set_email("user_a", "b@example.com")
        self.assertEqual(cm.exception.status, 409)
        self.assertEqual(self.db.items["user_a"]["email"]["S"], "a@example.com")

    def test_invalid_email_rejected(self):
        for bad in ("", "no-at", "a@b", "a b@example.com", "@example.com"):
            with self.assertRaises(P.ProfileError, msg=bad):
                P.set_email("user_a", bad)

    def test_email_is_case_normalised(self):
        P.set_email("user_a", "New@Example.COM")
        self.assertEqual(self.db.items["user_a"]["email"]["S"], "new@example.com")

    def test_old_email_no_longer_resolves_after_change(self):
        P.set_email("user_a", "new@example.com")
        self.assertEqual(self.db.query("t", "by_email", None,
                                       {":e": {"S": "a@example.com"}})["Items"], [])
        self.assertEqual(len(self.db.query("t", "by_email", None,
                                           {":e": {"S": "new@example.com"}})["Items"]), 1)

    def test_stable_identity_unchanged_by_email(self):
        P.set_email("user_a", "moved@example.com")
        self.assertEqual(self.db.items["user_a"]["user_id"]["S"], "user_a")
        self.assertEqual(self.db.items["user_a"]["tenant_id"]["S"], "tenant_a")

    def test_same_email_is_idempotent(self):
        self.assertFalse(P.set_email("user_a", "a@example.com")["changed"])


class ProfileReadTests(unittest.TestCase):
    def test_user_without_username_reports_none(self):
        db = FakeDDB({"user_a": user()})
        with mock.patch.object(P, "_ddb", db):
            self.assertIsNone(P.get_profile("user_a")["username"])

    def test_profile_exposes_no_password_material(self):
        db = FakeDDB({"user_a": {**user(), "password_hash": {"S": "$2b$secret"}}})
        with mock.patch.object(P, "_ddb", db):
            prof = P.get_profile("user_a")
        self.assertNotIn("password_hash", prof)
        self.assertNotIn("$2b$secret", str(prof))


if __name__ == "__main__":
    unittest.main()
