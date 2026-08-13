/* MarkON 시리얼 프레이밍 — HAL 비의존.
 *
 * 🔴 이 파일은 stm32h7xx_hal.h 를 include 하지 않는다.
 *    그래서 호스트 gcc 로 그대로 컴파일해 시험할 수 있고, Python 쪽
 *    host/core/framing.py 와 같은 벡터로 대조할 수 있다. 두 구현이
 *    같은 답을 내는지가 이 계층의 전부다.
 *
 * 규격: protocol/specification.md §3
 */
#ifndef MK_FRAMING_H
#define MK_FRAMING_H

#include <stddef.h>
#include <stdint.h>

/* 고정폭 버퍼. 넘치는 입력은 잘라 담지 않고 거부한다. */
#define MK_VERB_MAX   12
#define MK_ARG_MAX    23
#define MK_ARGS_MAX   4
#define MK_LINE_MAX   192

/* 규격 §3 의 두 동작에 그대로 대응한다.
 *
 *   MK_OK            정상. out 을 명령으로 실행한다.
 *   MK_ERR_CHECKSUM  체크섬 불일치. **out->verb 는 유효하다** —
 *                    $SACK,<verb>,ERR,CHECKSUM 을 보내는 데 쓴다.
 *                    🔴 out 의 나머지를 명령으로 실행하지 않는다.
 *                       검증되지 않은 바이트다.
 *   MK_ERR_MALFORMED verb 를 읽을 수 없을 만큼 깨졌다. 조용히 버린다
 *                    (링크는 유지). out 의 내용은 의미 없다. */
typedef enum {
    MK_OK = 0,
    MK_ERR_MALFORMED,
    MK_ERR_CHECKSUM
} MkParseResult;

typedef struct {
    char verb[MK_VERB_MAX + 1];
    char args[MK_ARGS_MAX][MK_ARG_MAX + 1];
    int  argc;
} MkCommand;

/* payload 바이트들의 XOR. '$' 와 '*' 는 포함하지 않는다. */
uint8_t mk_xor_checksum(const char *payload, size_t len);

/* "$<payload>*<CS>\r\n" 을 만든다.
 *
 * cap 은 NUL 을 포함해 `strlen(payload) + 7` 바이트 이상이어야 한다.
 * 반환: 쓴 바이트 수(NUL 제외), 실패면 음수.
 * 🔴 payload 에 제어문자가 있으면 거부한다 — 한 번 만들면 정확히 한 줄이어야
 *    한다. 제어문자가 섞이면 전송 중 여러 줄로 쪼개지고, 그 조각 하나가
 *    완결된 명령이 될 수 있다. */
int mk_build_line(char *out, size_t cap, const char *payload);

/* 완성된 줄을 파싱한다. 줄끝은 \r\n 또는 \n 둘 다 받는다.
 * 체크섬은 대소문자를 가리지 않는다(수신은 관대하게, 송신은 대문자로). */
MkParseResult mk_parse_line(const char *line, size_t len, MkCommand *out);

#endif /* MK_FRAMING_H */
