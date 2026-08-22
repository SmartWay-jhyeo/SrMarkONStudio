#include "mk_iwdg.h"

#include "stm32h7xx_hal.h"

/* 5초 = LSI(32 kHz 공칭) / 64 분주 → 2 ms 눈금 × 2500.
 *
 * 🔴 LSI 는 RC 라 몇 % 흔들린다 (RM0468 — 정밀 클럭이 아니다). 5초가
 *    4.8초가 되어도 상관없는 자리다: 슈퍼루프 한 바퀴의 최악은
 *    mk_i2c_tick 60 ms(main.c 주석), $CFG,SAVE 의 섹터 소거도 실측 2초
 *    안에 응답이 나온다(restore_board_config.py 가 timeout 2s 로 ACK 를
 *    받는다) — 예산 대비 자릿수가 다르다. */
#define IWDG_RELOAD_5S 2500u

static IWDG_HandleTypeDef s_iwdg;

void mk_iwdg_init(void)
{
    /* 🔴 GDB 가 코어를 세우면 워치독도 같이 선다. 이것이 없으면 실행 중
     *    보드에 붙어 변수 하나 보는 사이 5초가 지나 보드가 리셋된다 —
     *    "GDB 로 들여다보면 보드가 리셋된다"(CLAUDE.md §4)의 재판이 된다.
     *    HardFault 무한루프는 코어가 '멈춤'이 아니라 '도는' 상태라
     *    이 비트와 무관하게 잡힌다. */
    __HAL_DBGMCU_FREEZE_IWDG1();

    s_iwdg.Instance = IWDG1;
    s_iwdg.Init.Prescaler = IWDG_PRESCALER_64;
    s_iwdg.Init.Reload = IWDG_RELOAD_5S;
    s_iwdg.Init.Window = IWDG_WINDOW_DISABLE;
    (void)HAL_IWDG_Init(&s_iwdg);   /* 켜기 실패는 없다 — 레지스터 쓰기뿐 */
}

void mk_iwdg_kick(void)
{
    (void)HAL_IWDG_Refresh(&s_iwdg);
}
