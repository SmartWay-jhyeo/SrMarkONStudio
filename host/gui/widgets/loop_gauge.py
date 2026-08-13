"""4–20 mA 루프 게이지 — 이 화면의 시그니처.

🔴 **왜 4–20 mA 인가**

0–20 mA 였다면 "센서가 0 을 읽었다" 와 "선이 끊어졌다" 를 구분할 수 없다.
살아 있는 0 점을 4 mA 로 띄워 두면 그 아래로 떨어지는 것은 측정값이 아니라
**고장**이다. 산업용 계장에서 이 규격이 쓰이는 이유가 이것이고, 일반적인
대시보드 게이지는 이 구분을 표현하지 못한다.

그래서 게이지를 두 구간으로 그린다:

    ┃▓▓▓▓▓▓▓▓▓▓░░░░░░░░░░┃      4–20 mA : 실선 바
   ┌──┐                          4 mA 미만 : 사선 해칭
   │╱╱│                          — 색을 바꾸지 않는다. 색은 심각도에
   └──┘                            이미 쓰고 있고, 여기서 또 쓰면 의미가 겹친다

바늘이 해칭 구간에 들어가면 값 대신 `루프 단선` 을 쓴다. 숫자를 보여주면
그것이 측정값인 것처럼 읽히기 때문이다.

전기적 근거: 데이터시트 §5.3 — 120 Ω 0.1% 션트, 4 mA = 0.48 V,
20 mA = 2.40 V, ADS1256 외부 기준 2.5 V.
"""

import math
from dataclasses import dataclass

#: 살아 있는 0 점. 이 아래는 측정값이 아니다.
LOOP_MIN_MA = 4.0
LOOP_MAX_MA = 20.0


@dataclass(frozen=True)
class LoopReading:
    ma: float
    #: 🔴 **믿을 수 있는 정상 범위 측정일 때만 값이 들어간다.** 그 외에는 None.
    #:
    #: 단선·과입력·비유한값은 전부 None 이다. 위젯이 `fraction` 만 보고
    #: 순진하게 바를 그려도 **고장을 정상처럼 그릴 수가 없다.**
    #: `over` 플래그를 확인하는 것을 위젯의 성실함에 맡기지 않는다.
    fraction: float | None
    #: 바를 꽉 채워 그리고 싶을 때 쓴다. 과입력이면 1.0.
    #: 이 필드를 쓰려면 `over` 를 이미 확인했다는 뜻이다.
    bar_fraction: float
    broken: bool
    over: bool
    label: str


def read_loop(ma: float) -> LoopReading:
    """전류값을 게이지가 그릴 수 있는 형태로 해석한다."""
    # 🔴 NaN 은 두 비교를 모두 False 로 통과해 "정상" 분기로 들어간다.
    # 그러면 label 이 "nan mA" 가 되어 **확신에 찬 그럴듯한 측정값**처럼
    # 보인다. 단선은 fraction=None 이라 구조적으로 못 그리는데 NaN 은
    # 그 방어를 우회한다.
    if not math.isfinite(ma):
        return LoopReading(ma=ma, fraction=None, bar_fraction=0.0,
                           broken=True, over=False, label="값 없음")

    if ma < LOOP_MIN_MA:
        # 측정값이 아니다. 숫자를 보여주면 값으로 읽힌다.
        return LoopReading(ma=ma, fraction=None, bar_fraction=0.0,
                           broken=True, over=False, label="루프 단선")

    if ma > LOOP_MAX_MA:
        # 과입력은 단선과 다른 고장이다. 바는 꽉 채우되 fraction 은 비운다 —
        # 정상 만재(20.00 mA)와 데이터 형태가 같으면 안 된다.
        return LoopReading(ma=ma, fraction=None, bar_fraction=1.0,
                           broken=False, over=True, label="과입력")

    span = LOOP_MAX_MA - LOOP_MIN_MA
    f = (ma - LOOP_MIN_MA) / span
    return LoopReading(ma=ma, fraction=f, bar_fraction=f,
                       broken=False, over=False, label=f"{ma:.2f} mA")
