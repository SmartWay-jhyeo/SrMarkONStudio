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

import json
import re
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
    # 여기부터는 NDJSON 본문을 내는 명령이다. 값은 양쪽 설정표가 달라 같을 수
    # 없으므로 아래에서 **모양**으로 비교한다.
    ("MARK", "SHAPES"),
    ("CMD", "STAT"), ("AT", "6000"), ("FEED", "STAT"),
    ("CMD", "CFG,LIST"), ("AT", "6000"), ("FEED", "CFG,LIST"),
]

# 🔴 NDJSON 본문을 내는 명령은 **값**이 같을 수 없다. C 쪽 시나리오는 시험용
#    작은 설정표를 쓰고 시뮬레이터는 45항목짜리 실제 표를 쓰기 때문이다.
#    그래서 여기서는 모양을 비교한다.
#
#      - 레코드 `type` 의 나열 순서 (본문 → $SACK)
#      - $SACK payload (양쪽이 정확히 같아야 한다)
#      - `keys` 가 있으면 그 레코드의 **키 집합**을 양쪽 사이에서 대조
#
#    키 집합 대조가 요점이다. `stat` 은 모양이 고정된 레코드라 한쪽이 필드를
#    빠뜨리면 GUI 가 시뮬레이터에서만 동작하게 된다 — 실제로 이 대조를
#    붙이면서 C 쪽에 `time_source`·`time_quality` 가 없는 것을 찾았다.
#
#    `cfg_item` 의 키는 항목마다 다르다(min·max·unit·note·choices 는 있을 때만
#    나간다). 실제 45항목 표와 시뮬레이터의 대조는 crosscheck_cfgtable.py 가
#    한다 — crosscheck_cfg.py 가 아니다. 그쪽은 test_cfgwire.c 의 6항목짜리
#    시험용 표로 parse_catalog 를 확인하는 도구다.
#    여기서는 봉투만 본다 — 본문이 몇 줄이든 cfg_end 로 닫고 SACK 로 끝나는가.
#
#    🔴 명령별로 **짝지어** 확인한다. 응답을 한 통에 모아 놓고 "어딘가에
#       있다" 로 확인하면, STAT 의 응답과 CFG 의 응답이 뒤바뀌어도 통과한다.
#       처음에 그렇게 짜 두었다가 리뷰에서 지적받았다.
#
#    🔴 압축 형식인지도 함께 본다. 처음 대조했을 때 시뮬레이터의 카탈로그만
#       공백 있는 JSON 을 내고 있었고, 이 대조가 그것을 잡았다. 대역폭이
#       94.8% 인 링크에서 공백은 공짜가 아니고, mk_json 은 압축만 낸다.


class Shape:
    """한 명령의 응답 모양.

    types  레코드 type 의 나열. `+` 접미사는 1회 이상 반복.
    sack   $SACK payload — 양쪽이 정확히 같아야 한다.
    keys   키 집합을 양쪽 사이에서 대조할 레코드 type 들.
    """

    def __init__(self, types: list[str], sack: str, keys: tuple[str, ...] = ()):
        self.types = types
        self.sack = sack
        self.keys = keys


SHAPES: dict[str, Shape] = {
    "STAT": Shape(["stat"], "SACK,STAT,OK", keys=("stat",)),
    "CFG,LIST": Shape(["cfg_item+", "cfg_field+", "cfg_end"], "SACK,CFG,OK"),
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
        capture_output=True, text=True, check=True, encoding="utf-8",
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


# JSON 문자열 리터럴. 이스케이프된 따옴표를 건너뛴다.
_STRINGS = re.compile(r'"(?:[^"\\]|\\.)*"')


def _split_body(lines: list[str], side: str, cmd: str) -> tuple[list[dict], str]:
    """응답 줄들을 NDJSON 본문과 마지막 $SACK 로 나눈다."""
    body: list[dict] = []
    sack = ""
    for ln in lines:
        if ln.startswith("$"):
            sack = _payload_of(ln)
            continue
        # 🔴 압축 형식인지 여기서 본다. json.loads 는 공백을 그냥 먹으므로,
        #    파싱 전에 원문을 보지 않으면 공백 있는 JSON 이 통과한다.
        #
        #    문자열 안의 공백은 정상이다 — 라벨이 "전송 주기" 다. 문자열을
        #    먼저 지우고 남은 자리에 공백이 있는지 본다.
        if " " in _STRINGS.sub('""', ln):
            raise SystemExit(
                f"{side} 쪽 {cmd} 응답에 공백이 있다 — 압축 형식이어야 한다\n  {ln[:80]}")
        body.append(json.loads(ln))
    return body, sack


def _match_types(got: list[str], want: list[str]) -> str | None:
    """레코드 type 나열을 패턴과 맞춘다. `+` 는 1회 이상."""
    i = 0
    for pat in want:
        if pat.endswith("+"):
            name = pat[:-1]
            n = 0
            while i < len(got) and got[i] == name:
                i += 1
                n += 1
            if n == 0:
                return f"{name} 레코드가 하나도 없다 (받음: {got})"
        else:
            if i >= len(got) or got[i] != pat:
                return f"{i}번째가 {pat} 이어야 하는데 {got[i:i + 1] or '없음'} 이다"
            i += 1
    if i != len(got):
        return f"기대한 것보다 줄이 많다 — 남은 것: {got[i:]}"
    return None


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

    # NDJSON 본문을 내는 명령 — 값은 다를 수밖에 없으므로 모양을 대조한다.
    print("\n  -- 본문을 내는 명령 (모양 대조) --")
    c_by_cmd = _group_by_command(c_gap, "C")
    py_by_cmd = _group_by_command(py_gap, "py")

    for cmd, shape in SHAPES.items():
        c_lines = c_by_cmd.get(cmd)
        py_lines = py_by_cmd.get(cmd)
        if c_lines is None or py_lines is None:
            side = "C" if c_lines is None else "Python"
            mismatches.append(f"  {cmd}: {side} 쪽 기록에 이 명령 구간이 없다")
            continue

        c_body, c_sack = _split_body(c_lines, "C", cmd)
        py_body, py_sack = _split_body(py_lines, "py", cmd)

        bad = False
        for side, body in (("C", c_body), ("py", py_body)):
            err = _match_types([r.get("type", "?") for r in body], shape.types)
            if err:
                mismatches.append(f"  {cmd}: {side} 본문 순서가 다르다 — {err}")
                bad = True

        if c_sack != shape.sack or py_sack != shape.sack:
            mismatches.append(f"  {cmd}: $SACK 이 다르다\n"
                              f"    기대: {shape.sack}\n"
                              f"    C : {c_sack}\n    py: {py_sack}")
            bad = True

        # 🔴 모양이 고정된 레코드는 키 집합까지 맞춘다. 한쪽이 필드를
        #    빠뜨리면 GUI 가 시뮬레이터에서만 동작한다.
        for want_type in shape.keys:
            c_keys = {k for r in c_body if r.get("type") == want_type for k in r}
            py_keys = {k for r in py_body if r.get("type") == want_type for k in r}
            if c_keys != py_keys:
                mismatches.append(
                    f"  {cmd}: {want_type} 레코드의 필드가 다르다\n"
                    f"    C 에만: {sorted(c_keys - py_keys) or '없음'}\n"
                    f"    py 에만: {sorted(py_keys - c_keys) or '없음'}")
                bad = True

        if not bad:
            print(f"  ok   {cmd:9} {' '.join(shape.types)} + ${shape.sack}"
                  + (f"  (필드 {len(c_keys)}개 일치)" if shape.keys else ""))

    if mismatches:
        print("\n두 구현이 어긋난다:", file=sys.stderr)
        print("\n".join(mismatches), file=sys.stderr)
        return 1
    print(f"\nMATCH ({len(c_common)} steps, {len(SHAPES)} shapes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
