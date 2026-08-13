"""필드 선택이 전선에 얼마를 쓰는지 계산한다. Qt 를 import 하지 않는다.

🔴 이 화면이 있는 이유
=====================

사용자가 NDJSON 에 어떤 필드를 넣을지 고른다. 그런데 고르는 순간에는
그것이 무엇을 뜻하는지 보이지 않는다 — 체크박스 열 개가 나열되어 있을
뿐이다.

**보이지 않는 것이 대역폭이다.** Q2 에서 STM32→Jetson UART 직결에 2% 유실이
실측됐다(`HANDOFF.md`, 2026-06-17). 송신측·버퍼·주기·baud 등 8개 가설이
기각되고 물리계층이 남은 의심으로 미해결이다. 그 링크에서 필드를 몇 개
더 켜는 것은 사소한 선택이 아니다.

그래서 이 층은 **고르는 순간에 결과를 보여 준다.**

    줄 미리보기      실제로 나갈 한 줄
    초당 바이트      그 줄 × 채널 수 × 주기
    여유             링크 용량 대비 몇 %

여유가 없으면 켜기 전에 안다. 켜고 나서 유실을 겪고 원인을 찾는 것보다
훨씬 싸다.

계산은 어림이 아니라 **실제 줄을 만들어 재는 것**이다. 어림하면 이스케이프나
자릿수 같은 것이 빠지고, 빠진 만큼이 정확히 여유가 없을 때 문제가 된다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

#: 전선 위 한 줄에 붙는 줄바꿈. NDJSON 은 줄 하나가 레코드 하나다.
LINE_ENDING_BYTES = 1

#: 8N1 — 한 바이트를 보내는 데 10 비트가 든다 (시작 1 + 데이터 8 + 정지 1).
BITS_PER_BYTE_ON_WIRE = 10

#: 이 비율을 넘으면 경고한다. 링크를 100% 채우면 아무 여유가 없다 —
#: 명령·응답·하트비트가 텔레메트리와 같은 선을 쓴다.
WARN_RATIO = 0.70

#: 이 비율을 넘으면 물리적으로 못 보낸다.
FAULT_RATIO = 1.00


@dataclass(frozen=True)
class Budget:
    """지금 설정이 전선에 얼마를 쓰는지."""

    line_bytes: int
    """한 줄의 길이 (줄바꿈 포함)."""

    lines_per_s: float
    """초당 줄 수 = 켜진 채널 수 × (1000 / 주기)."""

    bytes_per_s: float
    capacity_bytes_per_s: float

    @property
    def ratio(self) -> float:
        if self.capacity_bytes_per_s <= 0:
            return float("inf")
        return self.bytes_per_s / self.capacity_bytes_per_s

    @property
    def over_capacity(self) -> bool:
        return self.ratio >= FAULT_RATIO

    @property
    def tight(self) -> bool:
        """넘지는 않지만 여유가 없다."""
        return WARN_RATIO <= self.ratio < FAULT_RATIO

    @property
    def headroom_bytes_per_s(self) -> float:
        return self.capacity_bytes_per_s - self.bytes_per_s


def capacity_bytes_per_s(baud: int) -> float:
    """8N1 에서 초당 보낼 수 있는 바이트 수.

    🔴 baud 를 8 로 나누면 안 된다. 시작 비트와 정지 비트가 있어서 한
       바이트에 10 비트가 든다 — 25% 를 더 잡게 되고, 그 25% 가 정확히
       여유가 없을 때 사라진다.
    """
    return baud / BITS_PER_BYTE_ON_WIRE


def measure_line(record: dict) -> int:
    """레코드 하나가 전선에서 차지하는 바이트 수.

    🔴 어림하지 않고 실제로 만들어 잰다. 필드 이름 길이만 더하는 식으로
       어림하면 따옴표·쉼표·이스케이프·실수 자릿수가 빠지고, 빠진 만큼이
       정확히 여유가 없을 때 문제가 된다.

    보드가 쓰는 것과 같은 압축 형식이어야 한다 — 공백을 넣는 기본값으로
    재면 실제보다 크게 나온다.
    """
    text = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    return len(text.encode("utf-8")) + LINE_ENDING_BYTES


def compute_budget(record: dict, *, channels_enabled: int, period_ms: int,
                   baud: int) -> Budget:
    """한 레코드 모양과 수집 설정으로 대역폭 사용량을 낸다.

    `channels_enabled` 가 0 이거나 `period_ms` 가 0 이하면 초당 0 바이트다.
    주기 0 을 무한대로 치지 않는 이유는, 화면에서 주기를 지우는 동안 잠깐
    0 이 되는데 그때 화면이 무한대를 외치면 안 되기 때문이다.
    """
    line = measure_line(record)
    if channels_enabled <= 0 or period_ms <= 0:
        lines_per_s = 0.0
    else:
        lines_per_s = channels_enabled * (1000.0 / period_ms)
    return Budget(
        line_bytes=line,
        lines_per_s=lines_per_s,
        bytes_per_s=line * lines_per_s,
        capacity_bytes_per_s=capacity_bytes_per_s(baud),
    )


def format_bytes_per_s(value: float) -> str:
    """초당 바이트를 사람이 읽는 말로. 자릿수를 늘리지 않는다."""
    if value < 1000:
        return f"{value:.0f} B/s"
    if value < 1_000_000:
        return f"{value / 1000:.1f} kB/s"
    return f"{value / 1_000_000:.2f} MB/s"


def budget_message(budget: Budget) -> tuple[str, str]:
    """(심각도, 문구). 심각도는 `ok` | `warn` | `fault`.

    🔴 문구가 무엇을 줄여야 하는지 말한다. "대역폭 초과" 만 띄우면 사용자는
       무엇을 끄거나 늦춰야 하는지 모른다. 규격 §5.1 이 CAPACITY 거부에
       요구값과 가용값을 함께 담으라고 한 것과 같은 이유다.
    """
    used = format_bytes_per_s(budget.bytes_per_s)
    cap = format_bytes_per_s(budget.capacity_bytes_per_s)
    pct = budget.ratio * 100

    if budget.over_capacity:
        return ("fault",
                f"링크 용량을 넘는다 — {used} 필요, {cap} 가능 ({pct:.0f}%). "
                f"필드를 줄이거나 주기를 늘려야 한다.")
    if budget.tight:
        return ("warn",
                f"여유가 없다 — {used} / {cap} ({pct:.0f}%). "
                f"명령·응답·하트비트도 같은 선을 쓴다.")
    return ("ok", f"{used} / {cap} ({pct:.0f}%)")
