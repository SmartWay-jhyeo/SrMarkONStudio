/* GNSS 초기화 명령 재시도 — HAL 비의존.
 *
 * UM981(J16)은 켜 두어도 스스로 말하지 않는다 — Unicore 계열은 기본이
 * 침묵이고 `LOG` 명령으로 원하는 문장을 켜야 한다
 * (`docs/datasheet/Unicore_N4_Commands.pdf` p.186, "Trigger mode of output
 * messages, ONTIME"). `gnss.enabled`가 켜지면 이 모듈이 그 명령을 대신
 * 보낸다 — 안 그러면 모듈은 계속 침묵하고, 그 사실을 알아채려면 GDB를
 * 붙여야 한다(이 모듈이 생긴 계기).
 *
 * 🔴 `SAVECONFIG`는 여기서 **절대** 보내지 않는다. 그것은 사용자 GNSS
 *    모듈의 FLASH를 바꾸는 일이고, 필요하면 사용자가 `$GNSS`(규격 §4.1)로
 *    직접 보낼 일이지 보드가 임의로 결정할 일이 아니다.
 *
 * 🔴 무한히 재시도하지 않는다. 모듈이 부팅 중일 수 있어 몇 번은 다시
 *    보내지만, 상한(`MK_GNSSCTL_MAX_ATTEMPTS`)을 넘기면 멈춘다 — 안
 *    켜져 있다는 사실을 재시도로 바꿀 수는 없고, 계속 보내는 것은 GNSS
 *    UART 대역만 갉아먹는다. 멈췄다는 사실은 `mk_gnssctl_exhausted()`로
 *    드러난다 — `$STAT`의 `gnss.init_exhausted`가 이것을 싣는다(규격 §7.4).
 *
 * 규격: protocol/specification.md §4.1.1
 */
#ifndef MK_GNSSCTL_H
#define MK_GNSSCTL_H

#include <stdint.h>

#include "mk_gnss.h"   /* MkGnssSend */

/* 🔴 값을 여기 한 곳에서만 정한다 — 규격 §4.1.1 의 "2000 ms 간격으로
 *    최대 3회"는 이 두 상수를 그대로 옮겨 적은 것이다. 문서를 고칠 때는
 *    여기도 함께 고친다. */
#define MK_GNSSCTL_MAX_ATTEMPTS        3u
#define MK_GNSSCTL_RETRY_INTERVAL_MS   2000

typedef struct MkGnssCtl {
    uint32_t attempts;             /* 이번에 켜진 뒤 보낸 횟수 */
    int64_t  last_attempt_ms;
    int      done;                 /* sentence_seen 이 되어 더 안 보낸다 */
    int      sentence_seen_cached; /* 마지막 tick 이 받은 sentence_seen 값 —
                                     * $STAT 조회용(mk_gnssctl_sentence_seen) */
} MkGnssCtl;

void mk_gnssctl_init(MkGnssCtl *c);

/* 매 tick 부른다.
 *
 *   enabled        지금 gnss.enabled 값
 *   sentence_seen  mk_gnss_any_sentence_seen() — 체크섬 통과 문장을
 *                  한 번이라도 받았는가. 이 값은 부팅 이후 누적이라
 *                  enabled 가 꺼져도 내려가지 않으므로, 호출 쪽은 disabled
 *                  tick 에도 실제 값을 그대로 넘긴다(꺼졌다고 0 으로
 *                  덮어쓰지 않는다).
 *   send           mk_gnss_io_write_line 급 콜백. NULL 이면(1단계 빌드
 *                  등 GNSS IO 가 안 붙은 경우) 아무것도 하지 않는다 —
 *                  mk_hostlink 의 cfg==NULL 처리와 같은 결.
 *
 * 규칙(규격 §4.1.1): enabled 가 꺼짐->켜짐이 되면(또는 이미 켜진 채
 * 시작하면) 즉시 한 번 보내고, sentence_seen 이 될 때까지
 * MK_GNSSCTL_RETRY_INTERVAL_MS 마다 다시 보내되 MK_GNSSCTL_MAX_ATTEMPTS
 * 를 넘기지 않는다. sentence_seen 이 되는 순간(재시도 간격을 기다리지
 * 않고) 즉시 멈춘다. enabled 가 꺼지면 다음에 켜질 때 처음부터 다시
 * 센다. */
void mk_gnssctl_tick(MkGnssCtl *c, int enabled, int sentence_seen,
                     int64_t now_ms, MkGnssSend send, void *ctx);

/* 초기화 명령을 한 번이라도 보냈는가. */
int mk_gnssctl_sent(const MkGnssCtl *c);

/* 재시도 상한까지 다 써서 더 이상 자동으로 보내지 않는데, 아직 문장을
 * 못 받았는가. */
int mk_gnssctl_exhausted(const MkGnssCtl *c);

/* 마지막 tick 이 본 sentence_seen 값 — $STAT 이 그대로 싣는다. */
int mk_gnssctl_sentence_seen(const MkGnssCtl *c);

#endif /* MK_GNSSCTL_H */
