#include "mk_crc.h"

/* 표를 만들지 않고 비트 단위로 돈다.
 *
 * 저장은 드물게 일어나고(사용자가 저장을 누를 때) 블록은 수백 바이트다.
 * 표를 두면 1 KB 를 더 쓰는데, 그만한 값이 없다. */
uint32_t mk_crc32(const void *data, size_t len)
{
    const uint8_t *p = (const uint8_t *)data;
    uint32_t crc = 0xFFFFFFFFu;

    for (size_t i = 0; i < len; i++) {
        crc ^= p[i];
        for (int b = 0; b < 8; b++) {
            /* (crc & 1) 이면 다항식을 XOR. 분기 대신 마스크로 쓴다. */
            crc = (crc >> 1) ^ (0xEDB88320u & (uint32_t)(-(int32_t)(crc & 1u)));
        }
    }
    return ~crc;
}
