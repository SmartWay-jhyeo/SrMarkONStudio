"""저장이 진짜인지 확인한다 — RESET 으로 기본값에 돌린 뒤 새 값으로 왕복.

앞선 검증에서 처음부터 cur:250 이 나왔다. 이전 세션의 저장이 살아남았다는
뜻이지만, 그 상태에서 250 을 다시 써 넣고 250 을 확인하면 아무것도
증명하지 못한다 — 저장이 안 돼도 통과한다.

그래서 여기서는:
  RESET 으로 100 으로 돌린다 → SAVE → 리셋 → 100 인지 확인
  777 로 바꾼다 → SAVE → 리셋 → 777 인지 확인
"""
from __future__ import annotations

import subprocess
import sys
import time

sys.path.insert(0, "D:/SourceCode/MarkON_Studio")

import serial

from host.core.framing import build_command
from host.core.limits import DEFAULT_BAUD

GDB = ("C:/ST/STM32CubeIDE_2.1.1/STM32CubeIDE/plugins/"
       "com.st.stm32cube.ide.mcu.externaltools.gnu-tools-for-stm32."
       "14.3.rel1.win32_1.0.100.202602081740/tools/bin/arm-none-eabi-gdb.exe")
FW = "D:/SourceCode/MarkON_Studio/firmware/stage1"

ok = True


def check(label: str, cond: bool, detail: str = "") -> None:
    global ok
    print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"  {detail}" if detail else ""))
    if not cond:
        ok = False


def open_port() -> serial.Serial:
    s = serial.Serial()
    s.port, s.baudrate, s.timeout = "COM23", DEFAULT_BAUD, 0.2
    s.dtr = False
    s.rts = False
    s.open()
    s.dtr = False
    s.rts = False
    time.sleep(0.3)
    return s


def ask(s: serial.Serial, *args: str, wait: float = 1.2) -> list[str]:
    s.reset_input_buffer()
    s.write(build_command(*args).encode())
    buf, out, t0 = b"", [], time.time()
    while time.time() - t0 < wait:
        chunk = s.read(1024)
        if chunk:
            buf += chunk
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                line = raw.decode("utf-8", "replace").strip()
                if line:
                    out.append(line)
        elif any(l.startswith("$SACK") for l in out):
            break
    return out


def sack(lines: list[str]) -> str:
    return next((l for l in lines if l.startswith("$SACK")), "")


def cur_of(lines: list[str]) -> str:
    return next((l for l in lines if "cfg_value" in l), "")


def reboot() -> None:
    subprocess.run([GDB, "--batch", "-x", "tools/reset.gdb"],
                   cwd=FW, capture_output=True)
    time.sleep(1.5)


def hb(s: serial.Serial) -> None:
    s.write(build_command("HB").encode())
    time.sleep(0.15)


print("1) RESET 으로 기본값(100)에 돌리고 저장")
with open_port() as s:
    hb(s)
    check("RESET 수락", "SACK,CFG,OK" in sack(ask(s, "CFG", "RESET")))
    hb(s)
    check("SAVE 수락", "SACK,CFG,OK" in sack(ask(s, "CFG", "SAVE", wait=2.0)))
reboot()
with open_port() as s:
    lines = ask(s, "CFG", "GET", "tx.period_ms")
    check("재부팅 후 100 (기본값이 저장됐다)",
          '"cur":100' in cur_of(lines), cur_of(lines))

print("\n2) 777 로 바꾸고 저장")
with open_port() as s:
    hb(s)
    check("SET 777 수락", "SACK,CFG,OK" in sack(
        ask(s, "CFG", "SET", "tx.period_ms", "777")))
    hb(s)
    check("SAVE 수락", "SACK,CFG,OK" in sack(ask(s, "CFG", "SAVE", wait=2.0)))
reboot()
with open_port() as s:
    lines = ask(s, "CFG", "GET", "tx.period_ms")
    check("재부팅 후 777 (새 값이 저장됐다)",
          '"cur":777' in cur_of(lines), cur_of(lines))

print("\n3) 저장하지 않은 변경은 살아남지 않는다")
with open_port() as s:
    hb(s)
    ask(s, "CFG", "SET", "tx.period_ms", "1234")
    lines = ask(s, "CFG", "GET", "tx.period_ms")
    check("바꾼 직후에는 1234", '"cur":1234' in cur_of(lines), cur_of(lines))
reboot()
with open_port() as s:
    lines = ask(s, "CFG", "GET", "tx.period_ms")
    check("재부팅 후 777 로 돌아온다 (저장 안 한 것은 안 남는다)",
          '"cur":777' in cur_of(lines), cur_of(lines))

print("\n4) 문자열 항목도 남는가")
with open_port() as s:
    hb(s)
    check("dev.id 를 boardX 로", "SACK,CFG,OK" in sack(
        ask(s, "CFG", "SET", "dev.id", "boardX")))
    hb(s)
    ask(s, "CFG", "SAVE", wait=2.0)
reboot()
with open_port() as s:
    lines = ask(s, "CFG", "GET", "dev.id")
    check("재부팅 후 boardX", '"cur":"boardX"' in cur_of(lines), cur_of(lines))
    # 원래대로 돌려 둔다
    hb(s)
    ask(s, "CFG", "SET", "dev.id", "1")
    hb(s)
    ask(s, "CFG", "SET", "tx.period_ms", "100")
    hb(s)
    ask(s, "CFG", "SAVE", wait=2.0)

print("\n" + ("전부 통과" if ok else "실패 있음"))
sys.exit(0 if ok else 1)
