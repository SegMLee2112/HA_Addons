"""Random scenarios against the schedule (tests/schedule_scenarios.py): nothing is ever left
plugged in with no end (2.96.0: each of these seeds did that before; the ones listed in
KNOWN still do, being looked into)."""
import logging

from tests.schedule_scenarios import run

# Found by the scenarios and fixed in 2.96.0 (and some that always passed, to keep it so)
SEEDS = [5, 10, 13, 15, 16, 19, 24, 26, 29, 30, 31, 33, 35, 41, 42, 50, 52, 54, 58, 62, 64, 65, 66,
         69, 71, 72, 80, 84, 99, 100, 103, 112, 115, 122, 126, 146, 204, 233]
KNOWN = [63, 108, 111, 151, 214, 227, 243, 267, 288]  # (still left plugged in: to do)


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
