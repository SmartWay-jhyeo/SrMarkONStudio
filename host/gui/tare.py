"""영점 맞추기 — 지금 흐르는 전류를 그 채널의 0 점으로 삼는다.

🔴 PyQt6 를 import 하지 않는다 (CLAUDE.md `host/gui/` 의 원칙). 여기 있는
   것은 "무엇을 보여 주고 무엇을 바꿀 것인가" 뿐이고, 위젯은 이것을 부르기만
   한다. 덕분에 보정 규칙이 화면 없이 시험된다.

왜 필요한가
----------

4~20 mA 센서가 물리량 0 에서 정확히 4.000 mA 를 내지 않는다. 실기기에서
3.982 mA 가 나왔다 [2026-08-17]. 규격 §7.2.1 의 `value = (ma - zero) * scale`
에서 `zero` 가 바로 그 값을 담는 자리인데, 화면에서 그것을 채울 방법이
없었다 — 범위 칸은 4 mA 를 0 점으로 **가정**하고 계산한다.

그래서 손으로 맞추려면 `(4 - 3.982) * scale` 을 계산해 범위의 아래 칸에 적어야
했다. 0~150 센서 하나에 0.169 를 적어 넣는 일이라, 사실상 못 맞추는 상태였다.

🔴 스팬은 건드리지 않는다
------------------------

영점 잡기는 **한 점** 보정이다. 한 점으로는 기울기를 알 수 없다. 기울기까지
고치면 그것은 근거 없이 지어낸 값이고, 계기 보정의 표준 동작도 아니다
— 0 점만 옮기고 기울기는 두 점 보정으로 잡는다.

그 결과 범위 칸의 위쪽 값이 아주 조금 움직인다(0.018 mA 어긋난 센서에서
150 → 150.17). 그것이 **실제**다. 4 mA 에서 0 을 내지 않는 센서는 20 mA
에서도 150 을 내지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass

from host.core.scaling import LOOP_MIN_MA
from host.gui.settings_form import COL_ZERO, SettingsForm, matrix_of

#: 되돌릴 때 쓰는 값 — 루프의 살아 있는 0 점 (데이터시트 §5.3).
NOMINAL_ZERO_MA = LOOP_MIN_MA

#: 이보다 크게 어긋나면 화면이 눈에 띄게 표시한다. 4~20 mA 의 0.1% 다 —
#: 이 아래는 ADS1256 의 잡음과 구분되지 않는다(실측 ±0.09%).
NOTABLE_OFFSET_MA = 0.016


def tared_zero(measured_ma: float) -> float:
    """지금 흐르는 전류를 새 영점으로.

    🔴 인자가 하나뿐인 것이 설계다. 스케일을 받지 않으면 스케일을 바꿀 수도
       없다 — 위 "스팬은 건드리지 않는다" 를 코드가 강제한다.
    """
    return measured_ma


@dataclass(frozen=True)
class TareRow:
    """영점 패널의 한 줄."""

    index: int
    """커넥터 번호. J4 면 4."""

    label: str
    zero_key: str
    """`ain1.zero` — 보드로 나갈 설정 키."""

    zero_ma: float
    """지금 보드에 들어 있는 영점."""

    live_ma: float | None
    """지금 들어오는 전류. 값이 안 오면 `None`."""

    enabled: bool
    """채널이 켜져 있나."""

    editable: bool = True

    @property
    def offset(self) -> float | None:
        """지금 값과 설정된 영점의 차이.

        🔴 4 mA 가 아니라 **설정된 영점** 기준이다. 이미 잡아 둔 채널은
           차이가 0 으로 보여야 한다 — 4 mA 기준으로 재면 맞춰 놓은 채널이
           계속 어긋나 보여 사용자가 자꾸 다시 잡는다.
        """
        if self.live_ma is None:
            return None
        return self.live_ma - self.zero_ma

    @property
    def can_tare(self) -> bool:
        """🔴 값이 없으면 못 잡는다. 없는 값을 0 으로 치고 잡으면 영점이
           0 mA 로 내려앉아 그 채널의 측정값이 통째로 틀어지는데, 화면은
           아무 일도 없었던 것처럼 보인다.

        🔴 꺼진 채널도 못 잡는다. 값이 안 들어오니 결과적으로 같아 보이지만
           이유가 다르다 — 켜면 잡을 수 있다는 뜻이므로 화면이 그렇게
           말해야 한다. `blocked_reason` 이 둘을 가른다.
        """
        return self.editable and self.enabled and self.live_ma is not None

    @property
    def blocked_reason(self) -> str:
        """못 잡는 이유. 잡을 수 있으면 빈 문자열."""
        if not self.editable:
            return "고칠 수 없는 항목"
        if not self.enabled:
            return "채널 꺼짐"
        if self.live_ma is None:
            return "값 없음"
        return ""

    @property
    def notable(self) -> bool:
        """눈에 띄게 어긋났나."""
        off = self.offset
        return off is not None and abs(off) >= NOTABLE_OFFSET_MA


def tare_rows(form: SettingsForm,
              live_ma: dict[int, float | None]) -> tuple[TareRow, ...]:
    """설정 폼 + 지금 들어오는 전류 → 패널에 그릴 줄들.

    🔴 채널 목록을 여기 적지 않는다. 카탈로그에 있는 `ain*.zero` 가 곧
       목록이다 — 보드가 채널을 늘리면 패널도 따라 늘어난다
       (CLAUDE.md "설정 항목은 보드에만 넣는다").

    🔴 값이 안 오는 채널도 뺴지 않고 남긴다. 빠지면 "왜 J6 이 없지?" 가
       되고 그 답이 화면 어디에도 없다. 남겨 두고 못 잡는 이유를 보여 준다.
    """
    out: list[TareRow] = []
    for group in form.groups:
        matrix = matrix_of(group)
        if matrix is None or COL_ZERO not in matrix.columns:
            continue
        zi = matrix.columns.index(COL_ZERO)
        for mrow in matrix.rows:
            cell = mrow.cells[zi]
            if cell is None:
                continue
            try:
                zero = float(cell.value)
            except (TypeError, ValueError):
                # 반쯤 입력된 중이면 기준을 알 수 없다. 명목값으로 둔다 —
                # 지어낸 기준으로 "차이 0" 이라고 말하는 것보다 낫다.
                zero = NOMINAL_ZERO_MA
            out.append(TareRow(
                index=mrow.index,
                label=mrow.label,
                zero_key=cell.key,
                zero_ma=zero,
                live_ma=live_ma.get(mrow.index),
                enabled=_channel_on(form, cell.key),
                editable=cell.editable,
            ))
    return tuple(out)


def _channel_on(form: SettingsForm, zero_key: str) -> bool:
    """`ain1.zero` 옆의 `ain1.enabled` 를 본다."""
    prefix = zero_key.rsplit(".", 1)[0]
    for group in form.groups:
        for row in group.rows:
            if row.key == f"{prefix}.enabled":
                return row.value.strip().lower() in ("1", "true", "on", "yes")
    return True
