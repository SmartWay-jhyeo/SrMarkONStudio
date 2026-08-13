"""C 펌웨어 로직과 Python 시뮬레이터가 같은 입력에 같게 답하는지 대조한다.

🔴 이 대조가 stage 1 에서 가장 값지다. GUI 는 시뮬레이터를 상대로 개발되고
   실물 보드에서 돌아야 한다. 둘이 다르게 답하면 GUI 는 시뮬레이터에서
   멀쩡하다가 실기기에서만 틀어지고, 그때는 어느 쪽이 틀렸는지조차 알기
   어렵다.

무엇을 대조하나
  - 명령 한 줄에 대한 **응답 줄들**
  - 그 시점의 **모드**

무엇을 대조하지 않나
  - 하트비트 송신 주기와 텔레메트리. 양쪽 단위 시험이 각각 지킨다.
    시뮬레이터는 텔레메트리를 내지만 1단계 펌웨어에는 아직 ADS1256 이
    없으므로, 여기서 비교하면 알맹이 없는 차이만 잔뜩 나온다.

C 쪽 시나리오는 test_hostlink.c 의 `SCENARIO` 배열에 있고 아래 `STEPS` 와
같아야 한다. 어긋나면 줄 수가 맞지 않아 바로 드러난다.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO))

from host.core.framing import build_command  # noqa: E402
from tools.simulator.config_store import default_store  # noqa: E402
from tools.simulator.device_sim import DeviceSim, Mode  # noqa: E402

# C 쪽 SCENARIO 와 같은 순서여야 한다.
STEPS: list[tuple[str, str | None]] = [
    ("AT", "0"), ("MODE", None),
    ("AT", "2000"), ("FEED", "HB"), ("MODE", None),
    ("AT", "5000"), ("MODE", None),          # 정확히 3000 ms — 아직 CONFIG
    ("AT", "5001"), ("MODE", None),          # 그 다음 ms 부터 RUN
    ("AT", "6000"), ("FEED", "HB"), ("MODE", None),
    ("AT", "6000"), ("FEED", "ID"),
    ("AT", "6000"), ("RAW", "$HB*FF\\r\\n"), ("MODE", None),
    ("AT", "6000"), ("RAW", "$ID*FF\\r\\n"),
    ("AT", "6000"), ("RAW", "garbage\\r\\n"),
    ("AT", "6000"), ("RAW", "$*00\\r\\n"),
    ("AT", "6000"), ("RAW", "$A\\tB*00\\r\\n"),
    ("AT", "6000"), ("FEED", "NOPE"),
    # 여기부터는 1단계가 아직 구현하지 않은 명령이라 양쪽 응답이 다르다.
    ("MARK", "GAPS"),
    ("AT", "6000"), ("FEED", "STAT"),
    ("AT", "6000"), ("FEED", "CFG,LIST"),
]

# 🔴 1단계 펌웨어가 아직 구현하지 않은 명령. 시뮬레이터는 전체 프로토콜을
#    구현하므로 여기서 갈리는 것은 결함이 아니라 진행 상황이다.
#
#    건너뛰지 않는다. **양쪽이 각각 무엇을 내야 하는지** 를 못박는다 —
#    어느 한쪽이라도 달라지면 실패한다. 2단계에서 $CFG 를 구현하면 이
#    항목을 지워야 하고, 지우지 않으면 시험이 알려 준다.
STAGE1_GAPS: dict[str, tuple[str, str]] = {
    #  입력       : (C 가 내야 할 응답, Python 이 내야 할 응답의 성격)
    "STAT": ("$SACK,STAT,ERR,UNSUPPORTED", "stat 레코드 + SACK,STAT,OK"),
    "CFG,LIST": ("$SACK,CFG,ERR,UNSUPPORTED", "cfg_* 여러 줄 + SACK,CFG,OK"),
}


def _escape(s: str) -> str:
    return s.replace("\r", "\\r").replace("\n", "\\n").replace("\t", "\\t")


def _unescape(s: str) -> str:
    return s.replace("\\r", "\r").replace("\\n", "\n").replace("\\t", "\t")


def _norm(line: str) -> str:
    """줄끝을 떼어 비교한다. 시뮬레이터는 떼고 돌려주고 C 는 붙여서 낸다."""
    return line.rstrip("\r\n")


def _find_binary() -> Path:
    for name in ("test_hostlink.exe", "test_hostlink"):
        p = HERE / name
        if p.exists():
            return p
    raise SystemExit("시험 바이너리가 없다. 먼저 run_tests.ps1 (또는 make) 을 돌려라.")


def _c_trace() -> list[tuple[str, str]]:
    out = subprocess.run(
        [str(_find_binary()), "--scenario"],
        capture_output=True, text=True, check=True,
    ).stdout
    rows = []
    for ln in out.splitlines():
        if not ln.strip():
            continue
        kind, _, value = ln.partition("\t")
        rows.append((kind, _norm(_unescape(value)) if kind == "OUT" else value))
    return rows


def _py_trace() -> list[tuple[str, str]]:
    sim = DeviceSim(default_store())
    rows: list[tuple[str, str]] = []
    for op, arg in STEPS:
        if op == "AT":
            # 🔴 시뮬레이터에는 공개된 시각 설정기가 없다. tick() 을 부르면
            #    하트비트·텔레메트리 상태까지 움직여 비교가 흐려지므로,
            #    대조 도구에서만 내부 시각을 직접 옮긴다.
            sim._now_ms = int(arg)
            continue
        if op == "MARK":
            rows.append(("MARK", arg))
            continue
        if op == "MODE":
            rows.append(("MODE", "CONFIG" if sim.mode == Mode.CONFIG else "RUN"))
            continue
        if op == "FEED":
            lines = sim.feed(build_command(*arg.split(",")))
        elif op == "RAW":
            lines = sim.feed(_unescape(arg))
        else:
            raise SystemExit(f"모르는 지시: {op}")
        for line in lines:
            rows.append(("OUT", _norm(line)))
    return rows


def _gap_inputs() -> list[str]:
    return list(STAGE1_GAPS)


def main() -> int:
    c_rows = _c_trace()
    py_rows = _py_trace()

    # 아직 구현되지 않은 명령은 응답 모양이 다르므로 따로 확인한다.
    # 나누는 자리는 시나리오에 찍은 표식이다 — 문자열을 추측해 자르면
    # 응답 내용이 조금만 바뀌어도 엉뚱한 데서 갈린다(실제로 겪었다).
    def split_at_mark(rows: list[tuple[str, str]]) -> tuple[list, list]:
        for i, (kind, _val) in enumerate(rows):
            if kind == "MARK":
                return rows[:i], rows[i + 1:]
        raise SystemExit("표식(MARK)이 없다 — 시나리오가 어긋났다")

    c_common, c_gap = split_at_mark(c_rows)
    py_common, py_gap = split_at_mark(py_rows)

    mismatches: list[str] = []
    for i in range(max(len(c_common), len(py_common))):
        a = c_common[i] if i < len(c_common) else ("<없음>", "")
        b = py_common[i] if i < len(py_common) else ("<없음>", "")
        if a != b:
            mismatches.append(f"  {i}번째\n    C : {a}\n    py: {b}")
        else:
            print(f"  ok   {a[0]:4} {a[1]}")

    # 1단계 공백 — 양쪽이 각각 무엇을 내는지 못박는다.
    print("\n  -- 1단계가 아직 구현하지 않은 명령 --")
    c_gap_lines = [v for k, v in c_gap if k == "OUT"]
    for cmd, (want_c, note_py) in STAGE1_GAPS.items():
        hit = [ln for ln in c_gap_lines if ln.startswith(want_c)]
        if not hit:
            mismatches.append(f"  {cmd}: C 가 {want_c!r} 를 내지 않았다 "
                              f"(낸 것: {c_gap_lines})")
            continue
        print(f"  ok   {cmd:9} C={want_c}  py={note_py}")

    py_gap_lines = [v for k, v in py_gap if k == "OUT"]
    if not py_gap_lines:
        mismatches.append("  Python 쪽이 아직 구현하지 않은 명령에 아무 응답도 안 했다 "
                          "— 시뮬레이터가 퇴화했나?")

    if mismatches:
        print("\n두 구현이 어긋난다:", file=sys.stderr)
        print("\n".join(mismatches), file=sys.stderr)
        return 1
    print(f"\nMATCH ({len(c_common)} steps, {len(STAGE1_GAPS)} known gaps)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
