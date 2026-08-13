"""MarkON Studio GUI 진입점.

    python -m host.gui.app --port sim      보드 없이 (시뮬레이터)
    python -m host.gui.app --port COM23    실물 보드

🔴 `--port sim` 이 기본이다. 보드 없이도 화면이 뜨고 설정을 만질 수 있어야
   한다. 시뮬레이터와 실물이 같은 답을 낸다는 것은 C 와 Python 을 바이트
   단위로 대조해 확인해 두었다
   (`firmware/stage1/tests/crosscheck_hostlink.py`).
"""

from __future__ import annotations

import argparse
import sys
import time

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from host.core.config_schema import parse_catalog
from host.gui.command_queue import CommandQueue
from host.gui.last_known import StateHistory, build_chip_state
from host.gui.qt.chip import ChipCard
from host.gui.qt.settings_page import SettingsPage
from host.gui.qt.worker import WorkerThread
from host.gui.settings_form import SettingsForm
from host.gui.theme import Color, Space, stylesheet
from host.gui.widgets.status_chip import Level, Verification, rail_label

WINDOW_TITLE = "MarkON Studio"


class Dashboard(QWidget):
    """레일과 채널 상태.

    🔴 전원 레일은 영원히 `COMMANDED` 다. 피드백 회로가 없으므로 GPIO 를
       올렸다는 것과 실제로 24V 가 나온다는 것은 다른 사실이고, 보드는
       후자를 모른다. 화면이 둘을 같은 초록 점으로 그리면 사용자는 확인된
       것으로 읽는다.
    """

    RAILS = (("pwr.24v", "24V"), ("pwr.14v9", "14.9V"), ("pwr.5v", "5V"))

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._history = StateHistory()
        self._cards: dict[str, ChipCard] = {}

        title = QLabel("전원")
        title.setObjectName("h1")

        rails = QHBoxLayout()
        rails.setSpacing(Space.MD)
        for key, _label in self.RAILS:
            card = ChipCard()
            self._cards[key] = card
            rails.addWidget(card)
        rails.addStretch(1)

        self._link = QLabel("보드 없음")
        self._link.setObjectName("dim")

        col = QVBoxLayout(self)
        col.setContentsMargins(Space.LG, Space.LG, Space.LG, Space.LG)
        col.setSpacing(Space.MD)
        col.addWidget(title)
        col.addLayout(rails)
        col.addWidget(self._link)
        col.addStretch(1)

    def update_rails(self, values: dict[str, bool], *, reachable: bool,
                     now_s: float | None = None) -> None:
        now = time.monotonic() if now_s is None else now_s
        for key, label in self.RAILS:
            on = bool(values.get(key, False))
            if reachable:
                # 명령 상태는 안다. 실제 상태는 모른다 — 그래서 COMMANDED.
                verification = Verification.COMMANDED
                level = Level.OK if on else Level.IDLE
            else:
                verification = Verification.UNKNOWN
                level = Level.IDLE
            state = build_chip_state(
                self._history, key, label, level, verification, now,
                detail=rail_label(on, verification),
            )
            self._cards[key].apply(state)

    def set_link(self, text: str, bad: bool = False) -> None:
        self._link.setText(text)
        self._link.setStyleSheet(f"color: {Color.FAULT};" if bad else "")


class MainWindow(QMainWindow):
    def __init__(self, service, port_label: str) -> None:
        super().__init__()
        self.setWindowTitle(f"{WINDOW_TITLE} — {port_label}")
        self.resize(1100, 720)

        self._service = service
        self._queue = CommandQueue()

        self._dashboard = Dashboard()
        self._settings = SettingsPage()
        self._settings.apply_requested.connect(self._on_apply)

        self._pages = QStackedWidget()
        self._pages.addWidget(self._dashboard)
        self._pages.addWidget(self._settings)

        nav = QHBoxLayout()
        for i, name in enumerate(("대시보드", "설정")):
            btn = QPushButton(name)
            btn.clicked.connect(lambda _, idx=i: self._pages.setCurrentIndex(idx))
            nav.addWidget(btn)
        nav.addStretch(1)

        self._mode = QLabel("RUN")
        self._mode.setObjectName("dim")
        nav.addWidget(self._mode)

        body = QWidget()
        col = QVBoxLayout(body)
        col.setContentsMargins(Space.MD, Space.MD, Space.MD, Space.SM)
        col.addLayout(nav)
        col.addWidget(self._pages, 1)
        self.setCentralWidget(body)

        self._worker = WorkerThread(service, self._queue)
        self._worker.worker.stepped.connect(self._on_step)
        self._worker.start()

        self._load_catalog()

    # ------------------------------------------------------------- 카탈로그

    def _load_catalog(self) -> None:
        """`$CFG,LIST` 만으로 설정 화면을 만든다.

        🔴 항목을 하드코딩하지 않는다. 펌웨어 단계마다 항목이 늘기 때문이다.
        """
        try:
            schema = self._service.fetch_schema()
        except Exception as exc:            # noqa: BLE001
            self._dashboard.set_link(f"설정을 불러오지 못했다: {exc}", bad=True)
            return
        if not schema.items:
            self._dashboard.set_link("보드가 설정 카탈로그를 주지 않았다", bad=True)
            return
        self._settings.set_form(SettingsForm(schema))

    # ------------------------------------------------------------- 워커

    def _on_step(self, result) -> None:
        self._mode.setText(result.mode)
        reachable = result.error is None
        if result.error:
            self._dashboard.set_link(f"통신 오류: {result.error}", bad=True)
        else:
            self._dashboard.set_link("연결됨")

        rails = {}
        form = getattr(self._settings, "_form", None)
        if form is not None:
            for key, _label in Dashboard.RAILS:
                if key in form.keys():
                    rails[key] = form.row(key).value == "true"
        self._dashboard.update_rails(rails, reachable=reachable)

        for res in result.results:
            key = res.tag.split(":", 1)[-1] if res.tag else ""
            if res.ok:
                self._settings.on_accepted(key)
            else:
                self._settings.on_rejected(key, res.reason or "거부됨")

    def _on_apply(self, changes: list) -> None:
        for key, value in changes:
            self._queue.submit("CFG", "SET", key, value, tag=f"set:{key}")

    def closeEvent(self, event) -> None:      # noqa: N802
        self._worker.stop()
        super().closeEvent(event)


def make_service(port: str, baud: int):
    """`sim` 이면 시뮬레이터, 아니면 실물 시리얼.

    CLI 와 같은 함수를 쓴다 — 두 곳에서 따로 만들면 시계 기준이 갈린다.
    """
    from tools.cli.markon_cli import make_service as cli_make_service

    return cli_make_service(port, baud)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="markon-gui")
    parser.add_argument("--port", default="sim",
                        help="시리얼 포트 또는 sim (기본: sim)")
    parser.add_argument("--baud", type=int, default=115200)
    args = parser.parse_args(argv)

    app = QApplication(sys.argv[:1])
    app.setStyleSheet(stylesheet())

    service = make_service(args.port, args.baud)
    window = MainWindow(service, args.port)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
