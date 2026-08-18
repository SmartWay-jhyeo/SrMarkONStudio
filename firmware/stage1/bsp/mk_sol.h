/* 디지털 입력 J18~J20 — 이 핀들을 만지는 **유일한** 파일.
 *
 *      PA4   J18
 *      PA5   J19
 *      PA6   J20
 *
 * 🔴 GPIOA 는 여러 파일이 쓴다 — PA7 이 WS2812 다(mk_ws2812_io.c). 포트가
 *    아니라 핀 번호까지 봐야 가려지고, host/tests/test_firmware_safety.py
 *    가 그렇게 본다.
 *
 * 🔴 입력이다(사용자 확정 2026-08-18, 넷리스트 확인). 커넥터 pin1 이 MCU
 *    핀에 직결이고 사이에 부품이 없다 — 옵토커플러가 커넥터 반대편에
 *    붙고, 보드는 그 신호를 **읽는다.** 출력으로 잡으면 반대편
 *    포토트랜지스터와 맞선다. PA4·PA5 는 3.3V 전용(절대최대 4.0V)이고
 *    PA6 만 5V 를 견디지만, 옵토가 앞에 있어 바깥 전압이 핀에 직접
 *    닿지 않는다.
 *
 * 🔴 핀마다 따로 `HAL_GPIO_Init` 을 부른다. 셋을 OR 로 묶어 한 번에 열지
 *    않는다 — 같은 포트에 WS2812(PA7)가 있고, 나중에 이 자리를 손볼
 *    사람이 무심코 Pin 마스크를 넓히면 그 핀까지 삼킬 수 있다. 핀별
 *    호출로 그 실수의 반경을 한 핀으로 좁힌다.
 *
 * 🔴 EXTI 는 둘로 갈린다 — PA4 는 EXTI4 를 혼자 쓰고, PA5·PA6 은 EXTI9_5
 *    를 함께 쓴다(RM0468). 벡터 안에서 어느 선인지 갈라야 한다.
 *
 * 🔴 ISR 은 판단하지 않는다. 핀 상태와 시각(`mk_time_ms()`)만 잡아
 *    `mk_solctl_on_edge()` 로 넘기고 즉시 나온다. 디바운스·극성 반전은
 *    app/mk_solctl.c 의 슈퍼루프 쪽이 한다.
 *
 * 🔴 상태의 근거는 레벨이다(사용자 확정 2026-08-18). `mk_sol_read()` 가
 *    `MkSolRead` 콜백으로 `mk_solctl_init()` 에 등록되어, 슈퍼루프가 매
 *    바퀴 이 함수로 세 핀을 직접 읽는다 — 엣지(ISR)를 하나 놓쳐도 다음
 *    안정 구간에서 레벨이 스스로 회복시킨다. ISR 은 여전히 "언제 바뀌었나"
 *    의 정밀 시각만 댄다.
 */
#ifndef MK_SOL_H
#define MK_SOL_H

#include "../app/mk_solctl.h"

/* GPIOA 의 입력 핀 셋을 연다(내부 풀업, 양쪽 엣지). 그 자리에서 실제
 * 핀을 동기적으로 읽어 `sc` 의 초기 상태를 세운다(mk_solctl_prime) —
 * 첫 엣지가 언제 올지 모르는 채로 $STAT 이 지금 상태를 답해야 한다.
 *
 * `sc` 는 이 함수가 들고 계속 살아 있어야 한다 — ISR 이 이 포인터로
 * 콜백한다. */
void mk_sol_init(MkSolCtl *sc);

/* 채널 하나의 raw 핀 레벨을 즉시 읽어 돌려준다(0/1) — `MkSolRead` 서명과
 * 맞는다. main.c 가 `mk_solctl_init(&s_sol, mk_sol_read, NULL)` 로 등록해,
 * `mk_solctl_tick()` 이 매 바퀴 이 함수를 불러 상태의 근거로 삼는다. */
int mk_sol_read(void *ctx, MkSolCh ch);

/* EXTI4 인터럽트에서 부른다(stm32h7xx_it.c) — PA4(J18) 전용선. */
void mk_sol_exti4_isr(void);

/* EXTI9_5 인터럽트에서 부른다 — PA5(J19)·PA6(J20) 공유선. 벡터 안에서
 * 어느 핀이 걸렸는지 이 함수가 가른다. */
void mk_sol_exti9_5_isr(void);

#endif /* MK_SOL_H */
