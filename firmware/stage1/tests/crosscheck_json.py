"""C 가 만든 NDJSON 이 Python 이 만드는 것과 **바이트 단위로** 같은지 대조한다.

🔴 보드와 호스트가 같은 레코드를 서로 다른 바이트로 쓰면, 호스트 시험은
   전부 통과하는데 실기기에서만 어긋난다. 그런 어긋남은 대개 실수 자릿수나
   이스케이프처럼 눈으로는 잘 안 보이는 곳에서 난다.

C 는 libc 의 printf 를 쓰지 않고 정수·실수 출력을 직접 짠다
(docs/measurements/2026-08-13_newlib_nano_printf.md). 그 직접 짠 출력이
Python 의 json.dumps 와 같은지가 이 대조의 전부다.

실수는 json.dumps 에 맡길 수 없다 — Python 은 float 를 repr 로 찍고 C 는
고정 자릿수로 반올림한다. 같은 반올림 규칙(0 에서 먼 쪽)을 Python 으로도
구현해 문자열을 만든 뒤 끼워 넣는다.
"""
from __future__ import annotations

import json
import math
import struct
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _find_binary() -> Path:
    for name in ("test_json.exe", "test_json"):
        p = HERE / name
        if p.exists():
            return p
    raise SystemExit("시험 바이너리가 없다. 먼저 run_tests.ps1 (또는 make) 을 돌려라.")


class _Raw:
    """json.dumps 가 손대지 않고 그대로 흘려보낼 조각."""

    def __init__(self, text: str) -> None:
        self.text = text


class _Encoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, _Raw):
            return f"@@{id(o)}@@"
        return super().default(o)


def f32(value: float, digits: int) -> _Raw:
    """C 의 mk_json_f32 와 같은 규칙으로 십진 문자열을 만든다.

    float32 로 한 번 접어 넣는 것이 중요하다 — C 쪽은 float 이므로,
    Python 의 double 로 계산하면 마지막 자리가 갈릴 수 있다.
    """
    v = struct.unpack("<f", struct.pack("<f", value))[0]
    if not math.isfinite(v):
        return _Raw("null")
    scaled = v * (10 ** digits)
    if not (-9.0e18 < scaled < 9.0e18):
        return _Raw("null")

    neg = scaled < 0
    if neg:
        scaled = -scaled
    units = int(scaled + 0.5)          # 0 에서 먼 쪽 반올림 (C 와 동일)

    ip, fp = divmod(units, 10 ** digits)
    sign = "-" if (neg and units != 0) else ""
    if digits == 0:
        return _Raw(f"{sign}{ip}")
    return _Raw(f"{sign}{ip}.{fp:0{digits}d}")


def dumps(obj: dict) -> str:
    """C 와 같은 압축 형식으로. _Raw 조각은 그대로 박아 넣는다."""
    raws = {f"@@{id(v)}@@": v.text for v in obj.values() if isinstance(v, _Raw)}
    text = json.dumps(obj, ensure_ascii=False, separators=(",", ":"), cls=_Encoder)
    for placeholder, literal in raws.items():
        text = text.replace(f'"{placeholder}"', literal)
    return text


EXPECTED: dict[str, dict] = {
    "id": {
        "schema_ver": 3, "seq": 0, "t": 1772200855875, "type": "id",
        "device_id": "1", "fw": "0.1.0", "board_rev": "2.0",
    },
    "id_escaped": {
        "schema_ver": 3, "seq": 0, "t": 0, "type": "id",
        "device_id": 'a"b\\c', "fw": "0.1.0", "board_rev": "2.0",
    },
    "ain": {
        "schema_ver": 3, "seq": 1234, "t": 1772200855875, "type": "ain",
        "connector_id": 3, "raw": 8388608,
        "ma": f32(12.0041, 4), "value": f32(3.4210, 4),
        "unit": "bar", "status": 0, "capture_counter": 123456789,
    },
    "ain_nan": {
        "schema_ver": 3, "seq": 1235, "t": 1772200855885, "type": "ain",
        "connector_id": 4, "raw": -1,
        "ma": f32(float("nan"), 4), "value": f32(float("nan"), 4),
        "status": 1,
    },
}


def main() -> int:
    out = subprocess.run(
        [str(_find_binary()), "--records"],
        capture_output=True, text=True, check=True,
    ).stdout

    rows = {}
    for line in out.splitlines():
        if not line.strip():
            continue
        name, payload = line.split("\t", 1)
        rows[name] = payload

    mismatches: list[str] = []
    for name, obj in EXPECTED.items():
        if name not in rows:
            mismatches.append(f"  {name}: C 출력에 없다")
            continue
        want = dumps(obj)
        got = rows[name]
        if got != want:
            mismatches.append(f"  {name}\n    C : {got}\n    py: {want}")
            continue
        # 형태만 같아서는 부족하다. 실제로 파싱되는지도 본다.
        try:
            json.loads(got)
        except Exception as exc:
            mismatches.append(f"  {name}: C 출력이 JSON 으로 파싱되지 않는다: {exc}")
            continue
        print(f"  ok   {name:12} {len(got):4}B")

    extra = set(rows) - set(EXPECTED)
    if extra:
        mismatches.append(f"  C 만 내놓은 레코드가 있다(기대값 없음): {sorted(extra)}")

    if mismatches:
        print("\n두 구현이 어긋난다:", file=sys.stderr)
        print("\n".join(mismatches), file=sys.stderr)
        return 1
    print(f"\nMATCH ({len(EXPECTED)} records)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
