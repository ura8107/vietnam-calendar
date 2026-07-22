from pathlib import Path
from vietnam_calendar.application.evals import evaluate_rules,load_cases,similar_cases

ROOT=Path(__file__).parents[3]
def test_canonical_eval_hash_and_complete_report():
    cases,digest=load_cases(ROOT/"evals/importance-v1.jsonl")
    report=evaluate_rules(ROOT/"evals/importance-v1.jsonl")
    assert len(cases)==57 and len(digest)==64 and report["case_count"]==57
    assert 0<=report["exact_accuracy"]<=1 and 0<=report["within_one_accuracy"]<=1
    assert isinstance(report["must_include_gate_passed"],bool)
    assert len(report["cases"])==57

def test_similar_cases_are_deterministic_and_corpus_backed():
    path=ROOT/"evals/importance-v1.jsonl"
    first=similar_cases(path,"ベトナムのGDP成長率を正式発表",category="economy",limit=3)
    assert first==similar_cases(path,"ベトナムのGDP成長率を正式発表",category="economy",limit=3)
    assert first["dataset_version"]=="importance-v1" and len(first["dataset_sha256"])==64
    assert len(first["matches"])==3 and first["matches"][0]["id"].startswith("IMP-")
    assert all({"expected_importance","reason","similarity"}<=match.keys() for match in first["matches"])
