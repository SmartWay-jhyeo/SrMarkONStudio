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

/* 설정 항목 개수. 늘리면 저장 영역 크기도 따라간다.
 *
 * 🔴 96 으로 올린 이유: 디지털 입력 3 · LED 14 · I2C 24 가 늘어 86 이 됐다.
 *    mk_cfgtable.c 의 _Static_assert 가 이 값과 Flash staging 버퍼를 함께
 *    본다 — 항목만 늘리고 버퍼를 안 키우면 $CFG,SAVE 가 ERR,BUSY 로
 *    떨어지는데, 실기기에서 그것을 겪고 나서야 알았다.
 *
 * 🔴 112 로 다시 올렸다(2026-08-19). GNSS 3 개로 정확히 96 이 차 있어서
 *    `lcd.enabled` 하나를 더하는 순간 _Static_assert 가 터졌다 — 그것이
 *    제 일을 한 것이다. 여유를 두고 올린다: 112 × 24 + 32 = 2,720 이라
 *    MK_CFG_BLOB_MAX(4096) 안이고, main.c 의 s_blob 도 2,688 바이트로
 *    남는다. */
/* 🔴 128 -> 160 [2026-08-21]. 클라우드 링크 설정(ain{n}.cloud·valve_tag,
 *    i2c1x.cloud·valve_tag, cloud.valve, gnss.imu — 설계 2026-08-21 §5)으로
 *    항목이 103 -> 131 이 되어 128 을 넘었다. 블롭 예산은 그대로 안이다 —
 *    160 × 24 B = 3,840 ≤ MK_CFG_BLOB_MAX(4096). */
#define MK_CFG_MAX_ITEMS   160
#define MK_CFG_KEY_MAX     MK_ARG_MAX
#define MK_CFG_STR_MAX     MK_ARG_MAX

/* 저장 덩어리가 쓸 수 있는 최대 바이트 (머리말 포함).
 *
 * 🔴 **app 이 선언하고 bsp 가 맞춘다.** 반대로 두면 `app/` 이 `bsp/` 를
 *    알아야 하고, 그러면 호스트에서 컴파일되지 않는다 — 이 경계가 단위
 *    시험의 전제다(CLAUDE.md §0).
 *
 *    2048 이었는데 항목이 86 개가 되면서 넘쳤다(86 × 24 + 32 = 2096).
 *    실기기에서는 $CFG,SAVE 가 ERR,BUSY 로 떨어지는 형태로 나타난다. */
#define MK_CFG_BLOB_MAX    4096u

/* 출력 항목(`out`)의 최대 개수. TEST 에서 $CFG,SAVE 가 잠깐 빼 둘 자리다.
 *
 * 🔴 J18~J20 은 이제 `out` 이 아니다(사용자 확정 2026-08-18) — 입력이라
 *    보드가 명령하는 것이 아니라 읽기만 하고, TEST 모드를 벗어날 때
 *    기본값으로 되돌릴 "명령 상태" 자체가 없다. 지금은 전원 3 · LED 14
 *    = 17 개. */
#define MK_CFG_MAX_OUT     32u

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
    /* 🔴 이 항목이 **실제 출력을 움직인다** (전원 레일·밸브·LED).
     *
     *    TEST 제어 모드에서 저장되지 않고, 모드를 벗어날 때 기본값으로
     *    돌아간다 (규격 §6.4). 호스트가 키 이름으로 짐작하지 않도록
     *    카탈로그에 실어 보낸다 — `sol` 이라는 글자를 보고 판단하면
     *    이름을 바꾸는 순간 조용히 틀린다. */
    uint8_t     out;
    const char *label;
    const char *note;
    /* enum 의 허용값. 개수가 0 이면 enum 이 아니다. */
    const uint32_t *choices;
    uint8_t     n_choices;
    /* `choices` 와 같은 순서·같은 개수의 이름표 (규격 §7.3).
     *
     * 🔴 NULL 이어도 된다. PGA 배율(1·2·4·8)이나 SPS 처럼 숫자가 곧 뜻인
     *    열거에는 필요 없다 — 이름표를 붙이면 오히려 값이 가려진다. */
    const char *const *choice_labels;
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

/* 출력 항목(`out`)만 기본값으로. 되돌린 개수를 반환한다 (규격 §6.4).
 *
 * 🔴 "안전 상태" 는 전부 꺼짐이 아니라 **각 항목의 기본값**이다.
 *    `pwr.5v` 의 기본값은 켜짐이고 거기 쿨링 팬과 ADS1256 의 아날로그
 *    전원이 걸려 있다 — 테스트를 끝냈다고 팬을 세우면 안 된다. */
size_t mk_cfg_outputs_to_default(MkConfig *cfg);

/* 출력 항목의 현재값을 backup 으로 빼내고 기본값을 채운다. TEST 에서
 * $CFG,SAVE 가 출력을 저장하지 않게 하는 데 쓴다. 반환은 담은 개수.
 * cap 이 모자라면 아무것도 하지 않고 0 을 돌려준다 — 절반만 바꾸면
 * 복원이 불가능해진다. */
size_t mk_cfg_outputs_stash(MkConfig *cfg, MkValue *backup, size_t cap);
void   mk_cfg_outputs_unstash(MkConfig *cfg, const MkValue *backup, size_t n);

/* 🔴 값이 바뀌었는지. SAVE 가 필요한지 판단하는 데 쓴다 — 바뀐 것이
 *    없는데 Flash 를 지웠다 쓰면 수명만 깎는다. */
int mk_cfg_dirty(const MkConfig *cfg);
void mk_cfg_mark_saved(MkConfig *cfg);

#endif /* MK_CONFIG_H */
