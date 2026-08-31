"""C 프레이밍과 Python 프레이밍이 같은 답을 내는지 기계로 대조한다.

🔴 이 대조가 stage 1 프레이밍 계층의 존재 이유다. 보드와 호스트가 체크섬을
   다르게 계산하면 모든 명령이 조용히 거부되고, 원인은 프로토콜 어디에도
   드러나지 않는다.

조립(B)과 파싱(P) 을 모두 대조한다. 조립만 보면 두 구현이 같은 줄을 서로
다르게 **거부**해도 드러나지 않는다 — 실제로 앞쪽 공백 처리와 체크섬 오류
분류에서 갈렸고, 조립 벡터 8개는 그것을 하나도 잡지 못했다.

C 시험 바이너리를 `--vectors` 로 돌려 그 출력을 Python 계산과 비교한다.
사람이 두 표를 눈으로 맞춰보는 방식은 조용히 실패한다.
"""
from __future__ import annotations

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

from host.core.errors import ChecksumError, MalformedLineError  # noqa: E402
from host.core.framing import build_line, parse_line, xor_checksum  # noqa: E402


def _find_binary() -> Path:
    for name in ("test_framing.exe", "test_framing"):
        p = HERE / name
        if p.exists():
            return p
    raise SystemExit("시험 바이너리가 없다. 먼저 run_tests.ps1 (또는 make) 을 돌려라.")


def _escape(s: str) -> str:
    return s.replace("\r", "\\r").replace("\n", "\\n").replace("\t", "\\t")


def _unescape(s: str) -> str:
    return s.replace("\\r", "\r").replace("\\n", "\n").replace("\\t", "\t")


# 🔴 설계상 갈리는 입력. 건너뛰지 않고, 양쪽이 각각 무엇을 내놓아야 하는지
#    못박는다. 어느 한쪽이라도 달라지면 실패한다.
#
#    C 는 고정폭 버퍼(verb[13], args[4][24])를 쓴다. 담을 수 없는 verb 는
#    규격 §3 의 "verb 를 읽을 수 없을 만큼 깨졌다"에 해당하므로 조용히 버린다
#    — $SACK,<verb>,ERR,CHECKSUM 을 만들 재료 자체가 없기 때문이다.
#    Python 은 호스트라 SACK 을 낼 일이 없어 상한이 없고, 같은 바이트에
#    체크섬 오류를 낸다. 이 갈림은 역할의 비대칭에서 나온 것이지 결함이 아니다.
#    입력을 여기 문자열로 다시 적으면 C 쪽 벡터와 어긋난다(실제로 반복
#    문자 개수를 잘못 세어 한 번 어긋났다). 정규식으로 알아본다.
KNOWN_DIVERGENCES: list[tuple[str, re.Pattern[str], tuple[str, str]]] = [
    # 설명, 입력을 알아보는 패턴, (C 가 내야 할 것, Python 이 내야 할 것)
    ("verb 가 MK_VERB_MAX 초과", re.compile(r"^\$A{13,}\*"), ("MALFORMED", "CHECKSUM")),
    ("arg 가 MK_ARG_MAX 초과", re.compile(r"^\$CFG,B{24,}\*"), ("MALFORMED", "CHECKSUM")),
    ("인자가 MK_ARGS_MAX 초과", re.compile(r"^\$CFG(,[a-z]){5,}\*"), ("MALFORMED", "CHECKSUM")),
]


def _divergence(escaped: str) -> tuple[str, tuple[str, str]] | None:
    for name, pattern, expect in KNOWN_DIVERGENCES:
        if pattern.match(escaped):
            return name, expect
    return None


def _py_parse(raw: str) -> tuple[str, str, int, tuple[str, ...]]:
    """C 쪽 --vectors 출력과 같은 모양으로 Python 파싱 결과를 낸다.

    🔴 verb 는 OK 일 때만 돌려준다. host/core/framing.py 의 ChecksumError 는
       verb 를 싣지 않는데, 여기서 payload 를 손으로 다시 쪼개 verb 를 만들면
       Python 의 동작이 아니라 C 의 규칙을 Python 으로 베껴 자기 자신과
       비교하는 꼴이 된다. 그건 아무것도 보증하지 않는다.

       체크섬 오류 시 verb 가 남는지는 C 만의 계약이므로 C 쪽 시험
       test_checksum_error_keeps_verb 가 지킨다. 여기서는 **분류**만 맞춘다.
    """
    try:
        cmd = parse_line(raw)
    except ChecksumError:
        return ("CHECKSUM", None, None, ())
    except MalformedLineError:
        return ("MALFORMED", None, None, ())
    return ("OK", cmd.verb, len(cmd.args), cmd.args)


def _check_build(fields: list[str]) -> str | None:
    payload, c_cs, c_len, c_line = fields
    py_cs = f"{xor_checksum(payload):02X}"
    py_raw = build_line(payload)
    py_line = _escape(py_raw)
    py_len = str(len(py_raw))
    if (c_cs, c_len, c_line) != (py_cs, py_len, py_line):
        return (f"  build {payload}\n"
                f"    C : cs={c_cs} len={c_len} {c_line}\n"
                f"    py: cs={py_cs} len={py_len} {py_line}")
    print(f"  ok   build  {payload:24} cs={c_cs} len={c_len}")
    return None


def _label(escaped: str) -> str:
    return escaped if len(escaped) <= 28 else escaped[:25] + "..."


def _check_parse(fields: list[str]) -> str | None:
    escaped, c_result, c_verb, c_argc = fields[:4]
    c_args = tuple(fields[4:])
    raw = _unescape(escaped)
    py_result, py_verb, py_argc, py_args = _py_parse(raw)

    known = _divergence(escaped)
    if known is not None:
        name, (want_c, want_py) = known
        if (c_result, py_result) != (want_c, want_py):
            return (f"  parse {_label(escaped)!r}  (설계상 갈림: {name})\n"
                    f"    C : {c_result}  (기대 {want_c})\n"
                    f"    py: {py_result}  (기대 {want_py})")
        print(f"  ok   parse  {_label(escaped):28} -> C={c_result} py={py_result}  [{name}]")
        return None

    # OK 일 때만 verb·argc·args 를 비교한다 — 위 _py_parse 의 주석 참조.
    if c_result != py_result or (
        c_result == "OK" and (c_verb, int(c_argc), c_args) != (py_verb, py_argc, py_args)
    ):
        return (f"  parse {_label(escaped)!r}\n"
                f"    C : {c_result} verb={c_verb!r} argc={c_argc} args={c_args}\n"
                f"    py: {py_result} verb={py_verb!r} argc={py_argc} args={py_args}")
    print(f"  ok   parse  {_label(escaped):28} -> {c_result}"
          + (f" verb={c_verb!r}" if c_result == "OK" else ""))
    return None


def main() -> int:
    binary = _find_binary()
    out = subprocess.run(
        [str(binary), "--vectors"], capture_output=True, text=True, check=True
    ).stdout

    rows = [ln for ln in out.splitlines() if ln.strip()]
    if not rows:
        raise SystemExit("--vectors 출력이 비었다.")

    mismatches: list[str] = []
    seen_divergences: set[str] = set()
    n_build = n_parse = 0
    for row in rows:
        kind, *fields = row.split("\t")
        if kind == "B":
            n_build += 1
            bad = _check_build(fields)
        elif kind == "P":
            n_parse += 1
            known = _divergence(fields[0])
            if known is not None:
                seen_divergences.add(known[0])
            bad = _check_parse(fields)
        else:
            raise SystemExit(f"알 수 없는 벡터 종류: {kind!r}")
        if bad:
            mismatches.append(bad)

    # 선언해 둔 갈림이 어떤 벡터와도 맞지 않으면 죽은 선언이다. 이대로 두면
    # C 쪽 벡터가 바뀌었을 때 갈림이 검증에서 조용히 빠진다.
    for name, _pattern, _expect in KNOWN_DIVERGENCES:
        if name not in seen_divergences:
            mismatches.append(
                f"  선언한 갈림 '{name}' 에 해당하는 벡터가 없다 — "
                f"C 쪽 PARSE_VECTORS 를 확인하라."
            )

    if mismatches:
        print("\n두 구현이 어긋난다:", file=sys.stderr)
        print("\n".join(mismatches), file=sys.stderr)
        return 1
    print(f"\nMATCH (build {n_build}, parse {n_parse}, "
          f"설계상 갈림 {len(seen_divergences)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
