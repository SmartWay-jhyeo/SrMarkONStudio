/* 디지털 출력 J18~J20 — 이 핀들을 만지는 **유일한** 파일.
 *
 *      PA4   J18
 *      PA5   J19
 *      PA6   J20
 *
 * 🔴 GPIOA 는 여러 파일이 쓴다 — PA7 이 WS2812 다(mk_ws2812_io.c). 포트가
 *    아니라 핀 번호까지 봐야 가려지고, host/tests/test_firmware_safety.py
 *    가 그렇게 본다.
 *
 * 🔴 MCU GPIO 가 커넥터에 직결이다 (데이터시트 §5.7). 버퍼도 직렬저항도
 *    클램프도 없어 핀당 20 mA 가 상한이고, 밸브는 외부 옵토·드라이버를
 *    거쳐야 한다. PA4·PA5 는 3.3V 전용(절대최대 4.0V)이고 PA6 만 5V 를
 *    견딘다 — 코드로 지킬 수 없고 배선이 정한다.
 *
 * 🔴 리셋 직후 전부 Low 다. 여기를 부르지 않으면 계속 꺼진 상태이고,
 *    그것이 안전한 기본값이다.
 *
 * 이 파일은 핀만 낸다. 무엇을 켤지는 app/mk_solctl 이 설정에서 읽는다.
 */
#ifndef MK_SOL_H
#define MK_SOL_H

#include "../app/mk_solctl.h"

/* GPIOA 의 출력 핀 셋을 연다. 전부 Low(꺼짐)로 시작한다. */
void mk_sol_init(void);

/* mk_solctl 에 넘길 콜백. */
void mk_sol_set(void *ctx, MkSolCh ch, int on);

#endif /* MK_SOL_H */
