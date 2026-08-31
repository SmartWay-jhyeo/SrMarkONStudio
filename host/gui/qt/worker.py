"""워커 루프를 Qt 스레드에 얹는 껍데기.

🔴 여기에 로직을 넣지 않는다. `worker_loop.WorkerLoop.step()` 이 "이번에
   무슨 일이 있었나" 를 돌려주고, 이 파일은 그것을 신호로 바꿔 GUI 스레드에
   넘기기만 한다.

   그 경계가 이 앱의 동시성 규약이다. 시리얼은 느리고(115200 에서 설정
   카탈로그 하나가 몇 초), GUI 스레드에서 부르면 창이 얼어붙는다. 반대로
   워커 스레드에서 위젯을 만지면 Qt 가 죽는다.

   그래서 오가는 것은 **값뿐**이다 — 위젯 참조도, 잠금도 넘기지 않는다.
"""

from __future__ import annotations

import time

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from host.gui.command_queue import CommandQueue
from host.gui.worker_loop import StepResult, WorkerLoop


class BoardWorker(QObject):
    """스레드 안에서 도는 쪽."""

    stepped = pyqtSignal(object)      #: StepResult
    stopped = pyqtSignal()

    def __init__(self, service, queue: CommandQueue, *,
                 interval_ms: int = 100) -> None:
        super().__init__()
        self._loop = WorkerLoop(service, queue)
        self._interval_ms = interval_ms
        self._running = False

    def start(self) -> None:
        """스레드에 얹힌 뒤 호출된다."""
        self._running = True
        self._tick()

    def stop(self) -> None:
        self._running = False

    def _tick(self) -> None:
        from PyQt6.QtCore import QTimer

        if not self._running:
            self.stopped.emit()
            return
        try:
            result = self._loop.step(time.monotonic())
        except Exception as exc:            # noqa: BLE001
            # 🔴 워커가 예외로 죽으면 GUI 는 아무것도 모른 채 영원히 기다린다.
            #    화면에 띄울 수 있도록 결과로 바꿔 보낸다.
            result = StepResult(pump_error=f"{type(exc).__name__}: {exc}")
        self.stepped.emit(result)
        QTimer.singleShot(self._interval_ms, self._tick)


class WorkerThread:
    """스레드와 워커를 함께 들고 있는 손잡이."""

    def __init__(self, service, queue: CommandQueue, *,
                 interval_ms: int = 100) -> None:
        self.thread = QThread()
        self.worker = BoardWorker(service, queue, interval_ms=interval_ms)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.start)

    def start(self) -> None:
        self.thread.start()

    def stop(self, wait_ms: int = 2000) -> None:
        self.worker.stop()
        self.thread.quit()
        self.thread.wait(wait_ms)
