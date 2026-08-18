/* 디지털 출력 J18~J20 (솔레노이드 등) — HAL 비의존.
 *
 * 설정표의 `sol.j18` · `sol.j19` · `sol.j20` 을 핀 명령으로 옮긴다.
 * 핀을 실제로 내는 것은 bsp/mk_sol.c 다.
 *
 * 🔴 순서도 간격도 없다. 전원 레일과 다르다 — 레일은 돌입 전류 때문에
 *    5V -> 14.9V -> 24V 로 띄우지만(mk_railctl), 이 셋은 서로 무관한
 *    커넥터라 동시에 바뀌어도 된다.
 *
 * 🔴 하드웨어 제약 (데이터시트 §5.7, 넷리스트 확인)
 *
 *      J18 = PA4 · J19 = PA5 · J20 = PA6
 *
 *      MCU GPIO 가 커넥터에 직결이다 — 버퍼도 직렬저항도 클램프도 없다.
 *      핀당 20 mA 가 상한이라 밸브를 직접 못 구동하고 외부 옵토커플러·
 *      드라이버가 반드시 붙는다. 설정 항목의 note 가 그것을 말한다.
 *
 *      PA4·PA5 는 TT 핀이라 3.3V 전용(절대최대 4.0V)이고 PA6 만 5V 를
 *      견딘다. 이 사실은 코드로 지킬 수 없다 — 배선이 정한다.
 *
 * 🔴 이 모듈이 들고 있는 것은 **명령 상태**이지 실측이 아니다. 되읽는
 *    회로가 없으므로 "핀을 High 로 냈다" 까지만 안다 (설계 원칙 4).
 */
#ifndef MK_SOLCTL_H
#define MK_SOLCTL_H

#include <stdint.h>

#include "mk_config.h"

typedef enum {
    MK_SOL_J18 = 0,
    MK_SOL_J19,
    MK_SOL_J20,
    MK_SOL_COUNT
} MkSolCh;

/* 핀 하나를 낸다. `on != 0` 이면 High. 즉시 돌아와야 한다. */
typedef void (*MkSolSet)(void *ctx, MkSolCh ch, int on);

typedef struct MkSolCtl {
    MkSolSet set;
    void    *ctx;

    /* 지금 **명령한** 상태. 실측이 아니다. */
    uint8_t  on[MK_SOL_COUNT];
    uint8_t  primed;            /* 첫 바퀴를 지났나 */
} MkSolCtl;

void mk_solctl_init(MkSolCtl *sc, MkSolSet set, void *ctx);

/* 설정표를 보고 핀을 맞춘다. 매 바퀴 불러도 된다 — 바뀐 채널만 쓴다.
 *
 * 🔴 항목을 못 찾으면 끈 것으로 본다. 설정표에서 항목이 사라지는 것은
 *    실수이고, 실수했을 때 출력이 켜지면 안 된다. */
void mk_solctl_tick(MkSolCtl *sc, MkConfig *cfg);

/* 지금 명령된 상태. */
int mk_solctl_is_on(const MkSolCtl *sc, MkSolCh ch);

#endif /* MK_SOLCTL_H */
