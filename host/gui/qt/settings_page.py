"""설정 화면 — 그룹 탭과 화면 전체의 배선.

🔴 항목을 하드코딩하지 않는다. `$CFG,LIST` 응답만으로 그린다 — 어떤 항목이
   있는지, 무슨 위젯인지, 범위가 얼마인지 전부 보드가 알려 준다.

🔴 칸을 그리는 일은 여기서 하지 않는다. `qt/cells.py`(보통 줄·물리량 범위) ·
   `qt/matrix_card.py`(반복 그룹을 접은 표) · `qt/field_mask.py`(전송 필드)가
   각자 맡고, 이 파일은 **무엇을 어디에 놓을지**만 정한다.

   예전에는 넷이 한 파일에 있어 984 줄이었다. 서로를 모르고 `Row` 만 아는
   구조였으니 이미 분리돼 있었고, 파일만 안 나뉜 상태였다.
"""
from __future__ import annotations

from PyQt6.QtCore import QPoint, QRect, QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from host.core.limits import DEFAULT_BAUD, LINK_BAUD_KEY
from host.gui.link_baud import confirm_text, is_link_baud
from host.gui.qt.cell import Cell
from host.gui.qt.cells import RangeFields, RowWidget
from host.gui.link_usage import (
    USAGE_GROUPS,
    compute_usage,
    link_baud,
    record_budget,
    record_line,
)
from host.gui.qt.field_mask import FieldMaskCard
from host.gui.qt.link_usage_card import LinkUsageCard
from host.gui.qt.matrix_card import MatrixCard
from host.gui.qt.parts import card_title, hairline
from host.gui.qt.tare_panel import TarePanel
from host.gui.settings_form import (
    COL_ZERO,
    FIELD_MASK_KEYS,
    Row,
    SettingsForm,
    group_label,
    matrix_of,
)
from host.gui.tare import tare_rows
from host.gui.theme import Color, Space

#: 폼 한 줄이 차지할 최대 폭. 🔴 예전에는 사유 칸이 stretch 라 창을 넓힐수록
#: 오른쪽이 비었다 — 1130 px 캔버스에서 700 px 가 빈 채였다.
FORM_WIDTH = 620

#: 탭 차례에서 앞자리를 받는 그룹들. 🔴 **여기 없는 그룹은 카탈로그가 준
#: 순서 그대로 뒤에 붙는다**(`ordered_groups`) — 보드가 그룹을 늘리거나
#: 이름을 바꿔도 화면은 그것을 잃지 않는다. 순서를 이름으로 박아 두는 것과
#: 다르다: 여기 적힌 것은 "사람이 자주 여는 곳" 이라는 사용 빈도이지
#: 카탈로그의 구조가 아니다.
#:
#: 🔴 카탈로그 순서를 그대로 따르면 `ain`·`i2c` 가 맨 끝이었다 — 가장 많이
#:    쓰는 둘이 화면 밖으로 밀려나 "설정이 없어졌다" 는 말이 나왔다.
TAB_PRIORITY: tuple[str, ...] = ("ain", "i2c")


def ordered_groups(groups: list):
    """탭에 늘어놓을 차례. 알려진 것이 앞, 나머지는 카탈로그 순서 그대로.

    🔴 `sorted` 는 안정 정렬이라 같은 등급 안에서는 들어온 순서가 그대로
       남는다 — 모르는 그룹의 상대 순서를 화면이 지어내지 않는다.
    """
    rank = {name: i for i, name in enumerate(TAB_PRIORITY)}
    return sorted(groups, key=lambda g: rank.get(g.name, len(rank)))


class FlowLayout(QLayout):
    """줄이 넘치면 다음 줄로 넘기는 배치. 탭 이름표 줄에 쓴다.

    🔴 **왜 필요한가.** 예전에는 탭이 `QHBoxLayout` 한 줄이었다. 그룹이
       열한 개로 늘자 창 너비를 넘었고, Qt 는 버튼을 최소 폭(약 50 px)까지
       눌러 이름표를 `아날...` 로 잘랐다. 뒤쪽이던 `i2c`·`ain` 이 그렇게
       읽을 수 없게 됐고, 사용자에게는 **없어진 것**으로 보였다.

    🔴 가로 스크롤이 아니라 줄바꿈을 고른 이유: 스크롤은 "닿을 수는 있다"
       일 뿐이고, 사용자가 스크롤 막대를 발견해야 한다. 설정 탭은 **한눈에
       다 보여야** 무엇을 고를 수 있는지 알 수 있는 물건이다. 줄바꿈은
       그룹이 몇 개로 늘어도 최소 폭이 버튼 하나 폭에 묶여 있어(아래
       `minimumSize`) 화면이 창보다 넓어지는 일이 다시 생기지 않는다.

    Qt 예제의 FlowLayout 과 같은 구조다. `heightForWidth` 로 필요한 줄 수를
    알려 주므로 바깥 세로 배치가 자리를 알맞게 내어 준다.
    """

    def __init__(self, parent: QWidget | None = None, *,
                 spacing: int = Space.XS) -> None:
        super().__init__(parent)
        self._items: list = []
        self._space = spacing
        self.setContentsMargins(0, 0, 0, 0)

    # ---- QLayout 계약 ------------------------------------------------
    def addItem(self, item) -> None:          # noqa: N802 (Qt 이름)
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, i: int):                 # noqa: N802
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i: int):                 # noqa: N802
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):            # noqa: N802
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:      # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:   # noqa: N802
        return self._arrange(QRect(0, 0, width, 0), place=False)

    def setGeometry(self, rect) -> None:      # noqa: N802
        super().setGeometry(rect)
        self._arrange(rect, place=True)

    def sizeHint(self) -> QSize:              # noqa: N802
        return self.minimumSize()

    def minimumSize(self) -> QSize:           # noqa: N802
        """🔴 가장 넓은 버튼 하나면 된다 — 개수와 무관하다. 이 한 줄이
        "그룹이 늘어도 설정 화면이 창보다 넓어지지 않는다" 를 보장한다."""
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        return size

    def _arrange(self, rect: QRect, *, place: bool) -> int:
        """왼쪽에서 채우다 폭을 넘으면 다음 줄로. 필요한 높이를 돌려준다."""
        x, y, line_h = rect.x(), rect.y(), 0
        for item in self._items:
            hint = item.sizeHint()
            nxt = x + hint.width() + self._space
            if nxt - self._space > rect.right() + 1 and line_h > 0:
                x = rect.x()
                y += line_h + self._space
                nxt = x + hint.width() + self._space
                line_h = 0
            if place:
                # 🔴 `sizeHint` 그대로 놓는다 — 눌러 담지 않는다. 눌리는
                #    순간 이름표가 잘리고, 그것이 이 배치가 생긴 이유다.
                item.setGeometry(QRect(QPoint(x, y), hint))
            x = nxt
            line_h = max(line_h, hint.height())
        return y + line_h - rect.y()


class SettingsPage(QWidget):
    """카탈로그로 그린 설정 화면."""

    #: 보드에 보낼 (키, 값) 목록
    apply_requested = pyqtSignal(list)
    #: $CFG,SAVE — Flash 에 남긴다
    save_requested = pyqtSignal()
    #: $CFG,RESET — 전부 기본값으로
    reset_requested = pyqtSignal()
    #: 🔴 링크 속도만 따로 나간다 (규격 §4.2).
    #:
    #:    `$CFG,SET` 하나로 끝나는 일이 아니다 — 보내고, 포트를 새 속도로
    #:    다시 열고, 확인을 보내고, 안 되면 옛 속도로 돌아와야 한다. 그
    #:    절차를 `apply_requested` 에 섞으면 **다른 설정 몇 개와 함께 큐에
    #:    들어가** 순서가 뒤엉킨다 — 링크가 바뀌는 중에 뒤따라 나가는
    #:    `$CFG,SET` 은 전부 허공으로 간다.
    baud_change_requested = pyqtSignal(int)

    def __init__(self, parent: QWidget | None = None, *,
                 baud: int = DEFAULT_BAUD) -> None:
        super().__init__(parent)
        self._form: SettingsForm | None = None
        self._baud = baud
        self._rows: dict[str, Cell] = {}
        #: 보드에 적용됐지만 아직 Flash 에 저장되지 않은 변경이 있는가.
        #: 🔴 보드의 dirty 플래그와 별개로 호스트가 자기가 보낸 것을 센다 —
        #:    보드는 그것을 알려 주지 않고, 물어볼 명령도 없다.
        self._unsaved = False
        #: 채널 번호 → 지금 들어오는 전류(mA). 영점 패널이 쓴다.
        #: 🔴 설정 화면이 텔레메트리를 직접 듣지 않는다. app 이 넣어 준다 —
        #:    뷰끼리 값을 주고받기 시작하면 배치를 바꿀 때마다 배선이 딸려
        #:    온다(app.py `_render` 머리말).
        self._live_ma: dict[int, float | None] = {}
        self._tare: TarePanel | None = None
        #: 채널·포트 표 위에 붙은 링크 사용량 요약들.
        #: 🔴 탭마다 하나씩이다 — 위젯 하나를 여러 부모에 붙일 수 없고,
        #:    무엇보다 **켜는 자리에서 보여야** 하기 때문이다. 값은 전부
        #:    같은 계산(`compute_usage`)에서 나온다.
        self._usage_cards: list[LinkUsageCard] = []
        #: 시험에서 확인 대화상자를 건너뛰기 위한 고리.
        self._confirm = self._ask_confirm

        # 🔴 그룹마다 자기 화면을 준다. 45 개 항목을 한 두루마리에 이어
        #    붙이면 무엇이 어디 있는지 알 수 없고, 스크롤 위치가 곧 문맥이
        #    되어 버린다. 탭으로 나누면 한 번에 한 가지만 본다.
        #
        # 🔴 한 줄(`QHBoxLayout`)이 아니라 **줄바꿈**이다. 그룹이 열한 개로
        #    늘자 한 줄에 안 들어가 뒤쪽(`i2c`·`ain`)이 눌려 이름표가 잘렸고,
        #    사용자는 그것을 "설정이 없어졌다" 로 읽었다. 근거는 `FlowLayout`
        #    머리말에 있다.
        self._tab_host = QWidget()
        self._tabs = FlowLayout(self._tab_host)
        policy = self._tab_host.sizePolicy()
        # 🔴 없으면 바깥 세로 배치가 줄바꿈으로 늘어난 높이를 안 내어 줘
        #    두 번째 줄부터 잘린다.
        policy.setHeightForWidth(True)
        policy.setVerticalPolicy(QSizePolicy.Policy.Minimum)
        self._tab_host.setSizePolicy(policy)
        self._tab_buttons: list[QPushButton] = []

        self._pages = QStackedWidget()

        self._status = QLabel("보드에 연결되면 설정을 불러온다.")
        self._status.setObjectName("dim")
        # 🔴 이 라벨의 글 길이가 창 최소폭이 되면 안 된다 — 상단 바의
        #    _ident 와 같은 이유(2026-08-20). 좁으면 뒤가 잘린다.
        self._status.setSizePolicy(QSizePolicy.Policy.Ignored,
                                   QSizePolicy.Policy.Preferred)

        self._apply = QPushButton("보드에 적용")
        self._apply.setEnabled(False)
        self._apply.clicked.connect(self._on_apply)

        self._revert = QPushButton("되돌리기")
        self._revert.setEnabled(False)
        self._revert.clicked.connect(self._on_revert)

        # 🔴 적용과 저장은 다른 사실이다.
        #
        #    적용은 보드의 RAM 에 들어간 것이고, 저장은 Flash 에 남은 것이다.
        #    적용만 하고 전원을 끄면 사라진다. 화면이 둘을 같은 것처럼 보이면
        #    사용자는 설정이 남은 줄 알고 보드를 떼어 간다.
        #
        #    그래서 저장만 채운 버튼이다 — 이 화면의 주 동작이다.
        self._save = QPushButton("보드에 저장")
        self._save.setObjectName("primary")
        self._save.setEnabled(False)
        self._save.clicked.connect(lambda: self.save_requested.emit())

        # 🔴 되돌릴 수 없는 동작이라 나머지와 떼어 놓는다. 손이 미끄러져
        #    옆 버튼을 누르는 일이 생기면 안 되는 쪽이다.
        self._reset = QPushButton("기본값으로")
        self._reset.setObjectName("danger")
        self._reset.setEnabled(False)
        self._reset.clicked.connect(self._on_reset)

        bar = QHBoxLayout()
        bar.setSpacing(Space.SM)
        bar.addWidget(self._reset)
        bar.addSpacing(Space.XL)
        bar.addWidget(self._status, 1)
        bar.addWidget(self._revert)
        bar.addWidget(self._apply)
        bar.addWidget(self._save)

        col = QVBoxLayout(self)
        col.setContentsMargins(Space.LG, Space.MD, Space.LG, Space.LG)
        col.setSpacing(Space.SM)
        col.addWidget(self._tab_host)
        col.addWidget(hairline())
        col.addWidget(self._pages, 1)
        col.addLayout(bar)

    # ------------------------------------------------------------- 구성

    @property
    def form(self):
        """지금 그리고 있는 폼. `None` 이면 아직 카탈로그를 못 받았다.

        🔴 공개한다. 예전에는 바깥에서 `getattr(page, "_form", None)` 으로
           private 를 뒤졌다 — 그런 접근은 리팩터링을 조용히 깨뜨린다.
        """
        return getattr(self, "_form", None)

    def set_form(self, form: SettingsForm) -> None:
        """카탈로그가 도착했다. 화면을 다시 그린다."""
        keep = self._pages.currentIndex()
        self._form = form
        self._rows.clear()
        self._usage_cards.clear()
        _clear(self._tabs)
        self._tab_buttons.clear()
        while self._pages.count():
            widget = self._pages.widget(0)
            self._pages.removeWidget(widget)
            widget.deleteLater()

        # 🔴 탭과 화면을 **같은 순서**로 만든다. 두 반복문으로 나누면
        #    언젠가 한쪽만 정렬돼 탭을 눌렀을 때 엉뚱한 화면이 열린다.
        for group in ordered_groups(form.groups):
            self._pages.addWidget(self._page_for(group))
            self._tabs.addWidget(self._tab_button(group_label(group.name)))

        # 되돌리기·초기화로 다시 그릴 때 보던 탭에 머문다 — 화면이 제멋대로
        # 첫 탭으로 튀면 방금 무엇을 고쳤는지 잃어버린다.
        self.select_tab(keep if 0 <= keep < self._pages.count() else 0)
        self._refresh_buttons()

    def _page_for(self, group) -> QWidget:
        """그룹 하나가 차지하는 화면. 카드가 하나일 수도 여럿일 수도 있다."""
        body = QVBoxLayout()
        body.setContentsMargins(0, 0, Space.SM, 0)
        body.setSpacing(Space.MD)

        matrix = matrix_of(group)
        if matrix is not None:
            # 🔴 접히지 않은 항목이 먼저다. LED 의 `체인 LED 수` 처럼 표
            #    **전체**에 걸리는 값이라, 표 아래에 두면 개별 줄의 설정으로
            #    읽힌다.
            for card in self._form_cards(matrix.leftovers):
                body.addWidget(card)
            # 🔴 표 **위**에 놓는다. 사용자가 켜고 끄는 것은 이 표이고,
            #    스크롤을 내리기 전에 이미 보여야 "켜면 얼마가 되는가" 를
            #    켜기 전에 안다. I2C 표는 여섯 포트 × 다섯 열이라 아래에
            #    두면 화면 밖으로 나간다.
            if group.name in USAGE_GROUPS:
                usage = LinkUsageCard()
                self._usage_cards.append(usage)
                body.addWidget(usage)
            body.addWidget(MatrixCard(
                "", matrix,
                lambda r: self._make_cell(r, compact=True),
                self._make_range))
            # 🔴 영점 패널은 범위 칸이 있는 그룹에만 붙는다. 그룹 이름을
            #    보고 정하지 않는다 — 카탈로그가 채널을 늘리거나 그룹 이름을
            #    바꿔도 따라온다.
            if COL_ZERO in matrix.columns:
                self._tare = TarePanel()
                self._tare.tare_requested.connect(self._on_tare)
                body.addWidget(self._tare)
                self._refresh_tare()
        else:
            for card in self._form_cards(group.rows):
                body.addWidget(card)
        body.addStretch(1)

        inner = QWidget()
        inner.setLayout(body)
        scroll = QScrollArea()
        scroll.setWidget(inner)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        return scroll

    def _form_cards(self, rows) -> list[QFrame]:
        """보통 항목들은 카드 한 장. 🔴 필드 마스크 셋(ain·i2c·din)은 따로
        뗀다 — 체크박스 열 개와 미리보기가 붙어서 한 줄에 담기지 않는다.

        🔴 [개정, 2026-08-19] 마스크 카드가 하나에서 셋으로 늘었다 —
           `tx.fields` 가 `tx.fields_ain`·`tx.fields_i2c`·`tx.fields_din`
           으로 나뉘었기 때문이다(규격 §7.2·§7.5·§7.6). `FIELD_MASK_KEYS`
           순서(ain·i2c·din)대로 그린다."""
        cards: list[QFrame] = []
        mask_keys = set(FIELD_MASK_KEYS.values())
        plain = [r for r in rows if r.key not in mask_keys]
        masks = {r.key: r for r in rows if r.key in mask_keys}

        if plain:
            card = QFrame()
            card.setObjectName("card")
            lay = QVBoxLayout(card)
            lay.setContentsMargins(Space.MD, Space.MD, Space.MD, Space.MD)
            lay.setSpacing(Space.SM)
            # 제목 없음 — 탭이 그룹 이름을 말한다. 마스크 카드만 자기
            # 제목을 갖는다(같은 탭 안의 다른 카드들이라 구분이 필요하다).
            for row in plain:
                w = self._make_cell(row)
                # 🔴 한 줄의 폭을 묶는다. 창이 넓어져도 라벨에서 입력까지의
                #    거리가 늘어나지 않아야 눈이 따라간다.
                w.setMaximumWidth(FORM_WIDTH)
                lay.addWidget(w)
            cards.append(card)

        if self._form is not None:
            for kind, key in FIELD_MASK_KEYS.items():
                row = masks.get(key)
                if row is None:
                    continue
                card = FieldMaskCard(row, self._form.fields,
                                     lambda k=kind: self._preview(k),
                                     record_kind=kind)
                card.changed.connect(self._on_changed)
                self._rows[row.key] = card
                cards.append(card)
        return cards

    def _make_range(self, zero_row: Row, scale_row: Row) -> RangeFields:
        """🔴 한 위젯이 두 키를 맡는다. 거부·오류 표시가 어느 쪽으로 와도
        같은 자리에 뜬다 — 사용자는 `zero` 를 고친 적이 없기 때문이다."""
        w = RangeFields(zero_row, scale_row)
        w.changed.connect(self._on_changed)
        self._rows[zero_row.key] = w
        self._rows[scale_row.key] = w
        return w

    def _preview(self, kind: str):
        """마스크 카드가 그릴 (나갈 줄, 예산).

        🔴 요약 표와 **같은 함수**를 지난다(`host/gui/link_usage.py`). 두
           화면이 각자 재면 언젠가 한쪽만 고쳐지고, 그때 사용자는 어느 쪽을
           믿어야 할지 알 수 없다.
        """
        form = self._form
        return (record_line(form, kind),
                record_budget(form, kind, link_baud(form, self._baud)))

    def _refresh_usage(self) -> None:
        """켜고 끈 결과를 즉시 반영한다. 🔴 어떤 항목이 바뀌어도 다시 잰다 —
        채널·포트·주기·필드·링크 속도가 전부 같은 수에 곱해진다."""
        if self._form is None:
            return
        usage = compute_usage(self._form, self._baud)
        for card in self._usage_cards:
            card.show_usage(usage)

    def _make_cell(self, row: Row, *, compact: bool = False) -> RowWidget:
        """🔴 표 안이든 폼이든 **같은 위젯**을 쓴다.

        따로 만들면 검증·오류 표시·dirty 표시가 두 벌이 되고, 언젠가
        한쪽만 고쳐진다. `self._rows` 도 키 하나에 위젯 하나로 유지된다.
        """
        w = RowWidget(row, compact=compact)
        w.changed.connect(self._on_changed)
        self._rows[row.key] = w
        return w

    def _tab_button(self, title: str) -> QPushButton:
        index = len(self._tab_buttons)
        button = QPushButton(title)
        button.setObjectName("tab")
        button.setCheckable(True)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(lambda _=False, i=index: self.select_tab(i))
        self._tab_buttons.append(button)
        return button

    def select_tab(self, index: int) -> None:
        if not (0 <= index < self._pages.count()):
            return
        self._pages.setCurrentIndex(index)
        for i, button in enumerate(self._tab_buttons):
            button.setChecked(i == index)

    # ------------------------------------------------------------- 영점

    def set_live_ma(self, live_ma: dict[int, float | None]) -> None:
        """지금 들어오는 채널별 전류를 받아 영점 패널을 갱신한다.

        🔴 값이 바뀌지 않았으면 다시 그리지 않는다. 텔레메트리는 초당 열 번
           오는데 그때마다 위젯을 만지면 `잡기` 버튼을 누르는 순간 밑에서
           갱신이 일어나 클릭이 씹힌다.
        """
        if live_ma == self._live_ma:
            return
        self._live_ma = dict(live_ma)
        self._refresh_tare()

    def _refresh_tare(self) -> None:
        if self._tare is None or self._form is None:
            return
        self._tare.show_rows(tare_rows(self._form, self._live_ma))

    def _on_tare(self, key: str, text: str) -> None:
        """🔴 보통 편집과 같은 길로 보낸다. 영점만 따로 보드에 바로 쏘면
           `적용` 을 안 눌러도 값이 나가는 항목이 하나 생기고, 화면의
           변경 표시와 실제 보드 상태가 어긋난다."""
        self.set_value(key, text)
        self._redraw_range(key)
        self._refresh_tare()

    def _redraw_range(self, zero_key: str) -> None:
        """범위 칸을 다시 그린다.

        🔴 `set_value` 만으로는 안 된다. 범위 칸은 영점·스케일 **둘**로
           그려지는 한 위젯이라 값 하나를 밀어 넣는 통로가 없고, 실제로
           `RangeFields.set_value` 는 일부러 아무 일도 하지 않는다.

           그냥 두면 폼에는 새 영점이 들어갔는데 화면의 범위는 옛 숫자를
           계속 보여 준다 — 잡기 버튼이 먹지 않은 것처럼 보이고, 그 상태로
           `적용` 을 누르면 보드만 조용히 바뀐다.
        """
        widget = self._rows.get(zero_key)
        if not isinstance(widget, RangeFields) or self._form is None:
            return
        scale_key = f"{zero_key.rsplit('.', 1)[0]}.scale"
        try:
            widget.show_settings(self._form.row(zero_key).value,
                                 self._form.row(scale_key).value)
        except KeyError:
            return

    # ------------------------------------------------------------- 편집

    def set_value(self, key: str, text: str) -> None:
        """대시보드 쪽에서 값을 바꿨다 (전원 레일).

        🔴 폼과 위젯을 **함께** 맞춘다. `screen.py` 의 `_rail_values()` 가
           설정 폼을 유일한 출처로 읽으므로, 폼만 고치고 위젯을 두면 두
           화면이 서로 다른 말을 한다.
        """
        if self._form is None or key not in self._rows:
            return
        self._form.edit(key, text)
        self._rows[key].set_value(text)
        self._rows[key].mark_dirty(self._form.is_dirty(key))
        self._refresh_buttons()

    def _on_changed(self, key: str, text: str) -> None:
        if self._form is None:
            return
        self._form.edit(key, text)

        w = self._rows[key]
        message = self._form.validate(key)
        if message:
            w.show_error(message)
        else:
            w.clear_error()
            w.mark_dirty(self._form.is_dirty(key))
        self._refresh_buttons()

    def _refresh_buttons(self) -> None:
        if self._form is None:
            return
        pending = self._form.pending_changes()
        errors = self._form.errors()
        self._apply.setEnabled(bool(pending))
        self._revert.setEnabled(self._form.has_changes)
        self._save.setEnabled(self._unsaved)
        self._reset.setEnabled(True)

        # 🔴 대역폭은 필드만으로 정해지지 않는다 — 채널 수와 주기가 함께
        #    곱해진다. 그래서 **아무 항목이 바뀌어도** 다시 잰다. 필드를
        #    켤 때만 갱신하면, 채널을 하나 더 켜 놓고 필드 화면으로 왔을 때
        #    화면이 옛날 숫자를 말한다.
        #
        #    🔴 [개정, 2026-08-19] 카드가 셋(ain·i2c·din)이라 전부 갱신한다.
        for key in FIELD_MASK_KEYS.values():
            mask_card = self._rows.get(key)
            if isinstance(mask_card, FieldMaskCard):
                mask_card.refresh()
        # 🔴 채널·포트 표 옆의 요약도 같은 순간에 갱신한다. 한쪽만 다시
        #    그리면 두 화면이 서로 다른 순간의 숫자를 말한다.
        self._refresh_usage()

        if errors:
            self._status.setText(f"고칠 것 {len(errors)}개 — 보낼 수 없다")
            self._status.setStyleSheet(f"color: {Color.FAULT};")
        elif pending:
            self._status.setText(f"보내지 않은 변경 {len(pending)}개")
            self._status.setStyleSheet("")
        elif self._unsaved:
            # 🔴 이 문구가 이 화면에서 가장 중요하다. 적용은 RAM 이고
            #    저장은 Flash 다. 여기서 그만두고 보드를 떼어 가면 설정이
            #    사라지는데, 화면이 말해 주지 않으면 알 길이 없다.
            self._status.setText("보드에 적용됐지만 저장되지 않았다 — "
                                 "전원을 끄면 사라진다")
            self._status.setStyleSheet(f"color: {Color.WARN};")
        else:
            self._status.setText("바뀐 것이 없다")
            self._status.setStyleSheet("")

    def _on_apply(self) -> None:
        """🔴 링크 속도는 나머지와 **갈라서** 보낸다 (규격 §4.2).

        같이 보내면 두 가지가 깨진다. 하나는 순서 — 링크가 바뀌는 중에
        뒤따라 나가는 `$CFG,SET` 은 옛 속도로 허공에 나간다. 다른 하나는
        확인 — 이것만은 사람이 "그래도 하겠다" 고 말해야 나가야 한다.
        """
        if self._form is None:
            return
        pending = self._form.pending_changes()
        rest = [(k, v) for k, v in pending if not is_link_baud(k)]
        baud = next((v for k, v in pending if is_link_baud(k)), None)

        if rest:
            self.apply_requested.emit(rest)

        if baud is None:
            return
        # 🔴 "지금 속도" 는 폼이 아니라 **호스트가 실제로 포트를 연 속도**다.
        #    카탈로그의 값은 보드가 마지막으로 말해 준 것이라, 되돌림이
        #    막 일어난 뒤에는 한 박자 낡았을 수 있다.
        try:
            old = int(self._baud)
            new = int(baud)
        except (TypeError, ValueError):
            return
        if not self._confirm(confirm_text(old, new)):
            # 되돌린다 — 안 하기로 한 값이 화면에 남아 다음 `적용` 에
            # 딸려 나가면, 사용자는 두 번째에는 묻지도 않고 바뀐 줄 안다.
            self._form.revert(LINK_BAUD_KEY)
            self.set_value(LINK_BAUD_KEY, str(old))
            return
        self.baud_change_requested.emit(new)

    def on_baud_changed(self, baud: int, message: str, bad: bool) -> None:
        """링크 속도 절차가 끝났다. 화면을 사실에 맞춘다.

        🔴 성공이든 실패든 **지금 실제로 쓰는 속도**로 되돌려 놓는다.
           실패했는데 화면에 새 값이 남아 있으면, 사용자는 바뀐 줄 알고
           다음 `적용` 을 누른다 — 그리고 그것은 아무 일도 안 한다.
        """
        self._baud = baud
        if self._form is not None and LINK_BAUD_KEY in self._rows:
            self._form.revert(LINK_BAUD_KEY)
            self._form.accept(LINK_BAUD_KEY)
            self.set_value(LINK_BAUD_KEY, str(baud))
            self._rows[LINK_BAUD_KEY].mark_dirty(False)
            if bad:
                self._rows[LINK_BAUD_KEY].show_error(message)
            else:
                self._rows[LINK_BAUD_KEY].clear_error()
                # 확정됐어도 Flash 에는 아직 없다 — 저장해야 남는다.
                self._unsaved = True
        # 🔴 상태 문구는 `_refresh_buttons()` **뒤에** 쓴다. 저쪽이 자기
        #    문구("바뀐 것이 없다" 등)로 덮어쓰기 때문이다 — 순서를 뒤집으면
        #    링크가 끊겼다 돌아온 사실이 화면에서 통째로 사라진다.
        self._refresh_buttons()
        self._status.setText(message)
        self._status.setStyleSheet(
            f"color: {Color.FAULT};" if bad else f"color: {Color.WARN};")

    def _on_revert(self) -> None:
        if self._form is None:
            return
        self._form.revert_all()
        unsaved = self._unsaved
        self.set_form(self._form)
        # 🔴 되돌리기는 **화면의 편집**을 되돌린다. 이미 보드에 보낸 것은
        #    되돌리지 않으므로 저장 여부도 그대로다. 여기서 지우면 사용자가
        #    저장하지 않은 채 화면을 떠난다.
        self._unsaved = unsaved
        self._refresh_buttons()

    def _ask_confirm(self, text: str) -> bool:
        from PyQt6.QtWidgets import QMessageBox

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("확인")
        box.setText(text)
        box.setStandardButtons(QMessageBox.StandardButton.Yes
                               | QMessageBox.StandardButton.No)
        box.setDefaultButton(QMessageBox.StandardButton.No)
        return box.exec() == QMessageBox.StandardButton.Yes

    def _on_reset(self) -> None:
        """🔴 되돌릴 수 없는 동작이라 먼저 묻는다.

        전 항목이 기본값으로 간다. 채널 영점·스케일처럼 손으로 맞춘 값이
        섞여 있고, 그것을 다시 만들려면 센서를 다시 재야 한다.
        """
        if not self._confirm("설정을 전부 기본값으로 되돌린다.\n"
                             "채널 영점·스케일처럼 손으로 맞춘 값도 사라진다.\n\n"
                             "계속할까?"):
            return
        self.reset_requested.emit()

    # ------------------------------------------------------------- 저장 상태

    def mark_unsaved(self) -> None:
        """보드에 무언가를 적용했다 — 아직 Flash 에는 없다."""
        self._unsaved = True
        self._refresh_buttons()

    def mark_saved(self) -> None:
        """$CFG,SAVE 가 성공했다."""
        self._unsaved = False
        self._refresh_buttons()

    @property
    def has_unsaved(self) -> bool:
        return self._unsaved

    # ------------------------------------------------------------- 응답

    def value_of(self, key: str) -> str | None:
        """항목의 현재 화면 값. 없으면 None — app 이 `*.name` 수락 순간
        스트림 트리 이름을 갱신할 때 쓴다(사용자 요청 2026-08-22)."""
        if self._form is None:
            return None
        try:
            return str(self._form.row(key).value)
        except KeyError:
            return None

    def on_accepted(self, key: str) -> None:
        if self._form is None:
            return
        self._form.accept(key)
        if key in self._rows:
            self._rows[key].mark_dirty(False)
            self._rows[key].clear_error()
        # 보드의 RAM 에 들어갔을 뿐이다. Flash 는 아직이다.
        self._unsaved = True
        self._refresh_buttons()

    def on_reset_done(self) -> None:
        """보드가 $CFG,RESET 을 받아들였다.

        보드의 값이 전부 바뀌었으므로 화면도 다시 읽어야 한다. 여기서는
        저장이 필요하다는 것만 세우고, 카탈로그 재요청은 호출 쪽이 한다.
        """
        self._unsaved = True
        self._refresh_buttons()

    def on_rejected(self, key: str, reason: str) -> None:
        """🔴 값을 되돌리지 않는다. 사용자가 방금 친 것을 지우면 무엇을
        고치려 했는지 사라진다. 사유를 보여 주고 고칠 기회를 준다."""
        if key in self._rows:
            self._rows[key].show_error(f"보드가 거부: {reason}")
        self._refresh_buttons()


def _clear(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().deleteLater()
