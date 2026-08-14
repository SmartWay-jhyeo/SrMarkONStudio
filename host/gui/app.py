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

from PyQt6.QtWidgets import (
    QApplication,
    QMainWindow,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from host.core.limits import DEFAULT_BAUD
from host.gui.command_queue import CommandQueue
from host.gui.qt.dashboard import AIN_COUNT, Dashboard
from host.gui.qt.settings_page import SettingsPage
from host.gui.qt.topbar import TopBar
from host.gui.qt.worker import WorkerThread
from host.gui.settings_form import SettingsForm
from host.gui.theme import stylesheet
from host.gui.widgets.status_chip import Level, Verification

WINDOW_TITLE = "MarkON Studio"
PAGES = ("대시보드", "설정")


class MainWindow(QMainWindow):
    def __init__(self, service, port_label: str) -> None:
        super().__init__()
        self.setWindowTitle(f"{WINDOW_TITLE} — {port_label}")
        self.resize(1180, 800)

        self._service = service
        self._port_label = port_label
        self._queue = CommandQueue()

        self._top = TopBar(PAGES)
        self._top.set_identity(port_label)
        self._dashboard = Dashboard()
        self._settings = SettingsPage()
        self._settings.apply_requested.connect(self._on_apply)

        self._pages = QStackedWidget()
        self._pages.addWidget(self._dashboard)
        self._pages.addWidget(self._settings)
        self._top.page_selected.connect(self._pages.setCurrentIndex)

        body = QWidget()
        col = QVBoxLayout(body)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        col.addWidget(self._top)
        col.addWidget(self._pages, 1)
        self.setCentralWidget(body)

        self._worker = WorkerThread(service, self._queue)
        self._worker.worker.stepped.connect(self._on_step)
        self._worker.start()

        self._load_identity()
        self._load_catalog()

    # ------------------------------------------------------------- 초기화

    def _load_identity(self) -> None:
        """`$ID` 로 보드가 자기를 뭐라고 하는지 받아 상단에 건다.

        🔴 벤치에서 여러 보드를 옮겨 다니므로, 지금 보는 화면이 어느 보드인지
           항상 보여야 한다. 헷갈리면 24V 를 엉뚱한 보드에 켠다.
        """
        try:
            self._service.send("ID")
        except Exception:                   # noqa: BLE001
            return
        payload = getattr(self._service, "last_payload", None) or {}
        self._top.set_identity(
            self._port_label,
            str(payload.get("device_id", "")),
            str(payload.get("fw", "")),
            str(payload.get("board_rev", "")),
        )

    def _load_catalog(self) -> None:
        """`$CFG,LIST` 만으로 설정 화면을 만든다.

        🔴 항목을 하드코딩하지 않는다. 펌웨어 단계마다 항목이 늘기 때문이다.
        """
        try:
            schema = self._service.fetch_schema()
        except Exception as exc:            # noqa: BLE001
            self._top.set_link(f"설정을 불러오지 못했다: {exc}", bad=True)
            return
        if not schema.items:
            self._top.set_link("보드가 설정 카탈로그를 주지 않았다", bad=True)
            return
        self._settings.set_form(SettingsForm(schema))

    # ------------------------------------------------------------- 워커

    def _on_step(self, result) -> None:
        self._top.set_mode(result.mode)
        reachable = result.error is None
        if result.error:
            self._top.set_link(f"통신 오류: {result.error}", bad=True)
            self._dashboard.set_link("확인 불가", bad=True)
        else:
            self._top.set_link("연결됨")
            self._dashboard.set_link(f"{AIN_COUNT}채널 · 4–20 mA")

        rails = {}
        form = getattr(self._settings, "_form", None)
        if form is not None:
            for key, _label in Dashboard.RAILS:
                if key in form.keys():
                    rails[key] = form.row(key).value == "true"
        self._dashboard.update_rails(rails, reachable=reachable)
        self._apply_records(result.records, reachable=reachable)

        for res in result.results:
            key = res.tag.split(":", 1)[-1] if res.tag else ""
            if res.ok:
                self._settings.on_accepted(key)
            else:
                self._settings.on_rejected(key, res.reason or "거부됨")

    def _apply_records(self, records, *, reachable: bool) -> None:
        """텔레메트리를 게이지에 옮긴다.

        🔴 채널 장애 격리 — 한 채널이 이상해도 나머지는 계속 갱신된다.
           레코드를 하나씩 보고, 오지 않은 채널은 건드리지 않는다.
        """
        if not reachable:
            for ch in range(AIN_COUNT):
                self._dashboard.update_channel(
                    ch, None, level=Level.IDLE,
                    verification=Verification.UNKNOWN,
                )
            return

        for rec in records or ():
            if not isinstance(rec, dict) or rec.get("type") != "ain":
                continue
            cid = rec.get("connector_id")
            if not isinstance(cid, int):
                continue
            ch = cid - 3          # AIN0 = J3 (데이터시트 §5.3)
            if not (0 <= ch < AIN_COUNT):
                continue
            ma = rec.get("ma")
            status = rec.get("status", 0)
            value = rec.get("value")
            self._dashboard.update_channel(
                ch,
                float(ma) if isinstance(ma, (int, float)) else None,
                level=Level.OK if not status else Level.WARN,
                verification=Verification.VERIFIED,
                value=float(value) if isinstance(value, (int, float)) else None,
                unit=str(rec.get("unit", "")),
            )

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
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUD)
    args = parser.parse_args(argv)

    app = QApplication(sys.argv[:1])
    app.setStyleSheet(stylesheet())

    service = make_service(args.port, args.baud)
    window = MainWindow(service, args.port)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
