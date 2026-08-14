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
#include "mk_uart.h"

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

void DMA1_Stream0_IRQHandler(void)   /* SPI4 RX */
{
    mk_ads_io_dma_rx_isr();
}

void DMA1_Stream1_IRQHandler(void)   /* SPI4 TX */
{
    mk_ads_io_dma_tx_isr();
}
