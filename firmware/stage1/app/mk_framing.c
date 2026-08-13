#include "mk_framing.h"

#include <string.h>

static int is_control(char c)
{
    unsigned char u = (unsigned char)c;
    return u < 0x20u || u == 0x7Fu;
}

static int hex_digit(char c)
{
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

uint8_t mk_xor_checksum(const char *payload, size_t len)
{
    uint8_t cs = 0u;
    for (size_t i = 0; i < len; i++) {
        cs ^= (uint8_t)payload[i];
    }
    return cs;
}

int mk_build_line(char *out, size_t cap, const char *payload)
{
    static const char HEX[] = "0123456789ABCDEF";
    size_t plen = strlen(payload);

    if (plen == 0u) {
        return -1;                       /* verb 없는 줄은 의미가 없다 */
    }
    for (size_t i = 0; i < plen; i++) {
        if (is_control(payload[i])) {
            return -1;                   /* 줄이 쪼개진다 */
        }
    }
    /* '$' + payload + '*' + 2 + "\r\n" + NUL */
    if (cap < plen + 6u) {
        return -1;
    }

    uint8_t cs = mk_xor_checksum(payload, plen);
    size_t n = 0;
    out[n++] = '$';
    memcpy(out + n, payload, plen);
    n += plen;
    out[n++] = '*';
    out[n++] = HEX[(cs >> 4) & 0x0Fu];
    out[n++] = HEX[cs & 0x0Fu];
    out[n++] = '\r';
    out[n++] = '\n';
    out[n]   = '\0';
    return (int)n;
}

MkParseResult mk_parse_line(const char *line, size_t len, MkCommand *out)
{
    /* 양끝 공백·줄끝을 떼어낸다. */
    while (len > 0u && (line[len - 1u] == '\r' || line[len - 1u] == '\n' ||
                        line[len - 1u] == ' '  || line[len - 1u] == '\t')) {
        len--;
    }
    if (len < 4u || line[0] != '$') {    /* 최소 "$X*CS" 도 안 된다 */
        return MK_ERR_MALFORMED;
    }

    /* 마지막 '*' 를 찾는다. payload 안에 '*' 가 있어도 체크섬은 항상 끝이다. */
    size_t star = 0u;
    int found = 0;
    for (size_t i = len; i > 1u; i--) {
        if (line[i - 1u] == '*') { star = i - 1u; found = 1; break; }
    }
    if (!found || star + 3u != len) {    /* '*' 뒤에 정확히 2자리 */
        return MK_ERR_MALFORMED;
    }

    size_t plen = star - 1u;             /* '$' 제외 */
    if (plen == 0u || plen > MK_LINE_MAX) {
        return MK_ERR_MALFORMED;
    }

    int hi = hex_digit(line[star + 1u]);
    int lo = hex_digit(line[star + 2u]);
    if (hi < 0 || lo < 0) {
        return MK_ERR_MALFORMED;
    }
    uint8_t given = (uint8_t)((hi << 4) | lo);
    if (given != mk_xor_checksum(line + 1, plen)) {
        return MK_ERR_CHECKSUM;
    }

    /* payload 를 쉼표로 쪼갠다. 넘치면 잘라 담지 말고 거부한다. */
    memset(out, 0, sizeof *out);
    size_t i = 1u;                       /* '$' 다음 */
    size_t end = star;
    size_t field = 0u;
    char *dst = out->verb;
    size_t dst_cap = MK_VERB_MAX;
    size_t w = 0u;

    for (; i < end; i++) {
        if (line[i] == ',') {
            dst[w] = '\0';
            if (field >= MK_ARGS_MAX) {
                return MK_ERR_MALFORMED;
            }
            dst = out->args[field];
            dst_cap = MK_ARG_MAX;
            w = 0u;
            field++;
            continue;
        }
        if (w >= dst_cap) {
            return MK_ERR_MALFORMED;     /* 고정폭 초과 */
        }
        dst[w++] = line[i];
    }
    dst[w] = '\0';

    if (out->verb[0] == '\0') {
        return MK_ERR_MALFORMED;
    }
    out->argc = (int)field;
    return MK_OK;
}
