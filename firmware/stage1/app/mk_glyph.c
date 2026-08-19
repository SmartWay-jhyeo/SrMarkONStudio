#include "mk_glyph.h"

#include <stddef.h>

static unsigned str_len(const char *s)
{
    unsigned n = 0u;
    while (s[n] != '\0') { n++; }
    return n;
}

unsigned mk_glyph_text_width(const MkFont *f, const char *s, unsigned scale)
{
    if (f == 0 || s == 0 || scale == 0u) {
        return 0u;
    }
    unsigned n = str_len(s);
    if (n == 0u) {
        return 0u;
    }
    /* 🔴 (n-1)개의 진행폭 + 마지막 글자의 폭. n*advance 로 세면 마지막
     *    자간까지 폭에 넣게 되어, 오른쪽 정렬이 한 칸씩 왼쪽으로 밀린다. */
    return ((n - 1u) * f->advance + f->width) * scale;
}

unsigned mk_glyph_text_height(const MkFont *f, unsigned scale)
{
    if (f == 0 || scale == 0u) {
        return 0u;
    }
    return (unsigned)f->height * scale;
}

int mk_glyph_text_pixel(const MkFont *f, const char *s,
                        unsigned x, unsigned y, unsigned scale)
{
    if (f == 0 || s == 0 || scale == 0u || f->glyph == 0) {
        return 0;
    }

    /* 배율은 그리기 쪽 성질이다 — 표는 배율을 모른다. */
    unsigned gx = x / scale;
    unsigned gy = y / scale;
    if (gy >= f->height) {
        return 0;
    }

    unsigned idx = gx / f->advance;
    unsigned col = gx % f->advance;
    if (col >= f->width) {
        return 0;               /* 자간 — 글리프가 없는 자리 */
    }
    if (idx >= str_len(s)) {
        return 0;               /* 문자열 끝 뒤 */
    }

    /* 🔴 부호 확장을 막는다. 0x80 이상인 바이트가 오면 int 로 승격될 때
     *    음수가 되고, 그 값으로 코드포인트를 만들면 표를 엉뚱하게 찾는다.
     *    지금 표는 ASCII 뿐이라 어차피 NULL 이 나오지만, 한글 표를 얹으면
     *    이것이 곧바로 문제가 된다. */
    uint32_t cp = (uint32_t)(unsigned char)s[idx];

    const uint8_t *g = f->glyph(f, cp);
    if (g == 0) {
        return 0;               /* 표에 없는 글자 — 빈칸으로 둔다 */
    }

    /* 열 우선, 비트 0 이 맨 윗줄 (mk_font.h). */
    const uint8_t *bytes = &g[(size_t)col * f->bytes_per_col];
    return (bytes[gy >> 3] >> (gy & 7u)) & 1u;
}
