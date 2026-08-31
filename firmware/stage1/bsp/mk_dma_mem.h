/* DMA 가 닿는 메모리에 버퍼를 두는 방법.
 *
 * 🔴 이 보드에서 가장 조용히 실패하는 자리다. 보통 하듯이
 *
 *        static uint8_t rx[3];
 *
 *    라고 쓰면 링커가 DTCM(0x2000_0000)에 잡는다. 컴파일도 링크도
 *    통과하고 경고 하나 없다. 그런데 DMA1·DMA2 는 DTCM 에 닿지 못한다:
 *
 *        RM0468, p.140: "DTCM-RAM on TCM interface is mapped at the
 *        address 0x2000 0000 and accessible by Cortex-M7, and by MDMA
 *        through AHBS slave bus of the Cortex-M7 CPU."
 *
 *        RM0468, p.106 Bus-master-to-bus-slave 표의 DTCM 행에 표시된
 *        마스터는 `Cortex-M7 - DTCM` 과 `MDMA - AHBS` 뿐이다.
 *
 *    그래서 전송만 조용히 안 된다. 이 보드는 ADS1256(SPI4)도 I2C 세 버스도
 *    DMA1/DMA2 를 쓰므로 전부 해당한다.
 *
 * 쓰는 법 — 선언에 MK_DMA_BUF 를 붙인다:
 *
 *        static uint8_t MK_DMA_BUF s_ads_rx[3];
 *
 *    링커가 .dma_buffers 로 모아 RAM_D2(0x3000_0000)에 둔다.
 *    RM0468 p.139: AHB SRAM1/SRAM2 는 "can be used as DMA buffers to
 *    store peripheral input/output data in D2 domain".
 *
 * 🔴 붙이는 것을 잊어도 아무 일도 일어나지 않는다는 것이 이 문제의 핵심이라,
 *    링크가 끝난 뒤 tools/check_dma_placement.py 가 실제 주소를 확인하고
 *    D2 밖이면 빌드를 실패시킨다. 사람이 기억하는 것에 기대지 않는다.
 */
#ifndef MK_DMA_MEM_H
#define MK_DMA_MEM_H

/* 32바이트 정렬 이유는 링커 스크립트의 .dma_buffers 주석에 있다 —
 * 지금은 D-캐시가 꺼져 있지만 켜면 Cortex-M7 의 캐시 라인이 32바이트다. */
#define MK_DMA_BUF   __attribute__((section(".dma_buffers"), aligned(32)))

/* 검사 도구와 링커 스크립트가 같은 수를 봐야 한다. */
#define MK_DMA_REGION_BASE   0x30000000u
#define MK_DMA_REGION_SIZE   (32u * 1024u)

#endif /* MK_DMA_MEM_H */
