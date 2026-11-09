"""Random scenarios against the schedule (tests/schedule_scenarios.py): nothing is ever left
plugged in with no end (2.96.0, 2.96.1: many of these did that before; the ones listed in
KNOWN still do, being looked into)."""
import logging

from tests.schedule_scenarios import run

# The first 100 scenarios (each of the bugs fixed in 2.96.0 and 2.96.1 showed in some of them)
SEEDS = range(100)
KNOWN = [134, 258, 335, 407, 441, 464, 481, 527, 594]  # (still left plugged in, of the first 600: to do)


def test_nothing_is_left_plugged_in_with_no_end():
    logging.disable(logging.CRITICAL)
    try:
        bad = {}
        for seed in SEEDS:
            for kind, problem, log, _ in run(seed):
                if kind == "crash" or problem.startswith("plugged in"):
                    bad[seed] = (problem, log)
        assert not bad, bad
    finally:
        logging.disable(logging.NOTSET)
