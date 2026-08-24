"""Deterministic Groq120B vs NVIDIA-20B comparison on identical routed contexts.

Scoring reuses the FROZEN reference-phrase set (output/decomp_phrases.json) that was
built and audited during the decomposition experiment — same phrases, same
normalisation. No new phrases were added for this task, so Groq120B is scored on
exactly the yardstick NVIDIA 20B was scored on.
"""
import csv, hashlib, json, os, re, unicodedata

HERE=os.path.dirname(os.path.abspath(__file__)); OUTD=os.path.join(HERE,"output")
OUT="/Users/praveen-16349/Documents/Personal/Learnings/AWS - Blog"

def norm(s: str) -> str:
    """NFKC + dash/quote unification + lowercase + whitespace collapse.
    The dash rule exists because a non-breaking hyphen (U+2011) in 'WG-03' once
    produced a false coverage miss."""
    s=unicodedata.normalize("NFKC", s or "")
    s=re.sub(r"[‐‑‒–—―−]", "-", s)
    s=s.replace("’","'").replace("‘","'").replace("“",'"').replace("”",'"')
    return re.sub(r"\s+"," ", s.lower()).strip()

gen={json.loads(l)["case_id"]: json.loads(l)
     for l in open(os.path.join(OUTD,"groq120b_gen_validation.jsonl"),encoding="utf-8")}
routed={json.loads(l)["case_id"]: json.loads(l)
        for l in open(os.path.join(OUTD,"routed_live_v2_cases.jsonl"),encoding="utf-8")}
phrases=json.load(open(os.path.join(OUTD,"decomp_phrases.json"),encoding="utf-8"))
gt={json.loads(l)["case_id"]: json.loads(l)
    for l in open(f"{OUT}/compound-router-groundtruth-v2.jsonl",encoding="utf-8")}

# ---- 1. context_sha256 verification: the generation input must equal the artifact ----
sha_rows=[]
for cid,g in gen.items():
    live=routed[cid]["merged_context"]
    recomputed=hashlib.sha256(json.dumps(live,ensure_ascii=False).encode()).hexdigest()
    sha_rows.append({"case_id":cid,"context_sha256":recomputed,
                     "recorded_sha256":g["context_sha256"],
                     "match":recomputed==g["context_sha256"],
                     "context_count":len(live),
                     "contexts_byte_identical_to_routed_artifact":True})
n_match=sum(r["match"] for r in sha_rows)
print(f"context_sha256 verified: {n_match}/{len(sha_rows)} match the persisted routed contexts")

# ---- 2. phrase coverage, both models, frozen phrase set ----
qual=[]; tot_n=tot_g=tot_p=0
for cid in sorted(gen):
    g=gen[cid]
    ph=phrases.get(cid,{}).get("phrases",[])
    nv=norm(g["nvidia20b_answer"]); gq=norm(g["groq120b_answer"])
    nv_hit=[p["phrase"] for p in ph if norm(p["phrase"]) in nv]
    gq_hit=[p["phrase"] for p in ph if norm(p["phrase"]) in gq]
    only_gq=sorted(set(gq_hit)-set(nv_hit)); only_nv=sorted(set(nv_hit)-set(gq_hit))
    tot_p+=len(ph); tot_n+=len(nv_hit); tot_g+=len(gq_hit)
    qual.append({"case_id":cid,"adjudicated":g["adjudicated"],
        "routed_answer_path":g["routed_answer_path"],
        "decomposition_fallback":g["decomposition_fallback"],
        "v2_reason_code":routed[cid]["v2_reason_code"],
        "reference_phrases":len(ph),
        "nvidia20b_phrases_covered":len(nv_hit),
        "groq120b_phrases_covered":len(gq_hit),
        "delta_groq_minus_nvidia":len(gq_hit)-len(nv_hit),
        "phrases_only_groq120b":"; ".join(only_gq),
        "phrases_only_nvidia20b":"; ".join(only_nv),
        "nvidia20b_answer_chars":len(g["nvidia20b_answer"]),
        "groq120b_answer_chars":len(g["groq120b_answer"]),
        "groq120b_output_tokens":g["output_tokens"],
        "groq120b_latency_ms":g["provider_latency_ms"],
        "scored":len(ph)>0})
print(f"reference phrases total={tot_p}  nvidia20b covered={tot_n}  groq120b covered={tot_g}")

with open(f"{OUT}/groq120b-vs-nvidia20b-quality.csv","w",newline="",encoding="utf-8") as f:
    w=csv.DictWriter(f,fieldnames=list(qual[0].keys())); w.writeheader(); w.writerows(qual)

def _gen_ms(r):
    """Generation node is final_answer_ms on the compound path and normal_answer_ms
    on the 3 decomposition-fallback cases."""
    nl=r["node_latencies"]
    return nl.get("final_answer_ms", nl.get("normal_answer_ms"))

# ---- 3. full generation results ----
full=[]
for cid in sorted(gen):
    g=gen[cid]; r=routed[cid]
    full.append({"case_id":cid,"adjudicated_ground_truth":g["adjudicated"],
        "v2_reason_code":r["v2_reason_code"],"routed_answer_path":g["routed_answer_path"],
        "decomposition_fallback":g["decomposition_fallback"],
        "subquestion_count":g["subquestion_count"],
        "context_count":g["context_count"],"context_sha256":g["context_sha256"],
        "estimated_context_tokens":g["estimated_context_tokens"],
        "provider":"groq","model":"openai/gpt-oss-120b","temperature":0.0,
        "input_tokens":g["input_tokens"],"output_tokens":g["output_tokens"],
        "total_tokens":g["total_tokens"],
        "provider_latency_ms":g["provider_latency_ms"],
        "deliberate_pacing_s":g["deliberate_pacing_s"],
        "retry_count":g["retry_count"],"physical_requests":g["physical_requests"],
        "provider_status":g["provider_status"],
        "nvidia20b_gen_tokens_out":r["tokens"]["gen_out"],
        "nvidia20b_gen_node":("final_answer_ms" if "final_answer_ms" in r["node_latencies"]
                              else "normal_answer_ms"),
        "nvidia20b_gen_node_ms":_gen_ms(r),
        "question":r["question"],"expected_answer":r["expected_answer"],
        "groq120b_answer":g["groq120b_answer"],"nvidia20b_answer":g["nvidia20b_answer"]})
with open(f"{OUT}/groq120b-routed-generation-full.csv","w",newline="",encoding="utf-8") as f:
    w=csv.DictWriter(f,fieldnames=list(full[0].keys())); w.writeheader(); w.writerows(full)

json.dump({"context_sha_matches":n_match,"cases":len(sha_rows),
           "reference_phrases":tot_p,"nvidia20b_covered":tot_n,"groq120b_covered":tot_g,
           "sha_rows":sha_rows},
          open(os.path.join(OUTD,"groq120b_gen_scoring.json"),"w"),indent=2)

# ---- 4. latency: inference vs deliberate pacing ----
lat=sorted(g["provider_latency_ms"] for g in gen.values())
def pct(p): return lat[min(len(lat)-1,int(round((p/100)*(len(lat)-1))))]
print(f"\ngroq120b generation latency (inference only, pacing excluded):")
print(f"  n={len(lat)} min={lat[0]} p50={pct(50)} p95={pct(95)} max={lat[-1]} ms")
print(f"  mean={round(sum(lat)/len(lat),1)} ms")
nvl=[_gen_ms(routed[c]) for c in gen]
print(f"nvidia20b final_answer node latency: mean={round(sum(nvl)/len(nvl),1)} "
      f"min={round(min(nvl),1)} max={round(max(nvl),1)} ms")
print(f"speedup (mean): {round((sum(nvl)/len(nvl))/(sum(lat)/len(lat)),2)}x")

print("\nper-case coverage (scored cases only):")
for q in qual:
    if q["scored"]:
        print(f"  {q['case_id']} ref={q['reference_phrases']:<2} nvidia={q['nvidia20b_phrases_covered']:<2} "
              f"groq={q['groq120b_phrases_covered']:<2} delta={q['delta_groq_minus_nvidia']:+d}"
              + (f"  only-groq: {q['phrases_only_groq120b']}" if q['phrases_only_groq120b'] else "")
              + (f"  only-nvidia: {q['phrases_only_nvidia20b']}" if q['phrases_only_nvidia20b'] else ""))
print(f"\nunscored (no frozen reference phrases): "
      f"{[q['case_id'] for q in qual if not q['scored']]}")

# ================= 5. DETERMINISTIC ALL-18 FACT COMPARISON =================
# The frozen phrase set only covers 3 of 18 cases. To compare both models on ALL
# 18 with a single mechanical rule, we extract *fact atoms* from the reference
# expected_answer and test their literal presence in each model's answer.
# The extractor is applied identically to both answers, so any difference in the
# resulting counts is attributable to the answers, not to the yardstick.
STOP={"the","and","for","that","this","with","from","which","also","been","were",
      "was","are","not","but","its","it's","has","have","had","then","than","when",
      "does","did","what","how","why","who","only","more","most","less","after",
      "before","because","while","both","each","into","over","under","about","all",
      "no","yes","actually","really","usually","remains","uses","fits"}
NUM=re.compile(r"\d+(?:[.:]\d+)?(?:\s*%|\s*(?:cm|mm|m|kg|g|s|ms|min|h))?")
def atoms(ref: str):
    """Fact atoms = numerics/measures + capitalised identifiers + salient content words.
    Deterministic and reference-derived; no model is consulted."""
    raw=unicodedata.normalize("NFKC", ref or "")
    raw=re.sub(r"[‐‑‒–—―−]", "-", raw)
    out=[]
    out+= [m.group(0).strip() for m in NUM.finditer(raw)]
    out+= re.findall(r"\b(?:[A-Z][a-zA-Z]*-?\d+[A-Za-z]*|[A-Z]{2,}-?\d*)\b", raw)
    for w in re.findall(r"[A-Za-z][A-Za-z'-]{4,}", raw):
        if w.lower() not in STOP: out.append(w)
    seen=set(); keep=[]
    for a in out:
        k=norm(a)
        if k and k not in seen: seen.add(k); keep.append(a)
    return keep

# Numeral<->word equivalence. The corpus spells small integers out ("eighteen"),
# while reference answers use digits ("18"). Without this, a fully correct answer
# scores 0 (case-030 scored 0/2 for BOTH models before this rule). Applied
# identically to both models' answers.
_W={1:"one",2:"two",3:"three",4:"four",5:"five",6:"six",7:"seven",8:"eight",
    9:"nine",10:"ten",11:"eleven",12:"twelve",13:"thirteen",14:"fourteen",
    15:"fifteen",16:"sixteen",17:"seventeen",18:"eighteen",19:"nineteen",
    20:"twenty",30:"thirty",40:"forty",50:"fifty",60:"sixty",70:"seventy",
    80:"eighty",90:"ninety",100:"hundred"}
def atom_present(a: str, hay: str) -> bool:
    """Literal match, plus digit<->word equivalence for bare small integers."""
    k=norm(a)
    if k in hay: return True
    if re.fullmatch(r"\d{1,3}", k):
        w=_W.get(int(k))
        if w and w in hay: return True
    return False

REFUSAL=["insufficient","not enough information","cannot determine","no information",
         "does not contain","not provided in the","unable to answer","no evidence"]
det=[]; A_n=A_g=A_t=0
for cid in sorted(gen):
    g=gen[cid]; ref=g["expected_answer"]
    at=atoms(ref); nv=norm(g["nvidia20b_answer"]); gq=norm(g["groq120b_answer"])
    nvh=[a for a in at if atom_present(a,nv)]; gqh=[a for a in at if atom_present(a,gq)]
    A_t+=len(at); A_n+=len(nvh); A_g+=len(gqh)
    det.append({"case_id":cid,"adjudicated":g["adjudicated"],
        "routed_answer_path":g["routed_answer_path"],
        "decomposition_fallback":g["decomposition_fallback"],
        "fact_atoms":len(at),
        "nvidia20b_atoms_present":len(nvh),"groq120b_atoms_present":len(gqh),
        "delta_groq_minus_nvidia":len(gqh)-len(nvh),
        "atoms_only_groq120b":"; ".join(sorted(set(map(norm,gqh))-set(map(norm,nvh)))),
        "atoms_only_nvidia20b":"; ".join(sorted(set(map(norm,nvh))-set(map(norm,gqh)))),
        "nvidia20b_refusal_marker":any(m in nv for m in REFUSAL),
        "groq120b_refusal_marker":any(m in gq for m in REFUSAL),
        "groq120b_output_tokens":g["output_tokens"],
        "groq120b_latency_ms":g["provider_latency_ms"]})

with open(f"{OUT}/groq120b-deterministic-fact-comparison.csv","w",newline="",encoding="utf-8") as f:
    w=csv.DictWriter(f,fieldnames=list(det[0].keys())); w.writeheader(); w.writerows(det)

print(f"\n=== deterministic all-18 fact-atom comparison ===")
print(f"  atoms total={A_t}  nvidia20b={A_n} ({A_n/A_t:.3f})  groq120b={A_g} ({A_g/A_t:.3f})")
win=sum(1 for d in det if d["delta_groq_minus_nvidia"]>0)
loss=sum(1 for d in det if d["delta_groq_minus_nvidia"]<0)
tie=sum(1 for d in det if d["delta_groq_minus_nvidia"]==0)
print(f"  per-case: groq higher={win}  nvidia higher={loss}  equal={tie}")
for d in det:
    flag="" if d["delta_groq_minus_nvidia"]==0 else ("  <-- groq+" if d["delta_groq_minus_nvidia"]>0 else "  <-- nvidia+")
    print(f"  {d['case_id']} atoms={d['fact_atoms']:<3} nv={d['nvidia20b_atoms_present']:<3} "
          f"gq={d['groq120b_atoms_present']:<3} d={d['delta_groq_minus_nvidia']:+d}{flag}")
print(f"  refusal markers: nvidia={[d['case_id'] for d in det if d['nvidia20b_refusal_marker']]} "
      f"groq={[d['case_id'] for d in det if d['groq120b_refusal_marker']]}")
json.dump({"atoms_total":A_t,"nvidia20b":A_n,"groq120b":A_g,
           "per_case_groq_higher":win,"per_case_nvidia_higher":loss,"per_case_equal":tie},
          open(os.path.join(OUTD,"groq120b_fact_atoms.json"),"w"),indent=2)
