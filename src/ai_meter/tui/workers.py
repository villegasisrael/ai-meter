from __future__ import annotations

import time
from threading import Event, Lock, Thread
from typing import Callable


class PeriodicWorker:
    """Runs one periodic task on a persistent daemon thread."""

    def __init__(
        self,
        name: str,
        target: Callable[[], None],
        interval_s: float,
        should_run: Callable[[], bool] | None = None,
    ) -> None:
        self.name = name
        self._target = target
        self._interval_s = max(0.1, float(interval_s))
        self._should_run = should_run
        self._stop = Event()
        self._wake = Event()
        self._lock = Lock()
        self._thread: Thread | None = None
        self._force_once = False
        self._running = False
        self._last_error: str | None = None

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._running

    @property
    def last_error(self) -> str | None:
        with self._lock:
            return self._last_error

    def start(self, initial_delay_s: float = 0.0) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = Thread(
            target=self._loop,
            args=(max(0.0, float(initial_delay_s)),),
            name=f"ai-meter-{self.name}",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout_s: float = 1.0) -> None:
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=max(0.0, float(timeout_s)))

    def set_interval(self, interval_s: float) -> None:
        with self._lock:
            self._interval_s = max(0.1, float(interval_s))

    def trigger(self, force: bool = False) -> None:
        if force:
            with self._lock:
                self._force_once = True
        self._wake.set()

    def _interval(self) -> float:
        with self._lock:
            return self._interval_s

    def _consume_force(self) -> bool:
        with self._lock:
            force = self._force_once
            self._force_once = False
            return force

    def _set_running(self, running: bool) -> None:
        with self._lock:
            self._running = running

    def _set_error(self, error: str | None) -> None:
        with self._lock:
            self._last_error = error

    def _loop(self, initial_delay_s: float) -> None:
        next_run = time.monotonic() + initial_delay_s
        while not self._stop.is_set():
            timeout = max(0.0, next_run - time.monotonic())
            woke = self._wake.wait(timeout)
            if self._stop.is_set():
                break
            if woke:
                self._wake.clear()

            force = self._consume_force()
            if not force and self._should_run is not None and not self._should_run():
                next_run = time.monotonic() + self._interval()
                continue

            self._set_running(True)
            try:
                self._target()
                self._set_error(None)
            except Exception as exc:
                self._set_error(str(exc)[:240])
            finally:
                self._set_running(False)

            next_run = time.monotonic() + self._interval()
