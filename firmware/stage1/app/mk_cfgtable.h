/* 이 보드의 설정 항목 목록 — HAL 비의존.
 *
 * 🔴 여기가 카탈로그의 유일한 출처다. 호스트는 항목을 하드코딩하지 않고
 *    `$CFG,LIST` 로 받아 화면을 그린다. 항목을 늘리려면 여기만 고친다.
 *
 * 🔴 라벨에 핀 번호를 쓰지 않는다 (설계 원칙 1, CLAUDE.md §3).
 *    화면과 프로토콜은 `PD8` 이 아니라 `24V 전원` 을 쓴다. PCB 배선은
 *    고정이므로 사용자가 핀을 알아야 할 이유가 없고, 알려 주면 임의로
 *    바꿀 수 있다는 인상을 준다.
 */
#ifndef MK_CFGTABLE_H
#define MK_CFGTABLE_H

#include "mk_cfgwire.h"
#include "mk_config.h"

/* 이 보드의 아날로그 입력 채널 수 (J3~J9). */
#define MK_AIN_COUNT   7

/* 설정 저장소를 기본값으로 채운다. */
void mk_cfgtable_init(MkConfig *cfg);

/* NDJSON 필드 마스크의 비트 목록 (규격 §7.2). */
const MkFieldBit *mk_cfgtable_fields(size_t *count);

/* 저장·복원할 값들만 모은 덩어리의 크기. */
size_t mk_cfgtable_blob_size(void);

/* 현재값을 덩어리로 모은다 / 덩어리에서 되돌린다.
 *
 * 🔴 구조체를 통째로 Flash 에 쓰지 않는다. 포인터(key·label·note)가 들어
 *    있어서, 펌웨어를 다시 구우면 그 주소가 달라진다. 값만 모은다. */
void mk_cfgtable_pack(const MkConfig *cfg, void *out);
void mk_cfgtable_unpack(MkConfig *cfg, const void *in);

#endif /* MK_CFGTABLE_H */
