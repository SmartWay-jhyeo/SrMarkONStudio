/* mk_solctl 단위 시험 — 디지털 입력 J18~J20 (사용자 확정 2026-08-18).
 *
 * 🔴 이 파일은 예전에 출력 시험이었다가, 방향이 뒤집히며 엣지 기반 입력
 *    시험이 됐다. 이제 다시 바뀐다 — 상태의 근거가 엣지가 아니라 레벨로
 *    바뀌었기 때문이다("엣지만 보는게 아니라 그 상태를 읽어야 하는거야
 *    엣지는 딱 감지하려고 하는거고", 사용자 확정 2026-08-18).
 *
 *    ISR(bsp/mk_sol.c)은 `mk_solctl_on_edge()` 로 흉내 낸다 — "raw 핀
 *    레벨과 시각만 큐에 들어온다"는 계약 그대로다. 폴링(bsp 의
 *    `mk_sol_read`)은 `mock_read()` 로 흉내 낸다 — 아래 `LEVEL[]` 배열이
 *    "지금 실제 핀"이고, `mk_solctl_tick()` 이 매 바퀴 그것을 직접 읽는다.
 *
 *    엣지 큐와 폴링은 이제 역할이 다르다: 폴링이 상태의 근거이고, 엣지
 *    큐는 "언제 바뀌었나"의 정밀 시각만 댄다. 엣지가 통째로 빠져도(예:
 *    PRIMASK 경합·인터럽트 지연·잡음으로 씹힘) 폴링이 다음 안정 구간에서
 *    반드시 회복시킨다 — `test_edge_loss_recovers_via_level_polling` 이
 *    그것을 본다. 반대로 폴링 콜백을 등록하지 않으면(예전 동작, 아래
 *    `setup()`) 놓친 엣지에서 회복하지 못한다는 것을
 *    `test_without_read_callback_lost_edge_does_not_recover` 가 같이
 *    확인한다 — 이 self-heal 로직이 조용히 없어지면 이 시험이 잡는다.
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
    /* read 콜백 없음 — 예전(엣지 전용) 동작. 대부분의 시험은 폴링과
     * 무관하게 디바운스·극성·큐 자체를 보므로 이걸로 충분하고,
     * `test_without_read_callback_lost_edge_does_not_recover` 는 바로 이
     * 자리를 되돌림 검사로 쓴다. */
    mk_solctl_init(&SC, NULL, NULL);
}

/* 🔴 "지금 실제 핀" 을 흉내 낸다. bsp/mk_sol.c 의 `mk_sol_read` 가 실기기에서
 *    하는 일 — mk_solctl_tick() 이 매 바퀴 이 함수로 세 채널을 직접 읽는다. */
static int LEVEL[MK_SOL_COUNT];

static int mock_read(void *ctx, MkSolCh ch)
{
    (void)ctx;
    return (ch < MK_SOL_COUNT) ? LEVEL[(int)ch] : 0;
}

static void setup_polling(void)
{
    mk_cfgtable_init(&CFG);
    mk_solctl_init(&SC, mock_read, NULL);
    for (int c = 0; c < MK_SOL_COUNT; c++) {
        LEVEL[c] = 1;   /* 기본 raw HIGH = 꺼짐(풀업, 신호 없음) */
        /* 🔴 실제 bsp/mk_sol.c 의 mk_sol_init() 이 그러듯, 부팅 시 세
         *    채널을 전부 prime() 해 둔다. 이걸 빼면 시험이 손대지 않은
         *    나머지 채널도 "has_candidate 없음" 상태로 남아 있다가 그
         *    시험의 첫 tick 부터 자기 나름의 디바운스를 시작해, 시험이
         *    보려는 채널과 같은 tick 에서 함께 확정돼 레코드 수를
         *    어긋나게 만든다. */
        mk_solctl_prime(&SC, (MkSolCh)c, LEVEL[c], 0);
    }
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

/* ---- 레벨 폴링이 상태의 근거다 (사용자 확정 2026-08-18) ------------------- */

static void test_edge_loss_recovers_via_level_polling(void)
{
    /* 🔴 이 시험의 핵심 — ISR 이 엣지를 하나도 못 올린 채로 여러 바퀴가
     *    돌아도, 실제 핀 레벨을 매 바퀴 읽으므로 확정 상태가 스스로
     *    레벨을 따라간다. mk_solctl_on_edge() 는 이 시험에서 단 한 번도
     *    부르지 않는다. */
    setup_polling();
    set_debounce(5u);

    LEVEL[MK_SOL_J18] = 0;   /* raw LOW = 켜짐 — 실제로는 이미 바뀌었다 */

    mk_solctl_tick(&SC, &CFG, 1000);   /* 첫 폴링 — 후보를 연다 */
    MkSolOut buf[MAX_OUT]; int n;
    take_into(buf, MAX_OUT, &n);
    CHECK(n == 0, "폴링만으로도 디바운스가 끝나기 전에는 확정하지 않는다");
    CHECK(mk_solctl_is_on(&SC, MK_SOL_J18) == 0, "확정 전이라 아직 꺼짐(초기값)");

    mk_solctl_tick(&SC, &CFG, 1005);   /* 5ms 뒤 — 엣지 없이 폴링만으로 확정 */
    take_into(buf, MAX_OUT, &n);
    CHECK(n == 1, "엣지를 하나도 안 받아도 폴링만으로 확정된다 — 자가회복");
    if (n == 1) {
        CHECK(buf[0].connector_id == 18u, "커넥터 번호는 J18");
        CHECK(buf[0].state == 1u, "raw LOW -> state 1(켜짐)");
        CHECK(buf[0].t_ms == 1000, "엣지가 없으니 t 는 폴링(첫 감지) 시각이다");
    }
    CHECK(mk_solctl_is_on(&SC, MK_SOL_J18) == 1, "확정 후 켜짐으로 보고한다");

    /* 확정된 뒤 레벨이 그대로면 매 바퀴 다시 내지 않는다(폴링도 마찬가지). */
    for (int64_t t = 1010; t <= 1200; t += 20) {
        mk_solctl_tick(&SC, &CFG, t);
    }
    take_into(buf, MAX_OUT, &n);
    CHECK(n == 0, "레벨이 그대로면 폴링뿐이어도 다시 내지 않는다");

    /* 반대 방향도 회복한다 — 실제 핀이 다시 꺼짐으로 바뀌었는데 이번에도
     * ISR 이 그 엣지를 놓쳤다고 가정한다. */
    LEVEL[MK_SOL_J18] = 1;   /* raw HIGH = 꺼짐 */
    mk_solctl_tick(&SC, &CFG, 2000);
    take_into(buf, MAX_OUT, &n);
    CHECK(n == 0, "두 번째 놓친 엣지도 디바운스 전에는 확정하지 않는다");

    mk_solctl_tick(&SC, &CFG, 2005);
    take_into(buf, MAX_OUT, &n);
    CHECK(n == 1, "두 번째로 놓친 엣지도 폴링이 회복시킨다");
    if (n == 1) {
        CHECK(buf[0].state == 0u, "raw HIGH -> state 0(꺼짐)");
        CHECK(buf[0].t_ms == 2000, "역시 폴링 시각이다");
    }
    CHECK(mk_solctl_is_on(&SC, MK_SOL_J18) == 0, "확정 상태도 꺼짐으로 돌아온다");
}

static void test_without_read_callback_lost_edge_does_not_recover(void)
{
    /* 🔴 되돌림 검사 — read 콜백을 등록하지 않으면(setup(), 예전 동작)
     *    실제 레벨이 바뀌어도 엣지가 안 오는 한 상태가 영원히 고정된다.
     *    이 시험이 실패한다면 self-heal 로직이 조용히 사라졌다는 뜻이다
     *    — `mk_solctl_tick()` 에서 `sc->read != NULL` 분기를 지우면 바로
     *    이 시험이 잡는다. */
    setup();   /* read = NULL */
    set_debounce(5u);
    mk_solctl_prime(&SC, MK_SOL_J18, 1 /* raw HIGH = 꺼짐 */, 0);

    /* 실제 핀은 켜짐(raw LOW)으로 바뀌었다고 가정하지만, ISR 이 그 엣지를
     * 완전히 놓쳤다 — mk_solctl_on_edge() 를 한 번도 안 부른다. read 가
     * 없으니 폴링으로 알아챌 길도 없다. */
    for (int64_t t = 10; t <= 500; t += 20) {
        mk_solctl_tick(&SC, &CFG, t);
    }
    MkSolOut buf[MAX_OUT]; int n;
    take_into(buf, MAX_OUT, &n);
    CHECK(n == 0, "read 콜백이 없으면 놓친 엣지에서 레코드가 나가지 않는다");
    CHECK(mk_solctl_is_on(&SC, MK_SOL_J18) == 0,
          "레벨 폴링 없이는 놓친 엣지에서 회복하지 못한다(예전 동작 그대로)");
}

static void test_level_flutter_during_polling_is_debounced(void)
{
    /* 🔴 엣지 큐를 전혀 거치지 않고 폴링 값만 흔들려도, 안정 구간이 끝날
     *    때까지는 확정하지 않는다 — "레벨이 흔들리는 동안에는 확정하지
     *    않는다"는 계약이 엣지 유무와 무관하게 성립해야 한다. */
    setup_polling();                            /* 세 채널 모두 꺼짐으로 이미 prime 됐다 */
    set_debounce(10u);

    LEVEL[MK_SOL_J19] = 0; mk_solctl_tick(&SC, &CFG, 100);
    LEVEL[MK_SOL_J19] = 1; mk_solctl_tick(&SC, &CFG, 105);
    LEVEL[MK_SOL_J19] = 0; mk_solctl_tick(&SC, &CFG, 108);

    MkSolOut buf[MAX_OUT]; int n;
    mk_solctl_tick(&SC, &CFG, 115);            /* 마지막 흔들림(108)로부터 7ms */
    take_into(buf, MAX_OUT, &n);
    CHECK(n == 0, "폴링만으로 흔들려도 마지막 흔들림 이후 디바운스가 안 끝났다");

    mk_solctl_tick(&SC, &CFG, 118);            /* 10ms 경과 */
    take_into(buf, MAX_OUT, &n);
    CHECK(n == 1, "잡음이 가라앉은 뒤(폴링의 마지막 값 기준)에만 확정한다");
    if (n == 1) {
        CHECK(buf[0].state == 1u, "마지막 흔들림(raw LOW)이 이긴다");
        CHECK(buf[0].t_ms == 108, "t 는 안정 구간이 시작된 마지막 폴링 시각");
    }
}

static void test_edge_time_wins_over_poll_time_when_both_agree(void)
{
    /* 🔴 엣지가 정상적으로 왔으면(정상 경로) 폴링이 함께 등록돼 있어도
     *    정밀한 엣지 시각이 이긴다 — 폴링은 값이 같으면 후보를 다시 열지
     *    않는다(mk_solctl_tick() 의 `raw != sc->candidate[c]` 검사). */
    setup_polling();                            /* 세 채널 모두 꺼짐으로 이미 prime 됐다 */
    set_debounce(5u);

    LEVEL[MK_SOL_J20] = 0;                      /* 실제 레벨도 함께 바뀐다 */
    mk_solctl_on_edge(&SC, MK_SOL_J20, 0, 1000);  /* 엣지가 정확히 잡혔다 */

    mk_solctl_tick(&SC, &CFG, 1002);   /* 엣지 처리 + 폴링(같은 값이라 그대로) */
    MkSolOut buf[MAX_OUT]; int n;
    take_into(buf, MAX_OUT, &n);
    CHECK(n == 0, "아직 디바운스 전");

    mk_solctl_tick(&SC, &CFG, 1005);   /* 엣지 시각(1000) 기준 5ms */
    take_into(buf, MAX_OUT, &n);
    CHECK(n == 1, "엣지가 있으면(폴링과 함께 있어도) 확정된다");
    if (n == 1) {
        CHECK(buf[0].t_ms == 1000,
              "엣지가 있으면 t 는 엣지 시각이다 — 폴링 시각(1002)이 아니다");
    }
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
    printf("-- 레벨 폴링이 상태의 근거다 --\n");
    test_edge_loss_recovers_via_level_polling();
    test_without_read_callback_lost_edge_does_not_recover();
    test_level_flutter_during_polling_is_debounced();
    test_edge_time_wins_over_poll_time_when_both_agree();
    printf("-- 카탈로그 --\n");
    test_the_catalog_has_the_debounce_item();
    test_the_catalog_no_longer_has_the_bool_switches();

    printf(failures ? "\nFAILED (%d)\n" : "\nPASSED\n", failures);
    return failures ? 1 : 0;
}
