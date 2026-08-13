#include "mk_framing.h"

#include <string.h>

static int is_control(char c)
{
    unsigned char u = (unsigned char)c;
    return u < 0x20u || u == 0x7Fu;
}

/* Python 쪽 str.strip() 이 떼어내는 것과 같은 집합. */
static int is_space(char c)
{
    return c == ' ' || c == '\t' || c == '\n' ||
           c == '\r' || c == '\v' || c == '\f';
}

static char to_upper(char c)
{
    return (c >= 'a' && c <= 'f') ? (char)(c - 'a' + 'A') : c;
}

static const char HEX[] = "0123456789ABCDEF";

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
    size_t plen = strlen(payload);

    if (plen == 0u) {
        return -1;                       /* verb 없는 줄은 의미가 없다 */
    }
    for (size_t i = 0; i < plen; i++) {
        if (is_control(payload[i])) {
            return -1;                   /* 줄이 쪼개진다 */
        }
    }
    /* '$' + payload + '*' + 2 + "\r\n" + NUL = plen + 7.
     * 🔴 plen + 6 으로 세면 딱 한 바이트가 모자란 버퍼를 통과시키고 NUL 을
     *    버퍼 밖에 쓴다. 세어 보면 1+plen+1+2+2+1 이다. */
    if (cap < plen + 7u) {
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
    /* 양끝 공백·줄끝을 떼어낸다.
     * 🔴 Python 쪽 parse_line 은 line.strip() 이라 앞쪽도 뗀다. 뒤쪽만 떼면
     *    " $HB*0A" 를 한쪽은 받고 한쪽은 버려 두 구현이 갈린다. */
    while (len > 0u && is_space(line[len - 1u])) {
        len--;
    }
    while (len > 0u && is_space(line[0])) {
        line++;
        len--;
    }
    if (len < 2u || line[0] != '$') {
        return MK_ERR_MALFORMED;
    }

    /* 마지막 '*' 를 찾는다. payload 안에 '*' 가 있어도 체크섬은 항상 끝이다. */
    size_t star = 0u;
    int found = 0;
    for (size_t i = len; i > 1u; i--) {
        if (line[i - 1u] == '*') { star = i - 1u; found = 1; break; }
    }
    if (!found) {
        return MK_ERR_MALFORMED;
    }

    size_t plen = star - 1u;             /* '$' 제외 */
    if (plen == 0u || plen > MK_LINE_MAX) {
        return MK_ERR_MALFORMED;
    }

    /* 🔴 체크섬보다 필드 분해를 먼저 한다.
     *
     * 규격 §3: "체크섬이 맞지 않는 줄은 폐기하고 $SACK,<verb>,ERR,CHECKSUM 을
     * 보낸다. verb 를 읽을 수 없을 만큼 깨졌으면 조용히 버린다."
     *
     * 즉 MK_ERR_CHECKSUM 을 돌려줄 때 호출자에게 SACK 에 담을 verb 가 있어야
     * 한다. 체크섬을 먼저 보면 out 이 채워지기 전에 반환되어 verb 가 없다.
     *
     * 이 순서 덕분에 두 반환값이 규격의 두 동작에 정확히 대응한다.
     *   MK_ERR_CHECKSUM  → out->verb 유효 → SACK 을 보낸다
     *   MK_ERR_MALFORMED → verb 를 못 읽었다 → 조용히 버린다 */
    memset(out, 0, sizeof *out);
    size_t field = 0u;
    char *dst = out->verb;
    size_t dst_cap = MK_VERB_MAX;
    size_t w = 0u;

    for (size_t i = 1u; i < star; i++) {
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
            return MK_ERR_MALFORMED;     /* 고정폭 초과 — 잘라 담지 않는다 */
        }
        dst[w++] = line[i];
    }
    dst[w] = '\0';

    if (out->verb[0] == '\0') {
        return MK_ERR_MALFORMED;
    }
    out->argc = (int)field;

    /* '*' 뒤 전부를 기대값과 비교한다.
     * Python 쪽도 given.upper() != expected 로 뒤쪽 전체를 비교하므로,
     * 자릿수가 2가 아닌 것도 같은 방식으로 불일치가 된다. */
    uint8_t cs = mk_xor_checksum(line + 1, plen);
    char expect[2];
    expect[0] = HEX[(cs >> 4) & 0x0Fu];
    expect[1] = HEX[cs & 0x0Fu];

    size_t glen = len - star - 1u;
    const char *given = line + star + 1u;
    if (glen != 2u ||
        to_upper(given[0]) != expect[0] ||
        to_upper(given[1]) != expect[1]) {
        return MK_ERR_CHECKSUM;
    }

    return MK_OK;
}
