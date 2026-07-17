# Importance rubric evaluation set

`importance-v1.jsonl` is the machine-readable form of `IMP-001` through
`IMP-057` in `REQUIREMENTS.md`. `expected_importance` is `null` only when the
example is outside the Vietnam diary scope. `must_include` is independent of
the four-level importance scale.

This set is a development contract, not a training/test split. Do not use it
alone to decide whether automatic publication is safe; a separate blind set
from real RSS reviews is required.

The contract test also pins the SHA-256 of the complete canonical JSONL bytes,
covering scenarios, reasons, and tags in addition to expected labels. An
intentional rubric change must create a new versioned dataset (for example
`importance-v2.jsonl`) and add its reviewed hash; do not silently replace the
v1 hash. If v1 itself is corrected, review the full diff and deliberately
refresh the fixed hash in `test_phase0_assets.py`.
