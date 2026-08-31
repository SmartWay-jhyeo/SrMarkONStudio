/* 설정을 Flash 에 남긴다. HAL 을 쓰는 층 — app/ 은 이 파일을 모른다.
 *
 * 🔴 배치 근거 (추측 아님)
 *
 *    STM32H723ZG 의 Flash 는 **8섹터 × 128 KB, 단일 뱅크 1 MB** 다.
 *    ST 헤더에서 확인했다:
 *
 *      stm32h723xx.h:10814  FLASH_SECTOR_TOTAL  8U
 *      stm32h723xx.h:10819  FLASH_SECTOR_SIZE   0x00020000UL  (128 KB)
 *      stm32h723xx.h:10818  FLASH_BANK_SIZE     FLASH_SIZE    (1 MB)
 *
 *    펌웨어는 12 KB 남짓이라 섹터 0 에 다 들어간다. 설정은 **마지막
 *    섹터(7번)**에 둔다 — 펌웨어가 커져도 부딪히지 않는 가장 먼 자리다.
 *
 * 🔴 H7 의 Flash 쓰기 단위는 256 bit(32 바이트)이고 **같은 워드를 두 번
 *    프로그래밍할 수 없다.** 그래서 갱신은 언제나 "섹터를 지우고 처음부터
 *    다시 쓴다" 이다. 부분 갱신은 없다.
 */
#ifndef MK_FLASH_H
#define MK_FLASH_H

#include <stddef.h>
#include <stdint.h>

/* 섹터 7. 0x080E0000 ~ 0x080FFFFF */
#define MK_FLASH_CFG_SECTOR   7u
#define MK_FLASH_CFG_ADDR     0x080E0000UL
#define MK_FLASH_CFG_SIZE     0x00020000UL

/* 저장 블록의 표지. 이 값이 아니면 저장된 적이 없거나 다른 형식이다. */
#define MK_FLASH_MAGIC        0x4D4B4346UL   /* "MKCF" */
#define MK_FLASH_VERSION      1u

/* 저장한다. 성공이면 0.
 *
 * 🔴 CRC 를 함께 쓴다. 쓰는 도중 전원이 끊기면 절반만 써진 블록이 남는데,
 *    그것을 그대로 읽어 설정으로 쓰면 어떤 값이 될지 알 수 없다. 전원
 *    레일을 켜는 설정이 섞여 있으므로 짐작으로 넘어가면 안 된다. */
int mk_flash_save(const void *data, size_t len);

/* 읽는다. 표지·판·CRC 가 맞으면 0 을 돌려주고 out 을 채운다.
 * 저장된 적이 없거나 깨졌으면 음수 — 호출 쪽은 기본값을 쓴다. */
int mk_flash_load(void *out, size_t len);

/* CRC 는 app/mk_crc.h 에 있다 — HAL 없이 시험할 수 있어야 하기 때문이다.
 * 여기 두면 시험이 자기 복사본을 검사하게 되고, 실제로 보드에서 도는
 * 코드는 아무도 확인하지 않는다. */

#endif /* MK_FLASH_H */
