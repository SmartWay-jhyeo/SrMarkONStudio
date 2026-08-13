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
