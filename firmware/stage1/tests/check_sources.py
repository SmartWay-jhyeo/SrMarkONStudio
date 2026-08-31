"""app/ 의 모든 모듈이 **빌드에도 시험에도** 들어가 있는지 본다.

🔴 목록이 두 벌이라 갈린다.

   보드용 이미지는 Makefile 의 `C_SRC` 로 빌드하고, 호스트 시험은
   run_tests.ps1 의 `$suites` 로 빌드한다. 새 모듈을 만들면 두 곳에 각각
   넣어야 하는데, 한쪽만 넣어도 그쪽은 멀쩡히 통과한다.

   실제로 겪었다 — mk_queue·mk_ads1256 을 시험 목록에만 넣었더니 C 시험
   9묶음이 전부 통과했다. 보드용 링크는 `undefined reference to
   mk_ads_queue` 로 깨져 있었는데, 그것은 ARM 빌드를 돌려 봐야만 나왔다.
   반대 방향이 더 나쁘다: 빌드에만 있고 시험에 없으면 아무 시험 없이
   보드에 실린다.

무엇을 보나
  1. app/*.c 가 전부 Makefile 의 C_SRC 에 있는가  (없으면 보드에 안 실린다)
  2. app/*.c 가 전부 run_tests.ps1 에 있는가       (없으면 시험되지 않는다)
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
STAGE = HERE.parent


def main() -> int:
    modules = sorted(p.name for p in (STAGE / "app").glob("*.c"))
    if not modules:
        print("app/ 에 .c 파일이 없다 — 경로가 바뀌었는지 확인한다", file=sys.stderr)
        return 2

    makefile = (STAGE / "Makefile").read_text(encoding="utf-8", errors="replace")
    runner = (HERE / "run_tests.ps1").read_text(encoding="utf-8", errors="replace")

    # C_SRC = \ ... 블록만 떼어 본다. 파일 어디에나 이름이 나오면 통과하는
    # 느슨한 검사로는, 주석에 적힌 이름까지 세어 버린다.
    m = re.search(r"^C_SRC\s*=\s*((?:.*\\\s*\n)*.*)$", makefile, re.MULTILINE)
    if m is None:
        print("Makefile 에서 C_SRC 블록을 못 찾았다 — 형식이 바뀌었다",
              file=sys.stderr)
        return 2
    c_src = m.group(1)

    problems: list[str] = []
    for name in modules:
        if name not in c_src:
            problems.append(f"  {name} 이 Makefile 의 C_SRC 에 없다 "
                            f"— 보드용 이미지에 안 실린다")
        if name not in runner:
            problems.append(f"  {name} 이 run_tests.ps1 에 없다 "
                            f"— 아무 시험도 이 파일을 링크하지 않는다")

    if problems:
        print("\n🔴 빌드 목록과 시험 목록이 어긋난다:", file=sys.stderr)
        print("\n".join(problems), file=sys.stderr)
        return 1

    print(f"  ok   app/ 모듈 {len(modules)}개가 빌드·시험 양쪽에 들어 있다")
    for name in modules:
        print(f"       {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
