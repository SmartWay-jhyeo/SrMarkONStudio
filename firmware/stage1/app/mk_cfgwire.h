/* 설정을 전선에 싣는 층 — HAL 비의존.
 *
 * `mk_config` 가 값을 들고 `mk_json` 이 줄을 만든다. 여기서는 규격 §7.3 의
 * 레코드 모양을 정한다.
 *
 * 🔴 호스트는 설정 항목을 하드코딩하지 않는다. 이 응답만으로 화면을
 *    구성한다 — 그래서 이 레코드가 빠짐없이 나가야 하고, Python 쪽이
 *    내는 것과 같은 모양이어야 한다.
 */
#ifndef MK_CFGWIRE_H
#define MK_CFGWIRE_H

#include <stddef.h>
#include <stdint.h>

#include "mk_config.h"

/* NDJSON 필드 마스크의 비트 하나 (규격 §7.2). */
typedef struct {
    uint8_t     bit;
    const char *name;
    uint8_t     def;
    const char *label;
} MkFieldBit;

/* 한 줄을 만들어 콜백에 넘긴다. 줄바꿈은 포함하지 않는다. */
typedef void (*MkCfgEmit)(void *ctx, const char *line, size_t len);

/* `$CFG,LIST` 응답 본문. cfg_item · cfg_field · cfg_end 순서로 낸다.
 *
 * 🔴 `cfg_end` 의 `count` 는 cfg_item + cfg_field 합계다(규격 §7.3).
 *    수신측이 이것을 대조해 전송이 중간에 잘렸는지 판정한다 — 링크가
 *    나쁠 때 절반만 온 카탈로그로 화면을 그리면 안 된다. */
void mk_cfgwire_list(const MkConfig *cfg,
                     const MkFieldBit *fields, size_t n_fields,
                     int64_t now_ms, MkCfgEmit emit, void *ctx);

/* `$STAT` 응답 본문 (규격 §7.4).
 *
 * 🔴 `rails` 는 **명령 상태**다. 피드백 회로가 없으므로 실측이 아니고,
 *    호스트는 이것을 `정상 ON` 이 아니라 `ON 명령됨` 으로 표시해야 한다.
 *
 * 🔴 `queues` 는 채널별 큐 깊이·최고치·유실이다. 3단계에서 ADS1256 이
 *    들어오면 이것이 유일한 진단 창구가 된다 — 유실이 나는데 어디서
 *    나는지 모르면 고칠 수 없다. 아직 큐가 없으므로 지금은 0 이다.
 */
/* 레일의 **명령 상태**. 실측이 아니다 — 피드백 회로가 없다.
 *
 * 🔴 설정표에서 만들지 않는다. 설정은 "원하는 것" 이고 이것은 "낸 것" 이다.
 *    둘 사이에 순차 기동 간격이 있어서, 설정을 그대로 보고하면 아직 안
 *    올린 레일을 켜졌다고 말하게 된다. */
typedef struct {
    uint8_t v24;
    uint8_t v14v9;
    uint8_t v5;
} MkRailState;

/* 🔴 `ch` 를 구조체가 들고 있다. 배열 첨자를 채널 번호로 쓰면, 꺼진 채널을
 *    건너뛴 순간 3번 채널의 유실이 1번 채널의 것으로 보고된다. 유실을
 *    찾으려고 보는 창구가 채널을 헷갈리면 없느니만 못하다. */
typedef struct {
    uint8_t  ch;
    uint16_t depth;
    uint16_t peak;
    uint32_t drops;
} MkQueueStat;

/* J18~J20 디지털 입력의 **지금 상태** (규격 §7.4·§7.6).
 *
 * 🔴 `rails` 와 반대로 이것은 실측이다 — 보드가 EXTI 로 핀을 직접 읽는다.
 *    상태 변화는 §7.6 의 `din` 텔레메트리로도 오지만 그것은 바뀔 때만
 *    오므로, 막 연결한 호스트는 `$STAT` 으로 지금 상태를 채운다. */
typedef struct {
    uint16_t connector_id;   /* 18·19·20 */
    uint8_t  state;          /* 이미 반전된 값 — 1 = 켜짐(신호 있음) */
} MkDinState;

/* 🔴 `time_source`·`time_quality` 는 규격 §7.4 예시에는 없지만 시뮬레이터와
 *    함께 낸다. 규격 §7.1.2 대로 `t` 는 시간 소스에 따라 UTC epoch 이기도
 *    하고 부팅 후 경과 ms 이기도 한데, 명령 응답에는 필드 마스크가 없어
 *    텔레메트리처럼 실어 보낼 자리가 없다. $STAT 이 그 답을 주는 유일한
 *    곳이다 — 호스트는 연결 직후 한 번 물어보면 된다. */
/* 🔴 설정표를 받지 않는다. 예전에는 여기서 pwr.* 를 읽어 rails 를 만들었는데,
 *    그것이 설계 원칙 4 위반이었다 — 설정은 사용자가 원하는 것이지 보드가
 *    낸 것이 아니다. 이제 rails 를 인자로 받는다. */
/* 🔴 `din` 도 rails 와 같은 계약이다 — 보드가 **실제로 읽은 것**을 싣는다.
 *    없으면(NULL) 빈 배열이다. 정상 경로에서는 항상 MK_SOL_COUNT(3)개를
 *    받는다 — 커넥터가 있으면 상태는 항상 있다(레일과 달리 "아직 안 낸"
 *    중간 상태가 없다). */
/* 🔴 `gnss_pps_age_ms`·`gnss_sats` 는 시간축 진단이다(규격 §7.4, Phase 3).
 *    `time_source`/`time_quality` 가 "지금 등급이 무엇인가"를 말한다면
 *    이 둘은 "그 등급을 얼마나 믿을 수 있나"를 보탠다 — 등급이 gnss_pps
 *    라도 마지막 PPS 가 1.4초 전이면 다음 tick 에 내려갈 참이라는 것을
 *    미리 알 수 있다. -1 은 "아직 모른다"(PPS 를 한 번도 못 봤다 · GGA 를
 *    한 번도 못 받았다)는 뜻이고 `null` 로 나간다 — 0 을 지어내지 않는다
 *    (설계 원칙 3·4와 같은 결). */
/* 🔴 `gnss_init_sent`·`gnss_init_exhausted`·`gnss_sentence_seen` 는 GNSS
 *    초기화 시퀀스(규격 §4.1.1)를 진단한다. `time_source`가 계속
 *    device_clock 에 머물 때 — 즉 아무것도 안 올 때 — 이 셋이 "명령을
 *    보내기는 했는가" / "재시도를 다 썼는가" / "문장을 받은 적은
 *    있는가"를 가른다. mk_gnssctl 이 안 붙어 있으면 호출 쪽이 전부 0을
 *    넘긴다. */
int mk_cfgwire_stat(int64_t now_ms,
                    const char *mode, const char *ctl_mode, const char *fw, const char *board_rev,
                    uint32_t uptime_ms,
                    const char *time_source, uint32_t time_quality,
                    int64_t gnss_pps_age_ms, int32_t gnss_sats,
                    int gnss_init_sent, int gnss_init_exhausted,
                    int gnss_sentence_seen,
                    const MkRailState *rails,
                    const MkDinState *din, size_t n_din,
                    const MkQueueStat *queues, size_t n_queues,
                    char *out, size_t cap);

/* `$CFG,GET` 응답 본문 한 줄. 반환은 길이, 실패면 음수.
 *
 * `cur` 의 JSON 타입은 항목의 vtype 을 따른다 — bool 은 참/거짓,
 * str 은 문자열, 나머지는 수다 (규격 §5.2). */
int mk_cfgwire_value(const MkCfgItem *item, int64_t now_ms,
                     char *out, size_t cap);

#endif /* MK_CFGWIRE_H */
