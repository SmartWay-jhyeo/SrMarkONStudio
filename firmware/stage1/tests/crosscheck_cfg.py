"""C 가 낸 설정 카탈로그를 호스트가 그대로 읽는지 대조한다.

🔴 여기서는 **바이트가 아니라 구조**를 비교한다.

   프레이밍은 체크섬이 바이트 위에서 계산되므로 바이트가 계약이었다.
   카탈로그는 다르다 — 호스트가 JSON 으로 파싱해 `ConfigSchema` 를 만들고,
   화면은 그 구조만 본다. `4.0` 과 `4.0000` 은 같은 값이고, 어느 쪽으로
   내든 화면은 같다.

   그래서 계약은 "같은 바이트" 가 아니라 **"parse_catalog 가 같은 것을
   만든다"** 이다. 바이트를 강요하면 두 구현이 서로의 실수 표기까지
   따라가야 하고, 정작 중요한 것(타입·범위·읽기전용·허용값)은 놓친다.

   대신 **놓치기 쉬운 것을 여기서 못박는다** — vtype 이 위젯을 정하고,
   ro 가 편집 가능 여부를 정하고, choices 가 콤보를 만든다. 하나라도
   빠지면 화면이 잘못 그려진다.
"""
from __future__ import annotations

import subprocess
import sys
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

from host.core.config_schema import parse_catalog  # noqa: E402


def _find_binary() -> Path:
    for name in ("test_cfgwire.exe", "test_cfgwire"):
        p = HERE / name
        if p.exists():
            return p
    raise SystemExit("시험 바이너리가 없다. 먼저 run_tests.ps1 (또는 make) 을 돌려라.")


#: C 쪽 test_cfgwire.c 의 setup() 이 만드는 항목들. 이것이 기대값이다.
EXPECTED = {
    "dev.id": dict(group="dev", vtype="str", readonly=False,
                   default="1", current="1", maximum=15, minimum=None,
                   unit="", note="", choices=()),
    "tx.period_ms": dict(group="tx", vtype="u16", readonly=False,
                         default=100, current=100, minimum=10, maximum=10000,
                         unit="ms", note="", choices=()),
    "pwr.5v": dict(group="pwr", vtype="bool", readonly=True,
                   default=True, current=True, minimum=None, maximum=None,
                   unit="", choices=()),
    "adc.drate": dict(group="adc", vtype="enum", readonly=False,
                      default=60, current=60, minimum=None, maximum=None,
                      unit="SPS",
                      choices=(2, 5, 10, 15, 25, 30, 50, 60, 100, 500,
                               1000, 2000, 3750, 7500)),
    "ain0.zero": dict(group="ain", vtype="f32", readonly=False,
                      minimum=None, maximum=None, unit="mA", note="",
                      choices=()),
    # 🔴 인터록만 있고 읽기 전용은 아닌 항목. pwr.5v 는 둘 다 걸려 있어
    #    `ro` 를 readonly 로만 계산해도 참이 나오므로 구분이 안 된다.
    "pwr.24v": dict(group="pwr", vtype="bool", readonly=True,
                    default=False, current=False, minimum=None, maximum=None,
                    unit="", choices=()),
}

EXPECTED_FIELDS = {
    0: ("device_id", False),
    1: ("time_source", True),
    3: ("raw", True),
}


def main() -> int:
    lines = subprocess.run(
        [str(_find_binary()), "--catalog"],
        capture_output=True, text=True, encoding="utf-8", check=True,
    ).stdout.splitlines()
    lines = [ln for ln in lines if ln.strip()]

    schema = parse_catalog(lines)
    problems: list[str] = []

    if set(schema.items) != set(EXPECTED):
        problems.append(f"  항목이 다르다\n    C : {sorted(schema.items)}\n"
                        f"    기대: {sorted(EXPECTED)}")

    for key, want in EXPECTED.items():
        item = schema.items.get(key)
        if item is None:
            problems.append(f"  {key}: C 카탈로그에 없다")
            continue
        for attr, expected in want.items():
            got = getattr(item, attr)
            if attr == "current" and want["vtype"] == "f32":
                continue                      # 아래에서 따로 본다
            if got != expected:
                problems.append(
                    f"  {key}.{attr}\n    C : {got!r}\n    기대: {expected!r}")
        print(f"  ok   {key:14} vtype={item.vtype:5} ro={item.readonly} "
              f"choices={len(item.choices)}")

    # 실수는 값으로 본다 — 표기가 4.0 이든 4.0000 이든 같은 값이다.
    zero = schema.items.get("ain0.zero")
    if zero is not None and abs(float(zero.current) - 4.0) > 1e-6:
        problems.append(f"  ain0.zero.current = {zero.current!r} (기대 4.0)")

    if set(schema.fields) != set(EXPECTED_FIELDS):
        problems.append(f"  필드 비트가 다르다: {sorted(schema.fields)}")
    for bit, (name, default) in EXPECTED_FIELDS.items():
        fb = schema.fields.get(bit)
        if fb is None:
            problems.append(f"  비트 {bit} 이 없다")
        elif (fb.name, fb.default) != (name, default):
            problems.append(f"  비트 {bit}: {fb.name!r}/{fb.default} "
                            f"(기대 {name!r}/{default})")
    print(f"  ok   필드 비트 {len(schema.fields)}개")

    # 🔴 cfg_end 의 count 로 잘림을 판정한다(규격 §7.3).
    import json
    end = [json.loads(ln) for ln in lines if '"cfg_end"' in ln]
    if len(end) != 1:
        problems.append(f"  cfg_end 가 {len(end)}개다")
    elif end[0]["count"] != len(schema.items) + len(schema.fields):
        problems.append(f"  cfg_end.count={end[0]['count']} 인데 실제는 "
                        f"{len(schema.items) + len(schema.fields)}")
    else:
        print(f"  ok   cfg_end.count {end[0]['count']} — 잘리지 않았다")

    if problems:
        print("\n두 구현이 어긋난다:", file=sys.stderr)
        print("\n".join(problems), file=sys.stderr)
        return 1
    print(f"\nMATCH ({len(schema.items)} items, {len(schema.fields)} fields)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
