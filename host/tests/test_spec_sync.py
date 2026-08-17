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


def test_all_ndjson_uses_the_compact_wire_format():
    """🔴 전선 위 JSON 은 한 형식이어야 한다.

    json.dumps 기본값은 `", "` 와 `": "` 로 공백을 넣어 줄을 12% 늘린다.
    대역폭 계산(docs/measurements/2026-08-14_link_budget.md)에서 기본 설정이
    115200 의 94.8% 를 쓰고 있으므로 그 공백은 공짜가 아니다.

    더 중요한 것은 펌웨어의 `mk_json` 이 압축 형식만 낸다는 점이다. 두
    형식이 섞이면 C 와 Python 을 바이트로 대조할 수 없고, 대조가 없으면
    보드와 호스트가 갈려도 실기기에서만 드러난다.

    실제로 카탈로그(`cfg_item`·`cfg_field`·`cfg_end`)만 공백 형식이었고,
    C 쪽 대조 도구가 그것을 잡았다.
    """
    import json

    from host.core.framing import build_command
    from tools.simulator.config_store import default_store
    from tools.simulator.device_sim import DeviceSim

    sim = DeviceSim(default_store())
    sim.feed(build_command("HB"))

    lines: list[str] = []
    lines += sim.feed(build_command("CFG", "LIST"))
    lines += sim.feed(build_command("ID"))
    lines += sim.feed(build_command("STAT"))
    lines += sim.feed(build_command("CFG", "GET", "tx.period_ms"))
    lines += [ln for ln in sim.tick(1000) if ln.startswith("{")]

    records = [ln for ln in lines if ln.startswith("{")]
    assert len(records) > 50, "레코드를 충분히 모으지 못했다"

    for ln in records:
        want = json.dumps(json.loads(ln), ensure_ascii=False,
                          separators=(",", ":"))
        assert ln == want, (
            f"압축 형식이 아니다 — 공백이 들어 있다.\n"
            f"  받음: {ln[:120]}\n  기대: {want[:120]}"
        )


def test_no_pin_names_on_the_wire():
    """🔴 설계 원칙 1 — 핀 번호를 노출하지 않는다 (CLAUDE.md §3).

    화면과 프로토콜은 `PD8` 이 아니라 `24V 전원` 을 쓴다. PCB 배선은
    고정이므로 사용자가 핀을 알아야 할 이유가 없고, 알려 주면 임의로 바꿀
    수 있다는 인상을 준다 — 이 프로젝트는 핀 재배치 도구를 만들지 않는다.

    실제로 설정 카탈로그의 전원 항목 라벨에 `(PD8)` 이 들어가 있었고,
    GUI 를 띄워 보고서야 발견했다.
    """
    import re

    from host.core.framing import build_command
    from tools.simulator.config_store import default_store
    from tools.simulator.device_sim import DeviceSim

    sim = DeviceSim(default_store())
    sim.feed(build_command("HB"))

    lines: list[str] = []
    lines += sim.feed(build_command("CFG", "LIST"))
    lines += sim.feed(build_command("ID"))
    lines += sim.feed(build_command("STAT"))
    lines += sim.tick(1000)

    # PA0~PE15 꼴. 단어 경계를 두어 `PGA` 같은 것이 걸리지 않게 한다.
    pin = re.compile(r"\bP[A-K](?:1[0-5]|[0-9])\b")
    for ln in lines:
        found = pin.findall(ln)
        assert not found, f"전선에 핀 이름이 실렸다 {found}: {ln[:120]}"


def test_loop_ends_in_the_spec_match_the_code():
    """규격 §7.2.1 의 `scale = (v20 - v4) / 16` 과 코드의 두 끝점이 같은가.

    🔴 16 은 20 − 4 다. 문서에서 이 숫자를 고치고 코드를 안 고치면(또는
       반대면) 화면이 물리량을 조용히 틀리게 환산한다 — 값이 그럴듯해서
       아무도 눈치채지 못하는 종류의 어긋남이다.
    """
    from host.core.scaling import LOOP_MAX_MA, LOOP_MIN_MA, LOOP_SPAN_MA

    text = _spec_text()
    block = text[text.index("#### 7.2.1"):text.index("### 7.3")]
    assert "scale = (v20 - v4) / 16" in block
    assert LOOP_SPAN_MA == 16
    assert (LOOP_MIN_MA, LOOP_MAX_MA) == (4.0, 20.0)


def test_the_worked_example_in_the_spec_is_still_true():
    """규격이 든 예(0~150 bar → scale 9.375)를 코드로 다시 계산해 본다.

    문서의 예시는 사람이 읽고 믿는 것이라, 틀리면 그대로 잘못 설정된다.
    """
    from host.core.scaling import zero_scale_for

    text = _spec_text()
    block = text[text.index("#### 7.2.1"):text.index("### 7.3")]
    numbers = re.search(
        r"0~150 bar 센서 → `zero=([\d.]+)`, `scale=([\d.]+)`", block)
    assert numbers, "규격의 예시 문장이 바뀌었다 — 시험도 함께 고칠 것"
    assert zero_scale_for(0.0, 150.0) == (float(numbers.group(1)),
                                          float(numbers.group(2)))


def test_heartbeat_timeout_matches():
    """§6.2 의 3000 ms 와 시뮬레이터의 상수가 같아야 한다."""
    from tools.simulator import device_sim

    text = _spec_text()
    assert "3000 ms" in text, "§6 에서 3000 ms 를 못 찾았다"
    timeout = getattr(device_sim, "HB_TIMEOUT_MS", None)
    assert timeout is not None, "device_sim 에 HB_TIMEOUT_MS 가 없다"
    assert timeout == 3000, f"규격은 3000 ms 인데 device_sim 은 {timeout} 이다"
