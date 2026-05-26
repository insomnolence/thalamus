"""thalamus.eval — the eval harness (OLR §13.20).

Two L1 proxies, by label source:
- **synthetic labels** (the frozen regression guard): recall@k/precision@k/MRR/hit@k
  via :func:`evaluate`, plus the ablation ``compare`` switch over named retrievers
  (brain-off / per-rung / full);
- **real signals** (the live proxy): :func:`utility_at_k`, computed offline by
  joining the retrieval-event log and the Tier-1 usage log on ``event_id``.

The L2 task outcomes and the L3 brain-on/off verdict arrive once outcome capture and
an actuator are wired in.
"""

from thalamus.eval.benchmark import BenchmarkCase, load_cases
from thalamus.eval.harness import EvalReport, NullRetriever, compare, evaluate
from thalamus.eval.metrics import hit_at_k, precision_at_k, recall_at_k, reciprocal_rank
from thalamus.eval.proxy_truth import (
    ProxyTruthReport,
    join_proxy_truth,
    proxy_truth,
    session_proxy_truth,
    session_utility,
)
from thalamus.eval.utility import UtilityReport, utility_at_k

__all__ = [
    "BenchmarkCase",
    "EvalReport",
    "NullRetriever",
    "ProxyTruthReport",
    "UtilityReport",
    "compare",
    "evaluate",
    "hit_at_k",
    "join_proxy_truth",
    "load_cases",
    "precision_at_k",
    "proxy_truth",
    "recall_at_k",
    "reciprocal_rank",
    "session_proxy_truth",
    "session_utility",
    "utility_at_k",
]
