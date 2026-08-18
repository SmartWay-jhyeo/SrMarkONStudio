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

/* 한 줄을 내보낸다. 줄바꿈은 이미 붙어 있다. */
typedef void (*MkTelemEmit)(void *ctx, const char *line, size_t len);

/* 🔴 한 번에 내보낼 줄 수 상한.
 *
 *    수집 주기가 전송 주기보다 빠르면 큐가 쌓인다. 그때 밀린 것을 한
 *    번에 다 쏟으면 UART 가 막히고, 그동안 명령·하트비트가 밀려 보드가
 *    RUN 으로 떨어진다 — 링크가 나쁠 때 정확히 터지는 종류의 실패다.
 *
 *    대신 채널을 **돌아가며** 꺼낸다. 한 채널이 밀려 있다고 나머지가
 *    굶으면 안 된다 (mk_ads1256 의 라운드로빈과 같은 이유). */
#define MK_TELEM_MAX_LINES  16

typedef struct {
    MkConfig         *cfg;
    MkAds            *ads;
    const MkFieldBit *fields;
    size_t            n_fields;
    const char       *device_id;
    MkI2c            *i2c;                  /* 없으면 NULL — ain 만 낸다 */

    /* 규격 §7.1 — 레코드마다 1씩 오른다. 호스트가 누락을 검출한다. */
    uint32_t          seq;
    int64_t           last_ms;
    /* 다음에 먼저 볼 채널. 라운드로빈의 출발점이다. */
    int               next_ch;
} MkTelem;

void mk_telem_init(MkTelem *t, MkConfig *cfg, MkAds *ads,
                   const MkFieldBit *fields, size_t n_fields,
                   const char *device_id);

/* I2C 층을 물린다. 🔴 seq 와 tx.fields 를 ain 과 나눠 쓰기 위해서다.
 *    따로 내보내면 두 곳이 갈린다 (규격 §7.5). */
void mk_telem_attach_i2c(MkTelem *t, MkI2c *i2c);

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
