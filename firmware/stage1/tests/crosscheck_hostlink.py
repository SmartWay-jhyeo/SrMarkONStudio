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
    ("CMD", "STAT"), ("AT", "6000"), ("FEED", "STAT"),
    ("CMD", "CFG,LIST"), ("AT", "6000"), ("FEED", "CFG,LIST"),
]

# 🔴 1단계 펌웨어가 아직 구현하지 않은 명령. 시뮬레이터는 전체 프로토콜을
#    구현하므로 여기서 갈리는 것은 결함이 아니라 진행 상황이다.
#
#    건너뛰지 않는다. **양쪽이 각각 무엇을 내야 하는지** 를 못박는다 —
#    어느 한쪽이라도 달라지면 실패한다. 2단계에서 $CFG 를 구현하면 이
#    항목을 지워야 하고, 지우지 않으면 시험이 알려 준다.
#    각 항목: 입력 -> (C 가 내야 할 줄들, Python 이 내야 할 줄들의 조건)
#
#    🔴 명령별로 **짝지어** 확인한다. 응답을 한 통에 모아 놓고 "어딘가에
#       있다" 로 확인하면, STAT 의 응답과 CFG 의 응답이 뒤바뀌어도 통과한다.
#       처음에 그렇게 짜 두었다가 리뷰에서 지적받았다.
#    C 쪽 기대값은 payload 로 적는다 — 체크섬을 손으로 적으면 어긋난다.
#    Python 쪽은 응답에 반드시 들어 있어야 할 조각들로 적는다.
#
#    🔴 조각을 압축 형식(`"type":"stat"`)으로 적는 것이 요점 중 하나다.
#       처음 대조했을 때 시뮬레이터의 카탈로그만 공백 있는 JSON 을 내고
#       있었고, 이 대조가 그것을 잡았다. 대역폭이 94.8% 인 링크에서 공백은
#       공짜가 아니고, 펌웨어의 mk_json 은 압축 형식만 낸다.
STAGE1_GAPS: dict[str, tuple[list[str], str, list[str]]] = {
    "STAT": (
        ["SACK,STAT,ERR,UNSUPPORTED"],
        "stat 레코드 1줄 + $SACK,STAT,OK",
        ['"type":"stat"', "SACK,STAT,OK"],
    ),
    "CFG,LIST": (
        ["SACK,CFG,ERR,UNSUPPORTED"],
        "cfg_item·cfg_field 여러 줄 + cfg_end + $SACK,CFG,OK",
        ['"type":"cfg_item"', '"type":"cfg_end"', "SACK,CFG,OK"],
    ),
}


def _escape(s: str) -> str:
    return s.replace("\r", "\\r").replace("\n", "\\n").replace("\t", "\\t")


def _unescape(s: str) -> str:
    return s.replace("\\r", "\r").replace("\\n", "\n").replace("\\t", "\t")


def _norm(line: str) -> str:
    """줄끝을 떼어 비교한다. 시뮬레이터는 떼고 돌려주고 C 는 붙여서 낸다."""
    return line.rstrip("\r\n")


def _payload_of(line: str) -> str:
    """`$<payload>*<CS>` 에서 payload 만. `$` 줄이 아니면 그대로 돌려준다."""
    if not line.startswith("$"):
        return line
    star = line.rfind("*")
    return line[1:star] if star > 0 else line[1:]


def _group_by_command(rows: list[tuple[str, str]], side: str) -> dict[str, list[str]]:
    """`CMD` 표식으로 구간을 나눠 명령별 응답 줄들을 모은다.

    🔴 응답을 한 통에 모아 두면 "어딘가에 있다" 로만 확인하게 되고, 두
       명령의 응답이 뒤바뀌어도 통과한다.
    """
    out: dict[str, list[str]] = {}
    current: str | None = None
    for kind, value in rows:
        if kind == "CMD":
            current = value
            out.setdefault(current, [])
        elif kind == "OUT" and current is not None:
            out[current].append(value)
    if not out:
        raise SystemExit(f"{side} 쪽 기록에 CMD 표식이 없다 — 시나리오가 어긋났다")
    return out


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
        if op in ("MARK", "CMD"):
            rows.append((op, arg))
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

    # 1단계 공백 — 양쪽이 각각 무엇을 내는지 명령별로 짝지어 못박는다.
    print("\n  -- 1단계가 아직 구현하지 않은 명령 --")
    c_by_cmd = _group_by_command(c_gap, "C")
    py_by_cmd = _group_by_command(py_gap, "py")

    for cmd, (want_c, note_py, py_needles) in STAGE1_GAPS.items():
        got_c_raw = c_by_cmd.get(cmd)
        # payload 만 비교한다. 체크섬은 프레이밍 계층(crosscheck.py)이 이미
        # 지키고 있고, 여기서 손으로 적으면 어긋난다.
        got_c = ([_payload_of(ln) for ln in got_c_raw]
                 if got_c_raw is not None else None)
        if got_c is None:
            mismatches.append(f"  {cmd}: C 쪽 기록에 이 명령 구간이 없다")
        elif got_c != want_c:
            mismatches.append(f"  {cmd}: C 응답이 다르다\n"
                              f"    기대: {want_c}\n    받음: {got_c}")

        got_py = py_by_cmd.get(cmd)
        if got_py is None:
            mismatches.append(f"  {cmd}: Python 쪽 기록에 이 명령 구간이 없다")
        else:
            joined = "\n".join(got_py)
            missing = [n for n in py_needles if n not in joined]
            if missing:
                mismatches.append(
                    f"  {cmd}: Python 응답에 있어야 할 것이 없다 {missing}\n"
                    f"    받음 {len(got_py)}줄: {got_py[:2]}...")

        if got_c == want_c and got_py is not None and not missing:
            print(f"  ok   {cmd:9} C={want_c[0]}  py={note_py}")

    if mismatches:
        print("\n두 구현이 어긋난다:", file=sys.stderr)
        print("\n".join(mismatches), file=sys.stderr)
        return 1
    print(f"\nMATCH ({len(c_common)} steps, {len(STAGE1_GAPS)} known gaps)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
