/* LCD(J25) 하드웨어 결선 — HAL 이 여기서만 나온다.
 *
 * `app/mk_lcd.c` 의 상태기계가 시키는 일(CS·D/CX·RESX·백라이트 조작,
 * SPI 전송 시작)을 실제 주변장치로 옮기고, DMA 완료를 그쪽으로 되돌려
 * 준다. `bsp/mk_ads_io.*` 와 같은 구조다.
 *
 * 결선 [KiCad 넷리스트 확인 2026-08-19,
 *       docs/superpowers/specs/2026-08-19-lcd-hardware-facts.md]
 *
 *      PB12  LCD_CS   (J25.3)   GPIO 출력. R88 10k 풀업
 *      PB13  SCK      (J25.7)   SPI2_SCK  = AF5. 직렬 22Ω(R91)
 *      PB14  MISO     (J25.9)   SPI2_MISO = AF5. 🔴 3차부터 연다 — 되읽기
 *      PB15  MOSI     (J25.6)   SPI2_MOSI = AF5. 직렬 22Ω(R92)
 *      PB6   백라이트 (J25.8)   GPIO 출력
 *      PD13  RESX     (J25.4)   GPIO 출력. R90 10k 풀업
 *      PD15  D/CX     (J25.5)   GPIO 출력
 *      PD14  터치 CS  (J25.11)  GPIO 출력 — **High 고정**
 *      PD12  터치 IRQ (J25.14)  1단계는 안 쓴다
 *
 * 🔴 LCD 와 터치(XPT2046)가 **같은 SPI 버스**다. SCK·MOSI·MISO 가 한 넷이고
 *    CS 만 갈린다. 그래서 mk_lcd_io_init() 이 PD14 를 출력 High(비선택)로
 *    못박고 그 뒤로 다시는 건드리지 않는다 — 떠 있는 채로 두면 터치 칩이
 *    같은 클럭에 반응해 MISO 를 물고 늘어진다. 리셋 직후에는 R89 풀업이
 *    그 일을 하지만, 풀업은 "아직 아무도 안 몬 상태" 일 뿐이라 근거로
 *    삼지 않는다.
 *
 * 🔴 PB6 은 이 보드에서 **백라이트**다. STM32H723 의 AF 표(DS13313 p.75
 *    Table 8)에는 PB6 에 USART1_TX 도 있지만, 이 보드는 그 핀을 J25.8 로
 *    뺐다. USART1 을 PB6 으로 열면 백라이트가 시리얼 파형으로 깜빡인다.
 *
 * 🔴 SPI2 커널 클럭을 **직접 골라야 한다**. SPI1/2/3 의 기본 소스는
 *    pll1_q_ck 인데(RCC_D2CCIP1R.SPI123SEL 리셋값 000), 이 코드를 쓸
 *    당시 펌웨어는 PLL 을 아예 켜지 않아 커널 클럭이 없었다 — SCK 가
 *    한 번도 안 움직였고, 증상은 "화면이 검다" 하나뿐이라 배선 불량과
 *    구분되지 않았다. SPI4 가 멀쩡했던 것은 SPI4/5 의 기본 소스가
 *    APB2 라서다. → per_ck(=hsi_ker_ck, 64 MHz)로 돌린다.
 *
 *    🔴 2026-08-19 에 시스템 클럭이 HSE→PLL1 로 바뀌어 pll1_q_ck 가
 *       실재하게 됐지만, **여기는 그대로 per_ck 를 쓴다.** 화면 SPI 에
 *       클럭 정확도는 아무 뜻이 없고(비동기 명령 버스다), 지금 실기기에서
 *       도는 조합을 이유 없이 흔들지 않는다. 그래서 아래 클럭 표는
 *       sys_ck 가 아니라 MK_SPI2_KERNEL_HZ(= MK_HSI_HZ)에 매여 있다.
 *
 * 🔴 SCK 상한은 20 MHz 다. ILI9488.pdf p.332 §17.4.3 "DBI Type C Option 3
 *    (4-Line SPI System) Timing Characteristics": twc(Serial clock cycle,
 *    Write) MIN 50 ns. 읽기는 trc MIN 150 ns 라 **6.6 MHz** 까지다.
 *
 *    쓰기 클럭은 `lcd.spi_khz` 가 정한다 — **기본 8 MHz**(64/8). 실기기에서
 *    "노이즈 타면 픽셀이 다 깨진다" 가 무작위로 나왔고(2026-08-19), 남은
 *    유력 후보가 "핀 헤더 + 점퍼선에 16 MHz 가 빠른 것" 이라 낮춰서
 *    시작한다. 낮춰서 증상이 사라지면 그것 자체가 신호 무결성 문제라는
 *    진단이 된다 (사용자 결정: "8mhz로 낮춰서 해보자").
 *
 *    되읽기는 io_xfer() 안에서만 64/16 = 4 MHz 로 내려간다.
 *
 * 🔴 SPI 모드 0 (CPOL=0, CPHA=0). ILI9488.pdf p.44 §4.2.1: "The bit is read
 *    by the ILI9488 on the first rising edge of the SCL signal." 즉 상승
 *    엣지 샘플링이고 SCL 은 Low 에서 쉰다.
 */
#ifndef MK_LCD_IO_H
#define MK_LCD_IO_H

#include "../app/mk_lcd.h"

/* GPIO·SPI2·DMA 를 켜고 상태기계에 붙인다.
 * `l` 은 계속 살아 있어야 한다 (mk_ads_io_init 과 같은 규약). */
void mk_lcd_io_init(MkLcd *l);

/* SPI2 인터럽트에서 부른다 — H7 은 EOT 로 완료를 알린다 (bsp/mk_ads_io.h
 * 의 같은 사연). */
void mk_lcd_io_spi_isr(void);

/* SPI2 TX DMA 인터럽트에서 부른다. */
void mk_lcd_io_dma_tx_isr(void);

#endif /* MK_LCD_IO_H */
