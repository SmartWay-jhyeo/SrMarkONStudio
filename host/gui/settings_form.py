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
    editable: bool = True
    reason: str = ""
    """편집이 안 되는 이유. 툴팁에 그대로 띄운다."""

    is_integer: bool = False

    @property
    def dirty(self) -> bool:
        """화면 값이 보드 값과 다른가 — `original` 과 비교는 Form 이 한다."""
        return False   # Row 는 불변이라 Form 이 판정한다


@dataclass
class Group:
    name: str
    rows: list[Row] = field(default_factory=list)


#: 그룹 이름을 사람이 읽는 말로. 없는 그룹은 이름 그대로 쓴다 —
#: 🔴 보드가 새 그룹을 보내도 화면이 깨지지 않아야 한다.
GROUP_LABELS = {
    "dev": "장치",
    "tx": "전송",
    "pwr": "전원",
    "adc": "ADC",
    "ain": "아날로그 입력",
}


def group_label(name: str) -> str:
    return GROUP_LABELS.get(name, name)


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
        editable=editable,
        reason=reason,
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
