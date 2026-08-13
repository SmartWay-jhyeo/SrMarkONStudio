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

import subprocess
import sys
from pathlib import Path

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


def _py_parse(raw: str) -> tuple[str, str, int, tuple[str, ...]]:
    """C 쪽 --vectors 출력과 같은 모양으로 Python 파싱 결과를 낸다."""
    try:
        cmd = parse_line(raw)
    except ChecksumError:
        # 규격 §3: verb 를 읽을 수 있으면 SACK 을 보내야 하므로 verb 가 남는다.
        # Python 은 예외에 verb 를 싣지 않으니 같은 규칙으로 다시 뽑아낸다.
        stripped = raw.strip()
        payload = stripped[1 : stripped.rfind("*")]
        verb = payload.split(",")[0]
        return ("CHECKSUM", verb, len(payload.split(",")) - 1, ())
    except MalformedLineError:
        return ("MALFORMED", "-", -1, ())
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


def _check_parse(fields: list[str]) -> str | None:
    escaped, c_result, c_verb, c_argc = fields[:4]
    c_args = tuple(fields[4:])
    raw = _unescape(escaped)
    py_result, py_verb, py_argc, py_args = _py_parse(raw)

    # C 는 고정폭 버퍼가 있어 Python 에 없는 길이 상한으로 거부할 수 있다.
    # 대조 벡터는 전부 상한 안이므로 여기서는 그대로 비교한다.
    if (c_result, c_verb, int(c_argc)) != (py_result, py_verb, py_argc) or (
        c_result == "OK" and c_args != py_args
    ):
        return (f"  parse {escaped!r}\n"
                f"    C : {c_result} verb={c_verb!r} argc={c_argc} args={c_args}\n"
                f"    py: {py_result} verb={py_verb!r} argc={py_argc} args={py_args}")
    print(f"  ok   parse  {escaped:28} -> {c_result} verb={c_verb!r}")
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
    n_build = n_parse = 0
    for row in rows:
        kind, *fields = row.split("\t")
        if kind == "B":
            n_build += 1
            bad = _check_build(fields)
        elif kind == "P":
            n_parse += 1
            bad = _check_parse(fields)
        else:
            raise SystemExit(f"알 수 없는 벡터 종류: {kind!r}")
        if bad:
            mismatches.append(bad)

    if mismatches:
        print("\n두 구현이 어긋난다:", file=sys.stderr)
        print("\n".join(mismatches), file=sys.stderr)
        return 1
    print(f"\nMATCH (build {n_build}, parse {n_parse})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
