/* MarkON 호스트 링크 — 명령 디스패치와 모드 판정. HAL 비의존.
 *
 * 🔴 이 파일은 stm32h7xx_hal.h 를 include 하지 않는다. 시각도 UART 도
 *    바깥에서 넣어 준다. 그래서 호스트에서 시계를 손으로 돌려가며
 *    3초 타임아웃 같은 것을 시험할 수 있다.
 *
 * mk_framing 과 mk_json 이 여기서 처음 만난다.
 *
 * 규격: protocol/specification.md §4·§5·§6
 */
#ifndef MK_HOSTLINK_H
#define MK_HOSTLINK_H

#include <stddef.h>
#include <stdint.h>

#include "mk_cfgwire.h"
#include "mk_config.h"
#include "mk_framing.h"

/* 규격 §6.2 */
#define MK_HB_TIMEOUT_MS   3000
#define MK_HB_INTERVAL_MS  1000

typedef enum {
    MK_MODE_RUN = 0,     /* 부팅 직후 기본값 */
    MK_MODE_CONFIG
} MkMode;

/* 제어 모드 — 규격 §6.4. `MkMode` 와 **다른 축**이다.
 *
 * 🔴 저쪽은 하트비트로 관측되고 이쪽은 `$MODE` 로 선언된다. 한 변수에
 *    섞으면 정해지는 방식이 뒤엉킨다.
 *
 *    필요한 이유: 같은 명령(밸브 열기)이 벤치에서는 배선 확인이고 운전
 *    중에는 공정을 돌리는 일이다. 항목이 같고 의도가 다르므로 항목의
 *    성질로는 구분할 수 없다. */
typedef enum {
    MK_CTL_ACTIVE = 0,   /* 부팅 기본값 — 보드는 혼자서도 제 일을 한다 */
    MK_CTL_TEST
} MkCtlMode;

/* 한 줄을 내보낸다. 줄끝(`\r\n` 또는 `\n`)은 이미 붙어 있다. */
typedef void (*MkEmit)(void *ctx, const char *line, size_t len);

/* 설정을 영구 저장한다. 없으면(NULL) $CFG,SAVE 가 BUSY 를 돌려준다.
 * 반환: 성공이면 0. Flash 를 다루는 것은 bsp 쪽이다. */
typedef int (*MkCfgSave)(void *ctx);

typedef struct {
    MkEmit      emit;
    void       *ctx;

    const char *device_id;
    const char *fw;
    const char *board_rev;

    /* 설정 저장소. NULL 이면 $CFG 명령이 전부 UNSUPPORTED 다 —
     * 1단계 펌웨어가 그 상태였다. */
    MkConfig        *cfg;
    const MkFieldBit *fields;
    size_t            n_fields;
    MkCfgSave         save;
    void             *save_ctx;

    /* 수집기. 붙어 있으면 $STAT 이 채널별 큐 깊이·최고치·유실을 보고한다.
     *
     * 🔴 없으면 `queues` 가 빈 배열이다. 0 을 채워 보내지 않는다 —
     *    "채널이 없다" 와 "채널이 있는데 유실이 0" 은 다른 말이고,
     *    유실을 찾으려고 이 창구를 보는 사람에게는 그 차이가 전부다. */
    struct MkAds     *ads;

    /* 레일 제어기. 붙어 있으면 $STAT 의 rails 가 **실제 명령 상태**를
     * 싣는다. 없으면 전부 false 다 — 설정표를 대신 읽지 않는다. */
    struct MkRailCtl *rails;

    /* 디지털 입력 J18~J20. 붙어 있으면 $STAT 의 `din` 이 **실측 상태**를
     * 싣는다(규격 §7.4). 없으면 빈 배열이다. */
    struct MkSolCtl *sol;

    /* 제어 모드 (규격 §6.4). `mk_hostlink_tick` 이 CONFIG->RUN 전이를
     * 보고 스스로 ACTIVE 로 되돌린다. */
    MkCtlMode   ctl_mode;

    int64_t     last_hb_rx_ms;   /* 검증을 통과한 $HB 를 마지막으로 받은 시각 */
    int64_t     last_hb_tx_ms;   /* 우리가 $HB 를 마지막으로 보낸 시각 */
    int         hb_seen;         /* 아직 한 번도 못 받았으면 0 */

    char        out[MK_LINE_MAX + 8];
} MkHostlink;

void mk_hostlink_init(MkHostlink *h, MkEmit emit, void *ctx,
                      const char *device_id, const char *fw,
                      const char *board_rev);

/* 설정 저장소를 붙인다. 부르지 않으면 $CFG 는 UNSUPPORTED 다. */
void mk_hostlink_attach_config(MkHostlink *h, MkConfig *cfg,
                               const MkFieldBit *fields, size_t n_fields,
                               MkCfgSave save, void *save_ctx);

/* 수집기를 붙인다. 부르지 않으면 $STAT 의 `queues` 가 빈 배열이다.
 *
 * 🔴 전방 선언으로 받는다. mk_hostlink 는 mk_ads1256.h 를 include 하지
 *    않는다 — 명령 디스패치가 수집기를 아는 순간, 수집기 없이 통신만
 *    시험하는 일이 어려워진다. 지금 시험들이 전부 그렇게 돌고 있다. */
struct MkAds;
void mk_hostlink_attach_ads(MkHostlink *h, struct MkAds *ads);

struct MkRailCtl;
void mk_hostlink_attach_rails(MkHostlink *h, struct MkRailCtl *rails);

/* 디지털 입력 J18~J20 을 붙인다. 부르지 않으면 $STAT 의 `din` 이 빈
 * 배열이다. */
struct MkSolCtl;
void mk_hostlink_attach_sol(MkHostlink *h, struct MkSolCtl *sol);

/* 받은 줄 하나를 처리한다. 응답이 있으면 emit 으로 내보낸다.
 *
 * 🔴 규격 §6.3 — **체크섬을 통과하고 verb 가 정확히 `HB` 일 때만**
 *    하트비트 시각을 갱신한다. 앞부분이 `$HB` 처럼 보이는 것만으로는
 *    안 된다. 기존 Q2 펌웨어(host_link.c:183-187)가 체크섬 검증 전에
 *    갱신했는데, 그 프로토콜에서 $HB 는 단순 생존 신호였다. 여기서
 *    $HB 는 **설정 변경을 여는 열쇠**라 그대로 옮기면 잡음으로 깨진
 *    프레임이 설정 변경을 계속 허용한다. */
void mk_hostlink_feed(MkHostlink *h, const char *line, size_t len,
                      int64_t now_ms);

/* 주기 처리. 1 Hz 로 우리 쪽 $HB 를 내보낸다. */
void mk_hostlink_tick(MkHostlink *h, int64_t now_ms);

/* 지금 모드. 시각을 넣는 이유는 마지막 수신 이후 흐른 시간으로 판정하기
 * 때문이다 — 상태를 저장하지 않으므로 tick 을 놓쳐도 답이 바뀌지 않는다. */
MkMode mk_hostlink_mode(const MkHostlink *h, int64_t now_ms);

/* 지금 제어 모드 (규격 §6.4). */
MkCtlMode mk_hostlink_ctl_mode(const MkHostlink *h);

#endif /* MK_HOSTLINK_H */
