"""C 의 i2c 레코드와 시뮬레이터의 build_i2c_record 를 바이트로 맞춘다.

🔴 두 구현이 갈리면 GUI 는 시뮬레이터에서 멀쩡하다가 보드에서만 틀어진다.
   카탈로그 대조(crosscheck_cfgtable.py)가 잡아낸 것과 같은 종류의 사고다.

C 쪽은 test_telem.exe --emit-i2c 가 out 버퍼에 세 벡터를 직접 채워
(mk_i2c 의 상태기계는 거치지 않는다 — 그건 test_i2c.exe 의 몫이다) 그대로
찍는다. Python 쪽은 같은 입력으로 tools/simulator/telemetry.py 의
build_i2c_record 를 불러 같은 딕셔너리를 만들고, crosscheck_json.py 와 같은
방식(정확한 십진 반올림)으로 문자열을 만들어 대조한다.
"""
from __future__ import annotations

import json
import math
import struct
import subprocess
import sys
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

# 🔴 콘솔 코드페이지에 기대지 않는다.
#
#    이 도구들은 한글과 `—` 를 찍는다. CP949 콘솔에서 돌면 글자가 깨지고,
#    환경에 따라서는 UnicodeEncodeError 로 죽는다. 그러면 논리는 멀쩡한데
#    시험이 실패한 것처럼 보인다 — 거짓 실패는 진짜 실패보다 나쁘다.
#    아무도 안 믿게 되기 때문이다.
#
#    부르는 쪽(run_tests.ps1, Makefile)에서 PYTHONUTF8 을 세우는 방법도
#    있지만, 그러면 손으로 직접 돌릴 때 다시 깨진다. 도구가 스스로 책임진다.
#    Codex 감사(2026-08-14 16:15)의 지적.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO))

from tools.simulator.config_store import default_store  # noqa: E402
from tools.simulator.telemetry import build_i2c_record  # noqa: E402


def _find_binary() -> Path:
    for name in ("test_telem.exe", "test_telem"):
        p = HERE / name
        if p.exists():
            return p
    raise SystemExit("시험 바이너리가 없다. 먼저 run_tests.ps1 (또는 make) 을 돌려라.")


class _Raw:
    """json.dumps 가 손대지 않고 그대로 흘려보낼 조각."""

    def __init__(self, text: str) -> None:
        self.text = text


class _Encoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, _Raw):
            return f"@@{id(o)}@@"
        return super().default(o)


def f32(value: float, digits: int) -> _Raw:
    """C 의 mk_json_f32 가 내야 할 십진 문자열.

    🔴 crosscheck_json.py 의 f32 와 같은 이유로 같은 방식을 쓴다 — C 의
       절차(스케일 → +0.5 → 정수 절단)를 그대로 베끼면 독립 검증이 아니다.
       Decimal 로 정확한 십진값을 0 에서 먼 쪽으로 반올림한다.
    """
    v = struct.unpack("<f", struct.pack("<f", value))[0]
    if not math.isfinite(v):
        return _Raw("null")
    if not (-9.0e18 < v * (10 ** digits) < 9.0e18):
        return _Raw("null")

    exact = Decimal(v)                      # float32 의 정확한 이진값
    quantum = Decimal(1).scaleb(-digits)
    rounded = exact.quantize(quantum, rounding=ROUND_HALF_UP)

    mag = rounded.copy_abs()
    neg = rounded.is_signed() and mag != 0
    return _Raw(("-" if neg else "") + f"{mag:.{digits}f}")


def dumps(obj: dict) -> str:
    """C 와 같은 압축 형식으로. _Raw 조각은 그대로 박아 넣는다."""
    raws = {f"@@{id(v)}@@": v.text for v in obj.values() if isinstance(v, _Raw)}
    text = json.dumps(obj, ensure_ascii=False, separators=(",", ":"), cls=_Encoder)
    for placeholder, literal in raws.items():
        text = text.replace(f'"{placeholder}"', literal)
    return text


#: C 쪽 test_telem.exe --emit-i2c (emit_i2c()) 이 정확히 이 세 벡터를,
#: 이 순서로 낸다. seq 는 이 함수 호출 전에 ain 이 한 번도 안 나가므로
#: (설정된 ADS 큐가 비어 있다) 1 부터 하나씩 오른다.
VECTORS = [
    dict(connector_id=10, quantity="lux", seq=1, t_ms=1000, value=401.5, status=0),
    dict(connector_id=11, quantity="", seq=2, t_ms=2000, value=None, status=1),
    dict(connector_id=12, quantity="", seq=3, t_ms=3000, value=None, status=3),
]


def main() -> int:
    out = subprocess.run(
        [str(_find_binary()), "--emit-i2c"],
        capture_output=True, text=True, check=True,
    ).stdout

    got_lines = [ln for ln in out.splitlines() if ln.strip()]

    # 시뮬레이터와 같은 카탈로그 경로 — default_store() 는 crosscheck_cfgtable.py
    # 도 쓰는, $CFG,LIST 뒤에 남는 것과 같은 기본값이다.
    store = default_store()
    digits = int(store.get("tx.float_digits"))

    mismatches: list[str] = []
    for i, vec in enumerate(VECTORS):
        rec = build_i2c_record(
            store,
            connector_id=vec["connector_id"],
            quantity=vec["quantity"],
            seq=vec["seq"],
            t_ms=vec["t_ms"],
            value=vec["value"],
            status=vec["status"],
        )
        # value 는 build_i2c_record 가 round() 로 접어 두는데, C 와 바이트
        # 단위로 맞추려면 crosscheck_json.py 와 같은 고정 자릿수 십진
        # 문자열이어야 한다 — round() + json.dumps 는 끝의 0 을 잘라낸다
        # (예: 401.5000 이 아니라 401.5).
        if rec.get("value") is not None:
            rec["value"] = f32(vec["value"], digits)
        want = dumps(rec)

        if i >= len(got_lines):
            mismatches.append(f"  벡터 {i}: C 출력에 줄이 없다")
            continue
        got = got_lines[i]
        if got != want:
            mismatches.append(f"  벡터 {i}\n    C : {got}\n    py: {want}")
            continue
        try:
            json.loads(got)
        except Exception as exc:
            mismatches.append(f"  벡터 {i}: C 출력이 JSON 으로 파싱되지 않는다: {exc}")
            continue
        print(f"  ok   벡터 {i} ({vec['quantity'] or '(없음)'!s:8}) {len(got):4}B")

    if len(got_lines) > len(VECTORS):
        mismatches.append(f"  C 만 내놓은 줄이 있다: {got_lines[len(VECTORS):]}")

    if mismatches:
        print("\n두 구현이 어긋난다:", file=sys.stderr)
        print("\n".join(mismatches), file=sys.stderr)
        return 1
    print(f"\nMATCH ({len(VECTORS)} records)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
