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

from host.tests.fake_board import FakeItem, FakeStore


def _items() -> list[FakeItem]:
    return [
        # 🔴 인터록과 읽기 전용이 **둘 다** 걸린 항목. 그래야 어느 쪽이
        #    먼저 보고되는지 판가름할 수 있다 — 하나만 걸린 항목으로는
        #    순서를 확인할 수 없다.
        FakeItem("test.locked", "test", "bool", True, True,
                 readonly=True, interlocked=True, label="시험용 잠금",
                 note="시험용 인터록 — 안전 정책상 바꿀 수 없다"),
        FakeItem("test.plain", "test", "u16", 100, 100,
                 minimum=10, maximum=1000, label="시험용 값"),
    ]


@pytest.fixture(autouse=True)
def _isolated_config_snapshot(tmp_path, monkeypatch):
    """🔴 시험이 실전 보드 사본(data/board_config.json)을 오염시키지 못하게
    전 시험 자동 격리한다.

    실사고(2026-08-31): GUI 시험이 가짜 보드 카탈로그로 _load_catalog 를
    돌리면서 실전 경로에 스텁 스냅샷(port="스텁")을 남겼고, 새 보드 굽기
    직후의 설정 복원이 그 파일을 진짜 사본으로 믿어 **실보드에 스텁
    설정이 들어갔다**(온습도 꺼짐·valve 미지정·영점 엉터리). 복원 소스가
    "사본 우선" 인 설계에서 사본의 출처 격리는 선택이 아니다.
    """
    import host.core.config_snapshot as snap

    monkeypatch.setattr(snap, "DEFAULT_PATH", tmp_path / "board_config.json")


@pytest.fixture
def interlocked_items():
    """호출할 때마다 새 항목 목록을 만든다 (시험끼리 상태를 공유하지 않게)."""
    return _items


@pytest.fixture
def interlocked_store() -> FakeStore:
    return FakeStore(_items())
