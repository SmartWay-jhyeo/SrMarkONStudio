/* GNSS 원문 에코 → `$GNSSRAW` 진단 줄.
 *
 * 🔴 [개정 2026-08-31, HANDOFF_0831 검토 6 — 사용자 확정 A안] 규격 §7.7 의
 *    gnss_raw JSON 레코드를 대체한다. 본선 레코드가 Cloud 계약으로 통일
 *    되면서(결정 2) 계약 밖 타입을 레코드로 흘릴 수 없게 됐고, 에코는
 *    측정값이 아니라 진단이므로 `$` 줄이 제자리다 — `$` 줄은 통일 대상
 *    밖이다. UM981 명령 응답·체크섬 실패를 GDB 없이 보는 유일한 수단이라
 *    기능 자체는 버리지 않는다(GDB 는 붙이면 보드가 리셋되는 함정,
 *    CLAUDE.md §4).
 *
 *    출력 한 줄: "$GNSSRAW," + 원문('$' 포함, CR/LF 제외) + "\n"
 *    원문의 NMEA 체크섬이 그대로 실리므로 추가 체크섬은 없다.
 *    배선은 USB 전용이다(main.c) — 젯슨은 `$` 줄을 버리지만 애초에 대역을
 *    안 쓰는 쪽이 맞다.
 */
#ifndef MK_GNSSECHO_H
#define MK_GNSSECHO_H

#include <stddef.h>
#include <stdint.h>

#include "mk_config.h"
#include "mk_gnss.h"

typedef void (*MkGnssEchoEmit)(void *ctx, const char *line, size_t len);

/* gnss.echo(기본 꺼짐)가 켜져 있을 때만 원시 큐를 비워 내보낸다. 꺼져
 * 있으면 큐에 손대지 않는다 — mk_gnss 의 원시 큐는 자체 용량이 차면
 * 오래된 것을 스스로 버린다(mk_telem 시절과 같은 규칙). 낸 줄 수 반환. */
int mk_gnssecho_tick(MkGnss *g, MkConfig *cfg, MkGnssEchoEmit emit, void *ctx);

#endif /* MK_GNSSECHO_H */
