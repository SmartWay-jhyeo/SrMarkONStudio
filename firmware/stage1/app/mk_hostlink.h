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
#include "mk_gnss.h"   /* MkGnssSend — $GNSS 명령 전달(규격 §4.1)에 쓴다 */
#include "mk_txring.h" /* MkTxRing — $STAT 의 `tx`(규격 §7.4)를 읽어 싣는다 */

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

/* 이어서 내보낼 수 있는 줄 하나. **자리가 없으면 0 을 돌려준다.**
 *
 * 🔴 [신설, 2026-08-20] `MkEmit` 과의 차이가 이번 결함의 핵심이다.
 *
 *    `MkEmit` 은 "냈다" 와 "못 냈다" 를 구별하지 않으므로, 부르는 쪽은
 *    받는 쪽이 감당하든 말든 계속 쏟는다. 카탈로그(103줄 ≈ 25 KB)를
 *    4,096 B 짜리 송신 링에 그렇게 쏟았더니 43줄만 들어가고 나머지가
 *    버려졌다 — 호스트는 `cfg_end` 가 없다며 카탈로그를 통째로 거부했고,
 *    GUI 는 설정 폼을 아예 못 만들었다(실기기 2026-08-20).
 *
 *    이 콜백은 거절을 돌려준다. 그래야 부르는 쪽이 **멈췄다가 다음
 *    바퀴에 이어서** 낼 수 있다. 기다리는 것(블로킹)이 아니다 — 기다리면
 *    슈퍼루프가 서고, 그것이 어제 없앤 32 ms 짜리 한 바퀴다.
 *
 * 반환: 냈으면 1, 자리가 없어 못 냈으면 0(유실이 아니다 — 다시 온다). */
typedef int (*MkEmitStream)(void *ctx, const char *line, size_t len);

/* 설정을 영구 저장한다. 없으면(NULL) $CFG,SAVE 가 BUSY 를 돌려준다.
 * 반환: 성공이면 0. Flash 를 다루는 것은 bsp 쪽이다. */
typedef int (*MkCfgSave)(void *ctx);

typedef struct {
    MkEmit      emit;
    void       *ctx;

    /* 이어서 내보내는 통로. NULL 이면 카탈로그를 예전처럼 한 호출에 전부
     * 쏟는다 — 시뮬레이터·호스트 시험처럼 받는 쪽이 무한 버퍼인 곳에서는
     * 그것이 맞고, 실물 보드만 이것을 붙인다. */
    MkEmitStream stream;

    /* 송신 링. 붙어 있으면 `$STAT` 의 `tx` 가 실제 수위·유실을 싣는다.
     * 없으면 null 이다 — 지어내지 않는다(clock 과 같은 결). */
    const MkTxRing *txring;

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

    /* GNSS/PPS 시간축(Phase 3). 붙어 있으면 $STAT 의 time_source·
     * time_quality·gnss.* 가 실제 등급을 싣는다. 없으면 "device_clock"·
     * quality 0 고정이다(1단계와 같다). */
    struct MkTimeAx *timeax;

    /* GNSS 모듈로 원시 명령을 내보내는 콜백(규격 §4.1) — $GNSS 가 이걸로
     * 보낸다. NULL 이면 $GNSS 가 UNSUPPORTED 다($CFG 가 cfg==NULL 일 때와
     * 같은 결). 줄 끝 CR/LF 는 on_gnss() 가 붙인 뒤 이 콜백을 부른다 —
     * 콜백(bsp 구현)은 받은 바이트를 그대로 내보내는 얇은 관이다. */
    MkGnssSend gnss_send;
    void      *gnss_send_ctx;

    /* GNSS 초기화 재시도 상태(규격 §4.1.1). 붙어 있으면 $STAT 의
     * gnss.init_sent·init_exhausted·sentence_seen 이 실제 상태를 싣는다.
     * 없으면 전부 거짓이다. */
    struct MkGnssCtl *gnssctl;

    /* 화면 회복 계수기(규격 §7.4). 붙어 있으면 $STAT 의 `lcd` 가 실제
     * 값을 싣는다. 없으면 전부 0 이고 readback 은 null 이다. */
    struct MkLcd *lcd;

    /* 호스트 링크 속도 상태기계 (규격 §4.2). 붙어 있으면
     * `$BAUD,CONFIRM` 이 동작하고, 확인 대기 중에는 `$CFG,SAVE` 를
     * 막는다. 없으면 `$BAUD` 가 UNSUPPORTED 이고 `$STAT` 의 `link` 가
     * null 이다 — 링크 속도를 안 붙인 빌드(1단계·호스트 시험)가 그 자리다. */
    struct MkLinkBaud *linkbaud;

    /* 시스템 클럭 출처(규격 §7.4). `mk_hostlink_attach_clock` 이 채운다.
     *
     * 🔴 `fw`·`board_rev` 와 같은 결로 **문자열을 받는다.** 이 층은 HAL 을
     *    모르므로 bsp/mk_clock.h 를 include 할 수 없고, 그래야 클럭 없이도
     *    호스트에서 통신만 시험할 수 있다. NULL 이면 `clock` 이
     *    `{"src":null,"sysclk_hz":null}` 로 나간다. */
    const char *clock_src;
    uint32_t    clock_sysclk_hz;

    /* 제어 모드 (규격 §6.4). `mk_hostlink_tick` 이 CONFIG->RUN 전이를
     * 보고 스스로 ACTIVE 로 되돌린다. */
    MkCtlMode   ctl_mode;

    int64_t     last_hb_rx_ms;   /* 검증을 통과한 $HB 를 마지막으로 받은 시각 */
    int64_t     last_hb_tx_ms;   /* 우리가 $HB 를 마지막으로 보낸 시각 */
    int         hb_seen;         /* 아직 한 번도 못 받았으면 0 */

    /* 내보내다 만 카탈로그 (규격 §5.2·§7.3).
     *
     * 🔴 [신설, 2026-08-20] 카탈로그는 25 KB 인데 송신 링은 4,096 B 다.
     *    한 바퀴에 다 못 내므로 **어디까지 냈는지**를 들고 있어야 한다.
     *    이 셋이 그 전부다. */
    struct {
        int      active;         /* 내보내는 중인가 */
        size_t   index;          /* 다음에 낼 줄 번호 */
        /* 🔴 카탈로그 한 벌은 **같은 `t`** 를 쓴다. 줄마다 지금 시각을
         *    새로 넣으면 한 응답 안에서 시각이 흩어진다 — 한 번에 쏟던
         *    때에는 저절로 같았던 성질이라, 나눠 내면서 지켜야 한다. */
        int64_t  now_ms;
        /* 마지막으로 한 줄이라도 나간 시각. 여기서 너무 오래 못 나가면
         * 포기하고 호스트에 ERR 로 알린다 — 조용히 멈추지 않는다. */
        int64_t  progress_ms;
    } catalog;

    char        out[MK_LINE_MAX + 8];
} MkHostlink;

void mk_hostlink_init(MkHostlink *h, MkEmit emit, void *ctx,
                      const char *device_id, const char *fw,
                      const char *board_rev);

/* 이어서 내보내는 통로를 붙인다 (`ctx` 는 `mk_hostlink_init` 의 것을 쓴다).
 *
 * 🔴 붙이면 `$CFG,LIST` 가 **한 줄씩 자리를 봐 가며** 나간다. 안 붙이면
 *    예전처럼 한 호출에 전부 쏟는다 — 받는 쪽이 무한 버퍼인 곳(시뮬레이터·
 *    호스트 시험)에서는 그것이 맞다. 실물 보드는 반드시 붙인다. */
void mk_hostlink_attach_stream(MkHostlink *h, MkEmitStream stream);

/* 송신 링을 붙인다. 부르지 않으면 $STAT 의 `tx` 가 null 이다.
 *
 * 🔴 읽기만 한다. 링을 밀고 빼는 것은 bsp 쪽 일이고, 여기서는 그 수를
 *    전선에 실어 사람이 볼 수 있게 하는 것이 전부다 — 그 수가 밖에서
 *    안 보여서 이번 결함을 GDB 로 찾아야 했다. */
void mk_hostlink_attach_txring(MkHostlink *h, const MkTxRing *ring);

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

/* GNSS/PPS 시간축을 붙인다(Phase 3). 부르지 않으면 $STAT 의 time_source 는
 * "device_clock" 고정이다(1단계와 같은 동작). */
struct MkTimeAx;
void mk_hostlink_attach_timeax(MkHostlink *h, struct MkTimeAx *timeax);

/* GNSS 명령 전달 콜백을 붙인다(규격 §4.1). 부르지 않으면 $GNSS 는
 * UNSUPPORTED 다. */
void mk_hostlink_attach_gnss(MkHostlink *h, MkGnssSend send, void *ctx);

/* GNSS 초기화 재시도 상태를 붙인다(규격 §4.1.1·§7.4). 부르지 않으면
 * $STAT 의 gnss.init_sent·init_exhausted·sentence_seen 이 전부 거짓이다. */
struct MkGnssCtl;
void mk_hostlink_attach_gnssctl(MkHostlink *h, struct MkGnssCtl *gnssctl);

/* LCD 회복 계수기를 붙인다 (규격 §7.4). 부르지 않으면 $STAT 의 `lcd` 가
 * 전부 0 이고 `readback` 은 null 이다 — 화면이 안 붙은 빌드다. */
struct MkLcd;
void mk_hostlink_attach_lcd(MkHostlink *h, struct MkLcd *lcd);

/* 호스트 링크 속도 상태기계를 붙인다 (규격 §4.2). 부르지 않으면
 * `$BAUD,CONFIRM` 이 UNSUPPORTED 이고 `$STAT` 의 `link.baud` 가 null 이다.
 *
 * 🔴 전방 선언으로 받는다 — 이 층은 UART 하드웨어를 모르고, 링크 속도를
 *    안 붙인 채로도 통신 시험이 그대로 돌아야 한다(attach_ads 와 같은 결). */
struct MkLinkBaud;
void mk_hostlink_attach_linkbaud(MkHostlink *h, struct MkLinkBaud *lb);

/* 시스템 클럭 출처를 붙인다 (규격 §7.4). 부르지 않으면 $STAT 의 `clock` 이
 * `{"src":null,"sysclk_hz":null}` 로 나간다 — "이 장치는 답할 수 없다"이고,
 * 클럭 없이 도는 호스트 시험이 그 자리다.
 *
 * `src` 는 계속 살아 있어야 한다(정적 문자열을 넘긴다). */
void mk_hostlink_attach_clock(MkHostlink *h, const char *src,
                              uint32_t sysclk_hz);

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
