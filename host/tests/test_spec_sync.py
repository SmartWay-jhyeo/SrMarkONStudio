"""규격 문서와 호스트 코드가 갈리지 않았는지 검사한다.

`protocol/specification.md` 는 펌웨어와 호스트의 공통 계약이자 **권위**다.
그런데 문서는 사람이 고치고 코드는 따로 고치므로, 둘이 조용히 갈린다.
갈린 결과는 대개 "보드가 응답을 안 한다" 로 나타나고 원인은 어디에도
드러나지 않는다.

여기서는 문서에서 값을 뽑아 코드와 대조한다. 문서를 고치면 시험이
따라오고, 코드만 고치면 시험이 막는다.
"""
from __future__ import annotations

import re
from pathlib import Path

from host.core.errors import Reason

SPEC = Path(__file__).resolve().parents[2] / "protocol" / "specification.md"


def _spec_text() -> str:
    return SPEC.read_text(encoding="utf-8")


def test_spec_exists():
    """규격이 옮겨가거나 사라지면 아래 대조가 조용히 통과하는 것을 막는다."""
    assert SPEC.exists(), f"규격 문서를 찾을 수 없다: {SPEC}"


def _reasons_in_spec() -> list[str]:
    """§5 의 사유 표에서 사유 이름을 뽑는다.

    표의 형태: `| \\`RANGE\\` | 값이 허용 범위 밖 |`
    """
    text = _spec_text()
    start = text.index("## 5. 보드 → 호스트 응답")
    end = text.index("### 5.1")
    table = text[start:end]
    return re.findall(r"^\|\s*`([A-Z_]+)`\s*\|", table, re.M)


def test_reason_list_matches_spec():
    """규격 §5 의 사유 표와 `Reason.ALL` 이 같아야 한다."""
    spec_reasons = _reasons_in_spec()
    assert spec_reasons, "규격 §5 에서 사유를 하나도 못 뽑았다 — 표 형식이 바뀌었나?"
    assert sorted(spec_reasons) == sorted(Reason.ALL), (
        f"규격과 코드가 갈렸다.\n"
        f"  규격에만: {sorted(set(spec_reasons) - set(Reason.ALL))}\n"
        f"  코드에만: {sorted(set(Reason.ALL) - set(spec_reasons))}"
    )


def test_reason_count_claim_matches():
    """규격 본문의 '이 N개가 전부다' 가 실제 개수와 맞아야 한다.

    숫자를 손으로 적어 둔 문장이라 사유를 늘릴 때 잊기 쉽다.
    """
    text = _spec_text()
    m = re.search(r"이 (\d+)개가 전부다", text)
    assert m, "'이 N개가 전부다' 문장을 못 찾았다"
    assert int(m.group(1)) == len(Reason.ALL), (
        f"규격은 {m.group(1)}개라 하는데 Reason.ALL 은 {len(Reason.ALL)}개다"
    )


def test_reason_constants_equal_their_names():
    """`Reason.RANGE == "RANGE"` — 전선에 나가는 문자열이 이름과 같아야 한다."""
    for name in Reason.ALL:
        assert getattr(Reason, name) == name, f"{name} 의 값이 이름과 다르다"


def test_schema_ver_matches():
    """규격의 `schema_ver` 와 시뮬레이터가 쓰는 값이 같아야 한다."""
    from tools.simulator.device_sim import SCHEMA_VER

    text = _spec_text()
    m = re.search(r"\|\s*`schema_ver`\s*\|\s*int\s*\|\s*항상\s*(\d+)\s*\|", text)
    assert m, "§7.1 에서 schema_ver 를 못 찾았다"
    assert int(m.group(1)) == SCHEMA_VER, (
        f"규격은 schema_ver {m.group(1)} 인데 시뮬레이터는 {SCHEMA_VER} 다"
    )


def test_heartbeat_timeout_matches():
    """§6.2 의 3000 ms 와 시뮬레이터의 상수가 같아야 한다."""
    from tools.simulator import device_sim

    text = _spec_text()
    assert "3000 ms" in text, "§6 에서 3000 ms 를 못 찾았다"
    timeout = getattr(device_sim, "HB_TIMEOUT_MS", None)
    assert timeout is not None, "device_sim 에 HB_TIMEOUT_MS 가 없다"
    assert timeout == 3000, f"규격은 3000 ms 인데 device_sim 은 {timeout} 이다"
