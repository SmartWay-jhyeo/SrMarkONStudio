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
from dataclasses import replace

from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QMainWindow,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from host.core.limits import DEFAULT_BAUD
from host.gui.command_queue import CommandQueue
from host.gui.diagnostics import build_diagnostics
from host.gui.qt.dashboard import Dashboard
from host.gui.qt.diagnostics_page import DiagnosticsPage
from host.gui.qt.parts import TestBand
from host.gui.qt.settings_page import SettingsPage
from host.gui.qt.stream_view import StreamView
from host.gui.qt.topbar import TopBar
from host.gui.qt.view import View
from host.gui.qt.worker import WorkerThread
from host.gui.settings_form import (
    SettingsForm,
    channel_ranges,
    channel_units,
    i2c_ports,
)
from host.gui.qt.rail import Rail
from host.gui.last_known import StateHistory
from host.gui.screen import (
    AIN_COUNT,
    RAILS,
    Identity,
    ScreenState,
    build_dins,
    build_screen,
    empty_channels,
)
from host.gui.stream import StreamState
from host.gui.qt.style import stylesheet

WINDOW_TITLE = "MarkON Studio"
#: 🔴 `진단` 은 맨 뒤다. 평소에 볼 화면이 아니라 **뭔가 이상할 때** 여는
#:    화면이고, 앞에 두면 대시보드로 가는 길이 한 칸 멀어진다.
PAGES = ("대시보드", "설정", "스트림", "진단")


def _checked_views(*views) -> tuple[View, ...]:
    """뷰 목록을 만들면서 계약을 확인한다.

    🔴 `View` 는 `runtime_checkable` 이라 `isinstance` 가 `render` 의 존재를
       본다. 서명까지는 못 보지만, 실제로 겪는 사고는 "render 를 안 만들었다"
       와 "이름을 다르게 썼다" 이지 인자 개수가 아니다.
    """
    for v in views:
        if not isinstance(v, View):
            raise TypeError(
                f"{type(v).__name__} 이 뷰 계약을 지키지 않는다 — "
                f"`render(state)` 가 필요하다 (host/gui/qt/view.py)"
            )
    return views


class MainWindow(QMainWindow):
    def __init__(self, service, port_label: str, *,
                 baud: int = DEFAULT_BAUD) -> None:
        super().__init__()
        self.setWindowTitle(f"{WINDOW_TITLE} — {port_label}")
        self.resize(1180, 800)
        # 🔴 대역폭 여유를 재려면 링크 속도를 알아야 한다. 설정 화면이
        #    필드를 고를 때 "이만큼이면 링크의 몇 %" 를 말해 준다.
        self._baud = baud

        self._service = service
        self._port_label = port_label
        self._queue = CommandQueue()

        self._top = TopBar(PAGES)
        self._top.set_identity(port_label)
        self._top.ctl_mode_requested.connect(self._on_ctl_mode)
        self._band = TestBand()
        self._dashboard = Dashboard()
        self._settings = SettingsPage(baud=baud)
        self._settings.apply_requested.connect(self._on_apply)
        self._settings.save_requested.connect(self._on_save)
        self._settings.reset_requested.connect(self._on_reset)
        #: 🔴 NDJSON 이 실제로 오고 있는지 보여주는 원문 스트림.
        #:    `StreamState` 는 Qt 를 모른다(host/gui/stream.py) — 여기서는
        #:    보관하고 `_on_step` 에서 먹이기만 한다.
        self._stream_state = StreamState()
        self._stream_view = StreamView()

        #: 🔴 `$STAT` 진단. 보드가 말하는 것 중 텔레메트리에 안 실리는 것들이
        #:    여기로 온다 — 클럭 출처·PPS 원시 캡처·큐 유실·화면 회복 계수기.
        #:    이게 없어서 "PPS 가 안 온다" 를 GDB 로 파고들었다(diagnostics.py).
        self._diagnostics = DiagnosticsPage()
        #: 마지막으로 읽은 `$STAT` 과 그 시각. 🔴 둘을 함께 들고 있어야
        #:    "언제 읽은 값인가" 를 화면이 말할 수 있다. 값만 남기면 링크가
        #:    끊긴 뒤에도 낡은 값이 지금 값처럼 보인다.
        self._stat: dict | None = None
        self._stat_at: float | None = None
        self._stat_error = ""

        self._pages = QStackedWidget()
        self._pages.addWidget(self._dashboard)
        self._pages.addWidget(self._settings)
        self._pages.addWidget(self._stream_view)
        self._pages.addWidget(self._diagnostics)
        self._top.page_selected.connect(self._pages.setCurrentIndex)

        # 🔴 세 구역으로 나눈다: 정체성 바(위) · 레일(왼쪽) · 캔버스.
        #
        #    처음에는 위·아래 두 덩이였는데, 화면을 띄워 보니 가운데 60% 가
        #    비고 전원 레일이 맨 아래 고아처럼 떨어져 있었다. 어디부터
        #    읽어야 할지 알 수 없는 화면이었다.
        #
        #    레일을 왼쪽에 세우면 세 가지가 한꺼번에 풀린다 — 화면에
        #    무게중심이 생기고, 전원이 늘 보이는 자리로 오고(5V 가 없으면
        #    채널이 하나도 안 돈다), 캔버스의 가로폭이 줄어 채널 카드가
        #    적당한 크기가 된다.
        self._rail = Rail()
        # 🔴 레일을 만지면 설정 화면을 거쳐 보드로 간다. 레일이 자기 상태를
        #    따로 들고 있지 않는 것이 요점이다 — 화면에 그려진 값이 곧
        #    보드에 보낸 값이다 (`_rail_values` 주석 참조).
        self._rail.railToggled.connect(self._on_rail_toggled)

        split = QWidget()
        row = QHBoxLayout(split)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addWidget(self._rail)
        row.addWidget(self._pages, 1)

        body = QWidget()
        col = QVBoxLayout(body)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        col.addWidget(self._top)
        col.addWidget(self._band)
        col.addWidget(split, 1)
        self.setCentralWidget(body)

        # 🔴 뷰 목록. 배치를 바꾸는 것은 이 목록과 위 레이아웃을 고치는
        #    일이고, 데이터 배선(_on_step)은 건드리지 않는다.
        # 🔴 뷰 계약을 **여기서** 확인한다 (qt/view.py).
        #
        #    예전에는 오리 타입이었다. `render` 를 빠뜨린 뷰를 넣으면 창은
        #    멀쩡히 뜨고 **다음 워커 주기에** 터진다 — 100 ms 뒤, 생성과
        #    상관없어 보이는 자리에서. 계약을 Protocol 로 적어 두고 아무도
        #    확인하지 않으면 그것은 계약이 아니라 주석이다.
        self._views = _checked_views(
            self._top, self._band, self._rail, self._dashboard)
        self._history = StateHistory()
        self._state = ScreenState(channels=empty_channels())

        self._worker = WorkerThread(service, self._queue)
        self._worker.worker.stepped.connect(self._on_step)
        self._worker.start()

        self._load_identity()
        self._load_catalog()
        self._load_din_state()

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

    def _load_din_state(self) -> None:
        """`$STAT` 을 한 번 읽어 din(J18~J20) 의 지금 상태를 세운다.

        🔴 사용자 확정 — "연결이 끊기면 마지막 상태가 지워지는게 아니라
           바로 읽을 수 있으니까 괜찮아". 그 전제가 성립하려면 호스트가
           실제로 `$STAT` 을 읽어야 한다. `din` 레코드(규격 §7.6)는 상태가
           **바뀔 때만** 오므로, 이걸 안 하면 막 연결한 화면은 보드가 이미
           알고 있는 지금 상태를 몰라 세 칸 다 "확인 불가"로 뜬다.

           연결 직후(`_load_identity`·`_load_catalog` 와 같은 자리) 한 번만
           부른다. 그 뒤의 변화는 `din` 레코드가 그대로 알려 주므로,
           주기적으로 다시 묻는 것은 낭비고 "실측은 보드가 안다"(설계
           원칙 3, CLAUDE.md §3)와도 어긋난다 — 폴링이 아니라 이벤트로
           받는다.

           지금 이 앱은 재연결을 자동으로 하지 않는다(연결은 시작할 때
           한 번뿐). 그래도 이 메서드를 `_load_identity`·`_load_catalog`
           와 나란히 독립된 함수로 둔 이유는, 재연결 흐름이 생기면 그
           자리에서 이 메서드 하나만 다시 부르면 되게 하기 위해서다.
        """
        try:
            stat = self._service.fetch_stat()
        except Exception as exc:             # noqa: BLE001
            self._stat_error = str(exc)
            return
        # 🔴 진단 화면도 이 응답으로 첫 화면을 세운다. 워커가 곧 다시 묻지만
        #    (worker_loop._fetch_stat), 그 전까지 "전부 모름" 을 보여줄 이유가
        #    없다 — 방금 읽은 답을 이미 손에 들고 있다.
        self._stat, self._stat_at, self._stat_error = stat, time.monotonic(), ""
        din = stat.get("din")
        if not isinstance(din, list) or not din:
            return
        # 🔴 `$STAT` 의 din 항목에는 `t` 가 없다(규격 §7.4 예시) — "언제
        #    바뀌었나" 가 아니라 "지금 무엇인가" 만 준다. build_dins() 는
        #    `t` 가 없으면 changed_at 을 건드리지 않으므로 그대로 넘긴다.
        seed = [
            {"type": "din", "connector_id": d.get("connector_id"),
             "state": d.get("state")}
            for d in din if isinstance(d, dict)
        ]
        self._state = replace(
            self._state,
            dins=build_dins(seed, reachable=True, previous=self._state.dins,
                            history=self._history, now_s=time.monotonic()),
        )

    def _rail_values(self) -> dict[str, bool]:
        """설정 화면이 들고 있는 레일 값.

        🔴 화면에 그려진 값이 곧 보드에 보낸 값이다. 별도로 보관하면 둘이
           갈리고, 그때 화면이 어느 쪽을 말하는지 알 수 없다.
        """
        form = self._settings.form
        if form is None:
            return {}
        return {key: form.row(key).value == "true"
                for key, _label in RAILS if key in form.keys()}

    def _channel_ranges(self) -> dict[int, tuple[float, float]]:
        """센서의 물리량 범위. 레일 값과 같은 이유로 설정 화면에서 읽는다 —
        화면에 그려진 값이 곧 보드에 보낸 값이다."""
        form = self._settings.form
        return channel_ranges(form) if form is not None else {}

    def _channel_units(self) -> dict[int, str]:
        """단위도 설정에서 읽는다 — `unit` 필드는 기본 마스크에서 꺼져 있다."""
        form = self._settings.form
        return channel_units(form) if form is not None else {}

    def _i2c_ports(self) -> dict[int, tuple[int, bool]]:
        """I2C 포트별 (종류, 사용 여부). 레일 값과 같은 이유로 설정
        화면에서 읽는다 — 화면에 그려진 값이 곧 보드에 보낸 값이다."""
        form = self._settings.form
        return i2c_ports(form) if form is not None else {}

    # ------------------------------------------------------------- 워커

    def _on_step(self, result) -> None:
        """워커가 한 바퀴 돌 때마다.

        🔴 하는 일이 셋뿐이다: **상태를 만들고 · 뷰들에 건네고 · 명령
           결과를 설정 화면에 알린다.** 뷰끼리 값을 주고받지 않는다.

           예전에는 여기서 레일 값을 설정 화면의 private 에서 꺼내
           대시보드를 거쳐 레일에 넣었다. 그래서 배치를 바꾸면 이 함수도
           같이 뜯어야 했다. 이제 배치를 바꿔도 이 함수는 그대로다.
        """
        self._state = build_screen(
            self._state,
            identity=self._state.identity,
            mode=result.mode,
            ctl_mode=result.ctl_mode,
            error=result.error,
            rail_values=self._rail_values(),
            records=result.records,
            history=self._history,
            ranges=self._channel_ranges(),
            units=self._channel_units(),
            i2c_ports=self._i2c_ports(),
        )
        for view in self._views:
            view.render(self._state)

        # 🔴 영점 패널은 뷰가 아니라 설정 화면의 일부라 `render` 로는 안
        #    닿는다. 여기서 넘긴다 — 화면 상태를 만드는 곳이 여기이고,
        #    설정 화면이 텔레메트리를 직접 듣게 하면 뷰끼리 값을 주고받는
        #    구조로 되돌아간다(위 머리말).
        self._settings.set_live_ma(
            {ch.index: ch.ma for ch in self._state.channels})

        # 🔴 스트림 화면도 같은 이유로 `_views` 밖에 있다 — `render` 서명이
        #    `(state, now_s)` 라 뷰 계약(`ScreenState` 만)과 다르다(위 머리말).
        #    `time.monotonic()` 을 쓴다 — 보드 시계(`result` 안의 `t`)가
        #    아니라 "화면이 지금 언제라고 느끼는가" 가 필요하기 때문이다.
        #    연결이 끊겨도 이 시계는 계속 흘러야 "마지막 수신 후 경과" 가
        #    실제로 늘어난다.
        now_s = time.monotonic()
        self._stream_state.ingest(result.raw_lines, now_s=now_s)
        self._stream_view.render(self._stream_state, now_s=now_s)

        # 🔴 진단 화면도 `_views` 밖이다 — 스트림과 같은 이유로 "지금이
        #    언제인가" 를 알아야 한다. `$STAT` 은 1.5 초에 한 번만 오므로
        #    (worker_loop.STAT_INTERVAL_S), 매 스텝 다시 그리는 목적은 값이
        #    아니라 **나이**다. 링크가 끊기면 값은 그대로인데 나이만 늘고,
        #    그 사실이 곧 사람이 알아야 할 것이다.
        self._render_diagnostics(result, now_s)

        for res in result.results:
            tag = res.tag or ""
            if tag == "cfg:save":
                if res.ok:
                    self._settings.mark_saved()
                    self._top.set_link("저장됨")
                else:
                    self._top.set_link(
                        f"저장 실패: {res.reason or res.error}", bad=True)
                continue
            if tag == "cfg:reset":
                if res.ok:
                    # 보드 값이 전부 바뀌었다 — 화면을 다시 읽는다.
                    self._settings.on_reset_done()
                    self._load_catalog()
                else:
                    self._top.set_link(
                        f"초기화 실패: {res.reason or res.error}", bad=True)
                continue

            key = tag.split(":", 1)[-1]
            if res.ok:
                self._settings.on_accepted(key)
            else:
                self._settings.on_rejected(key, res.reason or "거부됨")

    def _render_diagnostics(self, result, now_s: float) -> None:
        """워커가 물어온 `$STAT` 을 진단 화면에 먹인다.

        🔴 **판정은 여기 없다.** `build_diagnostics()` 가 Qt 없이 전부 하고
           (경고 여부·번역·"모름"), 이 함수는 무엇을 언제 읽었는지만 챙긴다.

        🔴 실패했다고 마지막 값을 지우지 않는다. 지우면 링크가 한 번 끊길
           때마다 화면이 통째로 비고, "끊기기 직전에 이랬다" 가 사라진다 —
           채널 트레이스를 남기는 것과 같은 판단(screen.build_channels).
           대신 나이를 함께 보여 주어 지금 값이 아님을 말한다.
        """
        if result.stat is not None:
            self._stat, self._stat_at, self._stat_error = result.stat, now_s, ""
        elif result.stat_error:
            self._stat_error = result.stat_error

        age = None if self._stat_at is None else now_s - self._stat_at
        self._diagnostics.render(build_diagnostics(
            self._stat, error=self._stat_error, age_s=age))

    def _on_ctl_mode(self, mode: str) -> None:
        """제어 모드 전환을 보드에 요청한다 (규격 §6.4).

        🔴 화면이 먼저 바뀌지 않는다. 보드가 거부할 수 있고(RUN 에서 TEST
           진입), 그때 눌린 대로 그려 두면 사용자가 실제와 다른 모드를
           믿는다. `$STAT` 이 돌려주는 값만 화면에 반영된다.
        """
        self._queue.submit("MODE", mode, tag="mode")

    def _on_rail_toggled(self, key: str, on: bool) -> None:
        """대시보드에서 전원 레일을 누르고 있었다.

        🔴 설정 화면의 `적용` 을 기다리지 않고 바로 보낸다. 누르고 있는
           0.7 초가 이미 확인이고, 거기서 또 한 번 누르게 하면 hold-to-run
           이 확인 대화상자와 다를 바 없어진다.
        """
        text = "true" if on else "false"
        self._settings.set_value(key, text)
        self._queue.submit("CFG", "SET", key, text, tag=f"set:{key}")

    def _on_apply(self, changes: list) -> None:
        for key, value in changes:
            self._queue.submit("CFG", "SET", key, value, tag=f"set:{key}")

    def _on_save(self) -> None:
        """🔴 태그를 주지 않는다 — 합쳐지면 안 된다.

        저장을 두 번 누른 것은 두 번 저장하겠다는 뜻이다. 값 설정과 달리
        "마지막 것만 반영되면 되는" 종류가 아니다 (command_queue 참조).

        다만 결과를 화면에 되돌려 붙이려면 태그가 필요하다. 고정 태그를
        쓰되, 저장은 보통 한 번에 하나만 떠 있으므로 합쳐질 일이 없다.
        """
        self._queue.submit("CFG", "SAVE", tag="cfg:save")

    def _on_reset(self) -> None:
        self._queue.submit("CFG", "RESET", tag="cfg:reset")

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
    window = MainWindow(service, args.port, baud=args.baud)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
