#include "mk_flash.h"

#include "mk_crc.h"
#include "stm32h7xx_hal.h"

#include <string.h>

/* 저장 블록 머리. 뒤에 본문이 붙는다.
 * 🔴 32 바이트 배수로 맞춘다. H7 은 256 bit 단위로 쓰므로 머리와 본문이
 *    같은 워드에 걸치면 두 번 프로그래밍해야 하는데, 그것이 금지돼 있다. */
typedef struct {
    uint32_t magic;
    uint32_t version;
    uint32_t length;
    uint32_t crc;         /* 본문에 대한 CRC */
    uint32_t _pad[4];
} MkFlashHeader;

int mk_flash_load(void *out, size_t len)
{
    const MkFlashHeader *h = (const MkFlashHeader *)MK_FLASH_CFG_ADDR;

    if (h->magic != MK_FLASH_MAGIC) {
        return -1;                       /* 저장된 적이 없다 */
    }
    if (h->version != MK_FLASH_VERSION) {
        return -2;                       /* 다른 형식 — 기본값을 쓴다 */
    }
    if (h->length != (uint32_t)len) {
        return -3;                       /* 항목 수가 바뀌었다 */
    }

    const uint8_t *body = (const uint8_t *)(MK_FLASH_CFG_ADDR
                                            + sizeof(MkFlashHeader));
    /* 🔴 CRC 를 확인한다. 쓰는 도중 전원이 끊기면 절반만 써진 블록이
     *    남는다. 전원 레일을 켜는 설정이 섞여 있으므로 짐작으로 넘어가면
     *    안 된다 — 깨졌으면 기본값(전부 꺼짐)으로 간다. */
    if (mk_crc32(body, len) != h->crc) {
        return -4;
    }
    memcpy(out, body, len);
    return 0;
}

int mk_flash_save(const void *data, size_t len)
{
    /* 머리 + 본문을 32 바이트 배수로 맞춘 버퍼에 모은다.
     *
     * 🔴 설정 45항목 × MkValue 24바이트 = 1,080 바이트다. 처음에 512 로
     *    잡았다가 실기기에서 $CFG,SAVE 가 ERR,BUSY 로 떨어졌다 — 크기를
     *    어림으로 정한 대가다. 항목이 더 늘어날 것을 감안해 넉넉히 잡되,
     *    넘치면 조용히 자르지 않고 실패한다. */
    static uint8_t staging[2048] __attribute__((aligned(32)));
    const size_t need = sizeof(MkFlashHeader) + len;

    if (need > sizeof staging) {
        return -1;
    }
    memset(staging, 0xFF, sizeof staging);

    MkFlashHeader *h = (MkFlashHeader *)staging;
    h->magic = MK_FLASH_MAGIC;
    h->version = MK_FLASH_VERSION;
    h->length = (uint32_t)len;
    h->crc = mk_crc32(data, len);
    memcpy(staging + sizeof(MkFlashHeader), data, len);

    /* 32 바이트 단위로 올림 */
    size_t words = (need + 31u) / 32u;

    if (HAL_FLASH_Unlock() != HAL_OK) {
        return -2;
    }

    FLASH_EraseInitTypeDef er = {0};
    er.TypeErase = FLASH_TYPEERASE_SECTORS;
    er.Banks = FLASH_BANK_1;
    er.Sector = MK_FLASH_CFG_SECTOR;
    er.NbSectors = 1;
    er.VoltageRange = FLASH_VOLTAGE_RANGE_3;

    uint32_t err = 0;
    if (HAL_FLASHEx_Erase(&er, &err) != HAL_OK) {
        HAL_FLASH_Lock();
        return -3;
    }

    for (size_t i = 0; i < words; i++) {
        uint32_t dst = MK_FLASH_CFG_ADDR + (uint32_t)(i * 32u);
        uint32_t src = (uint32_t)(uintptr_t)(staging + i * 32u);
        if (HAL_FLASH_Program(FLASH_TYPEPROGRAM_FLASHWORD, dst, src) != HAL_OK) {
            HAL_FLASH_Lock();
            return -4;
        }
    }

    HAL_FLASH_Lock();

    /* 🔴 쓴 것을 되읽어 확인한다. HAL 이 OK 를 돌려줘도 실제로 들어갔다는
     *    보장은 아니다 — 굽기에서 512B 청크 경계 손상을 compare-sections
     *    로도 못 잡은 전례가 있다(CLAUDE.md §4). */
    if (memcmp((const void *)MK_FLASH_CFG_ADDR, staging, need) != 0) {
        return -5;
    }
    return 0;
}
