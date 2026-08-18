"""C 의 종류→양 표(mk_i2c_kind_quantities)와 시뮬레이터의 I2C_QUANTITIES 를 맞춘다.

🔴 두 구현이 갈리면 GUI 는 시뮬레이터에서 멀쩡하다가 보드에서만 틀어진다
   (crosscheck_cfgtable.py 와 같은 이유). 지금은 조도 하나뿐이라 눈으로도
   맞았지만, 온습도처럼 값이 둘인 종류가 늘거나 순서가 갈려도 아무도
   못 잡는다 — 검토 지적 C1.

C 쪽은 test_i2c.exe --print-quantities 가 "종류,양1,양2..." 를 종류마다
한 줄씩 찍는다. Python 쪽은 tools/simulator/config_store.py 의
I2C_QUANTITIES 를 그대로 쓴다 — 그것이 시뮬레이터가 실제로 쓰는 표다.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# 🔴 콘솔 코드페이지에 기대지 않는다. crosscheck_i2c.py 와 같은 이유
#    (CP949 콘솔에서 한글·— 가 깨지거나 UnicodeEncodeError 로 죽는다).
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO))

from tools.simulator.config_store import I2C_QUANTITIES  # noqa: E402


def _find_binary() -> Path:
    for name in ("test_i2c.exe", "test_i2c"):
        p = HERE / name
        if p.exists():
            return p
    raise SystemExit("시험 바이너리가 없다. 먼저 run_tests.ps1 (또는 make) 을 돌려라.")


def main() -> int:
    out = subprocess.run(
        [str(_find_binary()), "--print-quantities"],
        capture_output=True, text=True, encoding="utf-8", check=True,
    ).stdout

    fw: dict[int, tuple[str, ...]] = {}
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(",")
        fw[int(parts[0])] = tuple(parts[1:])

    problems: list[str] = []
    kinds = sorted(set(fw) | set(I2C_QUANTITIES))
    for kind in kinds:
        a = fw.get(kind)
        b = I2C_QUANTITIES.get(kind)
        if a is None:
            problems.append(f"  종류 {kind}: 펌웨어 표에 없다 (시뮬레이터={b})")
        elif b is None:
            problems.append(f"  종류 {kind}: 시뮬레이터 표에 없다 (펌웨어={a})")
        elif a != b:
            problems.append(f"  종류 {kind}: 펌웨어={a} 시뮬레이터={b}")

    if problems:
        print("펌웨어와 시뮬레이터의 종류→양 표가 어긋난다:", file=sys.stderr)
        print("\n".join(problems), file=sys.stderr)
        return 1

    for kind in kinds:
        print(f"  ok   종류 {kind:<2} {fw[kind]}")
    print(f"\nMATCH ({len(kinds)} kinds)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
