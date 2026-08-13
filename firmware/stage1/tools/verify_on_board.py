"""1단계 펌웨어를 실기기에서 검증한다.

🔴 DTR/RTS 를 내린 채로 연다. 세운 채 열면 보드가 멈춘다 (CLAUDE.md §4).
"""
from __future__ import annotations

import sys
import time

sys.path.insert(0, "D:/SourceCode/MarkON_Studio")

import serial

from host.core.framing import build_command, parse_line
from host.core.errors import ProtocolError

PORT = "COM23"
BAUD = 115200


def open_port() -> serial.Serial:
    s = serial.Serial()
    s.port = PORT
    s.baudrate = BAUD
    s.timeout = 0.2
    s.dtr = False
    s.rts = False
    s.open()
    s.dtr = False
    s.rts = False
    return s


def collect(s: serial.Serial, seconds: float) -> list[str]:
    buf = b""
    out: list[str] = []
    t0 = time.time()
    while time.time() - t0 < seconds:
        chunk = s.read(256)
        if chunk:
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                text = line.decode("utf-8", "replace").strip()
                if text:
                    out.append(text)
    return out


ok = True


def check(label: str, cond: bool, detail: str = "") -> None:
    global ok
    if cond:
        print(f"  ok   {label}" + (f"  {detail}" if detail else ""))
    else:
        print(f"  FAIL {label}  {detail}")
        ok = False


with open_port() as s:
    time.sleep(0.3)
    s.reset_input_buffer()

    # 1. 보드가 스스로 $HB 를 1 Hz 로 낸다 (규격 §6.1)
    print("1) 보드 -> 호스트 하트비트 (3.5초 관찰)")
    lines = collect(s, 3.5)
    hbs = [ln for ln in lines if ln.startswith("$HB")]
    print(f"     받은 줄 {len(lines)}개, 그중 $HB {len(hbs)}개")
    for ln in lines[:4]:
        print(f"       {ln!r}")
    check("$HB 를 낸다", len(hbs) >= 3, f"3.5초에 {len(hbs)}개 (1 Hz 기대)")
    if hbs:
        try:
            cmd = parse_line(hbs[0] + "\r\n")
            check("$HB 의 체크섬이 맞다", cmd.verb == "HB", repr(hbs[0]))
        except ProtocolError as exc:
            check("$HB 의 체크섬이 맞다", False, str(exc))

    check("텔레메트리를 내지 않는다 (1단계는 ADS1256 이 없다)",
          not any(ln.startswith("{") and '"type":"ain"' in ln for ln in lines))

    # 2. $ID 에 답한다
    print("\n2) $ID 요청")
    s.reset_input_buffer()
    s.write(build_command("ID").encode())
    time.sleep(0.5)
    lines = collect(s, 1.0)
    recs = [ln for ln in lines if ln.startswith("{")]
    sacks = [ln for ln in lines if ln.startswith("$SACK")]
    for ln in lines:
        print(f"       {ln!r}")
    check("id 레코드가 온다", len(recs) == 1)
    check("$SACK,ID,OK 가 온다",
          any("SACK,ID,OK" in ln for ln in sacks))
    if recs:
        import json
        try:
            obj = json.loads(recs[0])
            check("schema_ver 가 3 이다", obj.get("schema_ver") == 3, str(obj.get("schema_ver")))
            check("type 이 id 다", obj.get("type") == "id")
            check("fw 가 0.1.0 이다", obj.get("fw") == "0.1.0", str(obj.get("fw")))
            check("board_rev 가 2.0 이다", obj.get("board_rev") == "2.0")
            check("seq 가 0 이다 (§5.2 명령 응답)", obj.get("seq") == 0)
        except Exception as exc:
            check("id 레코드가 JSON 이다", False, str(exc))

    # 3. 구현하지 않은 명령
    print("\n3) $STAT (1단계 미구현)")
    s.reset_input_buffer()
    s.write(build_command("STAT").encode())
    time.sleep(0.4)
    lines = collect(s, 0.8)
    for ln in lines:
        print(f"       {ln!r}")
    check("ERR,UNSUPPORTED 로 답한다",
          any("SACK,STAT,ERR,UNSUPPORTED" in ln for ln in lines))

    # 4. 체크섬이 깨진 $HB 는 조용해야 한다 (규격 §3 예외)
    print("\n4) 체크섬 깨진 $HB")
    s.reset_input_buffer()
    s.write(b"$HB*FF\r\n")
    time.sleep(0.4)
    lines = collect(s, 0.8)
    sacks = [ln for ln in lines if ln.startswith("$SACK")]
    for ln in lines:
        print(f"       {ln!r}")
    check("$SACK 를 보내지 않는다", not sacks, str(sacks))

    # 5. 체크섬이 깨진 $ID 는 알려 줘야 한다
    print("\n5) 체크섬 깨진 $ID")
    s.reset_input_buffer()
    s.write(b"$ID*FF\r\n")
    time.sleep(0.4)
    lines = collect(s, 0.8)
    for ln in lines:
        print(f"       {ln!r}")
    check("ERR,CHECKSUM 으로 답한다",
          any("SACK,ID,ERR,CHECKSUM" in ln for ln in lines))

print("\n" + ("전부 통과" if ok else "실패 있음"))
sys.exit(0 if ok else 1)
