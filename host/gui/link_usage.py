"""센서를 켜고 끄는 순간 링크를 몇 % 쓰는지 낸다. Qt 를 import 하지 않는다.

🔴 이 모듈이 있는 이유
=====================

`field_budget` 은 **레코드 한 종류**가 얼마를 쓰는지 잰다. 그런데 이 보드는
한 선으로 `ain`·`i2c`·`din` 을 함께 내보낸다(규격 §7.5·§7.6). 사용자가 알고
싶은 것은 "지금 이 선을 몇 % 쓰는가" 이지 "아날로그만 따지면 몇 %인가" 가
아니다.

그리고 실제로 어긋나 있었다 — I2C 포트를 켜도 화면의 대역폭 숫자가 꿈쩍도
하지 않았다. 켜는 사람은 그것이 공짜인 줄 안다. Q2 에서 UART 직결에 2%
유실이 실측된 링크에서(HANDOFF 2026-06-17) 공짜인 줄 알고 켜는 것은 나중에
원인을 못 찾는 유실이 된다.

여기서 정하는 세 가지
--------------------
    **줄 수**   종류마다 다르다. `ain` 은 켜진 채널 하나가 줄 하나,
                `i2c` 는 포트 하나가 줄 **여럿**(온습도 = temp·humidity),
                `din` 은 이벤트라 알 수 없다.
    **줄 길이** 종류마다 제 마스크(`tx.fields_ain`·`_i2c`·`_din`)로 잰다.
                `field_budget` 이 실제 줄을 만들어 재는 방식을 그대로 쓴다 —
                어림하면 따옴표·자릿수가 빠지고, 빠진 만큼이 정확히 여유가
                없을 때 사라진다.
    **용량**    `link.baud` 를 따라간다. 고정으로 박으면 사용자가 선을
                빠르게 바꿔도 화면의 % 가 그대로다.

🔴 **모르는 것은 모른다고 말한다.** `din` 은 이벤트성이라(규격 §7.6) 초당 몇
   줄인지 알 방법이 없다. 0 으로 적으면 거짓말이고 아무 수나 적으면 더 나쁜
   거짓말이다 — "이벤트" 라고 적고, 총합이 그만큼 낙관적이라는 것을 문구에
   함께 싣는다(설계 원칙 4: 명령 상태와 실제 상태를 구분한다와 같은 결).
"""

from __future__ import annotations

from dataclasses import dataclass

from host.core.limits import DEFAULT_BAUD, LINK_BAUD_KEY
from host.gui.field_budget import (
    FAULT_RATIO,
    WARN_RATIO,
    Budget,
    capacity_bytes_per_s,
    compute_budget,
    format_bytes_per_s,
    sample_record,
)
from host.gui.settings_form import (
    FIELD_MASK_KEYS,
    RECORD_GROUPS,
    SettingsForm,
    record_shape,
)

#: 이벤트성 레코드의 "주기" 자리에 적을 말.
EVENT_TEXT = "이벤트"

#: 알 수 없는 수의 자리. 🔴 빈 칸으로 두지 않는다 — 빈 칸은 "0" 으로도
#: "아직 안 쟀다" 로도 읽힌다.
UNKNOWN_TEXT = "—"

#: 켜진 것이 하나도 없는 줄.
NONE_TEXT = "없음"

#: 레코드 종류의 사람이 읽는 이름. 규격이 정한 세 종류다(§7.5·§7.6).
KIND_LABELS: dict[str, str] = {
    "ain": "아날로그",
    "i2c": "I2C",
    "din": "디지털",
}

#: 초당 줄 수를 알 수 없는 종류 — 보드가 **일이 생길 때** 보낸다(규격 §7.6).
EVENT_KINDS: tuple[str, ...] = ("din",)

#: 요약을 띄울 설정 그룹들 = 채널·포트를 **켜고 끄는** 자리.
#:
#: 🔴 그룹 이름을 여기 다시 적지 않는다. 어느 그룹이 어느 레코드를 만드는지는
#:    `settings_form.RECORD_GROUPS` 가 이미 알고 있고, 보드가 그룹을 늘리면
#:    그쪽 한 줄만 고치면 된다.
USAGE_GROUPS: tuple[str, ...] = tuple(dict.fromkeys(RECORD_GROUPS.values()))


@dataclass(frozen=True)
class UsageRow:
    """링크 사용량 표의 한 줄 = 레코드 종류 하나."""

    kind: str
    label: str
    detail: str
    """무엇을 세었는지 — 켜진 커넥터 이름들. 🔴 "몇 %" 만 보여 주면 무엇을
    꺼야 그 수가 줄어드는지 알 수 없다."""

    line_bytes: int
    period_ms: int
    lines_per_s: float | None
    bytes_per_s: float | None
    ratio: float | None
    event_driven: bool = False

    def cells(self) -> tuple[str, str, str, str, str]:
        """그대로 그리면 되는 다섯 칸.

        🔴 서식을 위젯에 맡기지 않는다. 두 화면이 같은 수를 다르게 적으면
           사용자는 어느 쪽을 믿어야 할지 모른다.
        """
        if self.event_driven:
            # 🔴 주기 칸까지 "이벤트" 라고 쓰지 않는다 — 설명 칸이 이미 그
            #    말을 했고, 주기는 **모르는 수**다. 모르는 것은 모르는 자리
            #    표시로 둔다.
            return (self.label, self.detail, UNKNOWN_TEXT,
                    UNKNOWN_TEXT, UNKNOWN_TEXT)
        return (
            self.label,
            self.detail,
            f"{self.period_ms} ms",
            format_bytes_per_s(self.bytes_per_s or 0.0),
            f"{(self.ratio or 0.0) * 100:.1f} %",
        )


@dataclass(frozen=True)
class LinkUsage:
    """지금 설정이 링크를 얼마나 쓰는가 — 종류별 줄과 합계."""

    baud: int
    capacity_bytes_per_s: float
    rows: tuple[UsageRow, ...]
    bytes_per_s: float
    """잴 수 있는 줄들의 합. 🔴 `unmeasured` 가 비어 있지 않으면 이 값은
    **낙관적**이다 — 이벤트로 오는 것이 여기 안 들어 있다."""

    unmeasured: tuple[str, ...]
    level: str
    """`ok` | `warn` | `fault` — `field_budget` 의 등급 규칙과 같다."""

    message: str

    @property
    def ratio(self) -> float:
        if self.capacity_bytes_per_s <= 0:
            return float("inf")
        return self.bytes_per_s / self.capacity_bytes_per_s

    def total_cells(self) -> tuple[str, str, str, str, str]:
        return ("합계", "", "",
                format_bytes_per_s(self.bytes_per_s),
                f"{self.ratio * 100:.1f} %")


def link_baud(form: SettingsForm, fallback: int = DEFAULT_BAUD) -> int:
    """지금 설정된 링크 속도 (규격 §4.2).

    🔴 폼의 값을 쓴다 — 사용자가 방금 고른 속도다. 고르는 순간 % 가 따라
       움직여야 "이 속도면 되겠다" 를 **적용하기 전에** 판단할 수 있다.

    🔴 못 읽으면 부르는 쪽이 준 값으로 돌아간다. 속도를 지우는 동안 값은 빈
       문자열이고, 그때 예외가 나면 숫자 하나 지웠다고 화면이 죽는다.
       `link.baud` 가 아예 없는(옛 펌웨어) 보드도 같은 길로 지나간다.
    """
    try:
        value = int(float(form.row(LINK_BAUD_KEY).value))
    except (KeyError, TypeError, ValueError):
        return fallback
    return value if value > 0 else fallback


def field_names(form: SettingsForm, kind: str) -> tuple[str, ...]:
    """이 종류의 마스크에서 **켜진** 필드 이름들.

    🔴 비트 목록을 호스트가 들고 있지 않다 — 보드가 `cfg_field` 로 알려 준
       것에서 고른다(규격 §7.3). 그리고 **그 종류에 해당하는 비트만** 본다:
       같은 비트 번호라도 어느 레코드의 것인지는 비트마다 다르다.
    """
    key = FIELD_MASK_KEYS.get(kind)
    if key is None:
        return ()
    try:
        mask = int(float(form.row(key).value))
    except (KeyError, TypeError, ValueError):
        mask = 0
    return tuple(
        info.name for bit, info in sorted(form.fields.items())
        if kind in getattr(info, "records", ()) and mask & (1 << bit)
    )


def record_line(form: SettingsForm, kind: str) -> dict:
    """이 종류가 실제로 내보낼 만한 줄 하나.

    🔴 어림하지 않는다. `field_budget.sample_record` 가 잠긴 필드까지 얹어
       진짜 레코드를 만들고, `measure_line` 이 그것을 잰다.
    """
    shape = record_shape(form, kind)
    return sample_record(field_names(form, kind),
                         float_digits=shape.float_digits, record_type=kind)


def record_budget(form: SettingsForm, kind: str, baud: int) -> Budget:
    """이 종류 하나가 링크에서 차지하는 몫.

    🔴 **요약 표와 필드 마스크 카드가 함께 부르는 유일한 계산이다.** 각자
       계산하면 언젠가 한쪽만 고쳐지고, 그때 두 화면이 다른 수를 말한다.
    """
    shape = record_shape(form, kind)
    return compute_budget(record_line(form, kind),
                          channels_enabled=shape.channels,
                          period_ms=shape.period_ms, baud=baud)


def _detail(kind: str, labels: tuple[str, ...], records: int) -> str:
    """무엇을 세었는지 한 줄로."""
    if kind in EVENT_KINDS:
        return EVENT_TEXT
    if not labels:
        return NONE_TEXT
    text = " ".join(labels)
    if kind == "i2c":
        # 🔴 포트 수와 레코드 수가 다르다 — 온습도는 포트 하나가 양을 둘
        #    낸다. 둘 다 보여 줘야 "포트 둘인데 왜 세 줄인가" 가 풀린다.
        return f"{text} ({records} 값)"
    return text


def compute_usage(form: SettingsForm,
                  fallback_baud: int = DEFAULT_BAUD) -> LinkUsage:
    """지금 폼 상태로 링크 사용량 표를 만든다.

    `fallback_baud` 는 카탈로그에 `link.baud` 가 없을 때만 쓰인다 — 호스트가
    실제로 포트를 연 속도를 넣으면 된다.
    """
    baud = link_baud(form, fallback_baud)
    capacity = capacity_bytes_per_s(baud)

    rows: list[UsageRow] = []
    total = 0.0
    unmeasured: list[str] = []
    for kind in FIELD_MASK_KEYS:
        shape = record_shape(form, kind)
        budget = record_budget(form, kind, baud)
        event = kind in EVENT_KINDS
        label = KIND_LABELS.get(kind, kind)
        if event:
            unmeasured.append(label)
        else:
            total += budget.bytes_per_s
        rows.append(UsageRow(
            kind=kind,
            label=label,
            detail=_detail(kind, shape.labels, shape.channels),
            line_bytes=budget.line_bytes,
            period_ms=shape.period_ms,
            lines_per_s=None if event else budget.lines_per_s,
            bytes_per_s=None if event else budget.bytes_per_s,
            ratio=None if event else budget.ratio,
            event_driven=event,
        ))

    level, message = _verdict(total, capacity, tuple(unmeasured))
    return LinkUsage(
        baud=baud,
        capacity_bytes_per_s=capacity,
        rows=tuple(rows),
        bytes_per_s=total,
        unmeasured=tuple(unmeasured),
        level=level,
        message=message,
    )


def _verdict(used: float, capacity: float,
             unmeasured: tuple[str, ...]) -> tuple[str, str]:
    """(심각도, 문구). 등급 규칙은 `field_budget.budget_message` 와 같다.

    🔴 문구가 **무엇을 줄여야 하는지** 말한다. "대역폭 초과" 만 띄우면
       사용자는 무엇을 끄거나 늦춰야 하는지 모른다(규격 §5.1 이 CAPACITY
       거부에 요구값과 가용값을 함께 담게 한 것과 같은 이유).

    🔴 그리고 **합계가 낙관적이라는 것**을 숨기지 않는다. 이벤트로 오는
       레코드가 여기 안 들어 있으므로, 여유가 빠듯할 때 실제로는 이미 넘어
       있을 수 있다.
    """
    ratio = float("inf") if capacity <= 0 else used / capacity
    text_used = format_bytes_per_s(used)
    text_cap = format_bytes_per_s(capacity)
    # 🔴 표의 합계 줄과 **같은 자릿수**로 쓴다. 같은 카드 안에서 한 줄은
    #    1.5 %, 다른 줄은 1 % 라고 하면 사용자는 둘이 다른 수인 줄 안다.
    pct = ratio * 100

    caveat = ""
    if unmeasured:
        caveat = (f"  {'·'.join(unmeasured)} 입력은 이벤트로 와서 여기 없다 — "
                  f"실제로는 이보다 더 쓴다.")

    if ratio >= FAULT_RATIO:
        return ("fault",
                f"링크 용량을 넘는다 — {text_used} 필요, {text_cap} 가능 "
                f"({pct:.1f}%). 채널을 끄거나 주기를 늘리거나 링크 속도를 "
                f"올려야 한다.{caveat}")
    if ratio >= WARN_RATIO:
        return ("warn",
                f"여유가 없다 — {text_used} / {text_cap} ({pct:.1f}%). "
                f"명령·응답·하트비트도 같은 선을 쓴다.{caveat}")
    return ("ok", f"{text_used} / {text_cap} ({pct:.1f}%){caveat}")
