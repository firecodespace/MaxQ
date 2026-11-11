"""Benchmark suite, protocol, lower bounds, Hall of Fame and runner (WP2-12).

Read-only to any agent (charter addition 1). The Hall of Fame's write path is deliberately
NOT exported here: it lives behind `benchmarks.hof.open_for_runner`, which the benchmark
runner calls and nothing else should.
"""

from benchmarks import lower_bound, protocol, suite

__all__ = ["lower_bound", "protocol", "suite"]
