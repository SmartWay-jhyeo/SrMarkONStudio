/* 상태 LED — WS2812 3개로 보드 상태를 밖에서 읽게 한다 (사용자 설계
 * 2026-08-22). HAL 을 모른다 — 색만 정하고, 보내는 것은 main 의 몫이다.
 *
 * 배정 (J21 부터):
 *     LED1  시스템   초록 숨쉬기 = 정상 루프 / 빨강 점멸 = 큐 드랍 발생
 *                    (최근 5초) / 주황 = 워치독 리셋으로 부활(부팅 후 5초)
 *     LED2  측위     초록 = RTK fixed / 노랑 = RTK float / 주황 = 단독·DGPS
 *                    / 빨강 점멸 = 측위 없음(fix 0 또는 3초 무수신)
 *                    / 꺼짐 = GNSS 비활성
 *     LED3  센서     초록 = 켜진 채널 전부 정상 / 노랑 = 단선 의심
 *                    (4~20mA 가 3.6mA 미만 — NAMUR NE43 의 하한 규칙)
 *                    / 빨강 점멸 = 고장(I2C 읽기 실패, ain 값 두절)
 *                    / 꺼짐 = 켜진 채널 없음
 *
 * 패턴 규칙: 안정 = 상시 점등, 진행 = 숨쉬기, 문제 = 점멸. 색만으로
 * 가르지 않는다 — 색약이어도 패턴으로 읽힌다.
 *
 * 🔴 설계 원칙 3(센서 미연결은 정상): 판정은 **켜진(enabled) 채널만**
 *    본다. 안 쓰는 커넥터가 LED 를 노랗게 만들면 안 된다.
 * 🔴 설계 원칙 4(명령 상태 ≠ 실제 상태): LED2·3 은 실측(수신된 fix,
 *    실제 mA, I2C status)에서만 나온다 — 설정값을 근거로 초록을 켜지
 *    않는다.
 */
#ifndef MK_STATLED_H
#define MK_STATLED_H

#include <stdint.h>

#include "mk_ads1256.h"
#include "mk_config.h"
#include "mk_gnss.h"
#include "mk_i2c.h"
#include "mk_ws2812.h"

/* 상태 LED 는 셋이다 — 체인(MK_LED_COUNT=4)의 앞 셋을 쓴다. */
#define MK_STATLED_COUNT 3u

/* 4~20mA 루프의 단선 의심 문턱. NAMUR NE43 이 3.6mA 미만을 "회로 고장"
 * 대역으로 정의한다 — 살아 있는 전송기는 최소 3.8mA 를 지킨다. */
#define MK_STATLED_BREAK_MA 3.6f

/* ain 값 두절 판정 — 마지막 표본이 이보다 늙으면 채널이 죽은 것이다.
 * 수집 주기 기본 100ms 의 20배: 주기를 크게 잡은 채널도 오탐하지 않게. */
#define MK_STATLED_STALE_MS 2000

/* 측위 무수신 판정 — GGA 는 1Hz 다. 3초면 세 번을 놓친 것이다. */
#define MK_STATLED_FIX_STALE_MS 3000

typedef struct {
    MkConfig *cfg;
    MkAds    *ads;
    MkI2c    *i2c;      /* NULL 허용 — 그 판정만 건너뛴다 */
    MkGnss   *gnss;     /* NULL 허용 */

    /* 부팅이 워치독 리셋이었나 — main 이 RCC RSR 에서 읽어 넣는다.
     * 이 모듈은 주변장치를 모른다. */
    int       woke_from_iwdg;

    /* 드랍 감시 — 합계가 늘어난 순간부터 5초간 빨강 점멸. */
    uint32_t  last_drops;
    int64_t   drops_bad_until;

    /* 측위 신선도 — fix_count 가 변한 마지막 시각. */
    uint32_t  last_fix_count;
    int64_t   last_fix_ms;
    int       fix_seen;
} MkStatLed;

void mk_statled_init(MkStatLed *s, MkConfig *cfg, MkAds *ads,
                     int woke_from_iwdg);
void mk_statled_attach_i2c(MkStatLed *s, MkI2c *i2c);
void mk_statled_attach_gnss(MkStatLed *s, MkGnss *g);

/* 지금 시각의 세 색을 낸다. 매 바퀴 불러도 싸다 — 계산뿐이다. */
void mk_statled_colors(MkStatLed *s, int64_t now_ms,
                       MkRgb out[MK_STATLED_COUNT]);

#endif /* MK_STATLED_H */
