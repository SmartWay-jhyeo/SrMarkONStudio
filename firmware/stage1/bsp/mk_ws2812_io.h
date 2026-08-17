/* WS2812 체인을 PA7 로 내보낸다 — TIM3_CH2 PWM + DMA.
 *
 * 근거:
 *   PA7 의 AF2 = TIM3_CH2   (DS13313 Rev 1, p.72, Table 8)
 *   DMA_REQUEST_TIM3_CH2    (stm32h7xx_hal_dma.h:247)
 *   배선 PA7 ─ R84(390Ω) ─ J21   (넷리스트 확인)
 *
 * 🔴 왜 GPIO 토글이 아닌가. WS2812 는 한 비트가 1.25us 이고 High 폭
 *    0.35us / 0.70us 로 0 과 1 을 가른다. 소프트웨어 지연으로 맞추면 인터럽트
 *    하나에 프레임이 깨지고, 이 펌웨어는 SPI4·USART3·EXTI 를 동시에 쓴다.
 *    타이머가 파형을 만들고 DMA 가 듀티를 갈아 끼우면 CPU 가 늦어도 파형은
 *    흔들리지 않는다.
 *
 * 🔴 DMA1 Stream2 를 쓴다. Stream0·Stream1 은 SPI4(ADS1256)가 이미 쓴다.
 *
 * 🔴 버퍼를 이쪽이 들고 있다. app/mk_ws2812 는 DMA 가 닿는 메모리가 어디인지
 *    알아서는 안 되고(HAL 비의존), 그렇다고 아무 데나 잡으면 DTCM 에 들어가
 *    조용히 전송이 안 된다(bsp/mk_dma_mem.h). 그래서 여기서 자리를 내주고
 *    app 이 그 위에 직접 인코딩한다 — 복사도 없고 잘못 놓일 수도 없다.
 */
#ifndef MK_WS2812_IO_H
#define MK_WS2812_IO_H

#include <stddef.h>
#include <stdint.h>

/* PA7·TIM3·DMA1_Stream2 를 연다. 체인은 꺼진 채로 시작한다. */
void mk_ws2812_io_init(void);

/* 인코딩해 넣을 자리. cap 에 슬롯 수를 돌려준다. */
uint16_t *mk_ws2812_io_buffer(size_t *cap);

/* 버퍼 앞 n 슬롯을 내보낸다. 앞 전송이 아직 안 끝났으면 0 을 돌려준다.
 *
 * 🔴 기다리지 않고 건너뛴다. 한 프레임 늦게 바뀌는 것은 눈에 안 보이지만,
 *    여기서 막히면 수집과 통신이 함께 멈춘다 — 그쪽이 훨씬 비싸다. */
int mk_ws2812_io_send(size_t n);

/* 전송 중인가. */
int mk_ws2812_io_busy(void);

/* DMA1_Stream2 인터럽트. stm32h7xx_it.c 에서만 부른다. */
void mk_ws2812_io_dma_isr(void);

#endif /* MK_WS2812_IO_H */
