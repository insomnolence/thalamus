"""``thalamus m1a-eval`` — run the M-1a gotcha-conversion probe.

Loads a frozen cases file, drives a pluggable actuator (Ollama / Claude / Codex /
Gemini) through the four arms, judges each output with the case's deterministic
oracle, and reports the pre-committed contrasts. This is opt-in research tooling, not
part of the serving brain — see ``docs/eval/m1a.md``.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from thalamus.eval.m1a import (
    TrialRecord,
    build_actuator,
    format_report,
    load_cases,
    run_cases,
    summarize,
)


@dataclass(frozen=True)
class M1aEvalConfig:
    cases: Path
    backend: str
    model: str
    base_url: str | None
    trials: int
    temperatures: tuple[float, ...]
    timeout: float
    retries: int
    seed: int
    audit: Path | None
    as_json: bool


def add_m1a_eval_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--cases", required=True, type=Path, help="frozen M-1a cases JSON file")
    parser.add_argument(
        "--actuator",
        default="ollama",
        choices=("ollama", "claude", "anthropic", "openai", "codex", "gemini"),
        help="actuator backend (default: ollama)",
    )
    parser.add_argument("--model", required=True, help="model name for the backend")
    parser.add_argument("--base-url", default=None, help="override the backend base URL")
    parser.add_argument("--trials", type=int, default=8, help="trials per arm per case (default 8)")
    parser.add_argument(
        "--temperatures", default="0.2,0.7", help="comma-separated trial temperatures"
    )
    parser.add_argument("--timeout", type=float, default=180.0, help="per-call timeout seconds")
    parser.add_argument(
        "--retries", type=int, default=2, help="retries per call before a conservative miss"
    )
    parser.add_argument("--seed", type=int, default=0, help="bootstrap seed (reproducible CI)")
    parser.add_argument(
        "--audit", default=None, type=Path, help="write a per-trial JSONL audit log"
    )
    parser.add_argument("--json", dest="as_json", action="store_true", help="emit a JSON report")


def m1a_eval_config(args: argparse.Namespace) -> M1aEvalConfig:
    temps = tuple(float(t) for t in str(args.temperatures).split(",") if t.strip())
    if not temps:
        raise ValueError("--temperatures must list at least one value")
    return M1aEvalConfig(
        cases=args.cases,
        backend=args.actuator,
        model=args.model,
        base_url=args.base_url,
        trials=int(args.trials),
        temperatures=temps,
        timeout=float(args.timeout),
        retries=int(args.retries),
        seed=int(args.seed),
        audit=args.audit,
        as_json=bool(args.as_json),
    )


def _recorder(handle: TextIO) -> Any:
    def record(trial: TrialRecord) -> None:
        handle.write(
            json.dumps(
                {
                    "case_id": trial.case_id,
                    "arm": trial.arm,
                    "trial": trial.trial,
                    "temperature": trial.temperature,
                    "hit": trial.hit,
                    "output": trial.output,
                }
            )
            + "\n"
        )

    return record


def run_m1a_eval(config: M1aEvalConfig) -> int:
    cases = load_cases(config.cases)
    options: dict[str, Any] = {"timeout": config.timeout}
    if config.base_url is not None:
        options["base_url"] = config.base_url
    actuator = build_actuator(config.backend, config.model, **options)

    print(
        f"M-1a: {len(cases)} case(s), actuator={actuator.name}, "
        f"{config.trials} trials/arm, temps={list(config.temperatures)}"
    )

    audit_handle: TextIO | None = None
    if config.audit is not None:
        audit_handle = config.audit.open("w", encoding="utf-8")
    try:
        recorder = _recorder(audit_handle) if audit_handle is not None else None

        def progress(result: Any) -> None:
            rates = " ".join(f"{arm}={result.avoid_rate(arm):.2f}" for arm in ("off", "full"))
            print(f"  [{result.case_set}] {result.case_id}: {rates}")

        results = run_cases(
            cases,
            actuator,
            trials=config.trials,
            temperatures=config.temperatures,
            recorder=recorder,
            on_case=progress,
            retries=config.retries,
        )
    finally:
        if audit_handle is not None:
            audit_handle.close()

    report = summarize(results, seed=config.seed)
    if config.as_json:
        print(
            json.dumps(
                {
                    "passes": report.passes,
                    "criteria": report.criteria,
                    "primary_mean_delta": report.primary.mean_delta,
                    "primary_ci": [report.primary.ci_low, report.primary.ci_high],
                    "negative_control_delta": report.negative_control_delta,
                }
            )
        )
    else:
        print(format_report(report))
    return 0


def _parse(argv: Sequence[str] | None = None) -> M1aEvalConfig:
    parser = argparse.ArgumentParser(prog="thalamus m1a-eval")
    add_m1a_eval_arguments(parser)
    return m1a_eval_config(parser.parse_args(argv))
