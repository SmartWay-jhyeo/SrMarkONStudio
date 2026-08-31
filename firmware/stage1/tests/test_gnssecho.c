/* $GNSSRAW 진단 줄 — 규격 §7.7 gnss_raw JSON 레코드의 후신.
 *
 * 🔴 [개정 2026-08-31, HANDOFF_0831 검토 6] 본선 레코드가 Cloud 계약으로
 *    통일되면서(결정 2) 계약 밖 타입(gnss_raw)을 레코드로 흘릴 수 없게 됐다.
 *    에코는 측정값이 아니라 진단이므로 `$` 줄로 옮긴다 — `$` 줄은 통일
 *    대상 밖이고, UM981 명령 응답을 GDB 없이 보는 유일한 수단이라 기능은
 *    버리지 않는다(사용자 확정 A안).
 */
#include <stdio.h>
#include <string.h>

#include "../app/mk_gnssecho.h"
#include "../app/mk_cfgtable.h"

static int failures = 0;

#define CHECK(cond, msg) do {                                   \
    if (!(cond)) { printf("  FAIL %s\n", msg); failures++; }    \
    else         { printf("  ok   %s\n", msg); }                \
} while (0)

#define CAP 8
static char LINES[CAP][256];
static int  N;

static void sink(void *ctx, const char *line, size_t len)
{
    (void)ctx;
    if (N >= CAP) return;
    size_t n = len < sizeof LINES[0] - 1u ? len : sizeof LINES[0] - 1u;
    memcpy(LINES[N], line, n);
    LINES[N][n] = '\0';
    N++;
}

static MkConfig CFG;
static MkGnss   G;

static void set_u32(const char *key, uint32_t v)
{
    MkCfgItem *it = mk_cfg_find(&CFG, key);
    if (it) { it->cur.u = v; }
}

static void feed_line(const char *line)
{
    for (const char *p = line; *p; p++) { mk_gnss_feed(&G, (uint8_t)*p); }
}

static void setup(void)
{
    N = 0;
    mk_cfgtable_init(&CFG);
    mk_gnss_init(&G);
}

static void test_echo_wraps_raw_line(void)
{
    setup();
    set_u32("gnss.echo", 1u);
    feed_line(
        "$GNRMC,235959.999,A,4807.038,N,01131.000,E,022.4,084.4,180826,,,A*73\r\n");
    int sent = mk_gnssecho_tick(&G, &CFG, sink, NULL);
    CHECK(sent == 1 && N == 1, "한 줄이 나간다");
    CHECK(strncmp(LINES[0], "$GNSSRAW,$GNRMC,", 16) == 0,
          "머리말 + 원문('$' 포함)");
    CHECK(strstr(LINES[0], "*73\n") != NULL, "원문 끝 그대로 + 개행");
    CHECK(strchr(LINES[0], '\r') == NULL, "CR 은 실리지 않는다");
}

static void test_echo_off_is_silent(void)
{
    setup();                               /* gnss.echo 기본 = 꺼짐 */
    feed_line(
        "$GNRMC,235959.999,A,4807.038,N,01131.000,E,022.4,084.4,180826,,,A*73\r\n");
    CHECK(mk_gnssecho_tick(&G, &CFG, sink, NULL) == 0,
          "꺼져 있으면 침묵 — 원시 큐는 스스로 오래된 것을 버린다");
}

static void test_echo_carries_bad_checksum_lines(void)
{
    /* "왜 파싱이 안 되는가"를 보는 채널이다 — 체크섬이 틀린 줄이야말로
     * 이 채널의 존재 이유다(구 규격 §7.7 과 같은 계약). */
    setup();
    set_u32("gnss.echo", 1u);
    feed_line("$GNGGA,000000.00,,,,,0,00,99.99,,,,,,*FF\r\n");
    CHECK(mk_gnssecho_tick(&G, &CFG, sink, NULL) == 1,
          "체크섬이 틀려도 원문 그대로 나간다");
}

int main(void)
{
    setvbuf(stdout, NULL, _IONBF, 0);
    test_echo_wraps_raw_line();
    test_echo_off_is_silent();
    test_echo_carries_bad_checksum_lines();

    if (failures) { printf("%d failure(s)\n", failures); return 1; }
    printf("all ok\n");
    return 0;
}
