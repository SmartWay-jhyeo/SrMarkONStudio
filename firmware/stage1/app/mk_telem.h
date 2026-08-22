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
 *    방식이라 상한이 같은 이유로 필요하다.
 *
 *    🔴 [검토, 2026-08-20] "다음 틱으로 넘긴다" 는 위 문장은 원래도 여기
 *    적혀 있었지만, ain 배출부가 옛날엔 채널 0 부터 순서대로 자기 큐를
 *    통째로 비웠기 때문에 실제로는 뒤 채널에게 거짓말이었다 — 앞 채널이
 *    계속 배부르면 뒤 채널은 이 상한 안에서 순서가 영영 안 왔다(실기기
 *    2026-08-20, J8·J9 30초 0건, 큐 64칸이 다 찬 채 drops 만 올랐다).
 *    ain 배출부를 채널당 한 표본씩 도는 라운드로빈으로 고친 뒤에야
 *    (mk_telem.c 의 ain 배출부 주석 참고) 이 문장이 모든 채널에 실제로
 *    성립한다.
 *
 *    🔴 [값 재계산, 2026-08-20 — 송신이 DMA 로 바뀐 뒤]
 *
 *    16 이었다. 그 값을 붙잡고 있던 근거는 "올리면 mk_uart_write 가
 *    블로킹으로 그만큼 더 오래 잡아먹는다" 였는데, 송신이 링버퍼+DMA 로
 *    바뀌면서(app/mk_txring.h, bsp/mk_uart.c) **그 전제가 사라졌다.**
 *    이제 억제 요인은 슈퍼루프가 아니라 **링크 용량**이다.
 *
 *    낼 수 있는 것보다 많이 만들면 남는 줄은 전선에 실리지 못하고 링에
 *    쌓이다가 버려진다. 버리는 것은 이미 타임스탬프까지 찍힌 표본을
 *    잃는 것이라, 그 자리를 아예 안 만드는 편이 낫다. 그래서 상한을
 *    실측 운용점의 링크 예산에서 뽑는다:
 *
 *        1.5 Mbps ÷ 10 bit/B (8N1)      = 150,000 B/s
 *        × tx.period_ms 하한 10 ms       =   1,500 B / 틱
 *        ÷ 실측 한 줄 135 B              =      11 줄 / 틱
 *              (실측 근거: 2026-08-20, 30초에 14,863줄 / 67 KB/s)
 *
 *    11 은 채널 7개(MK_ADS_CHANNELS)에 한 줄씩 주고도 4줄이 남는다 —
 *    밀린 것을 따라잡는 몫이 그 4줄이고, 큐 64칸이 가득 찬 최악에서도
 *    64/4 = 16틱(160 ms)이면 다 비운다. 하한(7)과 상한(11) 사이에서 가장
 *    큰 값을 고른 셈이다.
 *
 *    🔴 이 계산은 **설계 운용점**의 값이지 모든 설정에 맞는 값이 아니다.
 *       기본 921600 에서 7채널 × 10 ms 는 계산상 92 KB/s 링크에 94.5 KB/s
 *       를 밀어 넣는 것이라 애초에 안 들어간다 — 그래서 실기기 측정도
 *       1.5 Mbps 로 했다. 그 상황을 GUI 의 링크 사용량 표가 미리 알려
 *       주고(커밋 b710381·77f56b1), 그래도 넘기면 링이 줄을 버리며 호스트는
 *       `seq` 구멍으로 알아챈다(규격 §7.1).
 *
 *    host/tests/test_firmware_uart_dma.py 가 이 계산을 지킨다 — 7 미만이면
 *    매 틱 어떤 채널이 빠지고, 11 초과면 링크 용량을 넘긴다. */
#define MK_TELEM_MAX_LINES  11

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

    /* 🔴 [신규, 2026-08-20] ain 라운드로빈이 다음 틱에 시작할 채널.
     *
     *    실기기 재현: 채널당 10ms 로 7채널을 켜고 tx.period_ms=10 으로
     *    돌리면, 매 틱 항상 ch=0 부터 순회하던 옛 코드는 앞 채널이 예산을
     *    다 먹어 뒤 채널(J8·J9)이 30초 동안 단 한 줄도 못 냈다(큐 64칸이
     *    꽉 찬 채 drops 만 3038·3040 으로 올랐다). 이 값은 이번 틱이 멈춘
     *    다음 채널을 기억해 두어, 다음 틱은 거기서부터 순회를 시작한다 —
     *    어느 채널도 항상 마지막 순서로 밀리지 않는다. 자세한 근거는
     *    mk_telem.c 의 ain 배출부 주석. */
    int               ain_rr;

    /* 🔴 [신규, 2026-08-22] 호스트가 듣고 있는가 — $HB 신선도(규격 §7.1.3).
     *
     *    0 이면 텔레메트리를 **한 줄도 내지 않는다.** 아무도 안 읽는
     *    COM23 에 초당 ~1.5 KB 를 계속 부으면 F103(BMP) 브리지 버퍼가
     *    차서 디버거가 통째로 굳는다 — 실기기에서 반복 실증(2026-08-22,
     *    전원 20초 차단으로만 복구). 침묵 중에도 큐는 비운다(버림) —
     *    seq 는 안 올린다: 올리면 다시 붙은 호스트가 그 구간을 유실로
     *    센다. 부팅 기본은 0(첫 HB 전까지 침묵) — CONFIG/RUN 을 케이블이
     *    아니라 HB 로 판정하는 것과 같은 철학이다(CLAUDE.md §4). */
    int               host_alive;
} MkTelem;

void mk_telem_init(MkTelem *t, MkConfig *cfg, MkAds *ads,
                   const MkFieldBit *fields, size_t n_fields,
                   const char *device_id);

/* 호스트 생사를 알린다 — main 이 매 바퀴 hostlink 의 HB 신선도
 * (mk_hostlink_mode == CONFIG)를 넣는다. `host_alive` 주석이 계약이다. */
void mk_telem_set_host_alive(MkTelem *t, int alive);

/* I2C 층을 물린다. 🔴 `seq` 는 ain 과 같은 MkTelem 카운터를 함께 쓴다 —
 *    따로 세면 두 곳이 갈린다 (규격 §7.5). 필드 마스크는 반대로
 *    `tx.fields_i2c` 로 ain 과 **독립**이다(2026-08-19 개정, 규격 §7.5). */
void mk_telem_attach_i2c(MkTelem *t, MkI2c *i2c);

/* 디지털 입력(J18~J20) 층을 물린다. 붙이지 않으면 `din` 을 내지 않는다.
 * `seq` 는 i2c 와 같은 이유로 공유하지만, 마스크는 `tx.fields_din` 으로
 * 독립이다(규격 §7.6). */
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
