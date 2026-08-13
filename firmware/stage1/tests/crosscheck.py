"""C 프레이밍과 Python 프레이밍이 같은 답을 내는지 기계로 대조한다.

🔴 이 대조가 stage 1 프레이밍 계층의 존재 이유다. 보드와 호스트가 체크섬을
   다르게 계산하면 모든 명령이 조용히 거부되고, 원인은 프로토콜 어디에도
   드러나지 않는다.

C 시험 바이너리를 `--vectors` 로 돌려 그 출력을 Python 계산과 비교한다.
사람이 두 표를 눈으로 맞춰보는 방식은 조용히 실패한다.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO))

from host.core.framing import build_line, xor_checksum  # noqa: E402


def _find_binary() -> Path:
    for name in ("test_framing.exe", "test_framing"):
        p = HERE / name
        if p.exists():
            return p
    raise SystemExit("시험 바이너리가 없다. 먼저 run_tests.ps1 (또는 make) 을 돌려라.")


def main() -> int:
    binary = _find_binary()
    out = subprocess.run(
        [str(binary), "--vectors"], capture_output=True, text=True, check=True
    ).stdout

    rows = [ln for ln in out.splitlines() if ln.strip()]
    if not rows:
        raise SystemExit("--vectors 출력이 비었다.")

    mismatches = []
    for row in rows:
        payload, c_cs, c_len, c_line = row.split("\t")
        py_cs = f"{xor_checksum(payload):02X}"
        py_raw = build_line(payload)
        py_line = py_raw.replace("\r", "\\r").replace("\n", "\\n")
        py_len = str(len(py_raw))
        if (c_cs, c_len, c_line) != (py_cs, py_len, py_line):
            mismatches.append(
                f"  {payload}\n"
                f"    C : cs={c_cs} len={c_len} {c_line}\n"
                f"    py: cs={py_cs} len={py_len} {py_line}"
            )
        else:
            print(f"  ok   {payload:24} cs={c_cs} len={c_len}")

    if mismatches:
        print("\n두 구현이 어긋난다:", file=sys.stderr)
        print("\n".join(mismatches), file=sys.stderr)
        return 1
    print(f"\nMATCH ({len(rows)} vectors)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
