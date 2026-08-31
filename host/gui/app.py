"""MarkON Studio GUI 진입점.

    python -m host.gui.app --port COM23    실물 보드

🔴 `--port` 는 필수다. 예전에는 기본값이 `sim`(내장 시뮬레이터)이었지만
   시뮬레이터는 없앴다(2026-08-20) — 보드가 늘 붙어 있어서 진짜 카탈로그로
   바로 확인되고, 설정 항목을 늘릴 때마다 두 곳을 맞추는 비용만 남았다.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from dataclasses import dataclass, replace

from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QMainWindow,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from host.core.config_snapshot import items_from_schema, save_snapshot
from host.core.limits import DEFAULT_BAUD, LINK_BAUD_CHOICES
from host.gui.command_queue import CommandQueue
from host.gui.diagnostics import build_diagnostics
from host.gui.link_baud import failure_hint, outcome_text
from host.gui.qt.dashboard import Dashboard
from host.gui.qt.diagnostics_page import DiagnosticsPage
from host.gui.qt.parts import TestBand
from host.gui.qt.settings_page import SettingsPage
from host.gui.qt.stream_view import StreamView
from host.gui.qt.topbar import TopBar
from host.gui.qt.view import View
from host.gui.qt.worker import WorkerThread
from host.core.typemap import TypeMap
from host.gui.settings_form import (
    SettingsForm,
    channel_ranges,
    channel_units,
    cloud_duplicate_warning,
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


@dataclass(frozen=True)
class _BaudOutcome:
    """워커가 `payload` 로 돌려준 링크 속도 결과를 다시 세운 것 (규격 §4.2).

    🔴 큐를 지나오면서 dataclass 가 dict 가 된다(스레드 경계를 넘는 것은
       평범한 자료여야 한다). 화면 문구를 만드는 `host.gui.link_baud` 는
       필드 이름만 보므로, 여기서 같은 이름으로 다시 묶어 준다 —
       `payload.get(...)` 를 문구 함수 안에 흩뿌리지 않기 위해서다.
    """

    ok: bool
    baud: int
    stage: str
    reason: str
    error: str
    recovered: bool


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


class ConnectBar(QWidget):
    """포트를 골라 [연결] 하는 막대 — 자동 연결을 없앤 자리(사용자 결정).

    🔴 포트 목록은 [새로고침] 을 눌러야 다시 읽는다. 몰래 주기적으로
       읽으면 사용자가 고르는 중에 목록이 바뀐다.
    """

    def __init__(self, baud: int, parent=None) -> None:
        super().__init__(parent)
        from PyQt6.QtWidgets import QComboBox, QPushButton, QLabel
        self._ports = QComboBox()
        self._ports.setMinimumWidth(110)
        self._baud = QComboBox()
        # 🔴 목록을 손으로 적지 않는다 — 한 곳(host/core/limits, 펌웨어의
        #    MK_LINKBAUD_CHOICE_LIST 와 대조됨)에서 온다. test_spec_sync 가
        #    하드코딩을 잡았다.
        for b in LINK_BAUD_CHOICES:
            self._baud.addItem(str(b), b)
        self._baud.setCurrentText(str(baud))
        self._refresh_btn = QPushButton("새로고침")
        self._refresh_btn.clicked.connect(self.refresh)
        self._btn = QPushButton("연결")
        self._msg = QLabel("")
        self._msg.setObjectName("dim")
        row = QHBoxLayout(self)
        row.setContentsMargins(16, 4, 16, 4)
        row.setSpacing(8)
        row.addWidget(QLabel("포트"))
        row.addWidget(self._ports)
        row.addWidget(self._refresh_btn)
        row.addWidget(QLabel("속도"))
        row.addWidget(self._baud)
        row.addWidget(self._btn)
        row.addWidget(self._msg, 1)
        self.refresh()

    def refresh(self) -> None:
        import serial.tools.list_ports as lp
        cur = self._ports.currentText()
        self._ports.clear()
        for pinfo in sorted(lp.comports(), key=lambda x: x.device):
            self._ports.addItem(pinfo.device)
        if cur:
            i = self._ports.findText(cur)
            if i >= 0:
                self._ports.setCurrentIndex(i)

    def selection(self) -> tuple[str, int]:
        return self._ports.currentText(), int(self._baud.currentData())

    def set_connected(self, on: bool, msg: str = "") -> None:
        self._btn.setText("연결 해제" if on else "연결")
        self._ports.setEnabled(not on)
        self._baud.setEnabled(not on)
        self._refresh_btn.setEnabled(not on)
        self._msg.setText(msg)


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
        self._settings.baud_change_requested.connect(self._on_baud_change)
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

        # 🔴 캔버스를 스크롤로 감싼다 — 창을 내용의 최소 크기보다 작게 줄일
        #    수 있게. 안 감싸면 대시보드(게이지 7개 × 최소 150 px)가 창
        #    최소폭을 1100 px 대로 밀어올려서, 작은 화면·창 절반 배치에서
        #    창이 줄어들지 않는다(사용자 보고 2026-08-20). 감싸면 창은
        #    얼마든지 줄고, 안 들어가는 내용은 잘리는 대신 스크롤이 된다.
        self._pages_scroll = QScrollArea()
        self._pages_scroll.setWidget(self._pages)
        self._pages_scroll.setWidgetResizable(True)
        self._pages_scroll.setFrameShape(QFrame.Shape.NoFrame)
        # 🔴 대시보드 칸 수의 기준은 **뷰포트 폭**이다 — 대시보드 자신의
        #    resizeEvent 는 스크롤 영역이 최소폭에서 멈추는 순간 오지 않아,
        #    좁힌 창에서 칸 수가 넓던 시절에 박힌다(dashboard.py 주석).
        self._pages_scroll.viewport().installEventFilter(self)
        # 🔴 가로 스크롤은 없다 (사용자 결정 2026-08-20). 칸 수가 뷰포트
        #    폭을 따라 1칸까지 접히므로 가로로 넘칠 정당한 사유가 없다 —
        #    가로 스크롤바가 나타난다는 것 자체가 배치 결함이라는 뜻이고,
        #    그때는 숨어서 반쯤 보이느니 잘리는 편이 결함을 빨리 드러낸다.
        from PyQt6.QtCore import Qt as _Qt
        self._pages_scroll.setHorizontalScrollBarPolicy(
            _Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

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
        row.addWidget(self._pages_scroll, 1)

        body = QWidget()
        col = QVBoxLayout(body)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        col.addWidget(self._top)
        self._connbar = ConnectBar(baud)
        self._connbar._btn.clicked.connect(self._on_connect_clicked)
        col.addWidget(self._connbar)
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

        self._worker = None
        # 🔴 자동으로 연결하지 않는다 (사용자 결정 2026-08-20). 프로그램을
        #    켜는 것과 포트를 여는 것은 다른 일이다 — 예고 없는 포트 개폐가
        #    F103 브리지를 굳게 만드는 재발 패턴(HANDOFF §3)과도 맞물린다.
        #    사용자가 포트를 고르고 [연결] 을 눌러야 연다.
        if service is not None:
            self._attach(service, port_label)
            self._connbar.set_connected(True, "")

    def _on_connect_clicked(self) -> None:
        if self._worker is not None:
            self._detach()
            self._connbar.set_connected(False, "해제됨")
            return
        port, baud = self._connbar.selection()
        if not port:
            self._connbar.set_connected(False, "포트가 없다 — 새로고침을 눌러 보라")
            return
        try:
            service = make_service(port, baud)
        except Exception as exc:            # noqa: BLE001
            # 🔴 실패 이유를 그 자리에서 말한다 — "액세스 거부" 는 대개
            #    다른 프로그램이 그 포트를 잡고 있다는 뜻이다.
            self._connbar.set_connected(False, f"연결 실패: {exc}")
            return
        self._baud = baud
        self._attach(service, port)
        self._connbar.set_connected(True, "")

    # ------------------------------------------------------------- 연결
    def _attach(self, service, port_label: str) -> None:
        """포트가 열린 service 를 받아 워커를 돌리기 시작한다."""
        self._service = service
        self._port_label = port_label
        self.setWindowTitle(f"{WINDOW_TITLE} — {port_label}")
        self._top.set_identity(port_label)
        self._worker = WorkerThread(service, self._queue)
        self._worker.worker.stepped.connect(self._on_step)
        self._worker.start()
        self._load_identity()
        self._load_catalog()
        self._load_din_state()

    def _detach(self) -> None:
        """워커를 세우고 포트를 닫는다. 화면은 마지막 상태를 유지한다 —
        지우면 '끊기 직전이 어땠나' 를 잃는다(설계 원칙, last_known)."""
        if self._worker is not None:
            self._worker.stop()
            self._worker = None
        if self._service is not None:
            try:
                self._service.close()
            except Exception:               # noqa: BLE001
                pass
            self._service = None
        self._top.set_link("연결 해제됨", bad=False)

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
        # 🔴 [2026-08-31] 재시도 3회 — 부팅 직후·USB 재열거 직후에 연결하면
        #    첫 $CFG,LIST 가 한 번 실패할 수 있는데, 이 요청은 접속에 한 번
        #    뿐이라 그 한 번의 불운으로 설정 화면을 통째로 잃었다(실사고:
        #    새 보드 첫 연결에서 스트림은 흐르는데 설정만 빈 화면).
        schema = None
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                schema = self._service.fetch_schema()
                if schema.items:
                    break
            except Exception as exc:        # noqa: BLE001
                last_exc = exc
            time.sleep(0.5)
        if schema is None or not schema.items:
            if last_exc is not None:
                self._top.set_link(f"설정을 불러오지 못했다: {last_exc}",
                                   bad=True)
            else:
                self._top.set_link("보드가 설정 카탈로그를 주지 않았다",
                                   bad=True)
            return
        self._settings.set_form(SettingsForm(schema))
        self._apply_connector_names(schema)
        # 🔴 보드 파라미터의 PC 사본 (HANDOFF_0831 결정 3) — 접속할 때
        #    전체를, SET 이 수락될 때 그 키만 갱신해 남긴다. 굽기 후 복원
        #    (tools/restore_board_config.py)·역매핑·백업의 공용 소스다.
        self._snapshot_items = items_from_schema(schema)
        self._save_snapshot()
        # 🔴 타입 중복 경고 (HANDOFF_0831 결정 2 보완) — 같은 타입 문자열이
        #    두 채널이면 역매핑이 못 가린다. 접속 직후가 알릴 자리다.
        dup_warning = cloud_duplicate_warning(
            TypeMap.from_schema(schema).duplicates())
        if dup_warning:
            self._top.set_link(dup_warning, bad=True)

    def _save_snapshot(self) -> None:
        try:
            save_snapshot(getattr(self, "_snapshot_items", {}) or {},
                          port=getattr(self, "_port_label", ""))
        except OSError as exc:
            # 사본은 편의 장치다 — 저장 실패가 수집·설정을 막으면 안 된다.
            # 다만 조용히 삼키지도 않는다: 사람이 보는 줄에 남긴다.
            self._top.set_link(f"설정 사본 저장 실패: {exc}", bad=True)

    def _apply_connector_names(self, schema) -> None:
        """카탈로그의 `*.name` 항목을 커넥터 번호 → 이름 사전으로.

        🔴 키 규칙이 그룹마다 다르다 — `ain0` 은 0부터 세는 채널이라
           커넥터는 +3 이고(J3), `i2c10`·`din18` 은 숫자가 커넥터 번호
           그대로다. 사용자가 나중에 ain 키를 바꾸겠다고 했으니(2026-08-19)
           그때 이 갈림도 없어진다.
        """
        names: dict[int, str] = {}
        for key, item in schema.items.items():
            m = re.fullmatch(r"(ain|i2c|din)(\d+)\.name", key)
            if not m:
                continue
            value = str(getattr(item, "current", "") or "").strip()
            if not value:
                continue
            n = int(m.group(2))
            names[3 + n if m.group(1) == "ain" else n] = value
        self._stream_state.connector_names = names
        #: 대시보드 렌더링(_named)도 같은 사전을 쓴다.
        self._conn_names = names

    def _rename_connector(self, key: str, value: str) -> None:
        """`*.name` 이 보드에 **받아들여진** 순간 스트림 트리·대시보드의
        이름을 바로 바꾼다 (사용자 요청 2026-08-22).

        🔴 카탈로그 재로드를 기다리지 않는다 — 카탈로그는 다음 재접속
           때나 다시 오므로, 그때까지 설정 탭과 스트림 트리가 서로 다른
           이름을 말하게 된다. 사전을 새로 만들어 **교체**한다(제자리
           수정 금지) — 스트림 쪽이 사전 동일성으로 갱신을 알아챌 수
           있어야 하고, 뷰가 그리는 도중의 반쪽 상태도 없어야 한다.
        """
        m = re.fullmatch(r"(ain|i2c|din)(\d+)\.name", key)
        if not m:
            return
        n = int(m.group(2))
        connector = 3 + n if m.group(1) == "ain" else n
        names = dict(self._conn_names)
        value = value.strip()
        if value:
            names[connector] = value
        else:
            names.pop(connector, None)      # 비우면 J 번호로 돌아간다
        self._stream_state.connector_names = names
        self._conn_names = names

    def eventFilter(self, obj, event):  # noqa: N802 (Qt 서명)
        from PyQt6.QtCore import QEvent
        if (obj is self._pages_scroll.viewport()
                and event.type() == QEvent.Type.Resize):
            self._dashboard.set_available_width(event.size().width())
        return super().eventFilter(obj, event)

    def _named(self, state: ScreenState) -> ScreenState:
        """카드 이름표에 사용자가 붙인 이름을 앞세운 사본을 돌려준다.

        🔴 상태를 만드는 쪽(build_screen)이 아니라 **그리기 직전**에 바꾼다.
           내부에서는 "J3" 이 채널·센서를 잇는 키라서, 만드는 쪽을 바꾸면
           레코드 매칭까지 이름을 따라가야 한다. 화면 문구만 바꾸는 일이다.
           스트림 칩과 같은 꼴("유압 (J3)") — J 번호를 남기는 이유도 같다:
           배선을 만질 때는 결국 보드 실크를 찾는다.
        """
        names = getattr(self, "_conn_names", None)
        if not names:
            return state
        def lab(cstr: str) -> str:
            if not cstr.startswith("J"):
                return cstr
            try:
                n = int(cstr[1:])
            except ValueError:
                return cstr
            name = names.get(n)
            return f"{name} (J{n})" if name else cstr
        return replace(
            state,
            channels=tuple(replace(c, connector=lab(c.connector))
                           for c in state.channels),
            sensors=tuple(replace(x, connector=lab(x.connector))
                          for x in state.sensors),
            dins=tuple(replace(d, connector=lab(d.connector))
                       for d in state.dins),
        )

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
            view.render(self._named(self._state))

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
            if tag == "link:baud":
                self._on_baud_result(res)
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
                # 사본 갱신 (결정 3) — 수락된 값만 그 자리에서.
                accepted = self._settings.value_of(key)
                if accepted is not None and hasattr(self, "_snapshot_items"):
                    self._snapshot_items[key] = accepted
                    self._save_snapshot()
                # 🔴 이름은 수락 즉시 스트림 트리·대시보드에 반영한다
                #    (사용자 요청 2026-08-22 — "설정에서 바꾸면 스트림
                #    트리에도 바로"). 다음 카탈로그 로드를 기다리면
                #    그때까지 두 화면이 다른 이름을 말한다.
                if key.endswith(".name"):
                    value = self._settings.value_of(key)
                    if value is not None:
                        self._rename_connector(key, value)
            else:
                self._settings.on_rejected(key, res.reason or "거부됨")

    def _on_baud_result(self, res) -> None:
        """링크 속도 절차의 결말을 화면에 붙인다 (규격 §4.2).

        🔴 **실패했을 때 무슨 일이 있었는지 말한다.** "실패" 세 글자로 끝내면
           사용자가 알 수 있는 것이 없다 — 기다리면 되는지, 다른 값을 골라야
           하는지, 보드 전원을 손봐야 하는지가 전부 다른 대응이다.
        """
        payload = res.payload if isinstance(res.payload, dict) else {}
        outcome = _BaudOutcome(
            ok=bool(payload.get("ok", res.ok)),
            baud=int(payload.get("baud", self._baud)),
            stage=str(payload.get("stage", "confirm")),
            reason=str(payload.get("reason") or res.reason or ""),
            error=str(payload.get("error") or res.error or ""),
            recovered=bool(payload.get("recovered", False)),
        )
        message, bad = outcome_text(outcome)
        hint = failure_hint(outcome)
        if hint:
            message = f"{message} · {hint}"

        # 🔴 호스트가 실제로 쓰는 속도를 화면 전체에 알린다. 대역폭 여유
        #    표시(설정 화면의 필드 카드)가 이 값으로 계산되므로, 안 고치면
        #    바꾼 뒤에도 옛 속도 기준으로 "몇 %" 를 말한다.
        self._baud = outcome.baud
        self._settings.on_baud_changed(outcome.baud, message, bad)
        self._top.set_link(message, bad=bad)

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

    def _on_baud_change(self, baud: int) -> None:
        """링크 속도 변경 절차를 워커에 맡긴다 (규격 §4.2).

        🔴 **태그를 고정으로 준다.** 절차 하나가 십수 초까지 걸리므로 그동안
           사용자가 다시 누를 수 있는데, 두 절차가 겹치면 무엇으로 되돌아가야
           하는지가 흐려진다. 같은 태그면 아직 안 보낸 것이 덮어써진다
           (command_queue 의 판단 기준: "마지막 것만 반영되면 되는가?" — 링크
           속도는 그렇다. 중간값을 거쳐 갈 이유가 없다).
        """
        self._queue.submit("BAUD", "CHANGE", str(baud), tag="link:baud")

    def closeEvent(self, event) -> None:      # noqa: N802
        self._worker.stop()
        super().closeEvent(event)


def make_service(port: str, baud: int):
    """실물 시리얼에 붙은 `BoardService` 를 만든다.

    CLI 와 같은 함수를 쓴다 — 두 곳에서 따로 만들면 시계 기준이 갈린다.
    `sim` 은 CLI 쪽에서 "없어졌다" 로 거절된다.
    """
    from tools.cli.markon_cli import make_service as cli_make_service

    return cli_make_service(port, baud)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="markon-gui")
    # 🔴 --port 는 이제 **선택**이다 (사용자 결정 2026-08-20). 없으면
    #    미연결로 뜨고, 화면의 포트 선택에서 골라 [연결] 한다.
    #    주면 예전처럼 바로 연결한다(스크립트·단골 용도).
    parser.add_argument("--port", default=None,
                        help="시리얼 포트 (예: COM23). 없으면 화면에서 고른다")
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUD)
    args = parser.parse_args(argv)

    app = QApplication(sys.argv[:1])
    app.setStyleSheet(stylesheet())

    service = make_service(args.port, args.baud) if args.port else None
    window = MainWindow(service, args.port or "미연결", baud=args.baud)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
