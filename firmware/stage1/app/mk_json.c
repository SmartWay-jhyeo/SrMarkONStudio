#include "mk_json.h"

#include <string.h>

/* 10^0 .. 10^6 — digits 상한이 6인 이유가 이 표다. */
static const int64_t POW10[7] = {
    1, 10, 100, 1000, 10000, 100000, 1000000
};

static void put(MkJson *j, char c)
{
    if (!j->ok) {
        return;
    }
    if (j->len + 1u >= j->cap) {     /* NUL 자리를 남긴다 */
        j->ok = 0;
        return;
    }
    j->buf[j->len++] = c;
}

static void puts_raw(MkJson *j, const char *s)
{
    for (; *s; s++) {
        put(j, *s);
    }
}

/* uint64 를 10진수로. libc 를 쓰지 않는다. */
static void put_u64(MkJson *j, uint64_t v)
{
    char tmp[20];                    /* 2^64-1 은 20자리 */
    int n = 0;
    if (v == 0u) {
        put(j, '0');
        return;
    }
    while (v > 0u && n < (int)sizeof tmp) {
        tmp[n++] = (char)('0' + (v % 10u));
        v /= 10u;
    }
    while (n > 0) {
        put(j, tmp[--n]);
    }
}

static void put_i64(MkJson *j, int64_t v)
{
    /* 🔴 -v 로 뒤집으면 INT64_MIN 에서 넘친다. 부호 없는 쪽으로 옮긴다. */
    uint64_t mag;
    if (v < 0) {
        put(j, '-');
        mag = (uint64_t)(-(v + 1)) + 1u;
    } else {
        mag = (uint64_t)v;
    }
    put_u64(j, mag);
}

static void put_key(MkJson *j, const char *key)
{
    if (j->nfield > 0) {
        put(j, ',');
    }
    j->nfield++;
    put(j, '"');
    puts_raw(j, key);               /* 키는 우리가 정한 ASCII 리터럴이다 */
    put(j, '"');
    put(j, ':');
}

void mk_json_begin(MkJson *j, char *buf, size_t cap)
{
    j->buf = buf;
    j->cap = cap;
    j->len = 0u;
    j->ok = (buf != NULL && cap >= 3u);   /* 최소 "{}" + NUL */
    j->nfield = 0;
    put(j, '{');
}

void mk_json_str(MkJson *j, const char *key, const char *val)
{
    static const char HEX[] = "0123456789abcdef";
    put_key(j, key);
    put(j, '"');
    for (const unsigned char *p = (const unsigned char *)val; *p; p++) {
        unsigned char c = *p;
        if (c == '"' || c == '\\') {
            put(j, '\\');
            put(j, (char)c);
        } else if (c < 0x20u) {
            /* 설정 계층이 제어문자를 막지만, 여기서 한 번 더 막는다.
             * 값의 출처가 설정만은 아니게 될 수 있다. */
            put(j, '\\');
            put(j, 'u');
            put(j, '0');
            put(j, '0');
            put(j, HEX[(c >> 4) & 0x0Fu]);
            put(j, HEX[c & 0x0Fu]);
        } else {
            put(j, (char)c);
        }
    }
    put(j, '"');
}

void mk_json_i64(MkJson *j, const char *key, int64_t val)
{
    put_key(j, key);
    put_i64(j, val);
}

void mk_json_u64(MkJson *j, const char *key, uint64_t val)
{
    put_key(j, key);
    put_u64(j, val);
}

void mk_json_i32(MkJson *j, const char *key, int32_t val)
{
    mk_json_i64(j, key, (int64_t)val);
}

void mk_json_u32(MkJson *j, const char *key, uint32_t val)
{
    mk_json_u64(j, key, (uint64_t)val);
}

void mk_json_bool(MkJson *j, const char *key, int val)
{
    put_key(j, key);
    puts_raw(j, val ? "true" : "false");
}

void mk_json_f32(MkJson *j, const char *key, float val, int digits)
{
    put_key(j, key);

    if (digits < 0) {
        digits = 0;
    }
    if (digits > 6) {
        digits = 6;
    }

    double scaled = (double)val * (double)POW10[digits];

    /* 🔴 이 한 줄이 NaN·±Inf·범위초과를 모두 잡는다. 부정형으로 쓴 것이
     *    핵심이다 — NaN 은 어떤 비교도 거짓이므로 `scaled > -9e18` 과
     *    `scaled < 9e18` 이 둘 다 거짓이 되고, 부정하면 참이 된다.
     *    `if (val != val)` 로 NaN 을 따로 거르는 코드를 앞에 두었더니
     *    되돌림 검사에서 그 분기를 지워도 아무 시험도 실패하지 않았다.
     *    아무것도 지키지 않는 분기였다.
     *
     *    int64 로 옮길 수 없는 값은 지어내지 않는다. 경계를 int64 최대치
     *    (약 9.22e18)보다 낮게 잡아 부동소수점 오차 여유를 둔다. */
    if (!(scaled > -9.0e18 && scaled < 9.0e18)) {
        puts_raw(j, "null");
        return;
    }

    /* 0 에서 먼 쪽으로 반올림 — Python 의 round() 는 짝수 쪽이지만,
     * 여기서 맞춰야 하는 것은 json.dumps 가 아니라 사람이 읽는 값이다.
     * 대조 시험은 Python 쪽도 같은 규칙으로 계산한다. */
    int neg = scaled < 0.0;
    if (neg) {
        scaled = -scaled;
    }
    int64_t units = (int64_t)(scaled + 0.5);

    int64_t ip = units / POW10[digits];
    int64_t fp = units % POW10[digits];

    if (neg && (ip != 0 || fp != 0)) {
        put(j, '-');
    }
    put_u64(j, (uint64_t)ip);

    if (digits > 0) {
        put(j, '.');
        /* 앞자리 0 을 채운다. 12.5 를 digits=4 로 내면 12.5000 이다. */
        for (int d = digits - 1; d > 0; d--) {
            if (fp < POW10[d]) {
                put(j, '0');
            } else {
                break;
            }
        }
        put_u64(j, (uint64_t)fp);
    }
}

int mk_json_end(MkJson *j)
{
    put(j, '}');
    if (!j->ok) {
        if (j->buf != NULL && j->cap > 0u) {
            j->buf[0] = '\0';        /* 반쪽짜리 줄을 흘리지 않는다 */
        }
        return -1;
    }
    j->buf[j->len] = '\0';
    return (int)j->len;
}
