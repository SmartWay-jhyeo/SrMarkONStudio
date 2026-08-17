#include "mk_config.h"

#include <string.h>

/* 규격 §5 의 사유 문자열. 전선에 그대로 나간다. */
const char *mk_cfg_reason_text(MkCfgResult r)
{
    switch (r) {
    case MK_CFG_OK:          return "OK";
    case MK_CFG_UNKNOWN_KEY: return "UNKNOWN_KEY";
    case MK_CFG_RANGE:       return "RANGE";
    case MK_CFG_INTERLOCK:   return "INTERLOCK";
    case MK_CFG_READONLY:    return "READONLY";
    case MK_CFG_MODE:        return "MODE";
    case MK_CFG_BUSY:        return "BUSY";
    case MK_CFG_CAPACITY:    return "CAPACITY";
    default:                 return "RANGE";
    }
}

MkCfgItem *mk_cfg_find(MkConfig *cfg, const char *key)
{
    for (size_t i = 0; i < cfg->count; i++) {
        if (strcmp(cfg->items[i].key, key) == 0) {
            return &cfg->items[i];
        }
    }
    return NULL;
}

/* ---- 문자열 → 값 -------------------------------------------------------- */

static int parse_bool(const char *s, uint32_t *out)
{
    if (strcmp(s, "true") == 0 || strcmp(s, "1") == 0) { *out = 1u; return 1; }
    if (strcmp(s, "false") == 0 || strcmp(s, "0") == 0) { *out = 0u; return 1; }
    return 0;
}

/* 부호 없는 10진수. libc 를 쓰지 않는다 (mk_json 과 같은 이유). */
static int parse_u32(const char *s, uint32_t *out)
{
    if (*s == '\0') {
        return 0;
    }
    uint32_t v = 0;
    for (const char *p = s; *p; p++) {
        if (*p < '0' || *p > '9') {
            return 0;
        }
        uint32_t d = (uint32_t)(*p - '0');
        /* 🔴 넘침을 검사한다. 안 하면 4294967296 이 0 이 되어 범위 검사를
         *    통과한다 — 사용자가 넣은 값과 저장된 값이 달라진다. */
        if (v > (0xFFFFFFFFu - d) / 10u) {
            return 0;
        }
        v = v * 10u + d;
    }
    *out = v;
    return 1;
}

/* 부호 있는 십진 실수. 지수 표기는 받지 않는다 — 전선에 나올 일이 없다. */
static int parse_f32(const char *s, float *out)
{
    int neg = 0;
    const char *p = s;
    if (*p == '-') { neg = 1; p++; }
    else if (*p == '+') { p++; }
    if (*p == '\0') {
        return 0;
    }

    double v = 0.0;
    int digits = 0;
    for (; *p >= '0' && *p <= '9'; p++) {
        v = v * 10.0 + (*p - '0');
        digits++;
    }
    if (*p == '.') {
        p++;
        double scale = 0.1;
        for (; *p >= '0' && *p <= '9'; p++) {
            v += (*p - '0') * scale;
            scale *= 0.1;
            digits++;
        }
    }
    if (*p != '\0' || digits == 0) {
        return 0;
    }
    *out = (float)(neg ? -v : v);
    return 1;
}

/* ---- 검증 --------------------------------------------------------------- */

static MkCfgResult coerce(const MkCfgItem *item, const char *raw, MkValue *out)
{
    switch (item->vtype) {
    case MK_VT_BOOL:
        return parse_bool(raw, &out->u) ? MK_CFG_OK : MK_CFG_RANGE;

    case MK_VT_U8:
    case MK_VT_U16:
    case MK_VT_U32: {
        if (!parse_u32(raw, &out->u)) {
            return MK_CFG_RANGE;
        }
        if ((item->has_min && (float)out->u < item->min) ||
            (item->has_max && (float)out->u > item->max)) {
            return MK_CFG_RANGE;
        }
        return MK_CFG_OK;
    }

    case MK_VT_ENUM: {
        if (!parse_u32(raw, &out->u)) {
            return MK_CFG_RANGE;
        }
        for (uint8_t i = 0; i < item->n_choices; i++) {
            if (item->choices[i] == out->u) {
                return MK_CFG_OK;
            }
        }
        return MK_CFG_RANGE;
    }

    case MK_VT_F32: {
        if (!parse_f32(raw, &out->f)) {
            return MK_CFG_RANGE;
        }
        if ((item->has_min && out->f < item->min) ||
            (item->has_max && out->f > item->max)) {
            return MK_CFG_RANGE;
        }
        return MK_CFG_OK;
    }

    case MK_VT_STR: {
        size_t n = strlen(raw);
        if ((item->has_max && n > (size_t)item->max) || n > MK_CFG_STR_MAX) {
            return MK_CFG_RANGE;
        }
        /* 🔴 프로토콜 구분자와 제어문자를 막는다. 값 하나가 줄 구조를
         *    깨뜨리면 그 조각이 완결된 명령이 될 수 있다 — 호스트 쪽
         *    framing.build_line 이 막는 것과 같은 부류다. */
        for (const char *p = raw; *p; p++) {
            unsigned char c = (unsigned char)*p;
            if (c < 0x20u || c >= 0x7Fu) {
                return MK_CFG_RANGE;
            }
            if (c == '$' || c == ',' || c == '*') {
                return MK_CFG_RANGE;
            }
        }
        memcpy(out->s, raw, n);
        out->s[n] = '\0';
        return MK_CFG_OK;
    }

    default:
        return MK_CFG_RANGE;
    }
}

static int same_value(const MkCfgItem *item, const MkValue *v)
{
    if (item->vtype == MK_VT_STR) {
        return strcmp(item->cur.s, v->s) == 0;
    }
    if (item->vtype == MK_VT_F32) {
        return item->cur.f == v->f;
    }
    return item->cur.u == v->u;
}

MkCfgResult mk_cfg_set(MkConfig *cfg, const char *key, const char *raw)
{
    MkCfgItem *item = mk_cfg_find(cfg, key);
    if (item == NULL) {
        return MK_CFG_UNKNOWN_KEY;
    }

    MkValue v;
    memset(&v, 0, sizeof v);

    /* 🔴 규격 §5.2 의 순서를 지킨다.
     *      키 존재 → 타입·범위 → 인터록 → 읽기 전용 → 수락
     *
     *    범위 검사를 인터록보다 **먼저** 한다. 값이 틀린 것과 안전상
     *    거부된 것은 사용자에게 다른 메시지여야 하기 때문이다. 순서를
     *    바꾸면 범위 밖 값을 넣었을 때 INTERLOCK 이 나와, 사용자는 값이
     *    아니라 안전 정책 문제로 읽는다. */
    MkCfgResult r = coerce(item, raw, &v);
    if (r != MK_CFG_OK) {
        return r;
    }

    /* 🔴 현재값과 같으면 아무것도 바꾸지 않으므로 받아들인다.
     *    인터록·읽기 전용보다 먼저 본다 — 호스트가 전체 설정을 한꺼번에
     *    되돌려 쓸 때 pwr.5v 를 true 로 두는 요청까지 거부되면 안 된다. */
    if (same_value(item, &v)) {
        return MK_CFG_OK;
    }

    /* 🔴 인터록이 읽기 전용보다 우선한다 (규격 §5.2). 둘 다 해당하는
     *    항목은 INTERLOCK 을 돌려줘야 note 에 담긴 하드웨어 사실이
     *    사유로 전달된다. READONLY 만 돌려주면 그 이유가 사라진다. */
    if (item->interlocked) {
        return MK_CFG_INTERLOCK;
    }
    if (item->readonly) {
        return MK_CFG_READONLY;
    }

    item->cur = v;
    cfg->dirty = 1u;
    return MK_CFG_OK;
}

/* ---- 값 → 문자열 -------------------------------------------------------- */

static int put_u32(char *out, size_t cap, uint32_t v)
{
    char tmp[10];
    int n = 0;
    if (v == 0u) {
        if (cap < 2u) return -1;
        out[0] = '0';
        out[1] = '\0';
        return 1;
    }
    while (v > 0u && n < (int)sizeof tmp) {
        tmp[n++] = (char)('0' + (v % 10u));
        v /= 10u;
    }
    if ((size_t)n + 1u > cap) {
        return -1;
    }
    for (int i = 0; i < n; i++) {
        out[i] = tmp[n - 1 - i];
    }
    out[n] = '\0';
    return n;
}

int mk_cfg_format(const MkCfgItem *item, char *out, size_t cap)
{
    if (out == NULL || cap == 0u) {
        return -1;
    }
    switch (item->vtype) {
    case MK_VT_BOOL: {
        const char *s = item->cur.u ? "true" : "false";
        size_t n = strlen(s);
        if (n + 1u > cap) return -1;
        memcpy(out, s, n + 1u);
        return (int)n;
    }
    case MK_VT_STR: {
        size_t n = strlen(item->cur.s);
        if (n + 1u > cap) return -1;
        memcpy(out, item->cur.s, n + 1u);
        return (int)n;
    }
    case MK_VT_F32: {
        /* 소수 4자리 고정. mk_json_f32 와 같은 규칙으로 반올림한다. */
        double scaled = (double)item->cur.f * 10000.0;
        if (!(scaled > -2.0e9 && scaled < 2.0e9)) {
            return -1;
        }
        int neg = scaled < 0.0;
        if (neg) scaled = -scaled;
        uint32_t units = (uint32_t)(scaled + 0.5);
        uint32_t ip = units / 10000u;
        uint32_t fp = units % 10000u;

        size_t n = 0;
        if (neg && units != 0u) {
            if (n + 1u >= cap) return -1;
            out[n++] = '-';
        }
        int w = put_u32(out + n, cap - n, ip);
        if (w < 0) return -1;
        n += (size_t)w;
        if (n + 1u >= cap) return -1;
        out[n++] = '.';
        for (uint32_t d = 1000u; d > 1u; d /= 10u) {
            if (fp < d) {
                if (n + 1u >= cap) return -1;
                out[n++] = '0';
            } else {
                break;
            }
        }
        w = put_u32(out + n, cap - n, fp);
        if (w < 0) return -1;
        return (int)(n + (size_t)w);
    }
    default:
        return put_u32(out, cap, item->cur.u);
    }
}

void mk_cfg_reset(MkConfig *cfg)
{
    for (size_t i = 0; i < cfg->count; i++) {
        cfg->items[i].cur = cfg->items[i].def;
    }
    cfg->dirty = 1u;
}

size_t mk_cfg_outputs_to_default(MkConfig *cfg)
{
    size_t n = 0;
    for (size_t i = 0; i < cfg->count; i++) {
        MkCfgItem *it = &cfg->items[i];
        if (!it->out) {
            continue;
        }
        /* 이미 기본값이면 dirty 를 세우지 않는다 — 아무것도 안 바뀌었는데
         * Flash 를 지웠다 쓰면 수명만 깎는다. */
        if (memcmp(&it->cur, &it->def, sizeof it->cur) != 0) {
            it->cur = it->def;
            cfg->dirty = 1u;
            n++;
        }
    }
    return n;
}

size_t mk_cfg_outputs_stash(MkConfig *cfg, MkValue *backup, size_t cap)
{
    size_t n = 0;
    for (size_t i = 0; i < cfg->count; i++) {
        if (cfg->items[i].out) {
            n++;
        }
    }
    /* 🔴 절반만 바꾸면 복원이 불가능해진다. 자리가 모자라면 손대지 않는다. */
    if (n > cap) {
        return 0;
    }

    n = 0;
    for (size_t i = 0; i < cfg->count; i++) {
        MkCfgItem *it = &cfg->items[i];
        if (it->out) {
            backup[n++] = it->cur;
            it->cur = it->def;
        }
    }
    return n;
}

void mk_cfg_outputs_unstash(MkConfig *cfg, const MkValue *backup, size_t n)
{
    size_t k = 0;
    for (size_t i = 0; i < cfg->count && k < n; i++) {
        if (cfg->items[i].out) {
            cfg->items[i].cur = backup[k++];
        }
    }
}

int mk_cfg_dirty(const MkConfig *cfg)
{
    return cfg->dirty != 0u;
}

void mk_cfg_mark_saved(MkConfig *cfg)
{
    cfg->dirty = 0u;
}
