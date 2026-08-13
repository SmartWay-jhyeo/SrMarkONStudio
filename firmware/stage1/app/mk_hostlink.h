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

#include "mk_framing.h"

/* 규격 §6.2 */
#define MK_HB_TIMEOUT_MS   3000
#define MK_HB_INTERVAL_MS  1000

typedef enum {
    MK_MODE_RUN = 0,     /* 부팅 직후 기본값 */
    MK_MODE_CONFIG
} MkMode;

/* 한 줄을 내보낸다. 줄끝(`\r\n` 또는 `\n`)은 이미 붙어 있다. */
typedef void (*MkEmit)(void *ctx, const char *line, size_t len);

typedef struct {
    MkEmit      emit;
    void       *ctx;

    const char *device_id;
    const char *fw;
    const char *board_rev;

    int64_t     last_hb_rx_ms;   /* 검증을 통과한 $HB 를 마지막으로 받은 시각 */
    int64_t     last_hb_tx_ms;   /* 우리가 $HB 를 마지막으로 보낸 시각 */
    int         hb_seen;         /* 아직 한 번도 못 받았으면 0 */

    char        out[MK_LINE_MAX + 8];
} MkHostlink;

void mk_hostlink_init(MkHostlink *h, MkEmit emit, void *ctx,
                      const char *device_id, const char *fw,
                      const char *board_rev);

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

#endif /* MK_HOSTLINK_H */
