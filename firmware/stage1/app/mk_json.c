#include "mk_json.h"

/* 🔴 <string.h> 도 <stdio.h> 도 필요 없다. 이 파일은 libc 를 부르지 않는다. */

/* 10^0 .. 10^6 — digits 상한이 6인 이유가 이 표다.
 * 🔴 mk_json_f32 의 클램프가 이 배열의 범위를 지킨다. 클램프를 지우면
 *    digits=10 같은 호출이 POW10[10] 을 읽는다. */
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
    /* 🔴 `cap >= 3` 을 여기서 따로 막지 않는다. put() 의 경계 검사가
     *    같은 결과를 내므로 아무것도 지키지 않는 분기였다 — cap 이
     *    0·1·2 일 때 모두 첫 put 에서 ok 가 0 이 되고 범위 밖 쓰기도
     *    없다는 것을 시험으로 못박아 두었다.
     *    buf != NULL 은 다르다. 이것이 없으면 NULL 에 쓴다. */
    j->ok = (buf != NULL);
    j->nfield = 0;
    put(j, '{');
}

/* 짧은 이스케이프가 있는 제어문자. 🔴 Python 의 json.dumps 가 이 다섯을
 * 짧은 형태로 쓴다. 전부 \u00XX 로 쓰면 JSON 으로는 같은 값이지만 바이트가
 * 달라져, "C 와 Python 이 같은 바이트를 낸다"는 이 계층의 계약이 깨진다.
 * 계약이 깨지면 대조 시험이 무엇을 보증하는지도 흐려진다.
 * 덤으로 전선 바이트가 6에서 2로 준다. */
static char short_escape(unsigned char c)
{
    switch (c) {
    case '\b': return 'b';
    case '\t': return 't';
    case '\n': return 'n';
    case '\f': return 'f';
    case '\r': return 'r';
    default:   return '\0';
    }
}

void mk_json_str(MkJson *j, const char *key, const char *val)
{
    static const char HEX[] = "0123456789abcdef";
    put_key(j, key);
    put(j, '"');
    for (const unsigned char *p = (const unsigned char *)val; *p; p++) {
        unsigned char c = *p;
        char esc;
        if (c == '"' || c == '\\') {
            put(j, '\\');
            put(j, (char)c);
        } else if ((esc = short_escape(c)) != '\0') {
            put(j, '\\');
            put(j, esc);
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

void mk_json_u32_array(MkJson *j, const char *key,
                       const uint32_t *values, size_t count)
{
    put_key(j, key);
    put(j, '[');
    for (size_t i = 0; i < count; i++) {
        if (i > 0u) {
            put(j, ',');
        }
        put_u64(j, values[i]);
    }
    put(j, ']');
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

/* 중첩 — `nfield` 를 "이 층에 이미 뭔가 썼나" 로만 쓴다.
 *
 * 🔴 스택을 두지 않는다. 층을 열 때 0 으로 놓고 닫을 때 1 로 되돌리면,
 *    바깥 층은 "적어도 하나 있다" 를 알게 되어 다음 키 앞에 쉼표를 찍는다.
 *    깊이 1 까지만 필요하고(규격 §7.4 의 rails·queues), 그 안에서는 이
 *    규칙이 정확하다. 더 깊어지면 스택이 필요하다 — 그때 고친다. */
void mk_json_object_begin(MkJson *j, const char *key)
{
    put_key(j, key);
    put(j, '{');
    j->nfield = 0;
}

void mk_json_object_end(MkJson *j)
{
    put(j, '}');
    j->nfield = 1;
}

void mk_json_array_begin(MkJson *j, const char *key)
{
    put_key(j, key);
    put(j, '[');
    j->nfield = 0;
}

void mk_json_array_object_begin(MkJson *j)
{
    if (j->nfield > 0) {
        put(j, ',');
    }
    put(j, '{');
    j->nfield = 0;
}

void mk_json_array_object_end(MkJson *j)
{
    put(j, '}');
    j->nfield = 1;
}

void mk_json_array_end(MkJson *j)
{
    put(j, ']');
    j->nfield = 1;
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
