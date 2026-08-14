/* 전원 레일과 상태 LED — GPIOD 를 만지는 **유일한** 파일.
 *
 * 🔴 GPIOD 에 레일 셋과 상태 LED 가 함께 있다. 한 파일로 묶어 두는 이유는
 *    안전 검사(host/tests/test_firmware_safety.py)가 빠짐없이 돌게 하기
 *    위해서다 — 여러 파일로 흩어지면 하나를 놓친다.
 *
 *      PD8   24V     센서 구동
 *      PD9   14.9V
 *      PD10  5V      쿨링 팬(J34) 직결 · ADS1256 AVDD/VREFP · WS2812 전원
 *      PD11  상태 LED
 *
 * 🔴 리셋 직후에는 3.3V 만 살아 있다. 여기를 부르지 않으면 나머지는 계속
 *    꺼진 상태다 — 그것이 안전한 기본값이다.
 *
 * 이 파일은 핀만 낸다. 순서와 조건은 app/mk_railctl 이 정한다.
 */
#ifndef MK_RAILS_H
#define MK_RAILS_H

#include "../app/mk_railctl.h"

/* GPIOD 를 연다. 레일은 전부 Low(꺼짐)로 시작한다. */
void mk_rails_init(void);

/* mk_railctl 에 넘길 콜백. */
void mk_rails_set(void *ctx, MkRail rail, int on);

/* 상태 LED. */
void mk_rails_led(int on);

#endif /* MK_RAILS_H */
