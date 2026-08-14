"""굽고 나서 **되읽어 바이트로 확인한다.** 맞을 때까지 다시 굽는다.

🔴 `compare-sections` 를 믿으면 안 된다.

   2026-08-14 실증: `.text` 를 포함해 모든 섹션이 `matched` 로 나왔는데,
   플래시를 되읽어 보니 마지막 1,439 바이트가 통째로 `0xff` 였다. 그 자리가
   `.init_array` 라, `__libc_init_array` 가 `0xfffffffe` 를 함수 포인터로
   불러 부팅 즉시 HardFault 로 들어갔다 (CFSR=0x01000001, IACCVIOL).

   증상은 "보드가 조용하다" 뿐이다. 굽기는 성공했다고 말하고, GDB 로 붙어
   봐야 HardFault 인 것을 안다. 그 사이에 UART 배선이나 BMP 브리지를
   의심하며 시간을 버리게 된다 — 실제로 그랬다.

   CLAUDE.md §4 는 이 현상을 "compare-sections·dump 로는 못 잡는다" 고
   적어 두었는데, 되읽기(dump)로는 **잡힌다.** 안 잡히는 것은
   compare-sections 쪽이다.

그래서 여기서는 굽고 → 되읽고 → 바이트로 비교하고 → 틀리면 다시 굽는다.

사용:
    python tools/flash_verified.py [build/markon_stage1.elf] [시도횟수]
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
STAGE = HERE.parent

GDB = Path(
    "C:/ST/STM32CubeIDE_2.1.1/STM32CubeIDE/plugins"
    "/com.st.stm32cube.ide.mcu.externaltools.gnu-tools-for-stm32"
    ".14.3.rel1.win32_1.0.100.202602081740/tools/bin/arm-none-eabi-gdb.exe"
)

FLASH_BASE = 0x08000000


def _run_gdb(script: str, elf: str | None = None) -> str:
    with tempfile.NamedTemporaryFile("w", suffix=".gdb", delete=False,
                                     encoding="utf-8") as f:
        f.write(script)
        path = f.name
    cmd = [str(GDB), "--batch", "-x", path]
    if elf:
        cmd.append(elf)
    out = subprocess.run(cmd, capture_output=True, text=True,
                         encoding="utf-8", errors="replace", cwd=STAGE)
    Path(path).unlink(missing_ok=True)
    return (out.stdout or "") + (out.stderr or "")


def _flash(elf: Path) -> str:
    # 🔴 `monitor connect_rst enable` — 깨진 펌웨어가 붙기 전에 실행되면
    #    attach 자체가 실패한다. 리셋을 건 채로 붙으면 언제나 붙는다.
    # 🔴 `load` 앞에 `monitor reset` 을 넣지 않는다. attach 는 코어를 멈춘
    #    채 붙는데 거기서 리셋하면 다시 달리고, 깨진 판이면 그것이 HardFault 다.
    return _run_gdb(
        "set confirm off\n"
        "set pagination off\n"
        "set mem inaccessible-by-default off\n"
        "set remote memory-write-packet-size 256\n"
        "set remote memory-write-packet-size fixed\n"
        "target extended-remote \\\\.\\COM24\n"
        "monitor connect_rst enable\n"
        "monitor swdp_scan\n"
        "attach 1\n"
        "load\n"
        "detach\n"
        "quit\n",
        str(elf),
    )


def _readback(size: int, out: Path) -> str:
    return _run_gdb(
        "set confirm off\n"
        "set pagination off\n"
        "set mem inaccessible-by-default off\n"
        "target extended-remote \\\\.\\COM24\n"
        "monitor connect_rst enable\n"
        "monitor swdp_scan\n"
        "attach 1\n"
        f"dump binary memory {out.name} "
        f"0x{FLASH_BASE:08x} 0x{FLASH_BASE + size:08x}\n"
        "detach\n"
        "quit\n"
    )


def _release() -> str:
    """리셋을 풀고 펌웨어를 실행시킨다."""
    return _run_gdb(
        "set confirm off\n"
        "set pagination off\n"
        "target extended-remote \\\\.\\COM24\n"
        "monitor connect_rst disable\n"
        "monitor swdp_scan\n"
        "attach 1\n"
        "monitor reset\n"
        "detach\n"
        "quit\n"
    )


def main(argv: list[str]) -> int:
    elf = Path(argv[1]) if len(argv) > 1 else STAGE / "build" / "markon_stage1.elf"
    tries = int(argv[2]) if len(argv) > 2 else 4
    binary = elf.with_suffix(".bin")

    if not elf.exists() or not binary.exists():
        print(f"산출물이 없다: {elf} / {binary} — 먼저 make 를 돌린다",
              file=sys.stderr)
        return 2

    want = binary.read_bytes()
    readback = STAGE / "readback.bin"
    print(f"굽는다: {elf.name} ({len(want)} 바이트)\n")

    for attempt in range(1, tries + 1):
        print(f"[{attempt}/{tries}] load")
        log = _flash(elf)
        if "Error writing" in log:
            print("      쓰기 오류 — 다시 시도한다")
            continue

        _readback(len(want), readback)
        if not readback.exists():
            print("      되읽기 실패 — 다시 시도한다")
            continue
        got = readback.read_bytes()[:len(want)]

        bad = [i for i in range(len(want)) if want[i] != got[i]]
        if not bad:
            print(f"      되읽기 일치 ({len(want)} 바이트)")
            _release()
            print("\n구웠고 확인했다. 리셋을 풀었다.")
            return 0

        first = bad[0]
        print(f"      🔴 {len(bad)} 바이트가 다르다. 처음: "
              f"0x{FLASH_BASE + first:08x} "
              f"(bin={want[first]:02x} flash={got[first]:02x})")

    print(f"\n{tries}번 시도했지만 되읽기가 일치하지 않는다.", file=sys.stderr)
    print("보드 전원을 완전히 껐다 켠 뒤 다시 시도한다 — USB 재연결로는 "
          "풀리지 않는다(CLAUDE.md §4).", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
