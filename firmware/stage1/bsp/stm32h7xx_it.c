/*
 * stm32h7xx_it.c — 최소 인터럽트 핸들러
 *
 * HAL 타임베이스(HAL_GetTick)를 위해 SysTick_Handler 가 필수다. 나머지는
 * startup 의 weak Default_Handler 로 충분하나, 디버깅 편의로 코어 예외
 * 몇 개를 명시한다.
 *
 * 참고 펌웨어(h723_sensor_read)의 것에 USART3_IRQHandler 를 더했다 —
 * 1단계는 명령을 받아야 하므로 수신 인터럽트가 필요하다.
 */
#include "stm32h7xx_hal.h"

#include "mk_ads_io.h"
#include "mk_gnss_io.h"
#include "mk_lcd_io.h"
#include "mk_sol.h"
#include "mk_uart.h"
#include "mk_ws2812_io.h"

void NMI_Handler(void)        { for (;;) {} }
void HardFault_Handler(void)  { for (;;) {} }
void MemManage_Handler(void)  { for (;;) {} }
void BusFault_Handler(void)   { for (;;) {} }
void UsageFault_Handler(void) { for (;;) {} }
void SVC_Handler(void)        {}
void DebugMon_Handler(void)   {}
void PendSV_Handler(void)     {}

void SysTick_Handler(void)
{
    HAL_IncTick();   /* HAL_GetTick() 1 ms 증가 */
}

void USART3_IRQHandler(void)
{
    mk_uart_isr();
}

/* 🔴 아래 셋이 없으면 startup 의 weak Default_Handler 로 빠진다. 그것은
 *    무한루프라, DRDY 가 처음 떨어지거나 DMA 가 처음 끝나는 순간 보드가
 *    통째로 선다. 컴파일도 링크도 통과하므로 굽기 전에는 드러나지 않는다.
 *
 *    3단계 수집기를 붙이면서 함께 배선한다. */

void EXTI15_10_IRQHandler(void)
{
    /* 🔴 EXTI15 는 ADS1256 DRDY 가 점유한다 (CLAUDE.md §4). 10~15 가
     *    한 벡터를 공유하므로, 나중에 이 범위에 EXTI 를 더 붙이면 여기서
     *    함께 분기해야 한다. LCD 터치 IRQ 가 PD12 인 것이 그 때문이다. */
    mk_ads_io_drdy_isr();
}

/* 🔴 수집의 심장. 1 kHz 로 mk_ads_tick() 을 민다 (bsp/mk_ads_io.c).
 *
 *    슈퍼루프가 아니라 여기서 미는 이유: mk_i2c_tick() 의 HAL 블로킹이
 *    한 바퀴에 최악 60 ms 이고 mk_telem_tick() 의 HAL_UART_Transmit 도
 *    블로킹이라, 슈퍼루프가 시작 신호를 쥐고 있으면 채널당 10 ms 가
 *    구조적으로 불가능하다. 이것이 빠지면 컴파일도 부팅도 되고 값도
 *    나오지만, 표본이 조용히 사라진다. */
void TIM7_IRQHandler(void)
{
    mk_ads_io_tick_isr();
}

/* 🔴 디지털 입력 J18(PA4) 전용선. EXTI 는 포트가 아니라 핀 번호로 갈라져
 *    있어(RM0468), "4번 핀"은 어느 GPIO 포트든 항상 EXTI4 다. */
void EXTI4_IRQHandler(void)
{
    mk_sol_exti4_isr();
}

/* 🔴 디지털 입력 J19(PA5)·J20(PA6) 공유선. mk_sol_exti9_5_isr() 안에서
 *    둘을 나눈다. */
void EXTI9_5_IRQHandler(void)
{
    mk_sol_exti9_5_isr();
}

/* 🔴 H7 의 SPI 는 전송 종료를 EOT 플래그로 알리고, HAL 은 그 인터럽트에서
 *    완료 콜백을 부른다. 이것이 없으면 DMA 는 다 옮겨 놓고도 통보가 오지
 *    않아 상태머신이 영원히 기다린다 — 실기기에서 그 상태를 봤다. */
void SPI4_IRQHandler(void)
{
    mk_ads_io_spi_isr();
}

void DMA1_Stream0_IRQHandler(void)   /* SPI4 RX */
{
    mk_ads_io_dma_rx_isr();
}

void DMA1_Stream1_IRQHandler(void)   /* SPI4 TX */
{
    mk_ads_io_dma_tx_isr();
}

void DMA1_Stream2_IRQHandler(void)   /* TIM3_CH2 — WS2812 */
{
    mk_ws2812_io_dma_isr();
}

void DMA1_Stream3_IRQHandler(void)   /* SPI2 TX — LCD */
{
    mk_lcd_io_dma_tx_isr();
}

/* 🔴 호스트 링크 송신(USART3 TX). DMA1 은 Stream0~3 이 SPI4·WS2812·LCD 로
 *    차 있어 DMA2 로 갔다 — 겹치면 수집이 깨지고, 그것이 이 프로젝트에서
 *    가장 나쁜 실패다.
 *
 *    이 벡터가 없으면 첫 조각만 나가고 링크가 통째로 선다. 다음 조각을
 *    잇는 것도, 링이 감기는 자리를 잇는 것도 여기서 시작된다. */
void DMA2_Stream0_IRQHandler(void)   /* USART3 TX — 호스트 링크 */
{
    mk_uart_dma_isr();
}

/* 🔴 SPI4 와 같은 이유로 SPI2 자신의 인터럽트도 필요하다 — H7 은 전송
 *    종료를 EOT 로 알린다. 없으면 첫 전송에서 화면이 그대로 선다. */
void SPI2_IRQHandler(void)
{
    mk_lcd_io_spi_isr();
}

/* 🔴 GNSS UART(Phase 3). SWAP 을 걸어 PC6/PC7 을 넷 이름(GNSS_TX/RX)과
 *    맞춘 것은 bsp/mk_gnss_io.c 상단 주석 참고. */
void USART6_IRQHandler(void)
{
    mk_gnss_io_uart_isr();
}

/* 🔴 PPS 입력 캡처(Phase 3, TIM8 CH3 = PC8). 오버플로(Update)와
 *    Capture/Compare 가 서로 다른 벡터다 — mk_gnss_io.c 의
 *    update_pending 보정이 이 둘의 순서 경합을 다룬다. */
void TIM8_UP_TIM13_IRQHandler(void)
{
    mk_gnss_io_tim_up_isr();
}

void TIM8_CC_IRQHandler(void)
{
    mk_gnss_io_tim_cc_isr();
}
