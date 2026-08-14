"""2단계 설정 통신을 실기기에서 검증한다.

🔴 DTR/RTS 를 내린 채로 연다. 세운 채 열면 보드가 멈춘다 (CLAUDE.md §4).

무엇을 보나
  $CFG,LIST 로 카탈로그가 오는가 — 호스트가 그것만으로 화면을 만든다
  $CFG,GET 이 현재값을 주는가
  RUN 모드에서 $CFG,SET 이 MODE 로 거부되는가
  CONFIG 모드에서 받아들이는가
  규격 §5.2 의 사유가 그대로 오는가 (RANGE / INTERLOCK / UNKNOWN_KEY)
  $CFG,SAVE 뒤 전원을 껐다 켜도 값이 남는가
"""
from __future__ import annotations

import json
import sys
import time

sys.path.insert(0, "D:/SourceCode/MarkON_Studio")

import serial

from host.core.config_schema import parse_catalog
from host.core.limits import DEFAULT_BAUD

PORT = "COM23"

ok = True


def check(label: str, cond: bool, detail: str = "") -> None:
    global ok
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  {detail}" if detail else ""))
    if not cond:
        ok = False


def open_port() -> serial.Serial:
    s = serial.Serial()
    s.port, s.baudrate, s.timeout = PORT, DEFAULT_BAUD, 0.2
    s.dtr = False
    s.rts = False
    s.open()
    s.dtr = False
    s.rts = False
    return s


def collect(s: serial.Serial, seconds: float) -> list[str]:
    from host.core.framing import build_command  # noqa: F401

    buf = b""
    out: list[str] = []
    t0 = time.time()
    while time.time() - t0 < seconds:
        chunk = s.read(1024)
        if chunk:
            buf += chunk
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                line = raw.decode("utf-8", "replace").strip()
                if line:
                    out.append(line)
        elif out and any(l.startswith("$SACK") for l in out):
            break
    return out


def ask(s: serial.Serial, *args: str, wait: float = 1.2) -> list[str]:
    from host.core.framing import build_command

    s.reset_input_buffer()
    s.write(build_command(*args).encode())
    return collect(s, wait)


def sack(lines: list[str]) -> str:
    for l in lines:
        if l.startswith("$SACK"):
            return l
    return ""


def main() -> int:
    from host.core.framing import build_command

    with open_port() as s:
        time.sleep(0.3)

        print("1) $CFG,LIST — 카탈로그")
        lines = ask(s, "CFG", "LIST", wait=2.0)
        records = [l for l in lines if l.startswith("{")]
        check("SACK,CFG,OK 로 끝난다", "SACK,CFG,OK" in sack(lines), sack(lines))
        check("레코드가 온다", len(records) > 10, f"{len(records)}줄")

        schema = None
        if records:
            try:
                schema = parse_catalog(records)
                check("호스트가 파싱한다",
                      len(schema.items) > 10,
                      f"항목 {len(schema.items)}, 필드 {len(schema.fields)}")
            except Exception as exc:
                check("호스트가 파싱한다", False, str(exc))

        end = [json.loads(l) for l in records if '"cfg_end"' in l]
        if end and schema:
            want = len(schema.items) + len(schema.fields)
            check("cfg_end.count 가 실제와 맞는다 (잘리지 않았다)",
                  end[0]["count"] == want, f"{end[0]['count']} vs {want}")

        print("\n2) $CFG,GET")
        lines = ask(s, "CFG", "GET", "tx.period_ms")
        val = [l for l in lines if '"cfg_value"' in l]
        check("cfg_value 가 온다", bool(val), val[0] if val else "없음")
        lines = ask(s, "CFG", "GET", "없는키")
        check("모르는 키는 UNKNOWN_KEY", "UNKNOWN_KEY" in sack(lines), sack(lines))

        print("\n3) RUN 모드에서 $CFG,SET (하트비트를 4초 끊는다)")
        time.sleep(4.0)
        lines = ask(s, "CFG", "SET", "tx.period_ms", "250")
        check("MODE 로 거부된다", "ERR,MODE" in sack(lines), sack(lines))

        print("\n4) CONFIG 모드에서 $CFG,SET")
        s.write(build_command("HB").encode())
        time.sleep(0.2)
        lines = ask(s, "CFG", "SET", "tx.period_ms", "250")
        check("받아들인다", "SACK,CFG,OK" in sack(lines), sack(lines))
        lines = ask(s, "CFG", "GET", "tx.period_ms")
        check("값이 실제로 바뀌었다",
              any('"cur":250' in l for l in lines),
              next((l for l in lines if "cfg_value" in l), ""))

        print("\n5) 규격 §5.2 의 사유")
        s.write(build_command("HB").encode()); time.sleep(0.15)
        lines = ask(s, "CFG", "SET", "tx.period_ms", "999999")
        check("범위 밖 -> RANGE", "ERR,RANGE" in sack(lines), sack(lines))
        s.write(build_command("HB").encode()); time.sleep(0.15)
        lines = ask(s, "CFG", "SET", "pwr.5v", "false")
        check("인터록 -> INTERLOCK", "ERR,INTERLOCK" in sack(lines), sack(lines))

        print("\n6) $CFG,SAVE 와 재부팅 후 유지")
        s.write(build_command("HB").encode()); time.sleep(0.15)
        lines = ask(s, "CFG", "SAVE", wait=2.0)
        check("저장 성공", "SACK,CFG,OK" in sack(lines), sack(lines))

    print("\n   보드를 리셋한다...")
    import subprocess
    tc = ("C:/ST/STM32CubeIDE_2.1.1/STM32CubeIDE/plugins/"
          "com.st.stm32cube.ide.mcu.externaltools.gnu-tools-for-stm32."
          "14.3.rel1.win32_1.0.100.202602081740/tools/bin/arm-none-eabi-gdb.exe")
    subprocess.run([tc, "--batch", "-x", "tools/reset.gdb"],
                   cwd="D:/SourceCode/MarkON_Studio/firmware/stage1",
                   capture_output=True)
    time.sleep(1.5)

    with open_port() as s:
        time.sleep(0.3)
        lines = ask(s, "CFG", "GET", "tx.period_ms")
        check("재부팅 후에도 250 이다",
              any('"cur":250' in l for l in lines),
              next((l for l in lines if "cfg_value" in l), "없음"))

    print("\n" + ("전부 통과" if ok else "실패 있음"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
