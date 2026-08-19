/* GNSS UART(USART6) + PPS 입력 캡처(TIM8 CH3) — 이 핀들을 만지는 유일한 곳.
 *
 *      USART6_RX(전기적)  PC6 (J16 핀3, GNSS_TX)  AF7, SWAP 필요
 *      USART6_TX(전기적)  PC7 (J16 핀4, GNSS_RX)  AF7, SWAP 필요
 *      TIM8_CH3           PC8 (J16 핀5, GNSS_PPS) AF3
 *
 * 데이터시트 §5.5 · DS13313 Rev 1 p.72 Table 8 (PC6/PC7/PC8 의 AF 목록).
 *
 * 🔴 **SWAP 이 필요하다.** Table 8 은 PC6 을 USART6_TX, PC7 을 USART6_RX 로
 *    싣는다 — 그런데 데이터시트 §5.5 의 넷 이름은 "GNSS_TX → PC6"(모듈이
 *    보내는 쪽)이다. 모듈의 TX 가 MCU 의 "TX 로 이름 붙은" 핀에 물린
 *    교차 배선이다. RM0468 p.2116, USART_CR2 Bit 15 SWAP:
 *    "The TX and RX pins functions are swapped. This enables to work in
 *    the case of a cross-wired connection to another UART." — 정확히 이
 *    상황을 위한 비트다. SWAP=1 이면 PC6 이 전기적으로 RX(모듈→MCU),
 *    PC7 이 전기적으로 TX(MCU→모듈)가 되어 넷 이름과 맞아떨어진다.
 *
 *    🔴 [실기기 미검증] 이 SWAP 해석은 데이터시트 §5.5 의 넷 이름과
 *       RM0468 의 레지스터 설명을 조합한 추론이다. UM981 이 실제로
 *       PC7(→GNSS_RX)에서 명령을 받는지는 보드로 확인해야 한다 — 지금은
 *       수신(NMEA 파싱)만 쓰므로 SWAP 이 틀렸다면 "아예 안 들어온다"로
 *       바로 드러난다(수신 방향은 명확히 검증 가능).
 *
 * 🔴 **TIM8, TIM3 이 아니다.** PC8 의 AF 목록(Table 8)에는 TIM3_CH3(AF2)
 *    도 있다. 그런데 TIM3 은 WS2812(bsp/mk_ws2812_io.c)가 800kHz PWM 으로
 *    이미 통째로 쓰고 있다 — 같은 카운터를 PPS 입력 캡처로도 쓰면 WS2812
 *    타이밍이 깨지거나 PPS 캡처 주기(1초)가 WS2812 의 짧은 주기(1.25us)에
 *    맞춰 재구성돼 버린다. TIM8(AF3)은 이 저장소에서 전혀 안 쓰고 있어
 *    충돌이 없다.
 *
 * 🔴 **EXTI 가 아니라 타이머 입력 캡처다** (계획서 §7.3 요구, CLAUDE.md
 *    §1.2 — Q2 의 time_sync 가 PPS 를 HAL_GetTick 으로 잡아 분해능이
 *    1ms 였던 것을 그대로 이식하지 말라는 지시). EXTI 로 잡으면 ISR 이
 *    걸리는 시각의 분해능이 SysTick(1ms)에 묶인다. TIM8 은 여기서 1MHz
 *    로 돌려(PSC=63, 64MHz/64) 1us 분해능을 낸다 — CH3 가 PC8 을 직접
 *    보므로 EXTI15(ADS1256 DRDY)·EXTI4·EXTI9_5(sol) 와 라인 충돌도 없다.
 *
 * 🔴 ISR 은 캡처만 한다. 판단(짝짓기·등급)은 app/mk_timeax.c 가
 *    슈퍼루프에서 한다 — 이 저장소의 관례(mk_sol.c·mk_ads_io.c 와 같다).
 */
#ifndef MK_GNSS_IO_H
#define MK_GNSS_IO_H

#include <stddef.h>
#include <stdint.h>

void mk_gnss_io_init(uint32_t baud);

/* 수신 바이트 하나를 꺼낸다. 있으면 1(그리고 *out 을 채운다), 없으면 0.
 * 슈퍼루프가 이걸로 mk_gnss_feed() 를 반복해서 먹인다. */
int mk_gnss_io_read_byte(uint8_t *out);

/* 링이 넘쳐 버린 바이트 수(진단용). */
uint32_t mk_gnss_io_rx_overruns(void);

/* 새 PPS 캡처가 있으면 1 을 돌려주고 *dev_us 에 확장 tick(µs, 부팅 후
 * 단조 증가)을 채운다. 없으면 0. mk_timeax_on_pps() 에 그대로 넘긴다. */
int mk_gnss_io_pps_take(uint64_t *dev_us);

/* "지금" 확장 tick(µs). PPS 가 한 번도 없었어도 항상 값이 있다 — TIM8 은
 * 부팅 직후부터 자유 구동한다. mk_timeax_on_rmc()·mk_timeax_tick() 에
 * 넘길 "지금" 값이 필요할 때 쓴다. */
uint64_t mk_gnss_io_now_us(void);

/* 모듈로 바이트를 그대로 내보낸다(규격 §4.1) — MkGnssSend(mk_gnss.h)와
 * 정확히 같은 시그니처다. `ctx` 는 쓰지 않는다(UART 핸들이 정적이라
 * 필요 없다). 줄바꿈을 붙이지 않는다 — 그것은 부르는 쪽(app/mk_hostlink.c
 * 의 on_gnss, app/mk_gnssctl.c)이 한다. 성공하면 1, 실패하면 0.
 *
 * 🔴 SWAP 이 이미 mk_gnss_io_init() 에서 걸려 있다(이 파일 상단 주석) —
 *    PC7 이 전기적으로 TX(MCU→모듈)이므로 HAL_UART_Transmit 이 그대로
 *    나간다. 짧은 명령(23바이트 이하 + CRLF)이라 블로킹으로 충분하다 —
 *    mk_uart_write()(호스트 링크)와 같은 판단. */
int mk_gnss_io_write(void *ctx, const char *data, size_t len);

/* USART6 인터럽트에서 부른다(stm32h7xx_it.c). */
void mk_gnss_io_uart_isr(void);

/* TIM8 Capture/Compare 인터럽트에서 부른다 — CH3(PPS) 캡처. */
void mk_gnss_io_tim_cc_isr(void);

/* TIM8 Update 인터럽트에서 부른다 — 오버플로 확장 카운터. */
void mk_gnss_io_tim_up_isr(void);

#endif /* MK_GNSS_IO_H */
