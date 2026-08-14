"""C 의 CRC-32 가 표준(zlib) 과 같은지 대조한다.

🔴 표준이 아니면 저장된 설정을 호스트 도구로 검사할 수 없다. 더 나쁜 것은,
   "표준 CRC-32 를 쓴다"고 적어 놓고 실제로는 다른 다항식이나 초기값을
   쓰는 경우다 — 그러면 보드에서만 맞고 어디서도 확인할 수 없다.

   `zlib.crc32` 는 우리 코드와 아무 관계 없는 독립 구현이다.
"""
from __future__ import annotations

import subprocess
import sys
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _find_binary() -> Path:
    for name in ("test_crc.exe", "test_crc"):
        p = HERE / name
        if p.exists():
            return p
    raise SystemExit("시험 바이너리가 없다. 먼저 run_tests.ps1 (또는 make) 을 돌려라.")


def main() -> int:
    out = subprocess.run(
        [str(_find_binary()), "--vectors"],
        capture_output=True, text=True, encoding="utf-8", check=True,
    ).stdout

    rows = [ln for ln in out.splitlines() if "\t" in ln]
    if not rows:
        raise SystemExit("--vectors 출력이 비었다.")

    bad: list[str] = []
    for row in rows:
        text, got = row.rsplit("\t", 1)
        want = f"{zlib.crc32(text.encode('utf-8')) & 0xFFFFFFFF:08X}"
        if got != want:
            bad.append(f"  {text!r}\n    C   : {got}\n    zlib: {want}")
        else:
            print(f"  ok   {text[:40]!r:44} {got}")

    if bad:
        print("\n표준 CRC-32 가 아니다:", file=sys.stderr)
        print("\n".join(bad), file=sys.stderr)
        return 1
    print(f"\nMATCH ({len(rows)} vectors)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
