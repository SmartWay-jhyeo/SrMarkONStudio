"""백업(raw .bin)을 되돌린다. 되읽어 바이트로 비교하고, 틀리면 재시도한다.

🔴 `flash_verified.py` 는 ELF 를 `load` 로 굽는다. 백업은 ELF 가 아니라 플래시를
   그대로 뜬 이진 덩어리라 `restore ... binary` 를 써야 한다.

용도가 둘이다.
  1. 되돌리기 — 깨진 판이 올라갔을 때 쓸 수 있는 상태로 복구
  2. 가르기  — 예전(작은) 이미지가 깨끗이 구워지면, 굽기 실패의 방아쇠가
     프로브·보드가 아니라 **이미지 쪽**(크기나 새 코드)이라는 뜻이다

사용: python tools/restore_verified.py <backup.bin> [시도횟수]
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

TOOLCHAIN = Path(
    "C:/ST/STM32CubeIDE_2.1.1/STM32CubeIDE/plugins"
    "/com.st.stm32cube.ide.mcu.externaltools.gnu-tools-for-stm32"
    ".14.3.rel1.win32_1.0.100.202602081740/tools/bin"
)
GDB = TOOLCHAIN / "arm-none-eabi-gdb.exe"
OBJCOPY = TOOLCHAIN / "arm-none-eabi-objcopy.exe"
FLASH_BASE = 0x08000000

PROLOGUE = (
    "set confirm off\n"
    "set pagination off\n"
    "set mem inaccessible-by-default off\n"
    "set remote memory-write-packet-size 256\n"
    "set remote memory-write-packet-size fixed\n"
    "target extended-remote \\\\.\\COM24\n"
    # 🔴 깨진 펌웨어가 붙기 전에 실행되면 attach 자체가 실패한다.
    "monitor connect_rst enable\n"
    "monitor swdp_scan\n"
    "attach 1\n"
)


def _gdb(script: str) -> str:
    with tempfile.NamedTemporaryFile("w", suffix=".gdb", delete=False,
                                     encoding="utf-8") as f:
        f.write(script)
        path = f.name
    out = subprocess.run([str(GDB), "--batch", "-x", path],
                         capture_output=True, text=True,
                         encoding="utf-8", errors="replace", cwd=STAGE)
    Path(path).unlink(missing_ok=True)
    return (out.stdout or "") + (out.stderr or "")


def _wrap_elf(raw: Path, out: Path) -> None:
    """raw .bin 을 0x08000000 에 놓인 ELF 로 감싼다.

    🔴 gdb 의 `restore` 로는 플래시를 굽지 못한다. 그것은 평범한 메모리
       쓰기여서 플래시에는 **조용히 아무 일도 일어나지 않는다** — 실제로
       6번을 굽고도 예전 내용이 그대로 남아 있었고, 되읽기 검증이 없었다면
       성공했다고 착각할 뻔했다. 플래시 프로그래밍 경로를 타는 것은 `load`
       뿐이고, `load` 는 오브젝트 파일을 요구한다.

       그래서 섹션에 alloc·load 를 붙인다. 이것이 없으면 ELF 는 만들어지지만
       `load` 가 아무것도 싣지 않는다.

    🔴 섹션 이름을 바꾸지 않는다. `--rename-section` 을 같이 주면 주소 변경이
       **새 이름을 찾지 못해 조용히 무시된다** — VMA 가 0 인 ELF 가 나오고,
       `load` 는 0 번지에 쓰려 든다. 이름은 어차피 겉치레다.
    """
    subprocess.run(
        [str(OBJCOPY), "-I", "binary", "-O", "elf32-littlearm", "-B", "arm",
         "--set-section-flags", ".data=alloc,load,readonly,code,contents",
         "--change-section-address", f".data=0x{FLASH_BASE:08x}",
         str(raw), str(out)],
        check=True, capture_output=True)
    # 주소가 실제로 붙었는지 본다. 위 실패가 조용했기 때문이다.
    hdrs = subprocess.run([str(OBJCOPY.with_name("arm-none-eabi-objdump.exe")),
                           "-h", str(out)],
                          capture_output=True, text=True).stdout
    if f"{FLASH_BASE:08x}" not in hdrs:
        raise SystemExit(f"ELF 섹션 주소가 0x{FLASH_BASE:08x} 이 아니다:\n{hdrs}")


def _trim(data: bytes) -> bytes:
    """뒤쪽의 지워진 상태(0xFF)를 잘라 낸다.

    🔴 32 바이트 경계에 맞춘다. H7 은 256 비트 단위로 굽는다 — 어중간한
       길이로 끊으면 마지막 워드가 반쪽만 채워진다.
    """
    end = len(data)
    while end > 0 and data[end - 1] == 0xFF:
        end -= 1
    return data[:(end + 31) // 32 * 32]


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    src = Path(argv[1])
    tries = int(argv[2]) if len(argv) > 2 else 4

    want = _trim(src.read_bytes())
    tmp = STAGE / "_restore.bin"
    elf = STAGE / "_restore.elf"
    tmp.write_bytes(want)
    _wrap_elf(tmp, elf)
    print(f"되돌린다: {src.name} — 실제 {len(want)} 바이트 "
          f"(뒤쪽 0xFF 를 잘라 냈다)")

    readback = STAGE / "_readback.bin"
    for attempt in range(1, tries + 1):
        print(f"[{attempt}/{tries}] load")
        out = _gdb(PROLOGUE
                   + f"file {elf.name}\nload\n"
                   + "detach\nquit\n")
        # 🔴 "error" 를 통째로 잡지 않는다. gdb 는 무해한 줄에도 그 낱말을 쓴다
        #    (경고, 심볼 안내). 넓게 잡으면 성공한 굽기를 실패로 세고, 그러면
        #    되읽기 검증까지 가지 못해 무엇이 참인지 알 수 없게 된다.
        if "writing data to flash" in out or "Cannot access memory" in out:
            bad_line = next((ln for ln in out.splitlines()
                             if "rror" in ln or "Cannot access" in ln), "")
            print(f"      쓰기 오류 — {bad_line.strip()[:70]}")
            continue

        _gdb(PROLOGUE
             + f"dump binary memory {readback.name} 0x{FLASH_BASE:08x} "
               f"0x{FLASH_BASE + len(want):08x}\n"
             + "detach\nquit\n")
        got = readback.read_bytes()
        bad = [i for i in range(min(len(want), len(got)))
               if want[i] != got[i]]
        if not bad and len(got) >= len(want):
            print(f"      ✅ 되읽기 일치 ({len(want)} 바이트)")
            for f in (tmp, elf, readback):
                f.unlink(missing_ok=True)
            return 0
        print(f"      🔴 {len(bad)} 바이트가 다르다. "
              f"처음: 0x{FLASH_BASE + bad[0]:08x} "
              f"(bin={want[bad[0]]:02x} flash={got[bad[0]]:02x})")

    print(f"\n{tries}번 시도했지만 되읽기가 일치하지 않는다.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
