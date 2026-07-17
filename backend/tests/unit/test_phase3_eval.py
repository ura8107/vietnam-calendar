from pathlib import Path
from vietnam_calendar.application.evals import evaluate_rules,load_cases

ROOT=Path(__file__).parents[3]
def test_canonical_eval_hash_and_complete_report():
    cases,digest=load_cases(ROOT/"evals/importance-v1.jsonl")
    report=evaluate_rules(ROOT/"evals/importance-v1.jsonl")
    assert len(cases)==57 and len(digest)==64 and report["case_count"]==57
    assert 0<=report["exact_accuracy"]<=1 and 0<=report["within_one_accuracy"]<=1
    assert isinstance(report["must_include_gate_passed"],bool)
    assert len(report["cases"])==57
