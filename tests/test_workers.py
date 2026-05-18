import time
import unittest
from threading import Event

from ai_meter.tui.workers import PeriodicWorker


class WorkerTests(unittest.TestCase):
    def test_trigger_force_runs_when_paused(self) -> None:
        ran = Event()
        worker = PeriodicWorker(
            "test",
            target=ran.set,
            interval_s=60,
            should_run=lambda: False,
        )
        try:
            worker.start(initial_delay_s=60)
            worker.trigger(force=True)
            self.assertTrue(ran.wait(1.0))
        finally:
            worker.stop()

    def test_regular_run_honors_should_run(self) -> None:
        count = 0

        def target() -> None:
            nonlocal count
            count += 1

        worker = PeriodicWorker(
            "paused",
            target=target,
            interval_s=0.1,
            should_run=lambda: False,
        )
        try:
            worker.start(initial_delay_s=0)
            time.sleep(0.25)
            self.assertEqual(count, 0)
        finally:
            worker.stop()


if __name__ == "__main__":
    unittest.main()
