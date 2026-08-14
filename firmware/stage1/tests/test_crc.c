/* mk_flash 의 CRC-32 시험.
 *
 * 🔴 Flash 자체는 보드가 필요하지만 CRC 는 순수 계산이다. 여기서 확인해
 *    두면, 실기기에서 "저장이 안 된다" 를 만났을 때 CRC 를 의심 목록에서
 *    지울 수 있다.
 *
 * 표준 CRC-32(IEEE 802.3) 이므로 Python 의 zlib.crc32 와 같은 답이어야
 * 한다. crosscheck_crc.py 가 그것을 대조한다. */
#include <stdio.h>
#include <string.h>
#include <stdint.h>

/* 🔴 진짜 구현을 링크한다. 여기에 같은 알고리즘을 옮겨 적으면 시험이
 *    자기 복사본을 검사하게 되고, 보드에서 실제로 도는 코드는 아무도
 *    확인하지 않는다. 그래서 CRC 를 app/mk_crc.c 로 빼 두었다. */
#include "../app/mk_crc.h"

static int failures = 0;

#define CHECK(cond, msg) do {                                   \
    if (!(cond)) { printf("  FAIL %s\n", msg); failures++; }    \
    else         { printf("  ok   %s\n", msg); }                \
} while (0)

static const char *const VECTORS[] = {
    "", "a", "abc", "123456789",
    "The quick brown fox jumps over the lazy dog",
};

static void test_known_vectors(void)
{
    /* CRC-32(IEEE) 의 표준 시험 벡터. */
    CHECK(mk_crc32("", 0) == 0x00000000u, "빈 입력");
    CHECK(mk_crc32("a", 1) == 0xE8B7BE43u, "\"a\"");
    CHECK(mk_crc32("123456789", 9) == 0xCBF43926u, "\"123456789\"");
}

static void test_detects_single_bit_flips(void)
{
    /* 🔴 이것이 CRC 를 두는 이유다. 쓰는 도중 전원이 끊기면 한 바이트만
     *    다른 블록이 남을 수 있고, 그것을 그대로 설정으로 쓰면 전원 레일을
     *    켜는 값이 될 수도 있다. */
    uint8_t buf[64];
    memset(buf, 0x5A, sizeof buf);
    uint32_t base = mk_crc32(buf, sizeof buf);

    int missed = 0;
    for (size_t i = 0; i < sizeof buf; i++) {
        for (int b = 0; b < 8; b++) {
            buf[i] ^= (uint8_t)(1u << b);
            if (mk_crc32(buf, sizeof buf) == base) missed++;
            buf[i] ^= (uint8_t)(1u << b);
        }
    }
    CHECK(missed == 0, "512개 한 비트 뒤집기를 전부 잡는다");
}

static void test_length_matters(void)
{
    /* 잘린 블록을 같은 것으로 보면 안 된다. */
    const char *s = "abcdef";
    CHECK(mk_crc32(s, 6) != mk_crc32(s, 5), "길이가 다르면 다르다");
}

static void test_erased_flash_is_not_valid(void)
{
    /* 지워진 Flash 는 전부 0xFF 다. 그 상태의 CRC 가 우연히 0xFFFFFFFF 와
     * 같으면 빈 섹터를 정상 저장으로 읽는다. */
    uint8_t erased[32];
    memset(erased, 0xFF, sizeof erased);
    CHECK(mk_crc32(erased, sizeof erased) != 0xFFFFFFFFu,
          "지워진 영역이 우연히 유효해 보이지 않는다");
}

static void dump_vectors(void)
{
    for (size_t i = 0; i < sizeof VECTORS / sizeof *VECTORS; i++) {
        printf("%s\t%08X\n", VECTORS[i],
               mk_crc32(VECTORS[i], strlen(VECTORS[i])));
    }
}

int main(int argc, char **argv)
{
    if (argc > 1 && strcmp(argv[1], "--vectors") == 0) {
        dump_vectors();
        return 0;
    }
    printf("mk_flash crc\n");
    test_known_vectors();
    test_detects_single_bit_flips();
    test_length_matters();
    test_erased_flash_is_not_valid();
    printf(failures ? "\nFAILED (%d)\n" : "\nPASSED\n", failures);
    return failures ? 1 : 0;
}
