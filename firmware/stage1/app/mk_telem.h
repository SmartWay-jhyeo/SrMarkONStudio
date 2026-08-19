/* 텔레메트리 송신 — 큐를 비워 NDJSON `ain` 레코드로 내보낸다. HAL 비의존.
 *
 * 🔴 여태 없던 마지막 한 조각이다. 수집 사슬(DRDY EXTI → SPI4 DMA →
 *    mk_queue_push)은 실기기에서 확인됐는데 꺼내는 쪽이 없어 큐가 차고
 *    `drops` 만 올랐다. 호스트는 4초를 들어도 0건이었다.
 *
 * 🔴 [정정, 2026-08-19] 큐를 비우는 목적은 "수집 우선, 송신 지연 흡수"다
 *    (사용자 확정) — 표본을 버리는 자리가 아니다. `tx.period_ms` 마다
 *    채널의 큐에 쌓인 표본을 **전부** 각자 자기 획득 시각으로 내보내고,
 *    큐가 비어 있을 때만 마지막 값을 그 값의 획득 시각 그대로 반복한다.
 *    자세한 근거는 mk_telem.c 의 ain 배출부 주석과
 *    mk_ads1256.h 의 MkAdsChannel.last 주석.
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
#include "mk_gnss.h"
#include "mk_i2c.h"
#include "mk_solctl.h"
#include "mk_timeax.h"

/* 한 줄을 내보낸다. 줄바꿈은 이미 붙어 있다. */
typedef void (*MkTelemEmit)(void *ctx, const char *line, size_t len);

/* 🔴 한 번의 tick 에서 **레코드 종류마다** 내보낼 줄 수 상한 — din 루프,
 *    ain 배출부, i2c 배출부가 각각 독립으로 이 값까지 쓴다(하나로 합친
 *    전체 상한이 아니다).
 *
 *    🔴 [정정, 2026-08-19] 큐를 비우면서 꺼낸 것을 **전부** 내보내는 쪽으로
 *    되돌린 뒤(mk_telem.c 의 ain 배출부 주석 참고) "채널마다 정확히 한
 *    줄"이라는 보장은 다시 없어졌다 — 수집이 밀리면 한 채널이 한 틱에
 *    여러 줄을 낼 수 있다. 그래서 이 상수가 다시 뜻을 가진다: 정상 부하
 *    (수집 < 송신)에서는 채널·포트·슬롯마다 한 틱에 0~1개뿐이라 상한에
 *    안 걸리지만, 큐가 크게 밀렸을 때는 여기서 멈추고 **나머지는 큐에
 *    남겨 다음 틱으로 넘긴다**(버리지 않는다 — mk_queue_pop 을 안 부르면
 *    표본이 그대로 큐에 남아 있다). din 은 여전히 큐(엣지)를 비우는
 *    방식이라 상한이 같은 이유로 필요하다. */
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
    MkGnss           *gnss;                 /* 없으면 NULL — gnss_raw 를 안 낸다.
                                              * 있어도 gnss.echo 가 꺼져 있으면
                                              * 안 낸다(규격 §7.7) */

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

/* GNSS 원시 문장 파서를 물린다(규격 §7.7). 붙이지 않으면 `gnss_raw` 를
 * 안 낸다 — `gnss.echo` 설정과 별개의 게이트다(붙었어도 echo 가 꺼져
 * 있으면 안 낸다). */
void mk_telem_attach_gnss(MkTelem *t, MkGnss *gnss);

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
