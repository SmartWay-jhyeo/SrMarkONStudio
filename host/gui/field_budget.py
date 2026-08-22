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

from host.core.limits import TELEM_RECORD_JSON_MAX

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

    @property
    def record_dropped(self) -> bool:
        """한 줄이 보드 상한을 넘는가 — 넘으면 보드가 이 레코드를 **통째로
        버린다** (host/core/limits.py 의 TELEM_RECORD_JSON_MAX 근거).

        🔴 대역폭 초과보다 나쁘다. 대역폭 초과는 일부라도 오지만, 이것은
           한 줄도 안 온다 — 실기기에서 "아날로그가 멈췄다" 로 나타났다
           (2026-08-21)."""
        return self.line_bytes - LINE_ENDING_BYTES > TELEM_RECORD_JSON_MAX


#: 마스크로 끌 수 없는 필드들 (규격 §7.1). 표본에 언제나 들어간다.
ALWAYS_ON = ("schema_ver", "seq", "t", "type")

#: 레코드 종류별로 **항상** 실리는 잠긴 필드 (마스크 밖, 규격 §7.5·§7.6).
#: `sample_record` 가 마스크 선택과 무관하게 이 필드들을 얹는다 — 실제
#: 레코드가 늘 그렇기 때문이다. `host/gui/settings_form.py` 의
#: `LOCKED_FIELDS` 와 같은 표다(그쪽은 화면에 "잠김" 으로 보여 주는 용도,
#: 여기는 대역폭 표본을 만드는 용도 — 목적이 달라 따로 둔다).
_LOCKED_BY_KIND: dict[str, tuple[str, ...]] = {
    # 🔴 connector_id 는 ain·i2c 에서도 잠겼다(규격 §7.2·§7.5 개정
    #    2026-08-21). 채널이 빠진 다채널 레코드는 아무 말도 안 한다 —
    #    실기기에서 사용자가 이 필드를 끄자 대시보드가 레코드를 버렸다.
    "ain": ("connector_id",),
    "i2c": ("quantity", "value", "connector_id"),
    "din": ("connector_id", "state"),
    # 🔴 위치가 빠진 GNSS 레코드는 아무 말도 안 한다(규격 §7.8.5). 빼고
    #    재면 실제보다 60바이트쯤 짧게 잡힌다.
    "gnss": ("lat", "lon", "fix_t"),
    # [2026-08-22] imu(젯슨 링크, 계약 v1.7.x) — 6축은 마스크 밖이다.
    # 마스크가 정하는 것은 `imu_temp`(전선 이름 `degc`) 하나뿐이다.
    "imu": ("time_source", "ax", "ay", "az", "gx", "gy", "gz"),
}

#: 필드 하나가 실을 **넉넉한** 표본값.
#:
#: 🔴 좁은 값으로 재면 실제보다 짧게 나오고, 짧게 나온 만큼이 정확히
#:    여유가 없을 때 사라진다. 큰 쪽으로 잡는다 —
#:    raw 는 24 비트 ADC 의 큰 쪽, connector_id 는 J9, time_source 는
#:    규격의 네 값 중 가장 긴 것.
_SAMPLE = {
    "device_id": "board-01",
    "time_source": "device_clock",
    "time_quality": 3,
    "raw": 8_388_607,
    "unit": "L/min",
    "status": 0,
    "capture_counter": 4_294_967_295,
    "connector_id": 9,
    # 🔴 i2c·din 의 잠긴 필드. quantity 는 §7.5.1 어휘 중 가장 긴 것
    # ("temp_object") — 좁은 값으로 재면 실제보다 짧게 나온다(모듈 머리말).
    "quantity": "temp_object",
    "state": 1,
    # 🔴 GNSS(규격 §7.8). 자릿수가 **`tx.float_digits` 밖**이라 여기 고정된
    #    표본으로 들어간다 — 위·경도는 소수 8자리(2026-08-20 확장, §7.8.2
    #    — 실기기 분 필드가 이미 소수 8자리라 정보 손실 없이 그만큼 낸다),
    #    나머지는 필드마다 다르다(§7.8.5). 어림하면 정확히 그 자릿수만큼
    #    짧게 잡힌다. 값은 넉넉한 쪽(서반구·남반구의 세 자리 경도, 두 자리
    #    HDOP)으로 잡되 실기기가 낸 자릿수를 그대로 채운다.
    "lat": -37.31906943,
    "lon": -127.34059070,
    "fix_t": 1_787_193_075_000,
    "alt": 1234.567,
    "sats": 32,
    "fix": 5,
    "hdop": 99.99,
    "speed": 99.999,
    "course": 359.99,
    # 🔴 imu(젯슨 링크, 펌웨어 mk_cloud.c) — 자릿수가 필드마다 고정이라
    #    (6축 3자리, 온도 1자리) `tx.float_digits` 밖이다. 값은 넉넉한 쪽:
    #    가속은 ±2 g, 자이로는 ±250 dps 눈금의 끝(RAWIMUXA 눈금,
    #    UM981_Auto_Commands_R1.0.pdf §2.3.5), 온도는 음수 세 자리.
    "ax": -1.999, "ay": -1.999, "az": -1.999,
    "gx": -249.999, "gy": -249.999, "gz": -249.999,
    "degc": -105.0,
}

#: 실수 필드 — 자릿수가 `tx.float_digits` 를 따른다.
#:
#: 🔴 GNSS 필드는 여기 없다. 그쪽은 자릿수가 필드마다 고정이라(규격 §7.8.2)
#:    `tx.float_digits` 를 따르면 안 된다 — 기본값 4자리면 위치 분해능이
#:    약 11 m 가 되는데 화면에는 소수점이 그럴듯하게 붙어 있다.
_SAMPLE_FLOAT = {"ma": 19.999999, "value": 1234.567891}

#: 모르는 필드에 넣을 자리끼움. 🔴 빼지 않는다 — 빼면 적게 잡는다.
_UNKNOWN_PLACEHOLDER = "00000000"

#: 마스크 이름 ≠ 전선 이름인 필드 (규격 §7.2 표의 주석). 미리보기가 마스크
#: 이름으로 그리면 실제로 나가는 줄과 달라진다.
_WIRE_NAME = {"imu_temp": "degc"}

#: 젯슨 링크 계약(v1.7.x)으로만 나가는 레코드 종류 — 공통 필드가 v3 와
#: 다르다: `seq` 가 없고 `device_id`·`time_source` 가 있으며 `schema_ver`
#: 는 계약의 것(기본 1)이다. v3 머리로 재면 실제로 나가는 줄과 다른 것을
#: 보여 주게 된다(펌웨어 mk_cloud.c begin_record).
_CLOUD_KINDS = ("imu",)


def sample_record(names, *, float_digits: int = 4,
                  record_type: str = "ain") -> dict:
    """고른 필드들로 **실제로 나갈 만한** 줄 하나를 만든다.

    🔴 어림이 아니라 진짜 레코드를 만든다. `measure_line` 이 그것을 재고,
       그래야 따옴표·쉼표·자릿수가 빠지지 않는다(모듈 머리말).

    🔴 모르는 이름도 담는다. 보드가 규격을 올려 필드를 추가하면 호스트는
       그 이름을 모르는데, 그때 빼고 재면 화면이 여유가 있다고 말하면서
       실제로는 없는 상태가 된다.

    🔴 [개정, 2026-08-19] `record_type` 이 `"ain"`이 아니면 그 레코드의
       **잠긴 필드**(`_LOCKED_BY_KIND`)를 `names` 와 무관하게 항상 얹는다
       — 실제 `i2c`·`din` 레코드가 마스크와 무관하게 `quantity`·`value`나
       `connector_id`·`state` 를 늘 싣는 것과 같다. 이것 없이 재면 그
       레코드의 진짜 크기보다 작게 잡혀, 여유가 있다고 말하는데 실제로는
       없는 바로 그 실패가 된다(위 문단과 같은 이유).
    """
    if record_type in _CLOUD_KINDS:
        # 젯슨 링크 계약의 공통 필드(모듈 위 `_CLOUD_KINDS` 주석).
        rec: dict = {"schema_ver": 1, "device_id": "board-01",
                     "t": 1_700_000_000_000, "type": record_type}
    else:
        rec = {"schema_ver": 3, "seq": 4294967295, "t": 1_700_000_000_000,
               "type": record_type}
    for name in (*_LOCKED_BY_KIND.get(record_type, ()), *names):
        name = _WIRE_NAME.get(name, name)
        if name in ALWAYS_ON or name in rec:
            continue
        if name in _SAMPLE_FLOAT:
            rec[name] = round(_SAMPLE_FLOAT[name], float_digits)
        elif name in _SAMPLE:
            rec[name] = _SAMPLE[name]
        else:
            rec[name] = _UNKNOWN_PLACEHOLDER
    return rec


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

    # 🔴 대역폭보다 먼저 본다. 대역폭 초과는 일부라도 도착하지만 줄 상한
    #    초과는 한 줄도 안 도착한다 — 더 나쁜 쪽이 먼저 말해야 한다.
    if budget.record_dropped:
        json_bytes = budget.line_bytes - LINE_ENDING_BYTES
        return ("fault",
                f"보드 상한 {TELEM_RECORD_JSON_MAX} B 를 넘는다 "
                f"(지금 {json_bytes} B) — 이 레코드는 전선에 나가지 못하고 "
                f"통째로 버려진다. 필드를 꺼야 한다.")
    if budget.over_capacity:
        return ("fault",
                f"링크 용량을 넘는다 — {used} 필요, {cap} 가능 ({pct:.0f}%). "
                f"필드를 줄이거나 주기를 늘려야 한다.")
    if budget.tight:
        return ("warn",
                f"여유가 없다 — {used} / {cap} ({pct:.0f}%). "
                f"명령·응답·하트비트도 같은 선을 쓴다.")
    return ("ok", f"{used} / {cap} ({pct:.0f}%)")
