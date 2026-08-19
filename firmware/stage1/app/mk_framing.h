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

/* $GNSS 전용 원문 꼬리(raw tail) 상한 — 규격 §4.1.
 *
 * 🔴 MK_ARG_MAX(23) 를 못 쓴다. UM981 에 PPS 를 켜는 실제 명령이 46자다
 *    ("CONFIG PPS ENABLE2 GPS POSITIVE 500000 1000 0 0",
 *    docs/datasheet/Unicore_N4_Commands.pdf p.21 §4.3 · p.22 Table 4-6).
 *    줄여서 "CONFIG PPS ENABLE3"(18자)·"CONFIG PPS ENABLE2 GPS"(22자) 를
 *    실기기에 보내 봤더니 둘 다 모듈이
 *    "PARSING FAILD FIELD OUT OF RANGE, Too less field!" 로 거부했다 —
 *    파라미터 전부가 한 덩어리로 와야 한다.
 *
 * 🔴 MK_ARG_MAX 를 올려서 해결하지 않는다. MK_CFG_KEY_MAX·MK_CFG_STR_MAX
 *    (mk_config.h)가 이 값을 그대로 쓰므로, 올리면 설정 저장소의 항목
 *    배치가 통째로 바뀌어 보드 플래시에 이미 저장된 설정이 깨진다.
 *    그래서 $GNSS 만 별도 상한으로 원문을 통째로 받는다(mk_framing.c 의
 *    mk_parse_line 참고).
 *
 * 96 인 이유: 필요한 46자의 두 배로 여유를 뒀다. mk_gnss.h 의
 * MK_GNSS_LINE_MAX(NMEA 수신 버퍼, 96)와 값이 같은 것은 우연이다 — 서로
 * 다른 방향(호스트->모듈 명령 vs 모듈->보드 문장)의 별개 상수다. */
#define MK_GNSS_TEXT_MAX 96

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
    /* verb 가 "GNSS" 일 때만 채워진다 — payload 의 나머지 전부(쉼표
     * 분할을 거치지 않은 원문)를 담는다. args[]/argc 는 이 경우에도
     * argc 만 0 또는 1 로 세워진다(다른 명령과 같은 방식으로 "인자가
     * 있는가"를 볼 수 있게). 원문은 여기서 읽는다. */
    char gnss_text[MK_GNSS_TEXT_MAX + 1];
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
