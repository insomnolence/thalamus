"""thalamus.eval — the eval harness (OLR §13.20).

Three L1 sources, by label source:
- **synthetic labels** (the frozen regression guard): recall@k/precision@k/MRR/hit@k
  via :func:`evaluate`, plus the ablation ``compare`` switch over named retrievers
  (brain-off / per-rung / full);
- **real signals** (the live proxy): :func:`utility_at_k`, computed offline by
  joining the retrieval-event log and the Tier-1 usage log on ``event_id``;
- **real questions** (the transcript-corpus probe): :func:`evaluate_probes` over
  substantive user prompts extracted from Claude Code session transcripts — measures
  surface quality on the *actual* questions the actuator asked, ablated against a
  brain-off floor. No labels (transcripts don't carry them) → score-distribution
  metrics rather than recall@k.

The L2 task outcomes and the L3 brain-on/off verdict arrive once outcome capture and
an actuator are wired in.
"""

from thalamus.eval.benchmark import BenchmarkCase, load_cases
from thalamus.eval.harness import EvalReport, NullRetriever, compare, evaluate
from thalamus.eval.impact import (
    BlastRadiusOracle,
    ImpactPair,
    ImpactReport,
    evaluate_impact,
    map_changes_to_refs,
    parse_changed_lines,
)
from thalamus.eval.metrics import hit_at_k, precision_at_k, recall_at_k, reciprocal_rank
from thalamus.eval.plan_brief import (
    BriefCaseResult,
    BriefEvalReport,
    GotchaCase,
    PlanBriefView,
    evaluate_plan_briefs,
    score_case,
    view_from_brief,
)
from thalamus.eval.probe import (
    ProbeOutcome,
    ProbeReport,
    compare_probes,
    evaluate_probes,
)
from thalamus.eval.proxy_truth import (
    ProxyTruthReport,
    join_proxy_truth,
    proxy_truth,
    session_proxy_truth,
    session_utility,
)
from thalamus.eval.stability import UsageStabilityReport, usage_stability
from thalamus.eval.transcripts import (
    TranscriptProbe,
    default_transcripts_dir,
    extract_probes,
    find_transcripts,
)
from thalamus.eval.utility import UtilityReport, utility_at_k

__all__ = [
    "BenchmarkCase",
    "BlastRadiusOracle",
    "BriefCaseResult",
    "BriefEvalReport",
    "EvalReport",
    "GotchaCase",
    "PlanBriefView",
    "ImpactPair",
    "ImpactReport",
    "NullRetriever",
    "ProbeOutcome",
    "ProbeReport",
    "ProxyTruthReport",
    "TranscriptProbe",
    "UsageStabilityReport",
    "UtilityReport",
    "compare",
    "compare_probes",
    "default_transcripts_dir",
    "evaluate",
    "evaluate_impact",
    "evaluate_plan_briefs",
    "evaluate_probes",
    "extract_probes",
    "find_transcripts",
    "hit_at_k",
    "join_proxy_truth",
    "load_cases",
    "map_changes_to_refs",
    "parse_changed_lines",
    "precision_at_k",
    "proxy_truth",
    "recall_at_k",
    "reciprocal_rank",
    "score_case",
    "session_proxy_truth",
    "session_utility",
    "usage_stability",
    "utility_at_k",
    "view_from_brief",
]
