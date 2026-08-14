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
/* 🔴 `ch` 를 구조체가 들고 있다. 배열 첨자를 채널 번호로 쓰면, 꺼진 채널을
 *    건너뛴 순간 3번 채널의 유실이 1번 채널의 것으로 보고된다. 유실을
 *    찾으려고 보는 창구가 채널을 헷갈리면 없느니만 못하다. */
typedef struct {
    uint8_t  ch;
    uint16_t depth;
    uint16_t peak;
    uint32_t drops;
} MkQueueStat;

/* 🔴 `time_source`·`time_quality` 는 규격 §7.4 예시에는 없지만 시뮬레이터와
 *    함께 낸다. 규격 §7.1.2 대로 `t` 는 시간 소스에 따라 UTC epoch 이기도
 *    하고 부팅 후 경과 ms 이기도 한데, 명령 응답에는 필드 마스크가 없어
 *    텔레메트리처럼 실어 보낼 자리가 없다. $STAT 이 그 답을 주는 유일한
 *    곳이다 — 호스트는 연결 직후 한 번 물어보면 된다. */
int mk_cfgwire_stat(const MkConfig *cfg, int64_t now_ms,
                    const char *mode, const char *fw, const char *board_rev,
                    uint32_t uptime_ms,
                    const char *time_source, uint32_t time_quality,
                    const MkQueueStat *queues, size_t n_queues,
                    char *out, size_t cap);

/* `$CFG,GET` 응답 본문 한 줄. 반환은 길이, 실패면 음수.
 *
 * `cur` 의 JSON 타입은 항목의 vtype 을 따른다 — bool 은 참/거짓,
 * str 은 문자열, 나머지는 수다 (규격 §5.2). */
int mk_cfgwire_value(const MkCfgItem *item, int64_t now_ms,
                     char *out, size_t cap);

#endif /* MK_CFGWIRE_H */
