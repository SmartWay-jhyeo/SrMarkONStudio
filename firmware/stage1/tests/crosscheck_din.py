"""C 의 din 레코드와 시뮬레이터의 build_din_record 를 바이트로 맞춘다.

🔴 [검토 지적 I2] crosscheck_i2c.py 가 C 의 i2c 레코드와 시뮬레이터의
   build_i2c_record 를 바이트로 대조하는 선례가 있는데, din 에는 그게
   없었다 — 필드 순서 일치의 유일한 근거가 mk_telem.c 와 telemetry.py
   양쪽 주석에 손으로 옮겨 적은 문장뿐이었다. 두 구현이 갈리면 GUI 는
   시뮬레이터에서 멀쩡하다가 보드에서만 틀어진다 — CLAUDE.md §0 이
   "두 곳을 함께 고쳐야 한다" 고 못박은 바로 그 자리다.

C 쪽은 test_telem.exe --emit-din 이 out 버퍼에 세 벡터를 직접 채워
(mk_solctl 의 디바운스 상태기계는 거치지 않는다 — 그건 test_sol.c 의 몫이다)
그대로 찍는다. Python 쪽은 같은 입력으로 tools/simulator/telemetry.py 의
build_din_record 를 불러 같은 딕셔너리를 만들고, crosscheck_i2c.py 와 같은
방식으로 문자열을 만들어 대조한다. din 은 float 필드가 없어(connector_id·
state 가 전부 정수다) crosscheck_i2c.py 의 f32() 십진 반올림은 필요 없다.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

# 🔴 콘솔 코드페이지에 기대지 않는다 — crosscheck_i2c.py 와 같은 이유
#    (Codex 감사 2026-08-14 16:15).
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO))

from tools.simulator.config_store import default_store  # noqa: E402
from tools.simulator.telemetry import build_din_record  # noqa: E402


def _find_binary() -> Path:
    for name in ("test_telem.exe", "test_telem"):
        p = HERE / name
        if p.exists():
            return p
    raise SystemExit("시험 바이너리가 없다. 먼저 run_tests.ps1 (또는 make) 을 돌려라.")


def dumps(obj: dict) -> str:
    """C 와 같은 압축 형식으로."""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


#: C 쪽 test_telem.exe --emit-din (emit_din()) 이 정확히 이 세 벡터를,
#: 이 순서로 낸다. seq 는 이 함수 호출 전에 ain·i2c 가 한 번도 안 나가므로
#: (setup() 이 만든 ADS 큐가 비어 있고 i2c 는 attach 하지 않는다) 1 부터
#: 하나씩 오른다. 켜짐(state 1)·꺼짐(state 0)·세 커넥터(18·19·20)가 모두
#: 나오도록 골랐다.
VECTORS = [
    dict(connector_id=18, state=1, seq=1, t_ms=1000),
    dict(connector_id=19, state=0, seq=2, t_ms=2000),
    dict(connector_id=20, state=1, seq=3, t_ms=3000),
]


def main() -> int:
    out = subprocess.run(
        [str(_find_binary()), "--emit-din"],
        capture_output=True, text=True, check=True,
    ).stdout

    got_lines = [ln for ln in out.splitlines() if ln.strip()]

    # 시뮬레이터와 같은 카탈로그 경로 — default_store() 는 crosscheck_cfgtable.py·
    # crosscheck_i2c.py 도 쓰는, $CFG,LIST 뒤에 남는 것과 같은 기본값이다.
    store = default_store()

    mismatches: list[str] = []
    for i, vec in enumerate(VECTORS):
        rec = build_din_record(
            store,
            connector_id=vec["connector_id"],
            state=vec["state"],
            seq=vec["seq"],
            t_ms=vec["t_ms"],
        )
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
        print(f"  ok   벡터 {i} (connector={vec['connector_id']} "
              f"state={vec['state']}) {len(got):4}B")

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
