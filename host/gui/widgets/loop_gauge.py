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

from dataclasses import dataclass

#: 살아 있는 0 점. 이 아래는 측정값이 아니다.
LOOP_MIN_MA = 4.0
LOOP_MAX_MA = 20.0


@dataclass(frozen=True)
class LoopReading:
    ma: float
    #: 0.0~1.0. 단선이면 None — 그릴 바가 없다.
    fraction: float | None
    broken: bool
    over: bool
    label: str


def read_loop(ma: float) -> LoopReading:
    """전류값을 게이지가 그릴 수 있는 형태로 해석한다."""
    if ma < LOOP_MIN_MA:
        # 측정값이 아니다. 숫자를 보여주면 값으로 읽힌다.
        return LoopReading(ma=ma, fraction=None, broken=True, over=False,
                           label="루프 단선")

    if ma > LOOP_MAX_MA:
        # 과입력은 단선과 다른 고장이다. 바는 꽉 찬 채로 둔다.
        return LoopReading(ma=ma, fraction=1.0, broken=False, over=True,
                           label="과입력")

    span = LOOP_MAX_MA - LOOP_MIN_MA
    return LoopReading(
        ma=ma,
        fraction=(ma - LOOP_MIN_MA) / span,
        broken=False,
        over=False,
        label=f"{ma:.2f} mA",
    )
