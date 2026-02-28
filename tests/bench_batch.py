"""
Batch validation performance benchmark.

Tests sequential vs parallel execution across different worker counts
using the 200-loan fixture in tests/large_batch_loans.json.

Usage (from validation-lib root):
    python tests/bench_batch.py

    # Override worker counts:
    BENCH_WORKERS=1,2,4,8 python tests/bench_batch.py

Results are printed as they arrive so you can see progress in real time.
"""

import json
import os
import sys
import time
import statistics
from pathlib import Path

from validation_lib import ValidationService

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

LOANS_FILE = Path(__file__).parent / "large_batch_loans.json"
RULESET = "thorough"
N_WARMUP = 1
N_RUNS = 3

# Worker counts to test — override via BENCH_WORKERS env var (comma-separated)
_env_workers = os.environ.get("BENCH_WORKERS")
if _env_workers:
    WORKER_COUNTS = [int(w) for w in _env_workers.split(",")]
else:
    WORKER_COUNTS = [2, 4, 8]

# ---------------------------------------------------------------------------
# Load test data
# ---------------------------------------------------------------------------

with open(LOANS_FILE) as f:
    loans = json.load(f)

N_LOANS = len(loans)


# ---------------------------------------------------------------------------
# Benchmark runner
# ---------------------------------------------------------------------------


def run_config(label: str, n_workers=None) -> dict:
    """
    Benchmark one configuration.

    Args:
        label: Display label.
        n_workers: None = sequential (no pool). int = parallel with that many workers.

    Returns:
        Dict with label, mean_ms, min_ms, max_ms, throughput, speedup (added later).
    """
    svc = ValidationService()

    if n_workers is not None:
        # Patch local_config so we can test different worker counts without
        # editing the bundled YAML between runs.
        svc.config_loader.local_config["batch_parallelism"] = True
        svc.config_loader.local_config["batch_max_workers"] = n_workers
        svc._create_pool()

    try:
        times_ms = []
        for i in range(N_WARMUP + N_RUNS):
            t0 = time.perf_counter()
            results = svc.batch_validate(loans, ["id"], RULESET)
            elapsed_ms = (time.perf_counter() - t0) * 1000
            tag = (
                f"warmup {i + 1}"
                if i < N_WARMUP
                else f"run {i - N_WARMUP + 1}/{N_RUNS}"
            )
            print(f"    [{tag}] {elapsed_ms:,.0f} ms", flush=True)
            if i >= N_WARMUP:
                times_ms.append(elapsed_ms)

        assert len(results) == N_LOANS, (
            f"Expected {N_LOANS} results, got {len(results)}"
        )

    finally:
        svc.close()

    mean_ms = statistics.mean(times_ms)
    return {
        "label": label,
        "mean_ms": mean_ms,
        "min_ms": min(times_ms),
        "max_ms": max(times_ms),
        "throughput": N_LOANS / (mean_ms / 1000),
        "speedup": None,  # filled in after all configs complete
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    cpu = os.cpu_count()
    print(
        f"\nPython {sys.version.split()[0]}  |  cpus={cpu}  |  loans={N_LOANS}"
        f"  |  ruleset={RULESET}  |  runs={N_RUNS} timed + {N_WARMUP} warmup"
    )
    print("=" * 68)

    configs = [("Sequential (no pool)", None)] + [
        (f"Parallel — {n} worker{'s' if n != 1 else ''}", n)
        for n in WORKER_COUNTS
        if n <= cpu
    ]

    rows = []
    for label, n_workers in configs:
        print(f"\n{label}:")
        row = run_config(label, n_workers)
        rows.append(row)
        print(f"  → mean {row['mean_ms']:,.0f} ms  |  {row['throughput']:,.1f} ent/sec")

    # Speedup relative to sequential baseline
    seq_mean = rows[0]["mean_ms"]
    for row in rows:
        row["speedup"] = seq_mean / row["mean_ms"]

    # Summary table
    print(f"\n\n{'=' * 68}")
    print(
        f"  {N_LOANS} loans  |  {RULESET} ruleset  |  {N_RUNS} timed runs + {N_WARMUP} warmup"
    )
    print(f"{'=' * 68}")
    print(
        f"  {'Config':<26} {'Mean ms':>8} {'Min ms':>8} {'Max ms':>8}"
        f" {'Ent/sec':>9} {'Speedup':>8}"
    )
    print(f"  {'-' * 66}")
    for row in rows:
        print(
            f"  {row['label']:<26} "
            f"{row['mean_ms']:>8,.0f} "
            f"{row['min_ms']:>8,.0f} "
            f"{row['max_ms']:>8,.0f} "
            f"{row['throughput']:>9,.1f} "
            f"{row['speedup']:>8.2f}x"
        )
    print(f"{'=' * 68}")

    best = max(rows, key=lambda r: r["throughput"])
    print(
        f"\n  Optimal: {best['label']}"
        f"  —  {best['throughput']:,.1f} ent/sec"
        f"  ({best['speedup']:.2f}x sequential)\n"
    )
