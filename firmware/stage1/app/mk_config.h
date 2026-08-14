/* MarkON 설정 저장소 — HAL 비의존.
 *
 * 🔴 이 파일은 stm32h7xx_hal.h 를 include 하지 않는다. Flash 에 쓰는 것은
 *    바깥(`bsp/mk_flash.c`)이 하고, 여기서는 **무엇을 쓸지**만 정한다.
 *    그래서 검증 순서와 거부 사유를 호스트에서 그대로 시험할 수 있다.
 *
 * 규격: protocol/specification.md §4·§5·§7.3
 */
#ifndef MK_CONFIG_H
#define MK_CONFIG_H

#include <stddef.h>
#include <stdint.h>

#include "mk_framing.h"   /* MK_ARG_MAX */

/* 설정 항목 개수. 늘리면 저장 영역 크기도 따라간다. */
#define MK_CFG_MAX_ITEMS   64
#define MK_CFG_KEY_MAX     MK_ARG_MAX
#define MK_CFG_STR_MAX     MK_ARG_MAX

typedef enum {
    MK_VT_BOOL = 0,
    MK_VT_U8,
    MK_VT_U16,
    MK_VT_U32,
    MK_VT_F32,
    MK_VT_STR,
    MK_VT_ENUM
} MkVType;

/* 규격 §5 의 거부 사유. 문자열은 mk_cfg_reason_text() 가 준다. */
typedef enum {
    MK_CFG_OK = 0,
    MK_CFG_UNKNOWN_KEY,
    MK_CFG_RANGE,
    MK_CFG_INTERLOCK,
    MK_CFG_READONLY,
    MK_CFG_MODE,
    MK_CFG_BUSY,
    MK_CFG_CAPACITY
} MkCfgResult;

typedef union {
    uint32_t u;
    float    f;
    char     s[MK_CFG_STR_MAX + 1];
} MkValue;

typedef struct {
    const char *key;
    const char *group;
    MkVType     vtype;
    MkValue     def;
    MkValue     cur;
    /* 숫자 항목의 범위. 문자열은 max 를 길이 상한으로 쓴다.
     *
     * 🔴 `has_min`·`has_max` 가 0 이면 전선에 싣지 않는다. 규격 §7.3 은
     *    범위가 없는 항목에 min·max 를 요구하지 않고, 없는 것을 0 으로
     *    실어 보내면 화면이 "최소 0" 이라고 잘못 말한다. */
    float       min;
    float       max;
    uint8_t     has_min;
    uint8_t     has_max;
    const char *unit;
    /* 🔴 인터록은 읽기 전용과 다르다. 규격 §5.2 — 둘 다 해당하면
     *    INTERLOCK 이 이긴다. 사용자가 왜 안 되는지 알아야 하기 때문이다. */
    uint8_t     readonly;
    uint8_t     interlocked;
    const char *label;
    const char *note;
    /* enum 의 허용값. 개수가 0 이면 enum 이 아니다. */
    const uint32_t *choices;
    uint8_t     n_choices;
} MkCfgItem;

typedef struct {
    MkCfgItem *items;
    size_t     count;
    /* 마지막 저장 이후 값이 바뀌었는가. mk_cfg_set 이 세우고
     * mk_cfg_mark_saved 가 내린다. */
    uint8_t    dirty;
} MkConfig;

const char *mk_cfg_reason_text(MkCfgResult r);

/* 키로 항목을 찾는다. 없으면 NULL. */
MkCfgItem *mk_cfg_find(MkConfig *cfg, const char *key);

/* 문자열 값을 검사하고 받아들인다.
 *
 * 🔴 검증 순서가 규격 §5.2 에 못박혀 있다.
 *
 *        키 존재 → 타입·범위 → 인터록 → 읽기 전용 → 수락
 *
 *    값이 틀린 것과 안전상 거부된 것은 사용자에게 다른 메시지여야 하므로
 *    인터록을 범위 검사 **뒤에** 둔다. 순서를 바꾸면 범위 밖 값을 넣었을
 *    때 INTERLOCK 이 나와, 사용자는 값이 아니라 안전 정책 문제로 읽는다.
 *
 * 🔴 현재값과 같은 값을 쓰는 것은 거부하지 않는다. 호스트가 전체 설정을
 *    한꺼번에 되돌려 쓸 때 불필요한 거부가 나지 않게 한다. */
MkCfgResult mk_cfg_set(MkConfig *cfg, const char *key, const char *raw);

/* 현재값을 전선에 실을 문자열로. 반환은 out 에 쓴 길이, 실패면 음수. */
int mk_cfg_format(const MkCfgItem *item, char *out, size_t cap);

/* 전부 기본값으로. */
void mk_cfg_reset(MkConfig *cfg);

/* 🔴 값이 바뀌었는지. SAVE 가 필요한지 판단하는 데 쓴다 — 바뀐 것이
 *    없는데 Flash 를 지웠다 쓰면 수명만 깎는다. */
int mk_cfg_dirty(const MkConfig *cfg);
void mk_cfg_mark_saved(MkConfig *cfg);

#endif /* MK_CONFIG_H */
