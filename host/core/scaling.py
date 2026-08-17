"""전류 ↔ 물리량 환산 — 규격 §7.2.1.

🔴 **이 식은 계약이다.** 화면은 사람이 읽는 물리량 범위(`4 mA일 때` ·
   `20 mA일 때`)를 받아 `ainN.zero` · `ainN.scale` 로 바꿔 보내고, 보드는
   그 두 값으로 `value` 를 만든다. 어느 쪽이 식을 혼자 바꾸면 화면이
   조용히 틀린 물리량을 말한다 — 값이 그럴듯해서 아무도 눈치채지 못한다.

왜 범위를 입력받나
-----------------

`zero` · `scale` 만 내놓으면 사용자가 `150 / 16 = 9.375` 를 손으로 계산해야
한다. 0~150 bar 센서를 다는 사람이 알고 있는 것은 `0` 과 `150` 이지 `9.375`
가 아니다. 화면이 그 나눗셈을 대신한다.
"""

from __future__ import annotations

#: 루프의 양 끝. 120 Ω 션트에 4~20 mA — 하드웨어 사실이지 설정이 아니다
#: (데이터시트 §5.3, 4 mA = 0.48 V, 20 mA = 2.40 V).
LOOP_MIN_MA = 4.0
LOOP_MAX_MA = 20.0
LOOP_SPAN_MA = LOOP_MAX_MA - LOOP_MIN_MA


def value_at(ma: float, zero: float, scale: float) -> float:
    """규격 §7.2.1 — `value = (ma - zero) * scale`."""
    return (ma - zero) * scale


def range_of(zero: float, scale: float) -> tuple[float, float] | None:
    """영점·스케일 → (4 mA 일 때, 20 mA 일 때).

    `scale` 이 0 이면 어떤 전류에도 물리량이 0 이라 범위를 되짚을 수 없다.
    그때 `None` 을 돌려준다 — 지어내지 않는다.
    """
    if not scale:
        return None
    return (value_at(LOOP_MIN_MA, zero, scale),
            value_at(LOOP_MAX_MA, zero, scale))


def zero_scale_for(v_low: float, v_high: float) -> tuple[float, float] | None:
    """(4 mA 일 때, 20 mA 일 때) → 영점·스케일.

    🔴 양 끝이 같으면 기울기가 없어 어떤 영점·스케일로도 표현할 수 없다.
       0 으로 나누다 죽는 대신 `None` 을 돌려주고, 화면이 그 사실을
       사용자에게 말한다.

    🔴 `v_low > v_high` 인 역동작 센서도 그대로 받는다 — 스케일이 음수가
       될 뿐이다. 막을 이유가 없다.
    """
    scale = (v_high - v_low) / LOOP_SPAN_MA
    if not scale:
        return None
    return (LOOP_MIN_MA - v_low / scale, scale)
