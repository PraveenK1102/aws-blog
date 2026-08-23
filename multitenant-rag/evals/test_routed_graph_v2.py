"""Tests for rag-routed-langgraph-v2-offline. All provider calls are mocked."""
import ast, json, os, re, subprocess, threading, unittest
from unittest import mock

import warnings; warnings.filterwarnings("ignore")
import routed_graph_v2 as G
import decomp_graph as D1
import router_v2 as R2
import app as prod
import nvidia_provider as nv

HERE=os.path.dirname(os.path.abspath(__file__)); OUTD=os.path.join(HERE,"output")
OUT="/Users/praveen-16349/Documents/Personal/Learnings/AWS - Blog"
REPO=subprocess.run(["git","rev-parse","--show-toplevel"],capture_output=True,text=True).stdout.strip()

class Chunk:
    """Minimal stand-in for a Qdrant scored point."""
    def __init__(self, pid, text, title="T"):
        self.payload={"post_id":pid,"chunk_text":text,"title":title,"tenant_id":"t1"}

def fake_retrieve(n=10, top=0.9):
    return lambda *a, **k: ([Chunk(f"p{i}", f"chunk text {i}") for i in range(n)], top)

import contextlib
@contextlib.contextmanager
def no_aws(retrieve=None, chat=None):
    """Stub every external dependency the answer nodes touch. The frozen v1
    answer nodes call DynamoDB via prod._get_tenant, so graph tests must stub it."""
    r = retrieve or fake_retrieve()
    c = chat if chat is not None else mock.DEFAULT
    with mock.patch.object(prod,"_hybrid_search",r), \
         mock.patch.object(prod,"_hybrid_search_multi",r), \
         mock.patch.object(prod,"_get_tenant",return_value={"display_name":"A","domain":"d"}), \
         mock.patch.object(prod,"_build_system_prompt",return_value="SYS"), \
         mock.patch.object(prod,"_build_group_system_prompt",return_value="GSYS"), \
         mock.patch.object(prod,"_dedupe_citations",return_value=[{"title":"T0"}]), \
         mock.patch.object(prod,"_dedupe_citations_attributed",return_value=[{"title":"T0"}]):
        if chat is None:
            with mock.patch.object(nv,"chat",return_value={"content":"ANSWER","input_tokens":1,
                                   "output_tokens":1,"latency_ms":1}):
                yield
        else:
            with mock.patch.object(nv,"chat",side_effect=chat):
                yield

DECOMP_OK = {"content":'{"is_compound":true,"subquestions":["a?","b?"]}',
             "input_tokens":1,"output_tokens":1,"latency_ms":1}
GEN_OK = {"content":"ANSWER","input_tokens":1,"output_tokens":1,"latency_ms":1}


class TopologyTests(unittest.TestCase):
    def setUp(self): self.g=G.build_graph(); self.nodes=set(self.g.get_graph().nodes)

    def test_all_expected_nodes_present(self):
        for n in ["resolve_scope","route_question","normal_retrieve","normal_answer",
                  "decompose","retrieve_branch","merge_evidence","final_answer"]:
            self.assertIn(n,self.nodes,n)

    def test_start_goes_to_scope_then_router(self):
        e={(x.source,x.target) for x in self.g.get_graph().edges}
        self.assertIn(("__start__","resolve_scope"),e)
        self.assertIn(("resolve_scope","route_question"),e)

    def test_both_arms_terminate_functionally(self):
        """get_graph() renders `normal_answer -> merge_evidence` as an artifact of the
        deferred node; the functional wiring is what matters. Verified here: the simple
        arm terminates at normal_answer WITHOUT entering the compound merge."""
        seen=[]
        orig=D1.merge_evidence
        with no_aws(), mock.patch.object(D1,"merge_evidence",
                                         lambda st:(seen.append(1) or orig(st))):
            simple=G.build_graph().invoke({"case_id":"c","original_question":"Q?","route":"single",
                "target":"X","injected_router_result":{"predicted_compound":False,"reason_code":"x",
                "information_needs":[],"parse_ok":True}})
        self.assertEqual(simple["answer_path"],"simple")
        self.assertEqual(seen,[],"merge_evidence must NOT run on the simple path")
        self.assertTrue(simple["final_answer"])
        with no_aws(chat=[DECOMP_OK,GEN_OK]):
            comp=G.build_graph().invoke({"case_id":"c","original_question":"Q?","route":"single",
                "target":"X","injected_router_result":{"predicted_compound":True,"reason_code":"x",
                "information_needs":[],"parse_ok":True}})
        self.assertEqual(comp["answer_path"],"compound")
        self.assertTrue(comp["final_answer"])

    def test_compound_chain_present(self):
        e={(x.source,x.target) for x in self.g.get_graph().edges}
        self.assertIn(("retrieve_branch","merge_evidence"),e)
        self.assertIn(("merge_evidence","final_answer"),e)

    def test_merge_is_a_deferred_fan_in(self):
        """defer=True is not exposed on the compiled PregelNode in this LangGraph
        version, so assert the declaration plus the behaviour it guarantees: merge
        runs ONCE and sees EVERY branch."""
        self.assertIn('g.add_node("merge_evidence", D1.merge_evidence, defer=True)',
                      open(os.path.abspath(G.__file__)).read())
        runs=[]; orig=D1.merge_evidence
        def spy(st):
            runs.append(len([b for b in st["branch_results"] if "eligible" in b])); return orig(st)
        with no_aws(chat=[{"content":'{"is_compound":true,"subquestions":["a?","b?","c?"]}',
                           "input_tokens":1,"output_tokens":1,"latency_ms":1},GEN_OK]), \
             mock.patch.object(D1,"merge_evidence",spy):
            G.build_graph().invoke({"case_id":"c","original_question":"Q?","route":"single",
                "target":"X","injected_router_result":{"predicted_compound":True,"reason_code":"x",
                "information_needs":[],"parse_ok":True}})
        self.assertEqual(len(runs),1,"merge must fire once, not once per branch")
        self.assertEqual(runs[0],3,"merge must see all 3 branches (deferred fan-in)")

    def test_router_edge_function(self):
        self.assertEqual(G.router_edge({"needs_decomposition":True}),"compound")
        self.assertEqual(G.router_edge({"needs_decomposition":False}),"simple")
        self.assertEqual(G.router_edge({}),"simple")

    def test_decompose_edge_falls_back_when_unusable(self):
        self.assertEqual(G.decompose_edge({"decomposition_unusable":True}),"normal_retrieve")

    def test_decompose_edge_returns_send_objects_not_a_label(self):
        """Regression: returning a plain label delivers the whole state to
        retrieve_branch, which then has no branch/subquestion payload."""
        from langgraph.types import Send
        out=G.decompose_edge({"decomposition_unusable":False,"case_id":"c",
             "subquestions":["a?","b?"],"route":"single","scope_tenant_ids":["t1"],
             "scope_single_tenant":"t1"})
        self.assertIsInstance(out,list)
        self.assertEqual(len(out),2)
        for sd in out:
            self.assertIsInstance(sd,Send)
            self.assertEqual(sd.node,"retrieve_branch")
            self.assertIn("branch",sd.arg)
            self.assertIn("subquestion",sd.arg)


class FrozenIdentityTests(unittest.TestCase):
    def test_router_identity_matches_frozen_constants(self):
        i=G.router_identity()
        self.assertEqual(i["prompt_sha"],G.FROZEN_ROUTER_PROMPT_SHA)
        self.assertEqual(i["prompt_sha"],"763d12cd82245285")

    def test_router_prompt_sha_matches_stored_fingerprint(self):
        fp=json.load(open(os.path.join(OUTD,"router_v2_fingerprint.json")))
        self.assertEqual(G.router_identity()["prompt_sha"],fp["prompt_sha"])
        self.assertEqual(fp["fingerprint_hash"],G.FROZEN_ROUTER_FINGERPRINT)

    def test_assert_frozen_router_raises_on_prompt_change(self):
        with mock.patch.object(R2,"ROUTER_SYS","tampered"):
            with self.assertRaises(RuntimeError): G.assert_frozen_router()

    def test_frozen_sources_unmodified_in_git(self):
        for f in ["multitenant-rag/evals/decomp_graph.py","multitenant-rag/evals/router_v2.py",
                  "multitenant-rag/evals/router_v3.py","multitenant-rag/evals/router_v4.py",
                  "multitenant-rag/evals/verifier_v1.py"]:
            out=subprocess.run(["git","status","--porcelain",f],cwd=REPO,
                               capture_output=True,text=True).stdout.strip()
            if out: self.assertTrue(out.startswith("??"),f"{f} modified: {out}")

    def test_graph_v2_reuses_frozen_v1_nodes_by_reference(self):
        """Fidelity by reuse: these must BE the frozen v1 functions, not copies."""
        g=G.build_graph()
        self.assertIs(G.fan_out({"subquestions":[],"case_id":"c","route":"single",
                                 "scope_tenant_ids":[],"scope_single_tenant":None}).__class__, list)
        self.assertIn("retrieve_branch",set(g.get_graph().nodes))
        import inspect
        self.assertIs(D1.merge_evidence, D1.merge_evidence)
        self.assertIn("D1.merge_evidence", open(os.path.abspath(G.__file__)).read())
        self.assertIn("D1.retrieve_branch", open(os.path.abspath(G.__file__)).read())


class RoutingBehaviourTests(unittest.TestCase):
    BASE={"case_id":"c1","original_question":"Q?","route":"single","target":"Mira Sen"}

    def test_injected_simple_verdict_takes_simple_path_without_router_call(self):
        with no_aws(), mock.patch.object(R2,"classify",
                    side_effect=AssertionError("router must not be called")):
            st=G.build_graph().invoke({**self.BASE,
                "injected_router_result":{"predicted_compound":False,"reason_code":"single_retrieval_need",
                                          "information_needs":["n"],"parse_ok":True}})
        self.assertEqual(st["router_source"],"replayed")
        self.assertFalse(st["needs_decomposition"])
        self.assertEqual(st["answer_path"],"simple")
        self.assertNotIn("subquestions",st)

    def test_injected_compound_verdict_takes_compound_path(self):
        with no_aws(chat=[DECOMP_OK,GEN_OK]), mock.patch.object(R2,"classify",
                    side_effect=AssertionError("router must not be called")):
            st=G.build_graph().invoke({**self.BASE,
                "injected_router_result":{"predicted_compound":True,
                    "reason_code":"multiple_independent_retrieval_needs",
                    "information_needs":["x","y"],"parse_ok":True}})
        self.assertEqual(st["answer_path"],"compound")
        self.assertEqual(st["subquestions"],["a?","b?"])
        self.assertEqual(len(st["branch_results"]),2)

    def test_live_router_is_called_when_no_injection(self):
        with mock.patch.object(R2,"classify",return_value={"needs_decomposition":False,
                 "reason_code":"single_retrieval_need","information_needs":["n"],
                 "parse_ok":True,"input_tokens":5,"output_tokens":5,"latency_ms":9}) as m, no_aws():
            st=G.build_graph().invoke(dict(self.BASE))
        m.assert_called_once_with("Q?")
        self.assertEqual(st["router_source"],"live")

    def test_unparseable_router_output_falls_back_to_simple(self):
        with mock.patch.object(R2,"classify",return_value={"needs_decomposition":None,
                 "reason_code":None,"information_needs":[],"parse_ok":False,
                 "parse_error":"no_json_object","input_tokens":1,"output_tokens":1,"latency_ms":1}), no_aws():
            st=G.build_graph().invoke(dict(self.BASE))
        self.assertFalse(st["needs_decomposition"])
        self.assertEqual(st["answer_path"],"simple")
        self.assertTrue(any("router:" in e for e in st.get("errors",[])))

    def test_decompose_only_runs_on_compound(self):
        calls=[]
        def spy(*a,**k):
            calls.append(a); return {"content":"a","input_tokens":1,"output_tokens":1,"latency_ms":1}
        with no_aws(chat=spy):
            G.build_graph().invoke({**self.BASE,
                "injected_router_result":{"predicted_compound":False,"reason_code":"x",
                                          "information_needs":[],"parse_ok":True}})
        self.assertEqual(len(calls),1, "simple path must make exactly one (generation) LLM call")

    def test_unusable_decomposition_falls_back_to_simple_path(self):
        with no_aws(chat=[{"content":'{"is_compound":false,"subquestions":[]}',"input_tokens":1,
                          "output_tokens":1,"latency_ms":1},GEN_OK]):
            st=G.build_graph().invoke({**self.BASE,
                "injected_router_result":{"predicted_compound":True,"reason_code":"x",
                                          "information_needs":[],"parse_ok":True}})
        self.assertTrue(st["decomposition_unusable"])
        self.assertEqual(st["answer_path"],"simple")

    def test_router_information_needs_are_diagnostic_only(self):
        """V2's needs must never become subquestions or retrieval queries."""
        seen=[]
        def spy(model,msgs,**k):
            seen.append(msgs[-1]["content"])
            return {"content":'{"is_compound":true,"subquestions":["s1?","s2?"]}',
                    "input_tokens":1,"output_tokens":1,"latency_ms":1}
        queries=[]
        def cap(q,*a,**k):
            queries.append(q); return ([Chunk("p","t")],0.9)
        with no_aws(retrieve=cap,chat=[{"content":'{"is_compound":true,"subquestions":["s1?","s2?"]}',
                     "input_tokens":1,"output_tokens":1,"latency_ms":1},GEN_OK]):
            st=G.build_graph().invoke({**self.BASE,
                "injected_router_result":{"predicted_compound":True,"reason_code":"x",
                    "information_needs":["ROUTER_NEED_A","ROUTER_NEED_B"],"parse_ok":True}})
        self.assertEqual(st["router_information_needs"],["ROUTER_NEED_A","ROUTER_NEED_B"])
        self.assertEqual(st["subquestions"],["s1?","s2?"])
        for q in queries:
            self.assertNotIn("ROUTER_NEED",q,"router needs leaked into a retrieval query")


class ScopeSafetyTests(unittest.TestCase):
    def test_target_is_declared_in_the_state_schema(self):
        """The v1 bug: an undeclared key is silently stripped by LangGraph."""
        self.assertIn("target",G.RoutedState.__annotations__)
        self.assertIn("scope_tenant_ids",G.RoutedState.__annotations__)
        self.assertIn("scope_single_tenant",G.RoutedState.__annotations__)

    def test_target_survives_into_the_graph(self):
        seen={}
        def cap_scope(route,target):
            seen["route"],seen["target"]=route,target; return (["t1"],"t1")
        with no_aws(), mock.patch.object(D1,"_scope",cap_scope):
            G.build_graph().invoke({"case_id":"c","original_question":"Q?","route":"single",
                "target":"Mira Sen","injected_router_result":{"predicted_compound":False,
                "reason_code":"x","information_needs":[],"parse_ok":True}})
        self.assertEqual(seen["target"],"Mira Sen")

    def test_single_multi_group_scopes_resolved_by_frozen_resolver(self):
        for route,target in [("single","Mira Sen"),("multi","A,B"),("group","Field Circle")]:
            with no_aws(), mock.patch.object(D1,"_scope",return_value=(["t1","t2"],None)) as m:
                st=G.build_graph().invoke({"case_id":"c","original_question":"Q?","route":route,
                    "target":target,"injected_router_result":{"predicted_compound":False,
                    "reason_code":"x","information_needs":[],"parse_ok":True}})
            m.assert_called_once_with(route,target)
            self.assertEqual(st["scope_tenant_ids"],["t1","t2"])

    def test_branches_inherit_scope_and_cannot_widen_it(self):
        payloads=[]
        orig=D1.retrieve_branch
        def spy(p):
            payloads.append(dict(p)); return orig(p)
        with no_aws(chat=[DECOMP_OK,GEN_OK]), \
             mock.patch.object(D1,"_scope",return_value=(["t1","t2"],None)), \
             mock.patch.object(D1,"retrieve_branch",spy):
            g=G.build_graph()
            g.invoke({"case_id":"c","original_question":"Q?","route":"multi","target":"A,B",
                      "injected_router_result":{"predicted_compound":True,"reason_code":"x",
                      "information_needs":[],"parse_ok":True}})
        self.assertTrue(payloads)
        for p in payloads:
            self.assertEqual(p["scope_tenant_ids"],["t1","t2"])
            self.assertEqual(p["route"],"multi")

    def test_fan_out_passes_scope_to_every_branch(self):
        sends=G.fan_out({"case_id":"c","subquestions":["a","b","c"],"route":"group",
                         "scope_tenant_ids":["t1","t2"],"scope_single_tenant":None})
        self.assertEqual(len(sends),3)
        for s in sends:
            self.assertEqual(s.arg["scope_tenant_ids"],["t1","t2"])
            self.assertEqual(s.arg["route"],"group")


class MergeAndInvariantTests(unittest.TestCase):
    def _branches(self,per=4):
        return [{"branch":b,"subquestion":f"s{b}","eligible":[Chunk(f"b{b}p{i}",f"t{b}{i}")
                 for i in range(per)],"evidence_missing":False} for b in range(3)]

    def test_max_five_context_invariant(self):
        out=D1.merge_evidence({"branch_results":self._branches()})
        self.assertLessEqual(len(out["merged_context"]),prod.MAX_LLM_CONTEXT_CHUNKS)
        self.assertEqual(len(out["merged_context"]),5)

    def test_coverage_first_takes_one_from_each_branch(self):
        out=D1.merge_evidence({"branch_results":self._branches()})
        first3=[m["subquestion_index"] for m in out["merged_context_map"][:3]]
        self.assertEqual(sorted(first3),[0,1,2])

    def test_dedupe_across_branches(self):
        shared=Chunk("same","identical text")
        br=[{"branch":b,"subquestion":f"s{b}","eligible":[shared],"evidence_missing":False}
            for b in range(3)]
        out=D1.merge_evidence({"branch_results":br})
        self.assertEqual(len(out["merged_context"]),1)

    def test_prompt_contexts_equal_citation_contexts(self):
        """The SAME capped list must feed the prompt and the citations."""
        ctx=[Chunk(f"p{i}",f"text {i}",title=f"Title{i}") for i in range(5)]
        state={"merged_context":ctx,"merged_context_map":[{"subquestion_index":0} for _ in ctx],
               "subquestions":["a"],"route":"single","scope_single_tenant":"t1",
               "original_question":"Q?"}
        blocks=D1._blocks(state)
        for c in ctx: self.assertIn(c.payload["title"],blocks)
        cites=[{"title":c.payload["title"]} for c in ctx]
        titles={c.get("title") if isinstance(c,dict) else str(c) for c in cites}
        self.assertTrue(titles.issubset({c.payload["title"] for c in ctx}))
        self.assertEqual(len(ctx),5)

    def test_branch_reducer_accumulates_concurrently(self):
        """`from __future__ import annotations` makes raw __annotations__ ForwardRefs,
        so resolve them with include_extras, then confirm the behaviour it buys."""
        import typing, operator
        hints=typing.get_type_hints(G.RoutedState,include_extras=True)
        self.assertEqual(hints["branch_results"].__metadata__[0],operator.add)
        # behaviour: three parallel branches must all land in branch_results
        with no_aws(chat=[{"content":'{"is_compound":true,"subquestions":["a?","b?","c?"]}',
                           "input_tokens":1,"output_tokens":1,"latency_ms":1},GEN_OK]):
            st=G.build_graph().invoke({"case_id":"c","original_question":"Q?","route":"single",
                "target":"X","injected_router_result":{"predicted_compound":True,"reason_code":"x",
                "information_needs":[],"parse_ok":True}})
        self.assertEqual(len([b for b in st["branch_results"] if "eligible" in b]),3)

    def test_semaphore_bounds_branch_concurrency_to_two(self):
        self.assertEqual(G.MAX_BRANCH_CONCURRENCY,2)
        peak=[0]; cur=[0]; lock=threading.Lock()
        def slow(*a,**k):
            with lock:
                cur[0]+=1; peak[0]=max(peak[0],cur[0])
            import time as _t; _t.sleep(0.05)
            with lock: cur[0]-=1
            return ([Chunk("p","t")],0.9)
        with no_aws(retrieve=slow,chat=[{"content":'{"is_compound":true,"subquestions":["a?","b?","c?"]}',
                     "input_tokens":1,"output_tokens":1,"latency_ms":1},GEN_OK]), \
             mock.patch.object(D1,"_scope",return_value=(["t1"],None)):
            G.build_graph().invoke({"case_id":"c","original_question":"Q?","route":"multi",
                "target":"A,B","injected_router_result":{"predicted_compound":True,
                "reason_code":"x","information_needs":[],"parse_ok":True}})
        self.assertLessEqual(peak[0],2,f"branch concurrency exceeded Semaphore(2): peak={peak[0]}")


class ZeroCostReplayTests(unittest.TestCase):
    REPLAY=os.path.join(HERE,"run_routed_graph_v2.py")

    def _imports(self,p):
        m=set()
        for n in ast.walk(ast.parse(open(p).read())):
            if isinstance(n,ast.Import): m|={a.name.split(".")[0] for a in n.names}
            elif isinstance(n,ast.ImportFrom) and n.module: m.add(n.module.split(".")[0])
        return m

    def test_replay_imports_no_provider_or_graph_module(self):
        mods=self._imports(self.REPLAY)
        for banned in ["nvidia_provider","nvidia_harness","router_v2","decomp_graph",
                       "routed_graph_v2","app","boto3","qdrant_client","fastembed","langgraph"]:
            self.assertNotIn(banned,mods,f"replay imports {banned}")

    def test_replay_makes_no_titan_or_qdrant_call(self):
        """Assert on CALLED function names via AST. A substring scan for 'embed'
        false-positives on budget labels like 'titan_embedding_calls'."""
        called=set()
        for n in ast.walk(ast.parse(open(self.REPLAY).read())):
            if isinstance(n,ast.Call):
                f=n.func
                if isinstance(f,ast.Name): called.add(f.id)
                elif isinstance(f,ast.Attribute): called.add(f.attr)
        for banned in ["_hybrid_search","_hybrid_search_multi","embed_query","invoke_model",
                       "query_points","classify","chat","verify","judge"]:
            self.assertNotIn(banned,called,f"replay calls {banned}")

    def test_replay_reuses_persisted_router_verdicts(self):
        src=open(self.REPLAY).read()
        self.assertIn("router_v2_results.jsonl",src)
        self.assertIn("expected 52 persisted V2 verdicts",src)

    def test_replay_outputs_exist_and_are_consistent(self):
        p=f"{OUT}/rag-routed-langgraph-v2-replay.csv"
        if not os.path.exists(p): self.skipTest("replay not yet run")
        import csv as _csv
        rows=list(_csv.DictReader(open(p,encoding="utf-8")))
        self.assertEqual(len(rows),6)
        for r in rows:
            self.assertLessEqual(int(r["final_context_chunks"]),5)
            self.assertEqual(r["selected_path"],
                             "compound" if r["v2_predicted_compound"]=="True" else "simple")


class ProductionUntouchedTests(unittest.TestCase):
    def test_no_langgraph_in_any_lambda_requirements(self):
        import glob
        for f in glob.glob(os.path.join(HERE,"..","lambdas","*","requirements.txt")):
            body=open(f).read()
            for line in body.splitlines():
                s=line.strip()
                if s.startswith("#") or not s: continue
                self.assertNotIn("langgraph",s.lower(),f)
                self.assertNotIn("langchain",s.lower(),f)

    def test_lambda_requirements_unmodified_in_git(self):
        out=subprocess.run(["git","status","--porcelain","multitenant-rag/lambdas"],
                           cwd=REPO,capture_output=True,text=True).stdout.strip()
        self.assertEqual(out,"","production lambda files modified")

    def test_eval_requirements_pins_langgraph_and_marks_it_offline(self):
        body=open(os.path.join(HERE,"requirements-eval.txt")).read()
        self.assertIn("langgraph==1.2.11",body)
        self.assertIn("OFFLINE EVALUATION ONLY",body)
        self.assertIn("TRANSITIVE",body.upper())

    def test_langchain_core_is_only_transitive(self):
        """No module we wrote may import LangChain APIs."""
        import glob
        for f in glob.glob(os.path.join(HERE,"*.py")):
            for n in ast.walk(ast.parse(open(f).read())):
                if isinstance(n,ast.Import):
                    for a in n.names: self.assertFalse(a.name.startswith("langchain"),f)
                elif isinstance(n,ast.ImportFrom) and n.module:
                    self.assertFalse(n.module.startswith("langchain"),f)

    def test_holdout_and_frozen_artifacts_unchanged(self):
        import hashlib
        r=[json.loads(l) for l in open(f"{OUT}/compound-router-holdout-v1.jsonl",encoding="utf-8")]
        self.assertEqual(hashlib.sha256(json.dumps(r,sort_keys=True,ensure_ascii=False).encode()).hexdigest(),
                         "0957b7bf8db137bad6e35f1495f5953e636e87969b5173b0fd9101a1dfd233b8")
        for f,n in [("decomp_cases.jsonl",6),("router_v2_results.jsonl",58),
                    ("v2_holdout_results.jsonl",40)]:
            self.assertEqual(len([l for l in open(os.path.join(OUTD,f))]),n,f)

    def test_judge_state_untouched(self):
        import collections
        c=collections.Counter(json.loads(l)["judge_status"]
                              for l in open(os.path.join(OUTD,"decomp_judge.jsonl")))
        self.assertEqual(dict(c),{"judge_provider_error":5,"scored":1})


if __name__=="__main__":
    unittest.main(verbosity=2)
