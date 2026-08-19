/* 호스트 링크 속도 — 바꾸는 순간 대화가 끊기는 유일한 설정. HAL 비의존.
 *
 * 규격: protocol/specification.md §4.2
 *
 * 🔴 **왜 이 모듈이 따로 있나**
 *
 *    다른 설정은 잘못 넣어도 다시 고칠 수 있다. 이것은 못 고친다 —
 *    보드는 새 속도로 말하고 호스트는 옛 속도로 들으므로, 값이 틀리거나
 *    F103(BMP) 브리지가 그 속도를 못 견디면 되돌릴 방법이 **펌웨어를 다시
 *    굽는 것뿐**이다. 이 저장소에서 굽기는 하루에 네 번 막힌 적이 있고
 *    매번 보드 전원을 20초 빼야 풀렸다(CLAUDE.md §4).
 *
 *    그래서 모니터 해상도를 바꿀 때의 "15초 안에 확인 안 누르면 원래대로"
 *    와 같은 장치를 넣는다. 이 파일이 그 장치다:
 *
 *      IDLE  ──(설정표의 link.baud 가 바뀌었다)──▶  ARMED
 *      ARMED ──(apply: 실제로 속도를 바꾼다)─────▶  PENDING (시한 시작)
 *      PENDING ──($BAUD,CONFIRM 이 왔다)────────▶  IDLE (확정)
 *      PENDING ──(시한이 지났다)────────────────▶  IDLE (옛 속도로 복귀)
 *
 * 🔴 **ARMED 가 왜 따로 있나 — 이것이 응답 순서를 지키는 장치다.**
 *
 *    `$CFG,SET,link.baud` 의 응답($SACK,CFG,OK)은 **옛 속도로** 나가야
 *    한다. 순서를 뒤집으면 호스트가 응답을 못 읽어 성공했는지조차 모른다.
 *
 *    그 순서를 지키는 방법은 "요청을 받는 곳" 과 "속도를 바꾸는 곳" 을
 *    아예 다른 함수로 갈라 놓는 것이다. `mk_hostlink_feed()` 는 응답을
 *    내보내기만 하고 절대 apply 하지 않는다. apply 는 슈퍼루프가 그 뒤에
 *    부르는 `mk_linkbaud_tick()` 안에서만 일어난다. ARMED 는 그 사이에
 *    머무는 자리다 — 있으면 시험이 두 시점을 갈라 볼 수 있다.
 *
 * 🔴 **확정되지 않은 값은 Flash 에 가지 않는다.** `confirmed` 를 따로 들고
 *    있는 이유가 그것이다. 이 규칙이 없으면 "부팅하자마자 아무도 말을 못
 *    거는 보드" 가 만들어지고, 그 실패는 굽기로만 풀린다.
 */
#ifndef MK_LINKBAUD_H
#define MK_LINKBAUD_H

#include <stdint.h>

#include "mk_config.h"

/* 부팅 기본값. main.c 의 UART_BAUD 와 같아야 하고, main.c 가 그것을
 * _Static_assert 로 확인한다.
 *
 * 🔴 921600 을 그대로 둔다 (규격 §4.2.5). 이 값만 실기기에서 확인됐고
 *    ($ID 200회 왕복, 누락·손상 0), 더 높은 값은 아무도 시험한 적이 없다.
 *    선행 프로젝트(Q2)에서 2 Mbps 직결에 ~2 % 유실이 실측된 채 미해결이고
 *    (CLAUDE.md §1.2), 이 보드는 거기에 F103(BMP) 브리지가 하나 더 낀다. */
#define MK_LINKBAUD_DEFAULT      921600u

/* 확인 시한(ms). 근거는 규격 §4.2.4 — 호스트가 포트를 닫았다 새 속도로
 * 다시 열고 확인을 왕복하는 데 드는 시간(최악 7초)보다 넉넉하고, 사람이
 * 화면 앞에서 기다릴 수 있는 길이보다는 짧다. 되돌아가기까지의 시간은
 * 곧 **링크가 죽어 있는 시간**이다.
 *
 * 🔴 하트비트 시한(MK_HB_TIMEOUT_MS, 3000)보다 길다. 그래서 링크 속도
 *    변경이 실패하면 보드는 먼저 RUN 으로 떨어져 TEST 출력을 안전 상태로
 *    되돌린 뒤(규격 §6.4) 속도를 되돌린다. 순서가 이 방향인 것이 옳다. */
#define MK_LINKBAUD_CONFIRM_MS   10000

/* 허용 보율 오차. UART 는 보통 2~3 % 를 견딘다 — 보수적으로 2 % 로 자른다.
 * 이 값을 넘는 속도는 카탈로그에 넣지 않고, 넣어도 요청 단계에서 거부한다. */
#define MK_LINKBAUD_MAX_ERR_PPM  20000u

/* 고를 수 있는 속도. 🔴 **여기 한 곳에만 적는다.**
 *
 * 설정 카탈로그(app/mk_cfgtable.c)와 컴파일 시 오차 검사(main.c)가 이
 * 목록을 각각 펼쳐 쓴다. 두 곳에 손으로 적으면 카탈로그에는 있는데
 * 실제로는 못 내는 속도가 생기고, 그것은 사용자가 고르는 순간에만
 * 드러난다 — 즉 링크가 끊긴 뒤에 드러난다.
 *
 * 64 MHz(MK_USART3_KERNEL_HZ)에서의 실제 오차는 규격 §4.2.6 의 표에 있고,
 * host/tests/test_firmware_clock.py 가 그 표와 이 목록을 함께 계산한다. */
#define MK_LINKBAUD_CHOICE_LIST(X) \
    X(115200u)  X(460800u)  X(921600u) \
    X(1000000u) X(1500000u) X(2000000u)

/* BRR (오버샘플 16, 프리스케일 1). HAL 의 UART_DIV_SAMPLING16 과 같은
 * **반올림** 식이다 — 버림으로 계산하면 실제로 서는 속도와 다른 값이
 * 나와, 오차 검사가 통과했는데 전선은 깨지는 조합이 생긴다. */
#define MK_LINKBAUD_BRR(kern, baud)   (((kern) + (baud) / 2u) / (baud))

/* |kern - baud x BRR| — 정수 나눗셈으로 잃는 몫을 주파수 단위로 되살린 것. */
#define MK_LINKBAUD_DIFF(kern, baud)                                    \
    ((kern) > (baud) * MK_LINKBAUD_BRR(kern, baud)                      \
        ? (kern) - (baud) * MK_LINKBAUD_BRR(kern, baud)                 \
        : (baud) * MK_LINKBAUD_BRR(kern, baud) - (kern))

/* 오차(ppm). 🔴 64비트로 올린다 — 4.096e11 이 나오는 조합이 있어서
 * 32비트로는 조용히 접힌다(921600: DIFF 409600 x 1e6). */
#define MK_LINKBAUD_ERR_PPM(kern, baud)                                 \
    ((uint32_t)(((uint64_t)MK_LINKBAUD_DIFF(kern, baud) * 1000000u)     \
                / ((uint64_t)(baud) * MK_LINKBAUD_BRR(kern, baud))))

typedef enum {
    MK_LINKBAUD_IDLE = 0,    /* 대기 중인 변경이 없다 */
    MK_LINKBAUD_ARMED,       /* 요청은 받았고 **아직 안 바꿨다** — 응답이 나가는 중 */
    MK_LINKBAUD_PENDING      /* 바꿨다. 확인을 기다린다 */
} MkLinkBaudState;

typedef enum {
    MK_LINKBAUD_OK = 0,
    MK_LINKBAUD_ERR_RANGE,   /* 못 내는 속도거나, 확인 값이 대기 중인 값과 다르다 */
    MK_LINKBAUD_ERR_STATE    /* 확인할 것이 없다 */
} MkLinkBaudResult;

/* 실제로 하드웨어의 속도를 바꾼다. bsp 가 채운다(bsp/mk_uart.c).
 *
 * 🔴 이 콜백은 **보내던 바이트가 다 나간 뒤에** 재설정해야 한다. 규격
 *    §4.2.2 규칙 1 이 요구하는 것이 그것이고, 여기서는 그 책임을 bsp 에
 *    넘긴다 — app/ 은 TC 플래그를 모른다. */
typedef void (*MkLinkBaudApply)(void *ctx, uint32_t baud);

typedef struct MkLinkBaud {
    uint32_t kernel_hz;      /* 오차 계산의 기준. main.c 가 MK_USART3_KERNEL_HZ 를 준다 */
    uint32_t active;         /* 지금 전선에 서 있는 속도 */
    uint32_t confirmed;      /* 마지막으로 확인된 속도 — Flash 에 가는 값 */
    uint32_t pending;        /* 대기 중인 속도. IDLE 이면 0 */
    int64_t  deadline_ms;    /* PENDING 일 때만 뜻이 있다 */
    uint8_t  state;          /* MkLinkBaudState */
    uint32_t applied;        /* 누적 — 규격 §7.4 의 link.applied */
    uint32_t confirmed_count;
    uint32_t reverted;
} MkLinkBaud;

/* 부팅 속도로 시작한다. active 도 confirmed 도 그 값이다. */
void mk_linkbaud_init(MkLinkBaud *lb, uint32_t kernel_hz, uint32_t boot_baud);

/* 이 커널 클럭에서 낼 수 있는 속도인가 (오차 2 % 이내 · BRR >= 16). */
int mk_linkbaud_reachable(uint32_t kernel_hz, uint32_t baud);

/* 오차(ppm). 매크로와 같은 계산을 실행 시간에 한다 — 카탈로그에 없는
 * 값이 어떤 경로로든 들어왔을 때를 위한 마지막 방어선이다. */
uint32_t mk_linkbaud_err_ppm(uint32_t kernel_hz, uint32_t baud);
uint32_t mk_linkbaud_brr(uint32_t kernel_hz, uint32_t baud);

/* 속도 변경을 요청한다(IDLE -> ARMED). **아직 안 바꾼다.**
 *
 * 반환 MK_LINKBAUD_ERR_RANGE: 못 내는 속도. MK_LINKBAUD_ERR_STATE: 이미
 * 대기 중인 변경이 있다(하나씩만 처리한다 — 겹치면 무엇으로 되돌아가야
 * 하는지가 흐려진다). */
MkLinkBaudResult mk_linkbaud_request(MkLinkBaud *lb, uint32_t baud);

/* `$BAUD,CONFIRM,<baud>` (규격 §4.2.3). 값이 대기 중인 것과 같아야 한다. */
MkLinkBaudResult mk_linkbaud_confirm(MkLinkBaud *lb, uint32_t baud);

/* 슈퍼루프가 매 바퀴 부른다. 하는 일 셋:
 *
 *   1) 설정표의 `link.baud` 가 지금 속도와 다르면 요청으로 본다
 *   2) ARMED 면 apply 를 부르고 PENDING 으로 넘어간다(시한 시작)
 *   3) PENDING 인데 시한이 지났으면 apply(confirmed) 로 되돌린다
 *
 * 🔴 `cfg` 를 받는 이유: 되돌아갈 때 설정표의 현재값도 함께 되돌려야
 *    한다. 안 그러면 화면에는 새 속도가 떠 있는데 전선은 옛 속도로 도는
 *    상태가 남고, 그 다음 tick 이 그것을 새 요청으로 오해한다.
 *
 * `cfg` 나 `apply` 가 NULL 이면 아무 일도 하지 않는다 — 링크 속도를 안
 * 붙인 빌드(호스트 시험)에서 그대로 돈다. */
void mk_linkbaud_tick(MkLinkBaud *lb, MkConfig *cfg, int64_t now_ms,
                      MkLinkBaudApply apply, void *ctx);

uint32_t mk_linkbaud_active(const MkLinkBaud *lb);
uint32_t mk_linkbaud_confirmed(const MkLinkBaud *lb);
/* 대기 중인 속도. 없으면 0. */
uint32_t mk_linkbaud_pending(const MkLinkBaud *lb);
/* 확인을 기다리는 중인가 — `$CFG,SAVE` 를 막는 조건이다(규격 §4.2.2 규칙 5).
 *
 * 🔴 ARMED 도 참이다. 아직 속도를 안 바꿨을 뿐 이미 확정되지 않은 값이
 *    설정표에 들어와 있고, 그 상태로 저장하면 못 붙는 보드가 된다. */
int mk_linkbaud_is_pending(const MkLinkBaud *lb);
/* 되돌아가기까지 남은 ms. 대기 중이 아니면 -1. */
int64_t mk_linkbaud_remaining_ms(const MkLinkBaud *lb, int64_t now_ms);

#endif /* MK_LINKBAUD_H */
