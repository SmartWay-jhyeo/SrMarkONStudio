/* 카탈로그가 송신 링을 넘어설 때 한 줄도 잃지 않는지 시험한다.
 *
 * 🔴 왜 이 파일이 생겼나 (실기기, 2026-08-20)
 *
 *    송신을 링버퍼+DMA 로 바꾼 뒤(a85e6ae~aabd6c1) GUI 가 통째로 못 쓰게
 *    됐다. 사용자가 본 것은 "분명히 켜져있는데 안켜졌다고 GUI에서 나와.
 *    그리고 On/Off 명령을 내려도 변하지 않아" 였는데, 보드는 전부 맞게
 *    답하고 있었다:
 *
 *        $STAT.rails = {"v24":true,"v14v9":false,"v5":true}
 *        카탈로그 줄 = {"type":"cfg_item","key":"pwr.24v",...,"cur":true,...}
 *
 *    깨진 것은 카탈로그였다. `$CFG,LIST` 는 101줄 ≈ 25 KB 를 **한 번에**
 *    쏟아내는데 송신 링이 4,096 B 다. 43줄쯤에서 링이 차고 나머지가 전부
 *    버려져 호스트는 `cfg_end 가 없다` 로 끝났다. 카탈로그를 못 읽으면
 *    GUI 는 설정 폼을 못 만들고, 그러면 레일 표시도 토글도 실제 항목에
 *    연결되지 않는다 — 사용자가 본 증상 전부가 이것 하나다.
 *
 *    텔레메트리를 초당 972줄에서 10줄로 줄여도 똑같이 잘렸다. 부하가
 *    아니라 **한 번에 쏟는 양**이 원인이라는 실측이다.
 *
 * 🔴 무엇을 지키는 시험인가
 *
 *    1. 링이 작아 한 번에 다 못 담아도 카탈로그는 **한 줄도 안 빠진다**.
 *       텔레메트리와 정반대 정책이다 — 저쪽은 버려도 그 줄 하나로 끝나지만
 *       카탈로그는 한 줄만 잃어도 전체가 못 쓰게 된다.
 *    2. 그렇다고 슈퍼루프를 붙잡지 않는다. 한 바퀴에 링 크기 이상을
 *       밀어넣지 않는다 — 어제 32 ms 걸리던 한 바퀴를 없앤 것이 10 ms
 *       수집의 전제다.
 *
 * 링은 512 B 로 줄여 잡는다. 실기기의 4,096 B 가 25 KB 를 못 담은 것과
 * 같은 상황이고, 작을수록 "이어서 내보내기" 가 실제로 도는지 분명해진다.
 */
#include <stdio.h>
#include <string.h>

#include "../app/mk_cfgtable.h"
#include "../app/mk_framing.h"
#include "../app/mk_hostlink.h"
#include "../app/mk_txring.h"

static int failures = 0;

#define CHECK(cond, msg) do {                                   \
    if (!(cond)) { printf("  FAIL %s\n", msg); failures++; }    \
    else         { printf("  ok   %s\n", msg); }                \
} while (0)

/* ---- 링크 모형 ---------------------------------------------------------- */

/* 🔴 실기기 4,096 B 의 축소판. 카탈로그(25 KB)와의 비율을 유지한 채
 *    "한 번에 못 담는다" 는 조건만 남긴다. */
#define RING_CAP     512u
/* bsp/mk_uart.c 의 MK_TX_RESERVE 와 같은 뜻 — 명령 응답이 나갈 자리. */
#define RESERVE       64u
/* 한 바퀴에 링크가 실제로 빼 가는 양. DMA 완료 인터럽트가 하는 일이다. */
#define DRAIN_PER_TICK 48u

static uint8_t  s_ring_buf[RING_CAP];
static MkTxRing s_ring;

/* 전선에 실제로 나간 바이트. 링에서 빠져나온 것만 여기 쌓인다 —
 * 버려진 줄은 영영 안 들어온다. */
static char   s_wire[128u * 1024u];
static size_t s_wire_len;

/* 한 바퀴에 밀어넣은 바이트. 슈퍼루프를 붙잡는지 보는 계기다. */
static size_t s_pushed_this_tick;

static void wire_reset(void)
{
    mk_txring_init(&s_ring, s_ring_buf, (uint16_t)RING_CAP);
    s_wire_len = 0;
    s_pushed_this_tick = 0;
}

/* DMA 가 조각을 옮긴다. 한 번에 `max` 바이트까지. */
static void wire_drain(size_t max)
{
    while (max > 0u) {
        const uint8_t *p = NULL;
        size_t n = mk_txring_chunk(&s_ring, &p);
        if (n == 0u) {
            return;
        }
        if (n > max) {
            n = max;
        }
        if (s_wire_len + n < sizeof s_wire) {
            memcpy(&s_wire[s_wire_len], p, n);
            s_wire_len += n;
        }
        mk_txring_consume(&s_ring, n);
        max -= n;
    }
}

/* 명령 응답·하트비트 — 예약 몫까지 쓰고, 못 넣으면 제어 유실로 센다. */
static void emit(void *ctx, const char *line, size_t len)
{
    (void)ctx;
    s_pushed_this_tick += len;
    (void)mk_txring_push_ctl(&s_ring, line, len);
}

/* 이어서 내보내는 흐름(카탈로그). 자리가 없으면 0 을 돌려주고 **안 센다** —
 * 다음 바퀴에 같은 줄이 다시 온다. */
static int emit_stream(void *ctx, const char *line, size_t len)
{
    (void)ctx;
    if (!mk_txring_offer(&s_ring, line, len, RESERVE)) {
        return 0;
    }
    s_pushed_this_tick += len;
    return 1;
}

/* ---- 도구 -------------------------------------------------------------- */

static size_t count_occurrences(const char *hay, const char *needle)
{
    size_t n = 0;
    const char *p = hay;
    size_t len = strlen(needle);
    while ((p = strstr(p, needle)) != NULL) {
        n++;
        p += len;
    }
    return n;
}

static void feed(MkHostlink *h, const char *payload, int64_t now_ms)
{
    char line[256];
    int n = mk_build_line(line, sizeof line, payload);
    mk_hostlink_feed(h, line, (size_t)n, now_ms);
}

/* ---- 시험 -------------------------------------------------------------- */

/* 🔴 이 시험이 이번 작업의 핵심이다.
 *
 *    링이 카탈로그를 한 번에 못 담는 상황에서, 슈퍼루프를 여러 바퀴 돌리면
 *    카탈로그가 **하나도 안 빠지고** 나가야 한다. */
static void test_catalog_survives_a_ring_too_small(void)
{
    MkConfig cfg;
    mk_cfgtable_init(&cfg);
    size_t n_fields = 0;
    const MkFieldBit *fields = mk_cfgtable_fields(&n_fields);

    MkHostlink h;
    wire_reset();
    mk_hostlink_init(&h, emit, NULL, "1", "0.1.0", "2.0");
    mk_hostlink_attach_stream(&h, emit_stream);
    mk_hostlink_attach_config(&h, &cfg, fields, n_fields, NULL, NULL);

    int64_t now = 0;
    feed(&h, "CFG,LIST", now);

    /* 슈퍼루프. 한 바퀴마다 DMA 가 조금씩 빼 가고 tick 이 이어서 넣는다. */
    char sack[64];
    mk_build_line(sack, sizeof sack, "SACK,CFG,OK");
    for (int i = 0; i < 20000 && strstr(s_wire, sack) == NULL; i++) {
        wire_drain(DRAIN_PER_TICK);
        now += 1;
        mk_hostlink_tick(&h, now);
    }
    /* 링에 남은 것도 마저 빼낸다. */
    wire_drain(sizeof s_wire);

    size_t items  = count_occurrences(s_wire, "\"type\":\"cfg_item\"");
    size_t fieldn = count_occurrences(s_wire, "\"type\":\"cfg_field\"");
    size_t ends   = count_occurrences(s_wire, "\"type\":\"cfg_end\"");

    printf("       항목 %zu/%zu · 필드 %zu/%zu · cfg_end %zu · %zu 바이트\n",
           items, cfg.count, fieldn, n_fields, ends, s_wire_len);

    CHECK(items == cfg.count, "cfg_item 이 한 줄도 안 빠졌다");
    CHECK(fieldn == n_fields, "cfg_field 가 한 줄도 안 빠졌다");
    CHECK(ends == 1u, "cfg_end 가 정확히 한 번 나왔다");
    CHECK(strstr(s_wire, sack) != NULL, "$SACK,CFG,OK 로 닫힌다");

    /* 🔴 키 하나하나를 확인한다. 개수만 맞고 다른 줄이 왔을 수도 있다 —
     *    사용자가 실제로 잃은 것은 `pwr.24v` 같은 **특정 항목**이었다. */
    size_t missing = 0;
    for (size_t i = 0; i < cfg.count; i++) {
        char pat[96];
        snprintf(pat, sizeof pat, "\"key\":\"%s\"", cfg.items[i].key);
        if (strstr(s_wire, pat) == NULL) {
            printf("       빠진 항목: %s\n", cfg.items[i].key);
            missing++;
        }
    }
    CHECK(missing == 0u, "모든 설정 키가 전선에 나왔다");

    /* 🔴 버렸다는 사실이 조용하면 안 된다 — 여기서는 아예 안 버려야 한다.
     *    거절은 유실이 아니다(다음 바퀴에 다시 온다). */
    CHECK(mk_txring_drops(&s_ring) == 0u, "텔레메트리 유실 계기가 안 움직였다");
    CHECK(mk_txring_ctl_drops(&s_ring) == 0u, "제어 유실 계기가 안 움직였다");
}

/* 🔴 슈퍼루프를 붙잡지 않는다.
 *
 *    한 바퀴에 링 크기 이상을 밀어넣으면 그것은 곧 "링이 빌 때까지 기다린다"
 *    는 뜻이고, 어제 없앤 32 ms 짜리 한 바퀴가 그대로 돌아온다. */
static void test_one_tick_never_pushes_more_than_the_ring(void)
{
    MkConfig cfg;
    mk_cfgtable_init(&cfg);
    size_t n_fields = 0;
    const MkFieldBit *fields = mk_cfgtable_fields(&n_fields);

    MkHostlink h;
    wire_reset();
    mk_hostlink_init(&h, emit, NULL, "1", "0.1.0", "2.0");
    mk_hostlink_attach_stream(&h, emit_stream);
    mk_hostlink_attach_config(&h, &cfg, fields, n_fields, NULL, NULL);

    int64_t now = 0;
    size_t worst = 0;

    s_pushed_this_tick = 0;
    feed(&h, "CFG,LIST", now);
    if (s_pushed_this_tick > worst) {
        worst = s_pushed_this_tick;
    }

    char sack[64];
    mk_build_line(sack, sizeof sack, "SACK,CFG,OK");
    for (int i = 0; i < 20000 && strstr(s_wire, sack) == NULL; i++) {
        wire_drain(DRAIN_PER_TICK);
        now += 1;
        s_pushed_this_tick = 0;
        mk_hostlink_tick(&h, now);
        if (s_pushed_this_tick > worst) {
            worst = s_pushed_this_tick;
        }
    }

    printf("       한 바퀴 최대 %zu 바이트 (링 %u)\n", worst, (unsigned)RING_CAP);
    CHECK(worst < RING_CAP, "한 바퀴가 링 크기를 넘지 않는다");
}

/* 텔레메트리 정책은 그대로다 — 자리가 없으면 버리고, 그것을 센다.
 *
 * 🔴 되돌림 검사의 반대쪽이다. 카탈로그를 살리려고 "링이 찰 때까지
 *    기다린다" 로 바꿔 버리면 텔레메트리까지 기다리게 되고, 그러면
 *    슈퍼루프가 다시 선다. 두 정책이 갈라져 있는지 여기서 지킨다. */
static void test_telemetry_still_drops(void)
{
    wire_reset();
    /* 링을 거의 채운다. */
    char big[300];
    memset(big, 'x', sizeof big);
    CHECK(mk_txring_push(&s_ring, big, sizeof big, RESERVE) == 1,
          "첫 줄은 들어간다");
    CHECK(mk_txring_push(&s_ring, big, sizeof big, RESERVE) == 0,
          "자리가 없으면 텔레메트리는 버린다");
    CHECK(mk_txring_drops(&s_ring) == 1u, "버린 것을 센다");
    CHECK(mk_txring_ctl_drops(&s_ring) == 0u,
          "🔴 제어 유실과 같은 계기로 뭉치지 않는다");
}

/* 제어 경로에서 버린 것은 따로 센다. 심각도가 다르다 —
 * 텔레메트리 한 줄은 seq 구멍이지만, 명령 응답 한 줄은 호스트가
 * 영영 답을 못 받는 것이다. */
static void test_control_drops_are_counted_apart(void)
{
    wire_reset();
    char big[500];
    memset(big, 'y', sizeof big);
    CHECK(mk_txring_push_ctl(&s_ring, big, sizeof big) == 1,
          "제어는 예약 몫까지 쓴다");
    CHECK(mk_txring_push_ctl(&s_ring, big, sizeof big) == 0,
          "그래도 자리가 없으면 못 넣는다");
    CHECK(mk_txring_ctl_drops(&s_ring) == 1u, "제어 유실을 센다");
    CHECK(mk_txring_drops(&s_ring) == 0u, "텔레메트리 계기는 안 움직인다");
}

/* 거절(offer)은 유실이 아니다. 다음 바퀴에 같은 줄이 다시 오므로,
 * 이것을 세면 $STAT 의 제어 유실 수가 정상 동작에서도 계속 오른다 —
 * 그러면 그 수는 아무 말도 못 하게 된다. */
static void test_offer_rejection_is_not_a_drop(void)
{
    wire_reset();
    char big[500];
    memset(big, 'z', sizeof big);
    CHECK(mk_txring_offer(&s_ring, big, sizeof big, RESERVE) == 0,
          "예약 몫을 남기면 안 들어간다");
    CHECK(mk_txring_ctl_drops(&s_ring) == 0u, "거절은 제어 유실이 아니다");
    CHECK(mk_txring_drops(&s_ring) == 0u, "거절은 텔레메트리 유실도 아니다");
}

int main(void)
{
    printf("test_catalog\n");
    test_catalog_survives_a_ring_too_small();
    test_one_tick_never_pushes_more_than_the_ring();
    test_telemetry_still_drops();
    test_control_drops_are_counted_apart();
    test_offer_rejection_is_not_a_drop();
    printf("%s (%d 실패)\n", failures ? "실패" : "통과", failures);
    return failures ? 1 : 0;
}
