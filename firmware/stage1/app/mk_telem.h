/* 텔레메트리 송신 — 큐를 비워 NDJSON `ain` 레코드로 내보낸다. HAL 비의존.
 *
 * 🔴 여태 없던 마지막 한 조각이다. 수집 사슬(DRDY EXTI → SPI4 DMA →
 *    mk_queue_push)은 실기기에서 확인됐는데 꺼내는 쪽이 없어 큐가 차고
 *    `drops` 만 올랐다. 호스트는 4초를 들어도 0건이었다.
 *
 * 규격: protocol/specification.md §7.1·§7.2·§7.2.1
 */
#ifndef MK_TELEM_H
#define MK_TELEM_H

#include <stddef.h>
#include <stdint.h>

#include "mk_ads1256.h"
#include "mk_cfgwire.h"
#include "mk_config.h"
#include "mk_i2c.h"
#include "mk_solctl.h"
#include "mk_timeax.h"

/* 한 줄을 내보낸다. 줄바꿈은 이미 붙어 있다. */
typedef void (*MkTelemEmit)(void *ctx, const char *line, size_t len);

/* 🔴 한 번의 tick 에서 내보낼 줄 수 상한.
 *
 *    🔴 [2026-08-19] 수집과 송신을 뗀 뒤(사용자 설계 — MkAdsChannel.last·
 *    mk_i2c.c 의 last[][]) 더는 "밀린 큐를 나눠 꺼내는" 문제가 없다.
 *    채널·포트·슬롯마다 정확히 한 줄만 나가므로 한 tick 의 최댓값은
 *    MK_ADS_CHANNELS(7) + MK_I2C_COUNT×MK_I2C_VALUES_MAX(12) 로 고정이다.
 *    이 상수는 이제 din(mk_solctl_take 루프)과 함께 쓰는 안전망이고,
 *    din 쪽은 여전히 큐(엣지)를 비우는 방식이라 상한이 뜻을 가진다. */
#define MK_TELEM_MAX_LINES  16

typedef struct {
    MkConfig         *cfg;
    MkAds            *ads;
    const MkFieldBit *fields;
    size_t            n_fields;
    const char       *device_id;
    MkI2c            *i2c;                  /* 없으면 NULL — ain 만 낸다 */
    MkSolCtl         *sol;                  /* 없으면 NULL — din 을 안 낸다 */
    MkTimeAx         *timeax;               /* 없으면 NULL — time_source 는
                                              * "device_clock" 고정(1단계와 같다) */

    /* 규격 §7.1 — 레코드마다 1씩 오른다. 호스트가 누락을 검출한다. */
    uint32_t          seq;
    int64_t           last_ms;
} MkTelem;

void mk_telem_init(MkTelem *t, MkConfig *cfg, MkAds *ads,
                   const MkFieldBit *fields, size_t n_fields,
                   const char *device_id);

/* I2C 층을 물린다. 🔴 seq 와 tx.fields 를 ain 과 나눠 쓰기 위해서다.
 *    따로 내보내면 두 곳이 갈린다 (규격 §7.5). */
void mk_telem_attach_i2c(MkTelem *t, MkI2c *i2c);

/* 디지털 입력(J18~J20) 층을 물린다. 붙이지 않으면 `din` 을 내지 않는다.
 * i2c 와 같은 이유로 seq·tx.fields 를 나눠 쓴다 (규격 §7.6). */
void mk_telem_attach_sol(MkTelem *t, MkSolCtl *sol);

/* GNSS/PPS 시간축(Phase 3)을 물린다. 붙이면 ain·i2c·din 레코드의
 * `time_source`·`time_quality` 가 실제 등급을 싣는다. 붙이지 않으면
 * "device_clock"·0 고정이다(1단계와 같은 동작). */
void mk_telem_attach_timeax(MkTelem *t, MkTimeAx *timeax);

/* 전송 주기가 됐으면 큐를 비워 내보낸다. 반환은 내보낸 줄 수.
 *
 * 🔴 주기 전에는 아무것도 하지 않는다 — 큐에 있는 것을 즉시 쏟으면
 *    `tx.period_ms` 가 뜻을 잃는다. */
int mk_telem_tick(MkTelem *t, int64_t now_ms, MkTelemEmit emit, void *ctx);

/* 원시 코드를 루프 전류(mA)로. 시뮬레이터의 `raw_to_ma` 와 같은 식이다.
 *
 * 데이터시트 §5.3 — 120 Ω 0.1% 션트, ADS1256 외부 기준 2.5 V,
 * 24비트 양수 만재 = 2^23 - 1. */
float mk_telem_raw_to_ma(int32_t raw);

#endif /* MK_TELEM_H */
