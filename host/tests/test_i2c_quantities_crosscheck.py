"""`host.gui.screen.I2C_KIND_QUANTITIES` 와 `tools.simulator.config_store.I2C_QUANTITIES`
가 같은 표인지 대조한다.

🔴 **왜 이 대조가 필요한가**

   종류 → 양 표가 이제 **세 곳**에 있다: 펌웨어(`mk_i2c.c`), 시뮬레이터
   (`config_store.I2C_QUANTITIES`), 그리고 이번에 새로 생긴 호스트
   (`screen.I2C_KIND_QUANTITIES`, GUI 카드를 미리 까는 `seed_sensors()`가
   쓴다). 앞의 둘은 `firmware/stage1/tests/crosscheck_i2c_quantities.py`
   가 자동으로 맞춰 보지만, 호스트 표는 **아무도 대조하지 않았다** — GUI
   작업(`i2c-cards-report.md` 판단 1번)에서 사람이 옮겨 적었을 뿐이다.

   갈리면 화면이 조용히 틀어진다. 예를 들어 온습도(종류 2)가 호스트
   표에서 `("temp",)` 하나로 줄면, 보드는 `temp`·`humidity` 두 레코드를
   보내는데 카드는 한 장만 뜬다 — 값이 있는데 화면에 안 보이는 것과
   같다. 반대로 순서가 `("humidity", "temp")` 로 뒤집히면 `seed_sensors`
   가 까는 카드 순서가 실제 레코드 도착 순서와 어긋나 카드가 자리를
   바꿔 보인다(다른 시험 `test_order_is_stable_across_updates` 가 지키는
   바로 그 성질이 깨진다).

🔴 **`screen.py` 는 시뮬레이터를 import 하면 안 된다**

   `host/tests/test_layer_boundaries.py` 가 `host/gui` 전체를 Qt-free 로
   묶어 지키듯, `host/gui` 는 `tools/simulator` 도 몰라야 한다 — 그건
   시험용 대역이지 호스트 코드가 기대는 곳이 아니다. 그래서 이 대조는
   **시험 파일 안에서만** 양쪽을 import 한다. `screen.py` 자체는 그대로
   자기 표를 갖고 있는다.
"""
from __future__ import annotations

from host.gui.screen import I2C_KIND_NONE as HOST_I2C_KIND_NONE
from host.gui.screen import I2C_KIND_QUANTITIES
from tools.simulator.config_store import I2C_KIND_NONE as SIM_I2C_KIND_NONE
from tools.simulator.config_store import I2C_QUANTITIES


def test_kind_none_value_matches():
    """"미지정" 이 가리키는 정수값 자체가 갈리면 그 뒤 비교가 무의미하다."""
    assert HOST_I2C_KIND_NONE == SIM_I2C_KIND_NONE


def test_same_set_of_kinds():
    """한쪽에만 있는 종류를 잡는다 — 새 종류를 한쪽에만 추가하면 여기서 걸린다."""
    host_kinds = set(I2C_KIND_QUANTITIES)
    sim_kinds = set(I2C_QUANTITIES)
    only_host = host_kinds - sim_kinds
    only_sim = sim_kinds - host_kinds
    assert not only_host, f"호스트에만 있는 종류: {sorted(only_host)}"
    assert not only_sim, f"시뮬레이터에만 있는 종류: {sorted(only_sim)}"


def test_quantities_match_including_order():
    """양의 개수뿐 아니라 **순서**까지 같아야 한다.

    온습도(종류 2)처럼 값을 둘 내는 종류에서 `temp`·`humidity` 순서가
    갈리면, `seed_sensors()` 가 미리 까는 카드 순서가 실제 보드가 보내는
    레코드 순서와 어긋나 카드가 자리를 바꾼 것처럼 보인다.
    """
    mismatches = []
    for kind in sorted(set(I2C_KIND_QUANTITIES) | set(I2C_QUANTITIES)):
        host = I2C_KIND_QUANTITIES.get(kind)
        sim = I2C_QUANTITIES.get(kind)
        if host != sim:
            mismatches.append((kind, host, sim))
    assert not mismatches, (
        "호스트/시뮬레이터 종류→양 표가 어긋난다 "
        "(종류, 호스트, 시뮬레이터): " + repr(mismatches)
    )
