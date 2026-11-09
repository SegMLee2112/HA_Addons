"""Random scenarios against the schedule (tests/schedule_scenarios.py): nothing is ever left
plugged in with no end (2.96.0 to 2.96.2: many of these did that before; the ones listed in
KNOWN still do, being looked into)."""
import logging

from tests.schedule_scenarios import run

# The first 100 scenarios (each of the bugs fixed in 2.96.0 to 2.96.2 showed in some of them)
SEEDS = range(100)
KNOWN = [288, 658]  # (still left plugged in, of the first 700: a Charge now replaced while parts of the
# one before it were moved for a saving session, then one deleted on the page)


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
