from __future__ import annotations
import hashlib,json
from pathlib import Path
from typing import Any
from difflib import SequenceMatcher
import re
from .importance import classify

LEVELS={"low":0,"middle":1,"middle_high":2,"high":3}
def load_cases(path:Path)->tuple[list[dict[str,Any]],str]:
    raw=path.read_bytes(); cases=[json.loads(line) for line in raw.splitlines() if line.strip()]
    if len(cases)!=57 or len({c["id"] for c in cases})!=57: raise ValueError("canonical eval set must contain 57 unique cases")
    return cases,hashlib.sha256(raw).hexdigest()
def evaluate_rules(path:Path)->dict[str,Any]:
    cases,digest=load_cases(path); rows=[]
    for c in cases:
        r=classify(c["scenario"]); actual=r.importance.value if r.importance else None
        exact=r.relevance.value==c["expected_relevance"] and actual==c["expected_importance"] and r.must_include==c["must_include"]
        within=actual==c["expected_importance"] or (actual is not None and c["expected_importance"] is not None and abs(LEVELS[actual]-LEVELS[c["expected_importance"]])<=1)
        rows.append({"id":c["id"],"actual_relevance":r.relevance.value,"actual_importance":actual,"actual_must_include":r.must_include,"exact":exact,"within_one":within})
    must=[(c,row) for c,row in zip(cases,rows) if c["must_include"]]; targets=[(c,row) for c,row in zip(cases,rows) if c["expected_relevance"]=="target"]
    out=[(c,row) for c,row in zip(cases,rows) if c["expected_relevance"]=="out_of_scope"]
    ratio=lambda n,d: n/d if d else 1.0
    return {"rubric_version":"importance-rubric-v1","dataset_sha256":digest,"case_count":57,"exact_accuracy":ratio(sum(x["exact"] for x in rows),57),"within_one_accuracy":ratio(sum(x["within_one"] for x in rows),57),"must_include_recall":ratio(sum(row["actual_must_include"] for _,row in must),len(must)),"must_include_gate_passed":all(row["actual_must_include"] for _,row in must),"target_recall":ratio(sum(row["actual_relevance"]=="target" for _,row in targets),len(targets)),"out_of_scope_recall":ratio(sum(row["actual_relevance"]=="out_of_scope" for _,row in out),len(out)),"schema_success_rate":1.0,"cases":rows}

def similar_cases(path:Path,query:str,*,category:str|None=None,limit:int=5)->dict[str,Any]:
    """Deterministic corpus lookup; never asks an AI to invent precedents."""
    cases,digest=load_cases(path);limit=max(1,min(limit,10))
    tokens=lambda value:set(re.findall(r"[\w]+",value.casefold()))
    query_tokens=tokens(f"{query} {category or ''}")
    rows=[]
    for case in cases:
        candidate_tokens=tokens(f"{case['scenario']} {' '.join(case.get('tags',[]))}")
        union=query_tokens|candidate_tokens
        jaccard=len(query_tokens&candidate_tokens)/len(union) if union else 0.0
        sequence=SequenceMatcher(None,query.casefold(),case["scenario"].casefold()).ratio()
        category_match=1.0 if category and category.casefold() in {str(tag).casefold() for tag in case.get("tags",[])} else 0.0
        score=.55*jaccard+.35*sequence+.10*category_match
        rows.append({"id":case["id"],"scenario":case["scenario"],"expected_relevance":case["expected_relevance"],"expected_importance":case["expected_importance"],"must_include":case["must_include"],"reason":case["reason"],"tags":case.get("tags",[]),"similarity":round(score,6)})
    rows.sort(key=lambda row:(-row["similarity"],row["id"]))
    return {"dataset_version":"importance-v1","dataset_sha256":digest,"rubric_version":"importance-rubric-v1","matches":rows[:limit]}
