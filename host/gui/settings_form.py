"""설정 화면의 로직. Qt 를 import 하지 않는다.

🔴 항목을 하드코딩하지 않는다
=============================

화면은 `$CFG,LIST` 응답만으로 만들어진다. 어떤 항목이 있는지, 무슨 타입인지,
범위가 얼마인지 전부 보드가 알려 준다.

이유는 펌웨어를 단계로 올리기 때문이다. 2단계에서 설정 항목이 늘고 3단계에서
또 는다. 호스트에 목록을 박아 두면 그때마다 GUI 를 다시 배포해야 하고,
보드마다 펌웨어 버전이 다르면 맞출 방법이 없다.

여기서 하는 일
-------------
    항목을 그룹으로 묶고 순서를 정한다
    `vtype` 으로 어떤 입력 방식인지 정한다 (체크박스·숫자·선택·문자열)
    편집 가능한지, 안 되면 왜인지 정한다
    사용자가 고친 것만 골라 보낼 목록을 만든다

무엇을 안 하나
-------------
    Qt 위젯을 만들지 않는다. 위젯은 여기서 나온 값을 보고 그리기만 한다.
    인터록을 판정하지 않는다 — 보드가 한다(규격 §5). 호스트가 흉내 내면
    두 판정이 갈리고, 갈리면 사용자는 GUI 를 믿을 수 없게 된다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from host.core.config_schema import ConfigItem, ConfigSchema
from host.core.errors import ConfigError
from host.core.limits import MAX_ARG_BYTES


class Widget(Enum):
    """이 항목을 어떤 입력으로 그릴까. `vtype` 에서 정해진다."""

    TOGGLE = "toggle"      #: bool
    NUMBER = "number"      #: u8·u16·u32·f32
    CHOICE = "choice"      #: enum
    TEXT = "text"          #: str


_WIDGET_OF = {
    "bool": Widget.TOGGLE,
    "u8": Widget.NUMBER,
    "u16": Widget.NUMBER,
    "u32": Widget.NUMBER,
    "f32": Widget.NUMBER,
    "enum": Widget.CHOICE,
    "str": Widget.TEXT,
}


@dataclass(frozen=True)
class Row:
    """화면의 한 줄. 위젯이 그리는 데 필요한 전부."""

    key: str
    label: str
    widget: Widget
    value: str
    """지금 화면에 떠 있는 값. 문자열로 들고 있는다 — 전선에 나갈 형태다."""

    default: str
    unit: str = ""
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple = ()
    choice_labels: tuple[str, ...] = ()
    """열거 항목의 이름표. 비어 있으면 화면이 값을 그대로 보여 준다."""
    editable: bool = True
    reason: str = ""
    """편집이 **안 되는** 이유. 툴팁에 그대로 띄운다."""

    note: str = ""
    """보드가 이 항목에 붙여 보낸 사유. 편집 가능해도 실린다.

    🔴 예전에는 읽기 전용일 때만 `reason` 으로 옮겨 담고 나머지는 버렸다.
       그러다 `pwr.5v` 를 끌 수 있게 하면서(사용자 확정 2026-08-14) 문제가
       드러났다 — "끄면 쿨링 팬·아날로그 수집·WS2812 가 함께 멈춘다" 는
       경고가 화면에 뜨지 않았다. 인터록을 푼 대신 남긴 유일한 안전장치가
       바로 그 문구인데, 편집 가능해졌다는 이유로 버려지고 있었다.

       보드가 사유를 붙였으면 이유가 있다. 편집 여부와 무관하게 전달한다."""

    is_integer: bool = False

    @property
    def dirty(self) -> bool:
        """화면 값이 보드 값과 다른가 — `original` 과 비교는 Form 이 한다."""
        return False   # Row 는 불변이라 Form 이 판정한다


@dataclass
class Group:
    name: str
    rows: list[Row] = field(default_factory=list)


#: 표로 접을 만한 최소 반복 수. 이보다 적으면 헤더 줄이 본문보다 길어져
#: 폼보다 읽기 나빠진다.
MATRIX_MIN_ROWS = 3


@dataclass(frozen=True)
class MatrixRow:
    """표의 한 줄 — 번호 하나가 가진 칸들. 열 순서대로 늘어놓는다."""

    index: int
    label: str
    cells: tuple[Row | None, ...]


@dataclass(frozen=True)
class Matrix:
    """반복되는 항목들을 표로 접은 것. 행 = 번호, 열 = 속성."""

    prefix: str
    columns: tuple[str, ...]
    """키의 뒷부분들 (`zero` · `scale` …). 화면은 안 쓰지만 시험이 짚는다."""

    column_labels: tuple[str, ...]
    rows: tuple[MatrixRow, ...]

    leftovers: tuple[Row, ...] = ()
    """접히지 않고 남은 항목들. 화면이 표 위에 폼으로 그린다.

    🔴 그룹에 반복이 아닌 항목이 섞여 있다고 반복분까지 못 접을 이유는
       없다. LED 그룹이 그렇다 — `체인 LED 수`·`밝기` 는 하나씩이고
       J21~J24 의 R·G·B 는 반복이다.
    """


def _split_key(key: str) -> tuple[str, int, str] | None:
    """`ain3.zero` → (`ain`, 3, `zero`). 그 꼴이 아니면 None."""
    head, _, tail = key.partition(".")
    if not tail:
        return None
    digits = len(head) - len(head.rstrip("0123456789"))
    if digits == 0 or digits == len(head):
        return None
    return head[:-digits], int(head[-digits:]), tail


def _common_prefix(texts: list[str]) -> str:
    if not texts:
        return ""
    out = texts[0]
    for other in texts[1:]:
        i = 0
        while i < len(out) and i < len(other) and out[i] == other[i]:
            i += 1
        out = out[:i]
    return out.rstrip()


def matrix_of(group: Group) -> Matrix | None:
    """그룹이 표로 접을 만한 모양인지 보고, 그렇다면 접어서 돌려준다.

    🔴 **항목을 하드코딩하지 않는다** — 이 파일의 전제다(머리말). 그래서
       "ain 그룹은 표" 라고 적지 않고, 키가 `<접두><번호>.<속성>` 꼴로
       반복되는지를 **구조로 판정**한다. 보드가 채널을 8 개로 늘리거나
       속성을 추가해도 화면이 따라간다.

    🔴 왜 접는가. 45 개 항목 중 35 개가 7 채널 × 5 속성이다. 세로로 35 줄을
       쌓으면 스크롤이 길어지는 것보다 나쁜 일이 생긴다 — "J5 의 영점이
       J6 보다 큰가" 를 볼 수 없다. 채널끼리 비교하는 것이 이 화면의 주된
       용도인데도.
    """
    # 🔴 반복되는 것만 골라 낸다. 예전에는 "하나라도 어긋나면 표가 아니다"
    #    였는데, 그러면 LED 그룹처럼 스칼라 두 개가 섞였다는 이유로 J21~J24
    #    의 R·G·B 열두 줄이 통째로 늘어선다. 섞였다고 반복을 못 접을 이유가
    #    없다 — 남는 것은 표 위에 폼으로 그린다.
    parsed: list[tuple[str, int, str, Row]] = []
    leftovers: list[Row] = []
    for row in group.rows:
        bits = _split_key(row.key)
        if bits is None:
            leftovers.append(row)
        else:
            parsed.append((*bits, row))

    prefixes = {p for p, _, _, _ in parsed}
    if len(prefixes) != 1:
        return None

    indices = sorted({i for _, i, _, _ in parsed})
    if len(indices) < MATRIX_MIN_ROWS:
        return None

    # 열 순서는 **처음 나온 순서**다. 보드가 보낸 순서에 뜻이 있다고 보고
    # 재정렬하지 않는다 (groups 프로퍼티와 같은 규칙).
    columns: list[str] = []
    for _, _, suffix, _ in parsed:
        if suffix not in columns:
            columns.append(suffix)
    if len(columns) < 2:
        return None                          # 한 열짜리 표는 폼만 못하다

    prefix = next(iter(prefixes))
    by_cell = {(i, s): r for _, i, s, r in parsed}

    rows: list[MatrixRow] = []
    for index in indices:
        labels = [r.label for (i, _s), r in by_cell.items() if i == index]
        # 🔴 라벨이 `J3 영점` · `J3 스케일` 처럼 생겼으므로, 한 번호의
        #    라벨들이 공통으로 가진 앞부분이 곧 행 이름이다. 공통 부분이
        #    없으면 키로 되돌아간다 — 빈 행 이름을 내놓지 않는다.
        label = _common_prefix(labels) or f"{prefix}{index}"
        rows.append(MatrixRow(
            index=index,
            label=label,
            cells=tuple(by_cell.get((index, s)) for s in columns),
        ))

    return Matrix(
        prefix=prefix,
        columns=tuple(columns),
        column_labels=tuple(
            _column_label(by_cell, indices, rows, columns, k)
            for k in range(len(columns))
        ),
        rows=tuple(rows),
        leftovers=tuple(leftovers),
    )


def _column_label(by_cell, indices, rows, columns, k: int) -> str:
    """열 제목 — 항목 라벨에서 행 이름을 뺀 나머지.

    🔴 행 이름이 안 붙은 라벨이면 키의 뒷부분을 쓴다. 보드가 라벨을 어떻게
       주든 화면이 빈 제목을 내놓지 않아야 한다.
    """
    suffix = columns[k]
    for row in rows:
        cell = row.cells[k]
        if cell is None:
            continue
        text = cell.label
        if row.label and text.startswith(row.label):
            text = text[len(row.label):].strip()
        if text:
            return text
    return suffix


#: 그룹 이름을 사람이 읽는 말로. 없는 그룹은 이름 그대로 쓴다 —
#: 🔴 보드가 새 그룹을 보내도 화면이 깨지지 않아야 한다.
GROUP_LABELS = {
    "dev": "장치",
    "tx": "전송",
    "pwr": "전원",
    "adc": "ADC",
    "ain": "아날로그 입력",
    "sol": "디지털 출력",
    "led": "LED",
    "i2c": "I2C 센서",
}


def group_label(name: str) -> str:
    return GROUP_LABELS.get(name, name)


#: 규격 §7.2 가 이름 지은 전송 설정 항목들. 카탈로그 항목을 하드코딩하는
#: 것과 다르다 — 이것은 **규격이 정한 계약**이고, 규격이 바뀌면 여기도 바뀐다.
#: 규격 §7.2.1 이 이름 지은 채널 환산 항목의 뒷부분.
#: `value = (ma - ainN.zero) * ainN.scale`
COL_ZERO = "zero"
COL_SCALE = "scale"
COL_UNIT = "unit"

KEY_FIELD_MASK = "tx.fields"
KEY_PERIOD_MS = "tx.period_ms"
KEY_FLOAT_DIGITS = "tx.float_digits"


@dataclass(frozen=True)
class TelemetryShape:
    """지금 설정이 만들어 낼 텔레메트리의 모양. 대역폭 계산의 입력이다."""

    channels: int
    period_ms: int
    float_digits: int


def telemetry_shape(form: "SettingsForm") -> TelemetryShape:
    """켜진 채널 수·전송 주기·실수 자릿수를 폼에서 읽는다.

    🔴 값을 정수로 못 읽으면 **기본값으로 돌아간다.** 사용자가 주기를
       지우는 동안 값은 빈 문자열이고, 그때 예외가 나면 숫자 하나 지웠다고
       설정 화면이 통째로 죽는다.

    🔴 채널 수는 키 이름을 짐작하지 않고 **반복 그룹의 토글 열**에서 센다
       (`matrix_of`). 보드가 채널 항목의 이름을 바꿔도 따라간다.
    """
    channels = 0
    for group in form.groups:
        matrix = matrix_of(group)
        if matrix is None:
            continue
        for mrow in matrix.rows:
            for cell in mrow.cells:
                if cell is not None and cell.widget is Widget.TOGGLE:
                    channels += 1 if cell.value == "true" else 0
                    break
    return TelemetryShape(
        channels=max(channels, 0),
        period_ms=_int_or(form, KEY_PERIOD_MS, 100, minimum=1),
        float_digits=_int_or(form, KEY_FLOAT_DIGITS, 4, minimum=0),
    )


def channel_ranges(form: "SettingsForm") -> dict[int, tuple[float, float]]:
    """채널 번호 → (4 mA 일 때, 20 mA 일 때) 물리량.

    🔴 게이지가 `0 – 150 bar` 를 그리려면 설정을 되짚어야 한다. 보드는
       영점·스케일로 들고 있고 사람은 범위로 생각하며, 그 환산은 규격
       §7.2.1 이 정했다.

    되짚을 수 없는 채널(스케일 0, 값이 반쯤 입력된 중)은 **빠진다.** 없는
    것을 지어내면 게이지가 틀린 눈금을 그린다.
    """
    from host.core.scaling import range_of

    out: dict[int, tuple[float, float]] = {}
    for group in form.groups:
        matrix = matrix_of(group)
        if matrix is None or COL_ZERO not in matrix.columns:
            continue
        if COL_SCALE not in matrix.columns:
            continue
        zi = matrix.columns.index(COL_ZERO)
        si = matrix.columns.index(COL_SCALE)
        for mrow in matrix.rows:
            zero, scale = mrow.cells[zi], mrow.cells[si]
            if zero is None or scale is None:
                continue
            try:
                span = range_of(float(zero.value), float(scale.value))
            except (TypeError, ValueError):
                continue
            if span is not None:
                out[mrow.index] = span
    return out


def channel_units(form: "SettingsForm") -> dict[int, str]:
    """채널 번호 → 단위 문자열.

    🔴 `unit` 은 텔레메트리 필드이기도 하지만 기본 마스크에서 **꺼져 있다**
       (규격 §7.2). 대역폭을 아끼려고 끈 것이고, 그렇다고 화면이 단위를
       모를 이유는 없다 — 사용자가 설정에 적어 두었다.
    """
    out: dict[int, str] = {}
    for group in form.groups:
        matrix = matrix_of(group)
        if matrix is None or COL_UNIT not in matrix.columns:
            continue
        ui = matrix.columns.index(COL_UNIT)
        for mrow in matrix.rows:
            cell = mrow.cells[ui]
            if cell is not None and cell.value:
                out[mrow.index] = cell.value
    return out


def _int_or(form: "SettingsForm", key: str, fallback: int, *,
            minimum: int) -> int:
    try:
        value = int(float(form.row(key).value))
    except (KeyError, TypeError, ValueError):
        return fallback
    return value if value >= minimum else fallback


def _to_text(value: object) -> str:
    """전선에 나갈 문자열로. `$CFG,SET` 의 인자가 되는 형태다."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        # 🔴 repr 을 그대로 쓰면 0.30000000000000004 같은 것이 나간다.
        text = f"{value:.6f}".rstrip("0").rstrip(".")
        return text if text else "0"
    return str(value)


def _widget_of(item: ConfigItem) -> Widget:
    """모르는 vtype 은 문자열로 그린다.

    🔴 예외를 던지지 않는다. 보드가 새 vtype 을 보냈다고 설정 화면이 통째로
       안 열리면, 사용자는 나머지 44개 항목도 못 고친다. 하나를 문자열로
       그리는 편이 낫다 — 보드가 값을 검사하므로 잘못된 값은 거부된다.
    """
    return _WIDGET_OF.get(item.vtype, Widget.TEXT)


def build_row(item: ConfigItem) -> Row:
    """카탈로그 항목 하나를 화면 한 줄로."""
    editable = not item.readonly
    reason = ""
    if item.readonly:
        # 🔴 `note` 가 있으면 그것이 이유다. 보드가 왜 못 바꾸는지 알려
        #    주는데(예: 쿨링 팬이 5V 레일 직결), 그것을 버리고 "읽기 전용"
        #    이라고만 쓰면 사용자는 이유를 알 수 없다.
        reason = item.note or "이 항목은 보드에서 변경할 수 없다"

    return Row(
        key=item.key,
        label=item.label or item.key,
        widget=_widget_of(item),
        value=_to_text(item.current),
        default=_to_text(item.default),
        unit=item.unit,
        minimum=item.minimum,
        maximum=item.maximum,
        choices=tuple(item.choices),
        choice_labels=tuple(item.choice_labels),
        editable=editable,
        reason=reason,
        # 🔴 편집 가능해도 사유를 버리지 않는다. Row.note 의 설명 참조.
        note=item.note,
        is_integer=item.vtype in ("u8", "u16", "u32"),
    )


class SettingsForm:
    """카탈로그로 만든 폼. 편집 중인 값과 보드의 값을 함께 들고 있는다."""

    def __init__(self, schema: ConfigSchema) -> None:
        self._schema = schema
        self._rows: dict[str, Row] = {}
        self._original: dict[str, str] = {}
        self._edited: dict[str, str] = {}

        for key, item in schema.items.items():
            row = build_row(item)
            self._rows[key] = row
            self._original[key] = row.value

    # ------------------------------------------------------------- 읽기

    @property
    def groups(self) -> list[Group]:
        """그룹별로 묶은 줄들. 보드가 보낸 순서를 지킨다."""
        out: dict[str, Group] = {}
        for name in self._schema.groups():
            out[name] = Group(name=name)
        for key, item in self._schema.items.items():
            out.setdefault(item.group, Group(name=item.group))
            out[item.group].rows.append(self.row(key))
        return [g for g in out.values() if g.rows]

    def row(self, key: str) -> Row:
        """지금 화면에 떠 있는 상태의 줄."""
        base = self._rows[key]
        if key in self._edited:
            return Row(**{**base.__dict__, "value": self._edited[key]})
        return base

    def keys(self) -> list[str]:
        return list(self._rows)

    @property
    def fields(self) -> dict[int, object]:
        """NDJSON 필드 비트들 — 보드가 `cfg_field` 로 알려 준 것 (규격 §7.3).

        🔴 비트 목록을 호스트가 들고 있지 않다. 어떤 필드가 있는지, 무슨
           이름인지, 기본으로 켜져 있는지 전부 보드가 말한다. 그래야 펌웨어가
           필드를 늘려도 화면이 따라간다.
        """
        return dict(self._schema.fields)

    # ------------------------------------------------------------- 편집

    def edit(self, key: str, text: str) -> None:
        """화면 값을 바꾼다. 검사는 하지 않는다 — 타이핑 중이기 때문이다.

        🔴 한 글자 칠 때마다 빨간 줄을 긋지 않는다. `12` 를 치는 도중의
           `1` 은 범위 밖일 수 있는데, 그때마다 오류를 띄우면 읽을 수 없다.
           검사는 `validate` 와 보낼 때 한다.
        """
        if key not in self._rows:
            raise KeyError(key)
        self._edited[key] = text

    def revert(self, key: str) -> None:
        self._edited.pop(key, None)

    def revert_all(self) -> None:
        self._edited.clear()

    def reset_to_default(self, key: str) -> None:
        self._edited[key] = self._rows[key].default

    def is_dirty(self, key: str) -> bool:
        return key in self._edited and self._edited[key] != self._original[key]

    @property
    def dirty_keys(self) -> list[str]:
        """보드에 보낼 것들. 원래 값과 같아진 것은 빠진다.

        🔴 고쳤다가 되돌린 항목을 보내지 않는다. 규격상 같은 값을 쓰는 것은
           거부되지 않지만(§5), 보낼 이유가 없는 줄로 링크를 채우지 않는다 —
           115200 에서 여유가 초당 600 바이트뿐이다.
        """
        return [k for k in self._rows if self.is_dirty(k)]

    @property
    def has_changes(self) -> bool:
        return bool(self.dirty_keys)

    # ------------------------------------------------------------- 검사

    def validate(self, key: str) -> str:
        """지금 값이 보낼 수 있는 값인지. 문제가 없으면 빈 문자열.

        보드가 최종 판정을 하지만, 보내기 전에 걸러 주면 왕복 한 번을 아낀다.
        인터록은 여기서 판정하지 않는다 — 보드만 안다.
        """
        row = self.row(key)
        if not row.editable:
            return row.reason
        text = row.value
        if len(text.encode("utf-8")) > MAX_ARG_BYTES:
            # 🔴 전선 상한을 넘기면 보드가 조용히 버린다(규격 §3.1).
            #    응답이 없는 것을 사용자는 "먹통" 으로 읽는다.
            return f"{MAX_ARG_BYTES}바이트를 넘는다 (지금 {len(text.encode())})"
        try:
            self._schema.validate(key, text)
        except ConfigError as exc:
            return exc.detail or exc.reason
        return ""

    def errors(self) -> dict[str, str]:
        """보낼 것들 중 문제가 있는 것만."""
        out = {}
        for key in self.dirty_keys:
            msg = self.validate(key)
            if msg:
                out[key] = msg
        return out

    # ------------------------------------------------------------- 보내기

    def pending_changes(self) -> list[tuple[str, str]]:
        """보낼 (키, 값) 목록. 문제가 있는 것은 빠진다."""
        bad = self.errors()
        return [(k, self.row(k).value) for k in self.dirty_keys if k not in bad]

    def accept(self, key: str) -> None:
        """보드가 받아들였다. 화면 값을 보드의 값으로 굳힌다."""
        if key in self._edited:
            self._original[key] = self._edited[key]
            base = self._rows[key]
            self._rows[key] = Row(**{**base.__dict__, "value": self._edited[key]})
            del self._edited[key]

    def reject(self, key: str, reason: str) -> str:
        """보드가 거부했다. 화면 값은 그대로 두고 사유를 돌려준다.

        🔴 거부됐다고 값을 되돌리지 않는다. 사용자가 방금 친 것을 화면에서
           지우면 무엇을 고치려 했는지 사라진다. 사유를 보여 주고 고칠
           기회를 준다.
        """
        return reason
