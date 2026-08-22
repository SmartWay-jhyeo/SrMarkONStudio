"""$CFG,LIST 응답을 스키마 객체로 바꾸고 값을 검증한다.

호스트는 설정 항목을 하드코딩하지 않는다. 보드가 내려준 카탈로그만으로
화면을 구성하고 값을 검사한다.

규격: protocol/specification.md §7.3
"""

import math
from collections.abc import Iterable
from dataclasses import dataclass, field

from host.core.errors import ConfigError, Reason
from host.core.records import parse_record

_INT_TYPES = ("u8", "u16", "u32")
_TRUE_WORDS = ("true", "1", "on", "yes")
_FALSE_WORDS = ("false", "0", "off", "no")

#: 프로토콜 구분자. 설정값에 들어가면 줄 구조가 깨진다.
_PROTOCOL_DELIMITERS = frozenset("$,*")

#: 🔴 설정 문자열에 허용되는 문자 — 인쇄 가능 ASCII 에서 구분자를 뺀 것.
#:
#: `isascii()` 만으로는 부족하다. 제어문자가 전부 ASCII 이기 때문이다:
#: '\n'.isascii() 도 '\x00'.isascii() 도 True 다.
#:
#: 이 한 줄이 막는 것들:
#:   '\r' '\n'  — 값 안에 줄바꿈을 넣어 `$CFG,SET,dev.id,x\r\n$HB*0A` 처럼
#:                완결된 명령을 주입하는 공격. 주입된 $HB 는 체크섬까지
#:                유효해서 보드가 진짜 하트비트로 받아 CONFIG 모드를 연다
#:   '\x00'     — C 펌웨어에서 문자열이 중간에 끝난 것처럼 처리됨.
#:                호스트가 검증한 값과 펌웨어가 해석한 값이 달라진다
#:   ','        — 명령 인자를 추가로 쪼갬
#:   '*'        — 체크섬 구분자로 오인
#:   '$'        — 명령 시작으로 오인
#:
#: 🔴 [개정 2026-08-22] 비 ASCII(U+0080~)는 **허용한다** — 펌웨어
#: mk_config.c 가 2026-08-20 에 같은 이유로 풀었다: 커넥터 이름 "유압" 이
#: RANGE 로 떨어졌는데, 줄 구조를 깨는 것은 제어문자와 구분자뿐이고 UTF-8
#: 연속 바이트는 그 어느 것도 될 수 없다. 실기기가 이미 한글 이름을 받아
#: 저장하는데 호스트만 막고 있었다(GUI 에서 "유압" 입력 거부).
#: 대신 길이는 **UTF-8 바이트 수**로 센다 — 펌웨어의 상한이 바이트다.
_ALLOWED_STR_CHARS = frozenset(
    chr(c) for c in range(0x20, 0x7F)
) - _PROTOCOL_DELIMITERS


def _bad_str_chars(raw: str) -> list[str]:
    """줄 구조를 깨는 문자만 골라낸다 — 펌웨어 mk_config.c 와 같은 규칙."""
    return [c for c in raw if ord(c) < 0x80 and c not in _ALLOWED_STR_CHARS]


@dataclass(frozen=True)
class ConfigItem:
    key: str
    group: str
    vtype: str
    default: object
    current: object
    minimum: float | None = None
    maximum: float | None = None
    unit: str = ""
    readonly: bool = False
    out: bool = False
    """이 항목이 실제 출력을 움직인다 (규격 §7.3).

    🔴 TEST 제어 모드에서 저장되지 않고 모드를 벗어날 때 기본값으로
       돌아간다(§6.4). 호스트가 키 이름으로 짐작하지 않도록 보드가
       알려 준다 — `sol` 이라는 글자를 보고 판단하면 펌웨어가 이름을
       바꾸는 순간 조용히 틀린다.
    """

    label: str = ""
    note: str = ""
    choices: tuple = ()
    choice_labels: tuple[str, ...] = ()
    """`choices` 와 같은 순서의 이름표 (규격 §7.3).

    🔴 길이가 다르면 **통째로 버린다.** 짝이 어긋난 이름표를 그리면 사용자가
       엉뚱한 것을 고르는데 화면에는 아무 이상이 없어 보인다 — 숫자가 흉해도
       그쪽이 안전하다. 버리는 판단은 파싱에서 한 번만 한다.
    """


@dataclass(frozen=True)
class FieldBit:
    """NDJSON 필드 마스크의 비트 하나."""

    bit: int
    name: str
    default: bool
    label: str = ""
    records: tuple[str, ...] = ()
    """이 비트가 어느 레코드(`ain`·`i2c`·`din`)의 마스크에 해당하는가
    (규격 §7.3 개정, 2026-08-19). 화면은 이것으로 마스크 카드를 셋으로
    나눠 그린다 — 해당 없는 레코드의 카드에는 이 비트를 보여 주지 않는다."""


@dataclass
class ConfigSchema:
    items: dict[str, ConfigItem] = field(default_factory=dict)
    fields: dict[int, FieldBit] = field(default_factory=dict)
    _group_order: list[str] = field(default_factory=list)

    def groups(self) -> list[str]:
        """항목이 처음 나타난 순서대로 그룹 이름을 반환한다."""
        return list(self._group_order)

    def validate(self, key: str, raw: str) -> object:
        """문자열 값을 검사해 파싱된 값을 반환한다.

        검사 순서는 규격 §5 를 따른다 — 키 존재 → 읽기 전용 → 타입·범위.
        인터록은 보드가 판정하므로 호스트에서는 검사하지 않는다.

        Raises:
            ConfigError: reason 이 UNKNOWN_KEY / READONLY / RANGE 중 하나.
        """
        item = self.items.get(key)
        if item is None:
            raise ConfigError(Reason.UNKNOWN_KEY, key)
        if item.readonly:
            raise ConfigError(Reason.READONLY, item.note or key)
        return _coerce(item, raw)


def _coerce(item: ConfigItem, raw: str) -> object:
    """vtype 에 맞게 문자열을 변환하고 범위를 검사한다."""
    if item.vtype == "bool":
        low = raw.strip().lower()
        if low in _TRUE_WORDS:
            return True
        if low in _FALSE_WORDS:
            return False
        raise ConfigError(Reason.RANGE, f"불리언이 아님: {raw!r}")

    if item.vtype == "str":
        bad = _bad_str_chars(raw)
        if bad:
            raise ConfigError(Reason.RANGE, f"허용되지 않는 문자: {bad!r}")
        # 🔴 바이트로 센다 — 펌웨어 상한(strlen)이 바이트다. 문자로 세면
        #    한글 3자("유압펌")가 9바이트인데 상한 7을 통과시켜, 호스트는
        #    받고 보드가 거부하는 어긋남이 된다.
        nbytes = len(raw.encode("utf-8"))
        if item.maximum is not None and nbytes > int(item.maximum):
            raise ConfigError(
                Reason.RANGE,
                f"최대 {int(item.maximum)}바이트, 받음 {nbytes}바이트"
                f" (한글은 한 자에 3바이트)"
            )
        return raw

    if item.vtype == "enum":
        try:
            value: object = int(raw)
        except ValueError:
            value = raw
        if value not in item.choices:
            raise ConfigError(Reason.RANGE, f"허용값 {list(item.choices)}")
        return value

    if item.vtype in _INT_TYPES:
        try:
            # 🔴 base 0 을 쓰면 안 된다. int("08", 0) 은 ValueError 다 —
            # 파이썬이 앞의 0 을 8진수 접두사로 읽기 때문이다. 사용자가
            # 고정폭 습관으로 "08" 을 넣는 건 지극히 자연스럽고, 그게
            # "정수가 아님" 으로 거부되면 원인을 짐작할 수도 없다.
            value = int(raw, 10)
        except ValueError:
            raise ConfigError(Reason.RANGE, f"정수가 아님: {raw!r}") from None
    elif item.vtype == "f32":
        try:
            value = float(raw)
        except ValueError:
            raise ConfigError(Reason.RANGE, f"실수가 아님: {raw!r}") from None
        # 🔴 보드 저장소(config_store)와 같은 규칙이어야 한다.
        # NaN 은 아래 범위 검사를 그대로 뚫는다 — nan < min 도 nan > max 도
        # False 다. 범위가 아예 없는 항목(ain*.zero/scale)은 inf 도 통과한다.
        if not math.isfinite(value):
            raise ConfigError(Reason.RANGE, f"유한한 실수가 아님: {raw!r}")
    else:
        raise ConfigError(Reason.RANGE, f"알 수 없는 타입: {item.vtype}")

    if item.minimum is not None and value < item.minimum:
        raise ConfigError(Reason.RANGE, f"최소 {item.minimum}, 받음 {value}")
    if item.maximum is not None and value > item.maximum:
        raise ConfigError(Reason.RANGE, f"최대 {item.maximum}, 받음 {value}")
    return value


def parse_catalog(lines: Iterable[str]) -> ConfigSchema:
    """$CFG,LIST 응답 줄들을 ConfigSchema 로 모은다.

    Raises:
        ConfigError: cfg_end 의 count 가 실제 항목 수와 다른 경우
            (전송이 중간에 잘린 것으로 본다).
    """
    schema = ConfigSchema()
    declared: int | None = None

    for line in lines:
        rec = parse_record(line)
        rtype = rec["type"]

        if rtype == "cfg_item":
            group = rec.get("grp", "")
            if group not in schema._group_order:
                schema._group_order.append(group)
            schema.items[rec["key"]] = ConfigItem(
                key=rec["key"],
                group=group,
                vtype=rec["vtype"],
                default=rec.get("default"),
                current=rec.get("cur"),
                minimum=rec.get("min"),
                maximum=rec.get("max"),
                unit=rec.get("unit", ""),
                readonly=bool(rec.get("ro", False)),
                out=bool(rec.get("out", False)),
                label=rec.get("label", ""),
                note=rec.get("note", ""),
                choices=tuple(rec.get("choices", ())),
                choice_labels=_labels_for(rec),
            )

        elif rtype == "cfg_field":
            records = rec.get("records", ())
            schema.fields[rec["bit"]] = FieldBit(
                bit=rec["bit"],
                name=rec["name"],
                default=bool(rec.get("default", False)),
                label=rec.get("label", ""),
                # 🔴 보드가 안 보내는(구 펌웨어) 경우 빈 튜플 — 그 비트는
                #    어느 카드에도 그릴 수 없다는 뜻이지, 에러가 아니다.
                records=tuple(records) if isinstance(records, list) else (),
            )

        elif rtype == "cfg_end":
            declared = rec["count"]

    # 🔴 종료 줄이 아예 안 온 경우도 절단이다.
    #
    # 오히려 이쪽이 더 흔한 형태다 — cfg_end 는 마지막에 보내므로, 전송이
    # 중간에 끊기면 "개수가 틀린 cfg_end" 가 아니라 "cfg_end 자체가 없음" 이
    # 된다. 이걸 통과시키면 GUI 가 설정 몇 개가 빠진 화면을 아무 경고 없이
    # 정상인 것처럼 그린다.
    if declared is None:
        raise ConfigError(Reason.RANGE, "카탈로그에 cfg_end 가 없음 (전송 절단)")

    total = len(schema.items) + len(schema.fields)
    if declared != total:
        raise ConfigError(
            Reason.RANGE,
            f"카탈로그 개수 불일치: 선언 {declared}, 수신 {total}",
        )
    return schema


def _labels_for(rec: dict) -> tuple[str, ...]:
    """`choice_labels` 를 꺼낸다. 짝이 안 맞으면 버린다 (규격 §7.3)."""
    labels = rec.get("choice_labels")
    choices = rec.get("choices") or ()
    if not isinstance(labels, list) or len(labels) != len(choices):
        return ()
    return tuple(str(x) for x in labels)
