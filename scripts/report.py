"""Prints the simulation funnel and the independent invariant audit for the naive and the fixed orchestrator.
Caller behaviour is ASSUMED (src/callflow/sim.py PROFILE): the output shows how each implementation handles those
assumptions, not how real callers behave. Run from the lab root: python3 scripts/report.py"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from callflow.model import Policy  # noqa: E402
from callflow.naive import NaiveOrchestrator  # noqa: E402
from callflow.orchestrator import Orchestrator  # noqa: E402
from callflow.sim import audit, funnel, simulate  # noqa: E402

SEEDS, N = (1, 2, 3, 4, 5), 150
pol = Policy()
print(f"Simulation: {len(SEEDS)} seeds x {N} cases x 6 days, time step 15 minutes, window spans a US daylight-saving change.\n")
for name, impl in (("naive", NaiveOrchestrator), ("fixed", Orchestrator)):
    fun, aud = {}, {}
    for s in SEEDS:
        o, cust, obs, end = simulate(impl, n=N, seed=s, policy=pol)
        for k, v in funnel(o, cust, obs).items():
            fun[k] = fun.get(k, 0) + v
        for k, v in audit(o, cust, obs, end, pol).items():
            aud[k] = aud.get(k, 0) + v
    cases = fun["cases"]
    print(f"== {name} ({cases} cases) ==")
    print(f"  confirmed by the system: {fun['system_confirmed']}  declined by the system: {fun['system_declined']}  "
          f"sent to a person: {fun['reached_a_person']}  still open at the end: {fun['still_open']}  calls per case: {fun['calls'] / cases:.2f}")
    print("  invariant violations (0 is the goal):")
    for k, v in aud.items():
        print(f"    {k:<40} {v}")
    print()
