/* 전원 레일 순차 기동 — HAL 비의존.
 *
 * 이 보드는 리셋 직후 3.3V 만 살아 있다. 나머지 셋은 MCU 가 GPIO 로 올려야
 * 나온다 (데이터시트 §4, CLAUDE.md §4):
 *
 *      PD10  5V     쿨링 팬(J34) 직결
 *      PD9   14.9V
 *      PD8   24V    센서 구동
 *
 * 🔴 순서가 있다. 5V -> 14.9V -> 24V 로, 사이에 `pwr.seq_delay_ms` 만큼
 *    띄운다. 한꺼번에 올리면 돌입 전류가 겹친다 — 참고 펌웨어
 *    (h723_sensor_read)도 500 ms 간격으로 올린다.
 *
 * 🔴 5V 는 켠 뒤 **절대 내리지 않는다.** 쿨링 팬이 직결이고 상시 동작이
 *    요구사항이다. 이 모듈이 그것을 **구조적으로** 보장한다 — 끄라는
 *    요청을 받아도 무시한다. 호출하는 쪽의 선의에 기대지 않는다.
 *
 *    저전력 모드·에러 처리·레일 재시작 루틴에서 실수로 내리는 것이
 *    CLAUDE.md §4 가 경고하는 상황이고, 그때 이 함수가 마지막 방어선이다.
 *
 * 🔴 이 모듈이 들고 있는 것은 **명령 상태**이지 실측이 아니다. 피드백
 *    회로가 없으므로 "핀을 High 로 냈다" 까지만 안다. 호스트는 이것을
 *    `정상 ON` 이 아니라 `ON 명령됨` 으로 표시해야 한다 (설계 원칙 4).
 */
#ifndef MK_RAILCTL_H
#define MK_RAILCTL_H

#include <stdint.h>

typedef enum {
    MK_RAIL_5V = 0,
    MK_RAIL_14V9,
    MK_RAIL_24V,
    MK_RAIL_COUNT
} MkRail;

/* 핀 하나를 낸다. `on != 0` 이면 High. 즉시 돌아와야 한다. */
typedef void (*MkRailSet)(void *ctx, MkRail rail, int on);

/* 🔴 이름 있는 구조체다. mk_hostlink 가 `struct MkRailCtl *` 로 전방
 *    선언해서 받기 때문이다 — 익명 typedef 로 두면 그쪽의 `struct
 *    MkRailCtl` 이 전혀 다른 타입이 되고 포인터 형식이 안 맞는다.
 *    MkAds 에서 같은 것을 이미 겪었다. */
typedef struct MkRailCtl {
    MkRailSet set;
    void     *ctx;

    /* 지금 **명령한** 상태. 실측이 아니다. */
    uint8_t   on[MK_RAIL_COUNT];

    /* 마지막으로 레일을 올린 시각. 다음 단계를 띄우는 기준이다. */
    int64_t   last_step_ms;
    uint8_t   started;          /* 첫 tick 을 지났나 */
} MkRailCtl;

void mk_railctl_init(MkRailCtl *rc, MkRailSet set, void *ctx);

/* 원하는 상태를 반영한다. 매 바퀴 불러도 된다.
 *
 * `want_14v9`·`want_24v` 는 설정에서 온다. 5V 는 인자가 없다 — 늘 켠다.
 *
 * 올릴 때는 `seq_delay_ms` 간격으로 한 단계씩 나아간다. 내릴 때는 즉시
 * 내린다(부하를 줄이는 방향이라 기다릴 이유가 없다). 다만 5V 는 내리지
 * 않는다. */
void mk_railctl_tick(MkRailCtl *rc, int want_14v9, int want_24v,
                     uint16_t seq_delay_ms, int64_t now_ms);

/* 지금 명령된 상태. `$STAT` 의 `rails` 가 이것을 실어야 한다 —
 * 설정표를 읽으면 "사용자가 원하는 것" 이지 "보드가 낸 것" 이 아니다. */
int mk_railctl_is_on(const MkRailCtl *rc, MkRail rail);

/* 아직 순차 기동이 끝나지 않았나. 화면이 "기동 중" 을 표시할 수 있다. */
int mk_railctl_settling(const MkRailCtl *rc, int want_14v9, int want_24v);

#endif /* MK_RAILCTL_H */
