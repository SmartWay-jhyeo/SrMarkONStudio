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
    """규격의 `schema_ver` 기본값이 계약(Cloud v1.7)의 1 이어야 한다.

    🔴 [정정, 2026-08-20] 예전에는 시뮬레이터의 상수를 봤다. 시뮬레이터가
       사라졌고, 애초에 규격의 짝은 **호스트가 실제로 파싱에 쓰는 값**이다.

    🔴 [전환기, 2026-08-31 — HANDOFF_0831 결정 2] 규격 §7.1 이 Cloud 계약
       형식(설정 `tx.schema_ver`, 기본 1)으로 개정됐다. 호스트 파서
       (`host/core/records.SCHEMA_VER` = 3)는 아직 굽힌 보드의 v3 를 읽는
       중이고 계획 2(records 재작성)에서 계약을 따라간다 — 그때 이 시험에
       호스트 상수와의 대조를 복원할 것.
    """
    text = _spec_text()
    m = re.search(
        r"\|\s*`schema_ver`\s*\|\s*int\s*\|\s*계약의 버전.*기본\s*\*\*(\d+)\*\*",
        text)
    assert m, "§7.1 에서 schema_ver 를 못 찾았다"
    assert int(m.group(1)) == 1, (
        f"규격의 schema_ver 기본은 계약대로 1 이어야 하는데 {m.group(1)} 다"
    )


# 🔴 [정리, 2026-08-20] `test_all_ndjson_uses_the_compact_wire_format` 과
#    `test_no_pin_names_on_the_wire` 는 여기서 걷어냈다.
#
#    둘 다 시뮬레이터가 **실제로 내보낸 줄**을 훑었다. 그 자리를 스텁이나
#    얼린 스냅샷으로 대신하면 검사 대상이 시험 자료 자신이 되어, 보드가 무엇을
#    내보내든 통과한다 — 없는 안전망보다 있다고 믿는 안전망이 나쁘다.
#
#    압축 형식은 `firmware/stage1/tests/crosscheck_json.py` 가 C 의 `mk_json`
#    출력을 `host/core` 와 바이트로 맞춰 지킨다.
#
#    🔴 핀 이름(설계 원칙 1)을 지키는 자리는 **지금 비어 있다.** 예전에는
#       카탈로그 라벨에 `(PD8)` 이 들어간 것을 이 시험이 잡았다. 같은 검사를
#       `firmware/stage1/app/mk_cfgtable.c` 의 라벨·사유 문자열에 대고 다시
#       세워야 한다 — 그것이 이제 라벨의 유일한 출처다.


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
    """§6.2 의 3000 ms 와 펌웨어의 상수가 같아야 한다.

    🔴 [정정, 2026-08-20] 예전에는 시뮬레이터의 `HB_TIMEOUT_MS` 를 봤다.
       그 시한을 실제로 지키는 것은 보드이므로, 짝은 펌웨어 헤더다.
    """
    header = (Path(__file__).resolve().parents[2]
              / "firmware" / "stage1" / "app" / "mk_hostlink.h")
    text = _spec_text()
    assert "3000 ms" in text, "§6 에서 3000 ms 를 못 찾았다"

    m = re.search(r"#define\s+MK_HB_TIMEOUT_MS\s+(\d+)",
                  header.read_text(encoding="utf-8"))
    assert m, f"{header.name} 에서 MK_HB_TIMEOUT_MS 를 못 찾았다"
    assert int(m.group(1)) == 3000, (
        f"규격은 3000 ms 인데 펌웨어는 {m.group(1)} 이다"
    )
