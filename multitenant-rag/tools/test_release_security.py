"""Release-artifact security tests (§6).

Two controls, tested independently:
  CONTROL 1  release_guard  — path rule: NO .env may enter the archive, at any
                              depth, under any name. Cannot be defeated by an
                              unfamiliar key format.
  CONTROL 2  secret_scan    — content rule: known prefixes, JWTs, Authorization
                              headers, AWS key ids, and unquoted/quoted
                              credential assignments by entropy.

Run: PYTHONPATH=multitenant-rag/tools python -m unittest test_release_security -v
"""
import os
import shutil
import tempfile
import unittest
import zipfile

import release_guard
import secret_scan


class ForbiddenPathTests(unittest.TestCase):
    """CONTROL 1 — the strongest rule: no .env may enter the archive at all."""

    def test_local_env_cannot_enter_archive(self):
        self.assertIsNotNone(release_guard.is_forbidden("multitenant-rag/local/.env"))

    def test_root_env_cannot_enter_archive(self):
        # Regression: an earlier `lstrip("./")` stripped CHARACTERS, turning
        # ".env" into "env" and allowing a root-level .env straight through.
        self.assertIsNotNone(release_guard.is_forbidden(".env"))
        self.assertIsNotNone(release_guard.is_forbidden("./.env"))

    def test_nested_env_cannot_enter_archive(self):
        for p in ("a/.env", "a/b/.env", "a/b/c/d/e/.env",
                  "multitenant-rag/lambdas/ask/.env"):
            self.assertIsNotNone(release_guard.is_forbidden(p), p)

    def test_env_variants_cannot_enter_archive(self):
        for p in (".env.local", ".env.production", "svc/.env.staging",
                  "prod.env", "a/b/config.env"):
            self.assertIsNotNone(release_guard.is_forbidden(p), p)

    def test_other_credential_files_cannot_enter_archive(self):
        for p in ("certs/server.pem", "k/private.key", ".ssh/id_rsa",
                  ".ssh/id_ed25519", "aws/credentials", "credentials.json",
                  ".netrc", ".pgpass", "a/.npmrc", "store.p12", "vault.kdbx",
                  "a/keystore.jks"):
            self.assertIsNotNone(release_guard.is_forbidden(p), p)

    def test_env_example_is_the_only_allowed_exception(self):
        self.assertIsNone(
            release_guard.is_forbidden("multitenant-rag/local/.env.example"))
        # An allowlist that accepted PATTERNS is how .env.production returns.
        self.assertEqual(len(release_guard.ALLOWED_EXACT), 1)
        self.assertIsNotNone(release_guard.is_forbidden("other/.env.example"))

    def test_ordinary_files_are_not_blocked(self):
        for p in ("lambdas/ask/app.py", "README.md", "docs/environment.md",
                  "src/envelope.py", "tools/keyboard.py", "a/b/",
                  "lambdas/ask/rag/config.py", "notes/env-setup.md"):
            self.assertIsNone(release_guard.is_forbidden(p), p)

    def test_check_zip_detects_a_planted_env(self):
        tmp = tempfile.mkdtemp()
        try:
            z = os.path.join(tmp, "a.zip")
            with zipfile.ZipFile(z, "w") as zf:
                zf.writestr("multitenant-rag/lambdas/ask/app.py", "print(1)\n")
                zf.writestr("multitenant-rag/local/.env", "QDRANT_API_KEY=x\n")
            findings = release_guard.check_zip(z)
            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0][0], "multitenant-rag/local/.env")
        finally:
            shutil.rmtree(tmp)

    def test_check_zip_is_clean_for_a_normal_archive(self):
        tmp = tempfile.mkdtemp()
        try:
            z = os.path.join(tmp, "b.zip")
            with zipfile.ZipFile(z, "w") as zf:
                zf.writestr("multitenant-rag/lambdas/ask/app.py", "print(1)\n")
                zf.writestr("multitenant-rag/local/.env.example", "GROQ_MODEL=x\n")
            self.assertEqual(release_guard.check_zip(z), [])
        finally:
            shutil.rmtree(tmp)


class SecretPatternTests(unittest.TestCase):
    """CONTROL 2 — content detection. Fixtures use SYNTHETIC values only."""

    def _scan(self, files: dict):
        tmp = tempfile.mkdtemp()
        try:
            for name, body in files.items():
                p = os.path.join(tmp, name)
                os.makedirs(os.path.dirname(p), exist_ok=True)
                with open(p, "w", encoding="utf-8") as fh:
                    fh.write(body)
            findings, _ = secret_scan.scan_dir(tmp)
            return findings
        finally:
            shutil.rmtree(tmp)

    def test_unquoted_env_assignment_is_detected(self):
        """The exact class that was missed: no quotes, no known prefix."""
        f = self._scan({"cfg/.env":
                        "QDRANT_API_KEY=q7Fh2LkP9xVbN3mZ8wRtY6cD1sA4jE0uI5oG\n"})
        self.assertTrue(f)
        self.assertIn("QDRANT_API_KEY", f[0][2])

    def test_unquoted_assignment_in_a_non_config_extension_is_detected(self):
        f = self._scan({"notes/dump.txt":
                        "API_SECRET=Zx91QpLm44TbVn08KdRh27WsYc63JfEa\n"})
        self.assertTrue(f)

    def test_known_prefixes_are_detected(self):
        for name, val in (
            ("groq", "gsk_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4"),
            ("nvidia", "nvapi-" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4"),
            ("langsmith", "lsv2_pt_" + "A1b2C3d4E5f6G7h8I9j0K1l2"),
            ("openai", "sk-" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"),
            ("aws", "AKIA" + "ABCDEFGHIJKLMNOP"),
            ("aws_sts", "ASIA" + "ABCDEFGHIJKLMNOP"),
            ("github", "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4"),
            ("slack", "xoxb-" + "1234567890-abcdefghij"),
        ):
            f = self._scan({f"src/{name}.py": f'VALUE = "{val}"\n'})
            self.assertTrue(f, f"{name} not detected")

    def test_jwt_is_detected(self):
        jwt = ("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
               "eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvZSJ9.sIgNaTuRe")
        self.assertTrue(self._scan({"logs/out.txt": f"token {jwt}\n"}))

    def test_authorization_header_is_detected(self):
        # Synthetic value, assembled at runtime so this source line does not
        # itself look like a credential to the scanner.
        hdr = 'h = {"Authorization": "Bearer ' + "A1b2C3d4E5f6G7h8I9j0K1" + '"}\n'
        f = self._scan({"src/client.py": hdr})
        self.assertTrue(f)
        self.assertIn("AUTHORIZATION HEADER", f[0][2])

    def test_findings_never_include_the_secret_value(self):
        val = "q7Fh2LkP9xVbN3mZ8wRtY6cD1sA4jE0uI5oG"
        f = self._scan({"cfg/.env": f"QDRANT_API_KEY={val}\n"})
        self.assertTrue(f)
        for finding in f:
            self.assertNotIn(val, " ".join(str(x) for x in finding))


class FalsePositiveTests(unittest.TestCase):
    """A normal configuration/source fixture must not produce noise, or the
    control gets ignored in practice."""

    def _scan(self, files):
        return SecretPatternTests._scan(self, files)

    def test_normal_config_fixture_is_clean(self):
        self.assertEqual(self._scan({"local/.env.example": (
            "# Local config template\n"
            "AWS_REGION=ap-south-1\n"
            "TENANTS_TABLE=multitenant-tenants\n"
            "GROQ_MODEL=openai/gpt-oss-120b\n"
            "QDRANT_COLLECTION=multitenant_chunks\n"
            "# GROQ_API_KEY=<your-key-here>\n"
            "# QDRANT_API_KEY=${QDRANT_API_KEY}\n"
            "JWT_SECRET=changeme\n")}), [])

    def test_normal_source_fixture_is_clean(self):
        self.assertEqual(self._scan({"src/app.py": (
            'import os\n'
            'API_KEY = os.environ.get("GROQ_API_KEY")\n'
            'GROQ_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")\n'
            'input_tokens = usage.get("prompt_tokens", 0)\n'
            'output_tokens = usage.get("completion_tokens", 0)\n'
            'headers = {"Authorization": f"Bearer {api_key}"}\n'
            'resp = ddb.query(KeyConditionExpression="tenant_id = :t")\n'
            'SECRET_ID = "multitenant/qdrant"\n')}), [])

    def test_documentation_fixture_is_clean(self):
        self.assertEqual(self._scan({"docs/setup.md": (
            "# Setup\n\n"
            "Set `GROQ_API_KEY` in your environment.\n"
            "Example: `export QDRANT_API_KEY=<your-key>`\n"
            "The secret lives in Secrets Manager as `multitenant/qdrant`.\n")}), [])

    def test_the_real_repository_source_tree_is_clean(self):
        """End-to-end: the actual shipped source must produce zero findings."""
        repo = os.path.abspath(os.path.join(os.path.dirname(__file__), "..",
                                            "lambdas"))
        findings, scanned = secret_scan.scan_dir(repo)
        self.assertGreater(scanned, 10)
        self.assertEqual(findings, [], f"unexpected findings: {findings}")


if __name__ == "__main__":
    unittest.main()
