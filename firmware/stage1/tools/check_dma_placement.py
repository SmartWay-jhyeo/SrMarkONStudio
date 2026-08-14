"""링크가 끝난 이미지에서 DMA 버퍼가 실제로 DMA 가 닿는 곳에 있는지 본다.

🔴 왜 도구가 필요한가 — 잊어도 아무 일이 일어나지 않기 때문이다.

   `static uint8_t rx[3];` 는 DTCM(0x2000_0000)에 잡히고, DMA1·DMA2 는
   거기 닿지 못한다(RM0468 p.140, p.106 표). 그런데 컴파일도 링크도
   통과하고 경고가 없다. 실기기에서 전송만 조용히 안 된다.

   MK_DMA_BUF(bsp/mk_dma_mem.h)를 붙이면 해결되지만, **붙이는 것을 잊는
   것이 정확히 이 결함의 발현 방식**이다. 사람의 기억을 검사로 바꾼다.

무엇을 보나
  1. .dma_buffers 구역이 통째로 RAM_D2(0x3000_0000, 32K) 안에 있는가
  2. 그 안의 심볼이 하나도 DTCM 에 걸치지 않는가
  3. DMA 를 쓸 심볼이 DTCM 에 남아 있지 않은가 (이름 규칙으로 훑는다)

빌드가 끝날 때마다 Makefile 이 부른다. D2 밖이면 종료 코드 1 로 빌드를
세운다 — 굽고 나서 "왜 안 되지" 를 하지 않기 위해서다.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

#: bsp/mk_dma_mem.h 의 MK_DMA_REGION_* 와 같아야 한다.
D2_BASE = 0x30000000
D2_SIZE = 32 * 1024
D2_END = D2_BASE + D2_SIZE

#: RM0468 p.139 — AXI SRAM 도 "all system masters except BDMA" 라 DMA1/DMA2
#: 가 닿는다. 지금은 안 쓰지만 나중에 큰 버퍼가 필요하면 여기로 간다.
AXI_BASE = 0x24000000
AXI_END = AXI_BASE + 128 * 1024

#: DMA 가 못 닿는 곳. 여기 있으면 안 된다.
DTCM_BASE = 0x20000000
DTCM_END = DTCM_BASE + 128 * 1024

#: 이름만 보고도 DMA 버퍼로 짐작되는 것들. MK_DMA_BUF 를 빠뜨린 심볼을
#: 잡으려는 그물이지, 이것으로 충분하다는 뜻은 아니다.
SUSPECT = re.compile(r"(_dma_|_rx_buf|_tx_buf|_dmabuf)", re.IGNORECASE)


#: 입력 구역 한 줄: `.bss.s_ads_rx   0x2000003c   0x8 build/main.o`
_ONE_LINE = re.compile(
    r"^\s*(\.[\w.$-]+)\s+0x([0-9a-fA-F]+)\s+0x([0-9a-fA-F]+)\s", re.MULTILINE)

#: 🔴 이름이 길면 ld 가 줄을 접는다. 실제로 이렇게 나온다:
#:
#:      .bss.s_dma_probe
#:                     0x2000003c        0x8 build/main.o
#:
#:   처음에 한 줄짜리만 읽도록 짜서, DTCM 에 잡힌 버퍼를 하나도 못 잡았다.
#:   되돌림 검사에서 A 케이스가 통과하는 것을 보고 알았다.
_WRAPPED = re.compile(
    r"^\s*(\.[\w.$-]+)\s*\n\s+0x([0-9a-fA-F]+)\s+0x([0-9a-fA-F]+)\s",
    re.MULTILINE)

#: `-fdata-sections` 가 만드는 구역 이름에서 변수 이름을 떼어낸다.
_DATA_SECTION = re.compile(r"^\.(?:bss|data|rodata)\.(.+)$")


def _parse_map(text: str):
    """GNU ld 맵에서 (심볼, 주소) 와 구역 배치를 뽑는다.

    맵의 형식은 툴체인 판마다 흔들리므로, 파싱에 성공했다는 사실 자체를
    호출한 쪽이 확인할 수 있게 돌려준다 — 조용히 0건을 찾고 통과하는 것이
    이런 검사가 죽는 가장 흔한 방식이다.
    """
    # 🔴 같은 것을 두 번 세지 않는다. 한 줄짜리와 접힌 줄짜리 정규식이
    #    겹쳐 물 수 있고, 그러면 같은 심볼이 두 번 보고돼 읽는 사람이
    #    문제가 두 개인 줄 안다.
    seen: dict[str, int] = {}
    regions: dict[str, tuple[int, int]] = {}

    for pat in (_ONE_LINE, _WRAPPED):
        for m in pat.finditer(text):
            name = m.group(1)
            addr, size = int(m.group(2), 16), int(m.group(3), 16)
            if size <= 0:
                continue
            # 출력 구역(.bss, .data, .dma_buffers …) 은 배치를 본다.
            if name not in regions:
                regions[name] = (addr, size)
            # 🔴 `static` 변수는 맵에 심볼 줄로 나오지 않는다. `-fdata-sections`
            #    가 만든 **구역 이름**에 변수 이름이 들어 있는 것이 전부다.
            #    이것을 안 읽으면 정적 DMA 버퍼를 통째로 놓친다 — 그리고
            #    DMA 버퍼는 거의 항상 static 이다.
            hit = _DATA_SECTION.match(name)
            if hit:
                seen.setdefault(hit.group(1), addr)

    # `                0x0000000030000000                mk_some_global`
    for m in re.finditer(r"^\s+0x([0-9a-fA-F]{8,16})\s{2,}([A-Za-z_]\w*)\s*$",
                         text, re.MULTILINE):
        seen.setdefault(m.group(2), int(m.group(1), 16))

    return sorted(seen.items()), regions


def _where(addr: int) -> str:
    if D2_BASE <= addr < D2_END:
        return "RAM_D2"
    if AXI_BASE <= addr < AXI_END:
        return "AXISRAM"
    if DTCM_BASE <= addr < DTCM_END:
        return "DTCM"
    return f"0x{addr:08x}"


def main(argv: list[str]) -> int:
    path = Path(argv[1]) if len(argv) > 1 else Path("build/markon_stage1.map")
    if not path.exists():
        print(f"맵 파일이 없다: {path} — 먼저 빌드해야 한다", file=sys.stderr)
        return 2

    text = path.read_text(encoding="utf-8", errors="replace")
    syms, regions = _parse_map(text)

    # 🔴 파싱이 통째로 실패해도 "문제 없음" 으로 보이면 안 된다.
    if not syms:
        print(f"맵에서 심볼을 하나도 못 읽었다 ({path}). 툴체인이 바뀌어 "
              f"형식이 달라졌을 수 있다 — 검사가 헛돌고 있으므로 세운다",
              file=sys.stderr)
        return 2

    problems: list[str] = []

    region = regions.get(".dma_buffers")
    if region is not None:
        base, size = region
        if size > 0 and not (D2_BASE <= base and base + size <= D2_END):
            problems.append(
                f"  .dma_buffers 가 RAM_D2 밖이다: "
                f"0x{base:08x}~0x{base + size:08x} ({_where(base)})")
        else:
            print(f"  ok   .dma_buffers  0x{base:08x} +{size}B  ({_where(base)})")
    else:
        # 아직 DMA 버퍼를 쓰는 코드가 없으면 구역이 안 생긴다. 정상이다.
        print("  --   .dma_buffers 구역이 없다 (아직 DMA 버퍼를 쓰지 않는다)")

    for name, addr in syms:
        if not SUSPECT.search(name):
            continue
        if D2_BASE <= addr < D2_END or AXI_BASE <= addr < AXI_END:
            print(f"  ok   {name:28} 0x{addr:08x}  ({_where(addr)})")
        elif DTCM_BASE <= addr < DTCM_END:
            problems.append(
                f"  {name} 이 DTCM 에 있다 (0x{addr:08x}). DMA1·DMA2 는 "
                f"여기 닿지 못한다 — MK_DMA_BUF 를 붙였는지 확인한다")

    if problems:
        print("\n🔴 DMA 가 닿지 못하는 곳에 버퍼가 있다:", file=sys.stderr)
        print("\n".join(problems), file=sys.stderr)
        print("\n근거: RM0468 p.140 / p.106 — DTCM 은 Cortex-M7 과 MDMA 만 "
              "접근한다. 배치 방법은 bsp/mk_dma_mem.h 참조.", file=sys.stderr)
        return 1

    print(f"\nDMA 배치 OK (심볼 {len(syms)}개 확인)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
