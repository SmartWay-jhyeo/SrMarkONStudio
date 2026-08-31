"""GUI 로 바꾼 레일 설정을 보드가 **기억하는지** 실기기에서 확인한다.

이 프로젝트의 요점이 이것이다 — USB 를 꽂아 GUI 로 설정하고, 빼면 보드가
저장된 대로 혼자 돈다. 설정이 살아남지 않으면 나머지가 다 무의미하다.

절차
  1. 24V 를 켠다        ($CFG,SET)
  2. 저장한다           ($CFG,SAVE)
  3. 보드를 리셋한다     (GDB — 전원을 끊는 것과 같은 효과, Flash 는 남는다)
  4. 부팅 뒤 $STAT 의 rails 를 본다 -> v24 가 살아 있어야 한다
  5. 되돌리고 다시 저장한다 (보드를 안전한 상태로 남긴다)

🔴 4번에서 하트비트를 보내지 않는다. RUN 모드 그대로 물어본다 —
   "GUI 없이 혼자 도는 상태" 가 바로 확인하려는 것이기 때문이다.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import serial  # noqa: E402

from host.core.framing import build_command  # noqa: E402

GDB = ("C:/ST/STM32CubeIDE_2.1.1/STM32CubeIDE/plugins"
       "/com.st.stm32cube.ide.mcu.externaltools.gnu-tools-for-stm32"
       ".14.3.rel1.win32_1.0.100.202602081740/tools/bin/arm-none-eabi-gdb.exe")

PORT = sys.argv[1] if len(sys.argv) > 1 else "COM23"


def board_reset() -> None:
    """전원을 끊었다 넣은 것과 같은 상태로 만든다. Flash 는 남는다."""
    script = (
        "set confirm off\nset pagination off\n"
        "target extended-remote \\\\.\\COM24\n"
        "monitor connect_rst disable\nmonitor swdp_scan\nattach 1\n"
        "monitor reset\ndetach\nquit\n"
    )
    with tempfile.NamedTemporaryFile("w", suffix=".gdb", delete=False,
                                     encoding="utf-8") as f:
        f.write(script)
        path = f.name
    subprocess.run([GDB, "--batch", "-x", path],
                   capture_output=True, text=True, encoding="utf-8",
                   errors="replace")
    Path(path).unlink(missing_ok=True)


class Link:
    def __init__(self, port: str):
        s = serial.Serial()
        s.port = port
        s.baudrate = 921600
        s.timeout = 0.05
        # 🔴 열기 전에 내린다 (CLAUDE.md §4). 세운 채 열면 보드가 멈춘다.
        s.dtr = False
        s.rts = False
        s.open()
        s.dtr = False
        s.rts = False
        self.s = s
        self.buf = b""

    def pump(self, secs: float, heartbeat: bool = True) -> list:
        out = []
        t0 = time.monotonic()
        last = 0.0
        while time.monotonic() - t0 < secs:
            if heartbeat and time.monotonic() - last > 0.5:
                self.send("HB")
                last = time.monotonic()
            chunk = self.s.read(4096)
            if chunk:
                self.buf += chunk
                while b"\n" in self.buf:
                    raw, _, self.buf = self.buf.partition(b"\n")
                    line = raw.decode("utf-8", "replace").strip()
                    if line.startswith("{"):
                        try:
                            out.append(json.loads(line))
                        except ValueError:
                            pass
                    elif line.startswith("$SACK"):
                        out.append({"type": "sack", "line": line})
            time.sleep(0.01)
        return out

    def send(self, *args: str) -> None:
        self.s.write(build_command(*args).encode())
        self.s.flush()

    def stat(self, heartbeat: bool = True) -> dict | None:
        self.send("STAT")
        for r in self.pump(1.2, heartbeat):
            if r.get("type") == "stat":
                return r
        return None

    def close(self) -> None:
        self.s.close()


def main() -> int:
    failures = 0

    def check(ok: bool, msg: str) -> None:
        nonlocal failures
        print(f"  {'ok  ' if ok else 'FAIL'} {msg}")
        if not ok:
            failures += 1

    print(f"== {PORT} — 레일 설정이 리셋을 넘어 살아남는가 ==\n")

    link = Link(PORT)
    try:
        print("[1] CONFIG 열고 24V 켜기")
        link.pump(2.5)
        link.send("CFG", "SET", "pwr.24v", "true")
        sacks = [r["line"] for r in link.pump(1.0) if r.get("type") == "sack"]
        print(f"    {sacks[:1]}")
        st = link.stat()
        check(st is not None and st["rails"]["v24"], "24V 가 켜졌다")

        print("\n[2] 저장")
        link.send("CFG", "SAVE")
        sacks = [r["line"] for r in link.pump(1.5) if r.get("type") == "sack"]
        print(f"    {sacks[:1]}")
        check(any("CFG,OK" in s for s in sacks), "$CFG,SAVE 가 성공했다")
    finally:
        link.close()

    print("\n[3] 보드 리셋 (전원을 끊었다 넣은 것과 같다)")
    board_reset()
    time.sleep(1.5)

    print("\n[4] 부팅 뒤 — 하트비트 없이, RUN 모드 그대로 물어본다")
    link = Link(PORT)
    try:
        link.pump(1.0, heartbeat=False)
        st = link.stat(heartbeat=False)
        if st is None:
            check(False, "부팅 뒤 $STAT 이 오지 않는다")
        else:
            print(f"    mode  = {st['mode']}  uptime = {st['uptime_ms']} ms")
            print(f"    rails = {st['rails']}")
            check(st["mode"] == "RUN", "GUI 없이 RUN 모드로 돈다")
            check(st["rails"]["v24"],
                  "🔴 24V 가 리셋을 넘어 살아남았다 — 보드가 기억한다")
            check(st["rails"]["v5"], "5V 도 올라와 있다")
            q = st.get("queues", [])
            check(bool(q), f"수집도 혼자 시작했다 ({len(q)}채널)")

        print("\n[5] 되돌리고 저장 (보드를 안전한 상태로 남긴다)")
        link.pump(2.5)
        link.send("CFG", "SET", "pwr.24v", "false")
        link.pump(0.8)
        link.send("CFG", "SAVE")
        sacks = [r["line"] for r in link.pump(1.5) if r.get("type") == "sack"]
        st = link.stat()
        print(f"    rails = {st['rails'] if st else '?'}")
        check(st is not None and not st["rails"]["v24"], "24V 를 다시 껐다")
    finally:
        link.close()

    print(f"\n{'실패 ' + str(failures) if failures else '모두 통과'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
