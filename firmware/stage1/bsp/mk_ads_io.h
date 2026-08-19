/* ADS1256 하드웨어 결선 — HAL 이 여기서만 나온다.
 *
 * `app/mk_ads1256.c` 의 상태머신이 시키는 일(CS 내리기, SPI 전송 시작)을
 * 실제 주변장치로 옮기고, 하드웨어 사건(DMA 완료, DRDY 엣지)을 그쪽으로
 * 되돌려 준다.
 *
 * 결선 [넷리스트 확인 2026-08-14]
 *
 *      PE11  CS      (U9 pin20)      GPIO 출력 — SPI4_NSS 를 쓰지 않는다
 *      PE12  SCLK    (U9 pin24)      net `SPI4_SCK`
 *      PE13  DOUT    (U9 pin22)      net `SPI4_MISO`
 *      PE14  DIN     (U9 pin23)      net `SPI4_MOSI`
 *      PE15  DRDY    (U9 pin21)      net `ADS_DRDY` — EXTI15 하강 엣지
 *      PE9   SYNC/PDWN (U9 pin14)    GPIO 출력, High 로 둔다
 *      PE10  RESET  (U9 pin15)       GPIO 출력, High 로 둔다
 *
 * 🔴 CS 를 하드웨어 NSS 로 두지 않는다. ADS1256 은 한 번 내린 CS 를 명령
 *    여러 개에 걸쳐 유지해야 하는데(MUX -> SYNC -> WAKEUP -> ... -> RDATA),
 *    NSS 는 전송마다 올라간다. 상태머신이 CS 를 직접 쥐는 이유다.
 *
 * 🔴 EXTI15 는 DRDY 가 점유한다 (CLAUDE.md §4). LCD 터치 IRQ 가 PD12 인
 *    이유이며, 새 EXTI 를 붙일 때 라인 번호를 먼저 확인한다.
 *
 * 🔴 전원 [넷리스트 확인 2026-08-14] — ADS1256 의 아날로그와 기준전압은
 *    **5V 레일**에 있다:
 *
 *        V5 -> FB1 -> AVDD5 -> U9 pin1  (AVDD)
 *                           -> U10 VIN -> VREF2V5 -> U9 pin4 (VREFP)
 *        V3V3 -> U9 pin16 (DVDD)
 *
 *    디지털만 3.3V 상시다. 그래서 PD10(5V)이 Low 면 **SPI 레지스터는 정상
 *    응답하는데 변환이 안 되고 DRDY 가 영영 안 떨어진다.** 가장 헷갈리는
 *    형태의 고장이라 여기 적어 둔다 — 채널마다 타임아웃만 쌓이면 배선이
 *    아니라 레일부터 본다.
 */
#ifndef MK_ADS_IO_H
#define MK_ADS_IO_H

#include "../app/mk_ads1256.h"

/* SPI4·GPIO·EXTI 를 켜고 상태머신에 붙인다.
 * `a` 는 이 함수가 채워 주는 MkAdsIo 를 들고 계속 살아 있어야 한다. */
void mk_ads_io_init(MkAds *a);

/* EXTI15 인터럽트에서 부른다 (stm32h7xx_it.c). */
void mk_ads_io_drdy_isr(void);

/* 1 kHz 깨우기 (TIM7). stm32h7xx_it.c 에서 부른다.
 *
 * 🔴 수집의 진행은 **슈퍼루프에 있지 않다.** 다음 채널로 넘어가는 것은
 *    SPI 완료 인터럽트 안의 finish() 가, 다 돌고 쉬다가 다시 시작하는 것은
 *    이 타이머가 한다. main() 은 mk_ads_tick() 을 부르지 않는다 — 부르면
 *    수집 주기가 슈퍼루프 주기(mk_i2c 의 HAL 블로킹만으로도 최악 60 ms)에
 *    다시 묶여 채널당 10 ms 가 무너진다. mk_ads_io.c 의 MK_ADS_TICK_HZ
 *    주석에 계산이 있다. */
void mk_ads_io_tick_isr(void);

/* SPI4 인터럽트에서 부른다 — H7 은 EOT 로 완료를 알린다. */
void mk_ads_io_spi_isr(void);

/* SPI4 DMA 인터럽트에서 부른다. */
void mk_ads_io_dma_rx_isr(void);
void mk_ads_io_dma_tx_isr(void);

#endif /* MK_ADS_IO_H */
