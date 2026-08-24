"""Dependency-boundary tests (§25).

Proven by AST analysis of the SHIPPED source, not by import side effects:

  1. No production module imports anything from `evals/`.
  2. No production module imports a LangChain API. `langchain_core` arriving as a
     LangGraph transitive dependency is NOT application LangChain usage; an
     application `import langchain*` would be, and is forbidden here.
  3. RAGAS / DeepEval are absent from the Lambda requirements and source.
  4. The Docker image never copies `evals/`.
"""
import ast
import os
import unittest

ASK = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
LAMBDAS = os.path.abspath(os.path.join(ASK, ".."))
RAG = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(LAMBDAS, ".."))


def _shipped_py_files():
    """Exactly what the Dockerfile copies: ask/app.py, ask/llm.py, ask/rag/,
    common/. Test files are excluded — they are not part of the runtime."""
    out = []
    for f in ("app.py", "llm.py"):
        out.append(os.path.join(ASK, f))
    for base in (RAG, os.path.join(LAMBDAS, "common")):
        for name in sorted(os.listdir(base)):
            if name.endswith(".py") and not name.startswith("test_") \
                    and name != "conftest_helpers.py":
                out.append(os.path.join(base, name))
    return out


def _imports(path):
    """Every module name imported by `path`, from AST — not from a regex that
    would also match the word inside a comment or docstring."""
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=path)
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:                      # relative import: never external
                continue
            if node.module:
                names.append(node.module)
    return names


EVAL_MODULES = {
    "nvidia_harness", "nvidia_provider", "decomp_graph", "routed_graph_v2",
    "router_v2", "router_v3", "router_v4", "verifier_v1", "harness",
    "groq_provider_obs", "top5_baseline", "holdout_v1_cases",
    "router_gt_annotations", "chunk_analysis",
}


class NoEvalImportsTests(unittest.TestCase):
    def test_no_shipped_module_imports_an_eval_module(self):
        offenders = []
        for path in _shipped_py_files():
            for name in _imports(path):
                root = name.split(".")[0]
                if root in EVAL_MODULES or root == "evals":
                    offenders.append((os.path.relpath(path, REPO), name))
        self.assertEqual(offenders, [], f"eval imports in production: {offenders}")

    def test_rag_package_has_no_eval_import(self):
        for name in sorted(os.listdir(RAG)):
            if not name.endswith(".py") or name.startswith("test_"):
                continue
            for imp in _imports(os.path.join(RAG, name)):
                self.assertNotIn(imp.split(".")[0], EVAL_MODULES, f"{name}: {imp}")

    def test_evals_directory_is_not_copied_into_the_image(self):
        with open(os.path.join(ASK, "Dockerfile"), encoding="utf-8") as fh:
            copies = [l for l in fh if l.startswith("COPY")]
        for line in copies:
            self.assertNotIn("evals", line, line)


class NoLangChainApplicationImportTests(unittest.TestCase):
    def test_no_shipped_module_imports_langchain(self):
        offenders = []
        for path in _shipped_py_files():
            for name in _imports(path):
                root = name.split(".")[0]
                if root.startswith("langchain"):
                    offenders.append((os.path.relpath(path, REPO), name))
        self.assertEqual(
            offenders, [],
            "application LangChain usage is not architect-approved: "
            f"{offenders}")

    def test_langgraph_is_imported_only_by_the_graph_module(self):
        users = []
        for path in _shipped_py_files():
            if any(n.split(".")[0] == "langgraph" for n in _imports(path)):
                users.append(os.path.basename(path))
        self.assertEqual(users, ["graph.py"], users)

    def test_langchain_is_not_a_declared_lambda_dependency(self):
        with open(os.path.join(ASK, "requirements.txt"), encoding="utf-8") as fh:
            reqs = [l.strip() for l in fh
                    if l.strip() and not l.strip().startswith("#")]
        for r in reqs:
            self.assertFalse(r.lower().startswith("langchain"),
                             f"langchain declared directly: {r}")


class NoEvalFrameworkDependencyTests(unittest.TestCase):
    def test_ragas_and_deepeval_absent_from_every_lambda_requirements(self):
        for lam in ("ask", "create_post", "ingest_worker"):
            path = os.path.join(LAMBDAS, lam, "requirements.txt")
            if not os.path.exists(path):
                continue
            with open(path, encoding="utf-8") as fh:
                text = fh.read().lower()
            self.assertNotIn("ragas", text, lam)
            self.assertNotIn("deepeval", text, lam)

    def test_ragas_and_deepeval_not_imported_by_shipped_code(self):
        for path in _shipped_py_files():
            for name in _imports(path):
                root = name.split(".")[0]
                self.assertNotIn(root, ("ragas", "deepeval"),
                                 f"{os.path.basename(path)}: {name}")

    def test_langgraph_version_is_pinned_exactly(self):
        with open(os.path.join(ASK, "requirements.txt"), encoding="utf-8") as fh:
            lines = [l.strip() for l in fh]
        pin = [l for l in lines if l.startswith("langgraph")]
        self.assertEqual(pin, ["langgraph==1.2.11"], pin)


class ShippedFileSetTests(unittest.TestCase):
    def test_dockerfile_copies_the_rag_package(self):
        with open(os.path.join(ASK, "Dockerfile"), encoding="utf-8") as fh:
            content = fh.read()
        self.assertIn("COPY ask/rag/ ./rag/", content)

    def test_rag_modules_do_not_import_app_at_module_scope(self):
        """Circular-import guard: production functions arrive via RagDeps."""
        for name in sorted(os.listdir(RAG)):
            if not name.endswith(".py") or name.startswith("test_"):
                continue
            for imp in _imports(os.path.join(RAG, name)):
                self.assertNotEqual(imp.split(".")[0], "app",
                                    f"{name} imports app at module scope")


if __name__ == "__main__":
    unittest.main()
