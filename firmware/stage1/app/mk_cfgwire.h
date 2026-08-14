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

/* `$CFG,GET` 응답 본문 한 줄. 반환은 길이, 실패면 음수.
 *
 * `cur` 의 JSON 타입은 항목의 vtype 을 따른다 — bool 은 참/거짓,
 * str 은 문자열, 나머지는 수다 (규격 §5.2). */
int mk_cfgwire_value(const MkCfgItem *item, int64_t now_ms,
                     char *out, size_t cap);

#endif /* MK_CFGWIRE_H */
