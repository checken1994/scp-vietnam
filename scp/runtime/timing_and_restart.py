"""[G3-STUB → G4-FIX → G5-FIX] PhaseTimer + QueryTimeoutError.

Previous version: 265 LOC with AutoRestart class (never instantiated).
AutoRestart was dead code — run_247.py has its own restart loop.

[G4-FIX] Tried adding .phase as an int attribute to support AttackPolicyDecision
phase tracking — but that test actually checks `decision.phase`, not
`timer.phase`. The int attribute was therefore unused.

[G5-FIX] judgecore_mixin.py uses `_v100_timer.phase("knowledge")` as a context
manager AND `_v100_timer.finish().to_dict()`. Previous G4-FIX made `phase` an
int attribute → broke the context manager protocol → TypeError("'int' object
is not callable") on every judge() call (10+ characterization failures + 3 v98
wired tests). Fix: `phase(name)` is a contextmanager method; `finish()` returns
self with `.to_dict()` exposing per-phase elapsed times. `check_timeout(timer,
phase_name)` checks the elapsed of a named phase against the default 10s V100
budget.
"""
import time
from collections.abc import Iterator
from contextlib import contextmanager

# Default per-phase time budget (seconds). V100 contract: 10s per phase.
_DEFAULT_PHASE_BUDGET_S = 10.0


class QueryTimeoutError(Exception):
    """Raised when a query exceeds its time budget."""


class PhaseTimer:
    """Phase tracker + timer — supports context-manager phase blocks and
    per-phase elapsed queries.

    Usage:
        timer = PhaseTimer()
        with timer.phase("knowledge"):
            ... do work ...
        check_timeout(timer.finish(), "knowledge")  # raises if > 10s
        timings_dict = timer.finish().to_dict()  # {"knowledge": 1.2, ...}
    """

    def __init__(self) -> None:
        self.start = time.time()
        self.phase_number = 0  # legacy int phase number (unused by V100 path)
        self._phase_times: dict[str, float] = {}  # phase name → total elapsed
        self._phase_starts: dict[str, float] = {}  # phase name → start time (if open)
        self._phase_budgets: dict[str, float] = {}  # phase name → budget (s)

    def elapsed(self) -> float:
        """Total elapsed time since timer creation."""
        return time.time() - self.start

    def mark(self, phase: int) -> None:
        """Record entry into a numbered phase (legacy API)."""
        self.phase_number = phase

    def phase_elapsed(self, phase) -> float:
        """Time spent in a specific phase (name or number)."""
        if isinstance(phase, str):
            return self._phase_times.get(phase, 0.0)
        # Numeric phase — fallback to 0 (legacy mark() did not record times)
        return 0.0

    @contextmanager
    def phase(self, name: str, budget: float = _DEFAULT_PHASE_BUDGET_S) -> Iterator[None]:
        """Context manager: time a named phase.

        Records elapsed time into self._phase_times. Records budget for
        later check_timeout() validation.
        """
        self._phase_budgets[name] = budget
        t0 = time.time()
        self._phase_starts[name] = t0
        try:
            yield
        finally:
            elapsed = time.time() - t0
            self._phase_times[name] = self._phase_times.get(name, 0.0) + elapsed
            self._phase_starts.pop(name, None)

    def finish(self) -> "PhaseTimer":
        """Finalize timing — returns self so callers can chain .to_dict()."""
        # Close any still-open phases (defensive — shouldn't happen with `with`)
        for name in list(self._phase_starts.keys()):
            t0 = self._phase_starts.pop(name)
            elapsed = time.time() - t0
            self._phase_times[name] = self._phase_times.get(name, 0.0) + elapsed
        return self

    def to_dict(self) -> dict:
        """Return per-phase elapsed times as a dict (phase name → seconds)."""
        return dict(self._phase_times)

    def get_phase_elapsed(self, name: str) -> float:
        """Public accessor for a named phase's elapsed time."""
        return self._phase_times.get(name, 0.0)


def check_timeout(timer: PhaseTimer, phase_name: str = "", budget: float = _DEFAULT_PHASE_BUDGET_S) -> None:
    """Raise QueryTimeoutError if elapsed time of `phase_name` exceeds `budget`.

    If `phase_name` is empty, checks total timer.elapsed() against budget.
    Backwards-compat: if `timer` is a float/int (legacy callers passing elapsed
    directly), compares it against `budget`.
    """
    if isinstance(timer, PhaseTimer):
        if phase_name:
            elapsed = timer.get_phase_elapsed(phase_name)
            # Allow per-phase budget override set in phase()
            actual_budget = timer._phase_budgets.get(phase_name, budget)
        else:
            elapsed = timer.elapsed()
            actual_budget = budget
        if elapsed > actual_budget:
            raise QueryTimeoutError(
                f"Phase {phase_name!r} exceeded {actual_budget}s budget (elapsed={elapsed:.2f}s)"
            )
    else:
        # Legacy compat: caller passed a float elapsed + budget
        try:
            elapsed = float(timer)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return
        if elapsed > budget:
            raise QueryTimeoutError(f"Query exceeded {budget}s budget")
