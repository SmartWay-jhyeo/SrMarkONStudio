/* CRC-32 (IEEE 802.3) — HAL 비의존.
 *
 * 🔴 `bsp/mk_flash.c` 에 두지 않고 여기 둔 이유는 시험 때문이다.
 *    mk_flash.c 는 HAL 을 include 하므로 호스트에서 컴파일할 수 없다.
 *    CRC 를 거기 두면 시험이 자기 복사본을 검사하게 되고, 그러면 실제로
 *    보드에서 도는 코드는 아무도 확인하지 않는다.
 *
 * 표준 CRC-32 이므로 Python 의 `zlib.crc32` 와 같은 답이어야 한다.
 */
#ifndef MK_CRC_H
#define MK_CRC_H

#include <stddef.h>
#include <stdint.h>

uint32_t mk_crc32(const void *data, size_t len);

#endif /* MK_CRC_H */
