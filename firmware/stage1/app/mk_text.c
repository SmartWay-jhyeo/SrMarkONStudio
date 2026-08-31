#include "mk_text.h"

/* 🔴 mk_json.c 와 같은 표다. digits 상한이 6 인 이유가 이 배열이다. */
static const int64_t POW10[7] = {
    1, 10, 100, 1000, 10000, 100000, 1000000
};

#define MK_TEXT_DIGITS_MAX  6

void mk_text_begin(MkText *t, char *buf, size_t cap)
{
    t->buf = buf;
    t->cap = cap;
    t->len = 0u;
    t->ok = (buf != 0 && cap > 0u);
    if (t->ok) {
        buf[0] = '\0';
    }
}

int mk_text_ok(const MkText *t)      { return t->ok; }
size_t mk_text_len(const MkText *t)  { return t->len; }

void mk_text_putc(MkText *t, char c)
{
    if (!t->ok) {
        return;
    }
    if (t->len + 1u >= t->cap) {     /* NUL 자리를 남긴다 */
        t->ok = 0;
        return;
    }
    t->buf[t->len++] = c;
    t->buf[t->len] = '\0';
}

void mk_text_puts(MkText *t, const char *s)
{
    if (s == 0) {
        return;
    }
    for (; *s != '\0'; s++) {
        mk_text_putc(t, *s);
    }
}

void mk_text_u32(MkText *t, uint32_t v)
{
    char tmp[10];                    /* 2^32-1 은 10자리 */
    int n = 0;
    if (v == 0u) {
        mk_text_putc(t, '0');
        return;
    }
    while (v > 0u && n < (int)sizeof tmp) {
        tmp[n++] = (char)('0' + (v % 10u));
        v /= 10u;
    }
    while (n > 0) {
        mk_text_putc(t, tmp[--n]);
    }
}

/* ---- 실수 --------------------------------------------------------------- */

static size_t udigits(uint64_t v)
{
    size_t n = 1u;
    while (v >= 10u) { v /= 10u; n++; }
    return n;
}

/* `d` 자릿수로 반올림한 정수 단위. 담을 수 없으면 0 을 돌려준다. */
static int scale_to_units(float v, int d, uint64_t *units, int *neg)
{
    double scaled = (double)v * (double)POW10[d];

    /* 🔴 mk_json_f32 와 같은 한 줄이다. 부정형으로 쓴 것이 핵심 — NaN 은
     *    어떤 비교도 거짓이므로 두 비교가 다 거짓이 되고, 부정하면 참이
     *    된다. `v != v` 로 NaN 을 따로 거르는 분기는 아무것도 지키지
     *    못한다(mk_json.c 의 되돌림 검사에서 확인된 사실). */
    if (!(scaled > -9.0e18 && scaled < 9.0e18)) {
        return 0;
    }

    *neg = scaled < 0.0;
    if (*neg) {
        scaled = -scaled;
    }
    /* 0 에서 먼 쪽으로 반올림 — mk_json_f32 와 같은 규칙. */
    *units = (uint64_t)(scaled + 0.5);
    return 1;
}

size_t mk_text_f32_fit(char *out, size_t cap, float v, int digits)
{
    if (out == 0 || cap == 0u) {
        return 0u;
    }
    out[0] = '\0';

    /* "OVF"·"---" 를 담지도 못하는 칸이면 아무 말도 못 한다. 이런 칸을
     * 만든 것 자체가 배치 실수이고, 시험이 배치를 따로 본다. */
    if (cap < 4u) {
        return 0u;
    }

    if (digits < 0) { digits = 0; }
    if (digits > MK_TEXT_DIGITS_MAX) { digits = MK_TEXT_DIGITS_MAX; }

    MkText t;

    uint64_t units = 0u;
    int neg = 0;
    if (!scale_to_units(v, 0, &units, &neg)) {
        /* 유한하지 않거나 int64 밖 — 지어내지 않는다. */
        mk_text_begin(&t, out, cap);
        mk_text_puts(&t, "---");
        return t.len;
    }

    /* 🔴 자릿수를 줄여 가며 들어가는 것을 찾는다. 자르지 않는다. */
    for (int d = digits; d >= 0; d--) {
        if (!scale_to_units(v, d, &units, &neg)) {
            continue;                        /* 그 자릿수로는 넘친다 */
        }
        uint64_t ip = units / (uint64_t)POW10[d];
        uint64_t fp = units % (uint64_t)POW10[d];

        /* 반올림 결과가 0 이면 부호를 안 붙인다 — mk_json_f32 와 같다.
         * "-0.00" 은 값이 음수라는 뜻으로 읽히지만 실제로는 반올림의
         * 부산물이고, 화면과 호스트가 다르게 말하면 안 된다. */
        int sign = (neg && (ip != 0u || fp != 0u)) ? 1 : 0;

        size_t need = (size_t)sign + udigits(ip);
        if (d > 0) {
            need += 1u + (size_t)d;
        }
        if (need + 1u > cap) {
            continue;                        /* 이 자릿수로는 칸을 넘는다 */
        }

        mk_text_begin(&t, out, cap);
        if (sign) {
            mk_text_putc(&t, '-');
        }
        mk_text_u32(&t, (uint32_t)(ip % 1000000000u));
        if (ip >= 1000000000u) {
            /* 10자리를 넘는 정수부는 화면에 쓸 일이 없다 — 여기까지
             * 왔다면 스케일 설정이 잘못된 것이다. 값인 척하지 않는다. */
            mk_text_begin(&t, out, cap);
            mk_text_puts(&t, "OVF");
            return t.len;
        }
        if (d > 0) {
            mk_text_putc(&t, '.');
            /* 앞자리 0 을 채운다. 12.5 를 d=4 로 내면 12.5000 이다. */
            for (int k = d - 1; k > 0; k--) {
                if (fp < (uint64_t)POW10[k]) {
                    mk_text_putc(&t, '0');
                } else {
                    break;
                }
            }
            mk_text_u32(&t, (uint32_t)fp);
        }
        return t.len;
    }

    /* 정수부만으로도 안 들어간다. */
    mk_text_begin(&t, out, cap);
    mk_text_puts(&t, "OVF");
    return t.len;
}
