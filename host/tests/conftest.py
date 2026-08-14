"""시험 공용 픽스처.

🔴 인터록을 시험하는 항목을 여기서 **만든다.** 제품 설정표에서 빌리지
   않는다.

   예전에는 `pwr.5v` 가 유일한 인터록 항목이라 여러 시험이 그것을 예시로
   썼다. 그러다 사용자가 "5V 도 끌 수 있어야 한다" 고 하면서 인터록 항목이
   하나도 남지 않았고, 그 순간 규격 §5.2 의 검증 순서(인터록이 읽기 전용
   보다 앞)를 확인하는 시험이 통째로 사라질 뻔했다.

   검증 순서는 **규격이 정한 계약**이다. 그것을 쓰는 제품 항목이 지금
   없더라도 계약은 유효하고, 나중에 인터록이 필요한 항목이 생겼을 때
   동작해야 한다. 그래서 시험이 자기 항목을 들고 있는다.
"""
from __future__ import annotations

import pytest

from tools.simulator.config_store import ConfigStore, SimConfigItem


def _items() -> list[SimConfigItem]:
    return [
        # 🔴 인터록과 읽기 전용이 **둘 다** 걸린 항목. 그래야 어느 쪽이
        #    먼저 보고되는지 판가름할 수 있다 — 하나만 걸린 항목으로는
        #    순서를 확인할 수 없다.
        SimConfigItem("test.locked", "test", "bool", True, True,
                      readonly=True, interlocked=True, label="시험용 잠금",
                      note="시험용 인터록 — 안전 정책상 바꿀 수 없다"),
        SimConfigItem("test.plain", "test", "u16", 100, 100,
                      minimum=10, maximum=1000, label="시험용 값"),
    ]


@pytest.fixture
def interlocked_items():
    """호출할 때마다 새 항목 목록을 만든다 (시험끼리 상태를 공유하지 않게)."""
    return _items


@pytest.fixture
def interlocked_store() -> ConfigStore:
    return ConfigStore(_items())
