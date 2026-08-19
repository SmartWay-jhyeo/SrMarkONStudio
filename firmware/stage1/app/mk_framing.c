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

    /* verb 부터 홀로 읽는다. $GNSS 인지 알아야 이어지는 필드 파싱이
     * 갈린다(아래) — 그래서 verb 파싱을 나머지와 분리해 뒀다. */
    size_t i = 1u;
    size_t vw = 0u;
    while (i < star && line[i] != ',') {
        if (vw >= MK_VERB_MAX) {
            return MK_ERR_MALFORMED;     /* 고정폭 초과 — 잘라 담지 않는다 */
        }
        out->verb[vw++] = line[i++];
    }
    out->verb[vw] = '\0';
    if (out->verb[0] == '\0') {
        return MK_ERR_MALFORMED;
    }

    if (strcmp(out->verb, "GNSS") == 0) {
        /* 🔴 $GNSS 는 일반 인자 쪼개기(쉼표 분할)를 거치지 않는다 — payload
         * 의 나머지 전부를 하나의 원문으로 가져간다. Unicore 명령은
         * 공백으로 나뉘고 쉼표를 안 쓰므로 원문 그대로 넘기는 편이,
         * 나중에 어떤 명령이 와도 안전하다(규격 §4.1, mk_framing.h 의
         * MK_GNSS_TEXT_MAX 주석 — 실기기 "Too less field!" 근거).
         *
         * 상한을 넘으면 다른 필드 초과와 똑같이 조용히 버린다(고정폭
         * 버퍼 계약, 이 파일 머리말) — verb 는 이미 유효해도 마찬가지다.
         * 호스트가 보내기 전에 이미 이 상한을 확인하므로(host/core/
         * limits.py), 여기까지 오는 초과는 방어선일 뿐이다. */
        size_t tail_start = i;
        if (tail_start < star) {
            tail_start++;                 /* line[i] == ',' 를 건너뛴다 */
        }
        size_t tail_len = star - tail_start;
        if (tail_len > MK_GNSS_TEXT_MAX) {
            return MK_ERR_MALFORMED;
        }
        memcpy(out->gnss_text, line + tail_start, tail_len);
        out->gnss_text[tail_len] = '\0';
        out->argc = (tail_len > 0u) ? 1 : 0;
    } else {
        size_t field = 0u;
        if (i < star) {
            i++;                          /* line[i] == ',' 를 건너뛴다 */
            char *dst = out->args[0];
            size_t dst_cap = MK_ARG_MAX;
            size_t w = 0u;
            for (; i < star; i++) {
                if (line[i] == ',') {
                    dst[w] = '\0';
                    field++;
                    if (field >= MK_ARGS_MAX) {
                        return MK_ERR_MALFORMED;
                    }
                    dst = out->args[field];
                    dst_cap = MK_ARG_MAX;
                    w = 0u;
                    continue;
                }
                if (w >= dst_cap) {
                    return MK_ERR_MALFORMED; /* 고정폭 초과 — 잘라 담지 않는다 */
                }
                dst[w++] = line[i];
            }
            dst[w] = '\0';
            field++;                      /* 마지막 필드도 센다 */
        }
        out->argc = (int)field;
    }

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
