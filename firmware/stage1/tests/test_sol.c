/* mk_solctl 단위 시험 — 디지털 입력 J18~J20 (사용자 확정 2026-08-18).
 *
 * 🔴 이 파일은 예전에 출력 시험이었다. 방향이 뒤집히면서 여기서 보는
 *    것도 바뀐다 — "설정이 핀까지 닿는가" 가 아니라 "핀 엣지가 디바운스를
 *    거쳐 확정 상태로, 확정 상태가 레코드로 정확히 옮겨지는가" 다.
 *
 *    ISR(bsp/mk_sol.c)은 여기서 흉내 낸다 — mk_solctl_on_edge() 를 직접
 *    불러 "raw 핀 레벨과 시각만 큐에 들어온다"는 계약을 그대로 쓴다.
 */
#include <stdio.h>
#include <string.h>
#include "../app/mk_solctl.h"
#include "../app/mk_cfgtable.h"

static int failures = 0;

#define CHECK(cond, msg) do {                                   \
    if (!(cond)) { printf("  FAIL %s\n", msg); failures++; }    \
    else         { printf("  ok   %s\n", msg); }                \
} while (0)

static MkSolCtl SC;
static MkConfig CFG;

static void setup(void)
{
    mk_cfgtable_init(&CFG);           /* 진짜 카탈로그 — 가짜 키로 시험하지 않는다 */
    mk_solctl_init(&SC);
}

static void set_debounce(unsigned ms)
{
    MkCfgItem *it = mk_cfg_find(&CFG, "sol.debounce_ms");
    if (it) { it->cur.u = ms; }
}

/* out 큐에 쌓인 레코드를 전부 배열로 뽑는다. */
#define MAX_OUT 8

static void take_into(MkSolOut *buf, int cap, int *n)
{
    *n = 0;
    MkSolOut o;
    while (*n < cap && mk_solctl_take(&SC, &o)) {
        buf[(*n)++] = o;
    }
}

/* ---- 엣지 -> 디바운스 -> 확정 -------------------------------------------- */

static void test_edge_confirms_after_debounce_elapses(void)
{
    setup();
    set_debounce(5u);

    /* 부팅 기준: 아직 아무것도 모른다(prime 안 함) — J18 이 LOW 로 떨어졌다. */
    mk_solctl_on_edge(&SC, MK_SOL_J18, 0 /* raw LOW */, 1000);

    mk_solctl_tick(&SC, &CFG, 1002);      /* 아직 2ms — 디바운스 전 */
    MkSolOut buf[MAX_OUT]; int n;
    take_into(buf, MAX_OUT, &n);
    CHECK(n == 0, "디바운스가 끝나기 전에는 확정하지 않는다");
    CHECK(mk_solctl_is_on(&SC, MK_SOL_J18) == 0,
          "확정 전이라 아직 켜짐으로 보고하지 않는다");

    mk_solctl_tick(&SC, &CFG, 1005);      /* 정확히 5ms — 안정 구간 끝 */
    take_into(buf, MAX_OUT, &n);
    CHECK(n == 1, "디바운스가 끝나면 레코드 하나를 낸다");
    if (n == 1) {
        CHECK(buf[0].connector_id == 18u, "커넥터 번호는 J18");
        CHECK(buf[0].state == 1u, "raw LOW 는 state 1(켜짐)");
        CHECK(buf[0].t_ms == 1000, "t 는 엣지를 잡은 시각이다(확정 시각이 아니다)");
    }
    CHECK(mk_solctl_is_on(&SC, MK_SOL_J18) == 1, "확정 후에는 켜짐으로 보고한다");
}

/* ---- 디바운스 시간 안의 흔들림은 무시 ------------------------------------- */

static void test_glitches_within_debounce_window_are_ignored(void)
{
    setup();
    set_debounce(10u);
    mk_solctl_prime(&SC, MK_SOL_J19, 1 /* raw HIGH */, 0);   /* 꺼짐으로 시작 */

    /* 10ms 창 안에서 세 번 흔들린다: LOW, HIGH, LOW — 매번 후보가 바뀌므로
     * 안정 구간이 그때마다 다시 시작돼야 한다. */
    mk_solctl_on_edge(&SC, MK_SOL_J19, 0, 100);
    mk_solctl_on_edge(&SC, MK_SOL_J19, 1, 105);
    mk_solctl_on_edge(&SC, MK_SOL_J19, 0, 108);

    mk_solctl_tick(&SC, &CFG, 115);       /* 마지막 엣지(108)로부터 7ms */
    MkSolOut buf[MAX_OUT]; int n;
    take_into(buf, MAX_OUT, &n);
    CHECK(n == 0, "마지막 흔들림 이후 디바운스가 아직 안 끝났다");
    CHECK(mk_solctl_is_on(&SC, MK_SOL_J19) == 0, "그래서 아직 꺼짐 그대로다");

    mk_solctl_tick(&SC, &CFG, 118);       /* 마지막 엣지로부터 10ms */
    take_into(buf, MAX_OUT, &n);
    CHECK(n == 1, "잡음이 가라앉은 뒤(마지막 값 기준)에만 확정한다");
    if (n == 1) {
        CHECK(buf[0].state == 1u, "마지막 흔들림(raw LOW)이 이긴다");
        CHECK(buf[0].t_ms == 108, "t 는 안정 구간이 시작된 마지막 엣지 시각");
    }
}

static void test_glitch_that_returns_to_original_state_emits_nothing(void)
{
    /* 🔴 흔들리다 원래 값으로 돌아오면, 디바운스가 끝나도 "바뀐 것이
     *    없다" — 그래서 레코드가 안 나간다. 이것이 "무시" 의 진짜 뜻이다. */
    setup();
    set_debounce(5u);
    mk_solctl_prime(&SC, MK_SOL_J20, 1 /* raw HIGH = 꺼짐 */, 0);

    mk_solctl_on_edge(&SC, MK_SOL_J20, 0, 200);   /* 잠깐 켜짐 후보 */
    mk_solctl_on_edge(&SC, MK_SOL_J20, 1, 202);   /* 다시 꺼짐 — 원래대로 */

    mk_solctl_tick(&SC, &CFG, 210);
    MkSolOut buf[MAX_OUT]; int n;
    take_into(buf, MAX_OUT, &n);
    CHECK(n == 0, "원래 상태로 돌아온 흔들림은 레코드를 남기지 않는다");
    CHECK(mk_solctl_is_on(&SC, MK_SOL_J20) == 0, "확정 상태도 그대로 꺼짐이다");
}

/* ---- 극성 반전은 한 곳에서만 --------------------------------------------- */

static void test_polarity_is_flipped_exactly_once(void)
{
    /* 🔴 raw LOW(옵토 도통) = 신호 있음 = state 1. 두 곳에서 뒤집으면
     *    원래대로 돌아와 raw==state 가 되는데, 그러면 이 시험이 깨진다. */
    setup();
    set_debounce(1u);

    mk_solctl_on_edge(&SC, MK_SOL_J18, 0 /* raw LOW */, 0);
    mk_solctl_tick(&SC, &CFG, 5);
    MkSolOut buf[MAX_OUT]; int n;
    take_into(buf, MAX_OUT, &n);
    CHECK(n == 1 && buf[0].state == 1u, "raw LOW -> state 1(켜짐), 한 번만 반전");

    mk_solctl_on_edge(&SC, MK_SOL_J18, 1 /* raw HIGH */, 10);
    mk_solctl_tick(&SC, &CFG, 15);
    take_into(buf, MAX_OUT, &n);
    CHECK(n == 1 && buf[0].state == 0u, "raw HIGH -> state 0(꺼짐), 한 번만 반전");
}

/* ---- 안 바뀌면 안 낸다 ---------------------------------------------------- */

static void test_repeated_same_level_emits_only_once(void)
{
    setup();
    set_debounce(5u);
    mk_solctl_prime(&SC, MK_SOL_J18, 1, 0);   /* 꺼짐으로 시작 */

    mk_solctl_on_edge(&SC, MK_SOL_J18, 0, 100);   /* 켜짐으로 확정될 것 */
    mk_solctl_tick(&SC, &CFG, 106);
    MkSolOut buf[MAX_OUT]; int n;
    take_into(buf, MAX_OUT, &n);
    CHECK(n == 1, "첫 확정에서 한 번 낸다");

    /* 그 뒤로 여러 바퀴를 더 돌려도, 상태가 그대로면 아무것도 안 낸다. */
    for (int64_t t = 110; t <= 500; t += 20) {
        mk_solctl_tick(&SC, &CFG, t);
    }
    take_into(buf, MAX_OUT, &n);
    CHECK(n == 0, "확정된 뒤 상태가 그대로면 매 바퀴 다시 내지 않는다");
    CHECK(mk_solctl_is_on(&SC, MK_SOL_J18) == 1, "명령 상태는 켜짐으로 남는다");
}

static void test_prime_does_not_emit_a_record(void)
{
    /* 🔴 규격 §7.6 — "막 연결한 호스트는 지금 상태를 모른다, $STAT 이 그
     *    공백을 채운다." 초기값은 텔레메트리가 아니라 $STAT 전용이다. */
    setup();
    mk_solctl_prime(&SC, MK_SOL_J18, 0, 500);   /* raw LOW = 켜짐으로 시작 */
    mk_solctl_prime(&SC, MK_SOL_J19, 1, 500);
    mk_solctl_prime(&SC, MK_SOL_J20, 1, 500);

    MkSolOut buf[MAX_OUT]; int n;
    take_into(buf, MAX_OUT, &n);
    CHECK(n == 0, "prime() 은 레코드를 남기지 않는다");
    CHECK(mk_solctl_is_on(&SC, MK_SOL_J18) == 1,
          "그래도 $STAT 이 읽을 확정 상태는 즉시 세워진다");
}

/* ---- 큐 넘침을 센다 -------------------------------------------------------- */

static void test_queue_overflow_is_counted(void)
{
    /* 🔴 MK_SOL_QUEUE_CAP(16) 보다 많은 엣지를 비우지 않고 밀어 넣으면
     *    mk_queue_push() 규칙대로 가장 오래된 것을 버리고 drops 를 센다
     *    (mk_i2c.c 의 dropped, mk_queue.c 의 drops 와 같은 관례 — 조용히
     *    버리면 유실이 없었던 것으로 보인다). */
    setup();
    for (int i = 0; i < 20; i++) {
        /* LOW/HIGH 를 번갈아 채워 값 자체가 아니라 "칸 수" 를 넘친다. */
        mk_solctl_on_edge(&SC, MK_SOL_J19, i % 2, 1000 + i);
    }
    CHECK(mk_queue_drops(&SC.q[MK_SOL_J19]) == 4u,
          "16칸에 20개를 밀어 넣으면 4개가 버려진 것으로 세어진다");
}

/* ---- 채널 독립 ------------------------------------------------------------ */

static void test_channels_are_independent(void)
{
    setup();
    set_debounce(5u);
    mk_solctl_prime(&SC, MK_SOL_J18, 1, 0);
    mk_solctl_prime(&SC, MK_SOL_J19, 1, 0);
    mk_solctl_prime(&SC, MK_SOL_J20, 1, 0);

    /* J19 만 흔든다. */
    for (int i = 0; i < 10; i++) {
        mk_solctl_on_edge(&SC, MK_SOL_J19, i % 2, 100 + i);
    }
    mk_solctl_on_edge(&SC, MK_SOL_J19, 0, 200);   /* 마지막에 켜짐으로 안정 */

    mk_solctl_tick(&SC, &CFG, 210);
    MkSolOut buf[MAX_OUT]; int n;
    take_into(buf, MAX_OUT, &n);

    CHECK(n == 1, "흔든 채널 하나만 레코드를 낸다");
    if (n == 1) {
        CHECK(buf[0].connector_id == 19u, "그 하나가 J19 다");
    }
    CHECK(mk_solctl_is_on(&SC, MK_SOL_J18) == 0, "J18 은 건드리지 않아 그대로다");
    CHECK(mk_solctl_is_on(&SC, MK_SOL_J20) == 0, "J20 도 그대로다");
    CHECK(mk_queue_drops(&SC.q[MK_SOL_J18]) == 0u,
          "J18 의 큐는 흔들리지 않았으니 유실도 없다");
}

/* ---- 카탈로그 --------------------------------------------------------------
 *
 * 🔴 설정이 핀까지 닿는지는 이제 "출력 명령" 이 아니라 "debounce_ms 를
 *    읽어 쓰는가" 로 본다. 위의 디바운스 시험들이 진짜 카탈로그(setup())
 *    로 돌므로 이미 이 경로를 확인하지만, 항목 자체가 사라지면 그
 *    시험들도 fallback 기본값(5)으로 조용히 통과해 버린다 — 그래서
 *    카탈로그에 항목이 있는지 이름으로 따로 못박는다. */
static void test_the_catalog_has_the_debounce_item(void)
{
    mk_cfgtable_init(&CFG);
    MkCfgItem *it = mk_cfg_find(&CFG, "sol.debounce_ms");
    CHECK(it != NULL, "카탈로그에 sol.debounce_ms 가 있다");
    if (it != NULL) {
        CHECK(it->vtype == MK_VT_U16, "u16 이다");
        CHECK(it->def.u == 5u, "기본값 5ms");
        CHECK(it->out == 0, "out 이 아니다 — 필터 값이지 되돌릴 출력이 없다");
    }
}

static void test_the_catalog_no_longer_has_the_bool_switches(void)
{
    /* 🔴 sol.j18~sol.j20 은 "켜라/꺼라" 가 성립하지 않는 입력이라
     *    카탈로그에서 빠졌다(사용자 확정 2026-08-18). 남아 있으면 화면에
     *    동작하지 않는 스위치가 뜬다. */
    mk_cfgtable_init(&CFG);
    CHECK(mk_cfg_find(&CFG, "sol.j18") == NULL, "sol.j18 은 이제 없다");
    CHECK(mk_cfg_find(&CFG, "sol.j19") == NULL, "sol.j19 도 없다");
    CHECK(mk_cfg_find(&CFG, "sol.j20") == NULL, "sol.j20 도 없다");
}

int main(void)
{
    printf("-- 엣지 -> 디바운스 -> 확정 --\n");
    test_edge_confirms_after_debounce_elapses();
    printf("-- 디바운스 시간 안의 흔들림 --\n");
    test_glitches_within_debounce_window_are_ignored();
    test_glitch_that_returns_to_original_state_emits_nothing();
    printf("-- 극성 반전은 한 곳에서만 --\n");
    test_polarity_is_flipped_exactly_once();
    printf("-- 안 바뀌면 안 낸다 --\n");
    test_repeated_same_level_emits_only_once();
    test_prime_does_not_emit_a_record();
    printf("-- 큐 넘침 --\n");
    test_queue_overflow_is_counted();
    printf("-- 채널 독립 --\n");
    test_channels_are_independent();
    printf("-- 카탈로그 --\n");
    test_the_catalog_has_the_debounce_item();
    test_the_catalog_no_longer_has_the_bool_switches();

    printf(failures ? "\nFAILED (%d)\n" : "\nPASSED\n", failures);
    return failures ? 1 : 0;
}
