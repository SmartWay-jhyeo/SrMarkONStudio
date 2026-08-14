/* mk_railctl 단위 시험.
 *
 * 🔴 여기서 확인하는 것들은 실물로 확인하기에 **위험한** 것들이다.
 *    24V 를 잘못 올려 보고 아는 것은 늦다. 순서와 금지가 코드로 지켜지는지
 *    보드 없이 여기서 판가름한다.
 */
#include <stdio.h>
#include <string.h>
#include "../app/mk_railctl.h"

static int failures = 0;

#define CHECK(cond, msg) do {                                   \
    if (!(cond)) { printf("  FAIL %s\n", msg); failures++; }    \
    else         { printf("  ok   %s\n", msg); }                \
} while (0)

/* ---- 핀 동작 기록 -------------------------------------------------------- */

#define MAX_EV 32
typedef struct {
    MkRail rail[MAX_EV];
    int    on[MAX_EV];
    int    n;
} Log;

static Log LG;
static MkRailCtl RC;

static void log_set(void *ctx, MkRail rail, int on)
{
    Log *l = (Log *)ctx;
    if (l->n < MAX_EV) {
        l->rail[l->n] = rail;
        l->on[l->n] = on;
        l->n++;
    }
}

static void setup(void)
{
    memset(&LG, 0, sizeof LG);
    mk_railctl_init(&RC, log_set, &LG);
}

static const char *name(MkRail r)
{
    switch (r) {
    case MK_RAIL_5V:   return "5V";
    case MK_RAIL_14V9: return "14.9V";
    default:           return "24V";
    }
}

static void dump(void)
{
    for (int i = 0; i < LG.n; i++) {
        printf("       %s -> %s\n", name(LG.rail[i]), LG.on[i] ? "ON" : "OFF");
    }
}

/* ---- 시험 --------------------------------------------------------------- */

static void test_five_volts_comes_up_first_and_alone(void)
{
    setup();
    mk_railctl_tick(&RC, 1, 1, 500, 0);
    CHECK(LG.n == 1, "첫 tick 에 한 레일만 올린다");
    CHECK(LG.n >= 1 && LG.rail[0] == MK_RAIL_5V && LG.on[0], "그것은 5V 다");
    CHECK(mk_railctl_is_on(&RC, MK_RAIL_14V9) == 0, "14.9V 는 아직");
    CHECK(mk_railctl_is_on(&RC, MK_RAIL_24V) == 0, "24V 는 아직");
}

static void test_sequence_order_and_spacing(void)
{
    /* 🔴 5V -> 14.9V -> 24V, 사이에 seq_delay 만큼. 한꺼번에 올리면
     *    돌입 전류가 겹친다. */
    setup();
    mk_railctl_tick(&RC, 1, 1, 500, 0);          /* 5V */
    mk_railctl_tick(&RC, 1, 1, 500, 499);
    CHECK(LG.n == 1, "간격 전에는 다음 단계로 안 간다");

    mk_railctl_tick(&RC, 1, 1, 500, 500);        /* 14.9V */
    CHECK(LG.n == 2 && LG.rail[1] == MK_RAIL_14V9 && LG.on[1], "다음은 14.9V");

    mk_railctl_tick(&RC, 1, 1, 500, 999);
    CHECK(LG.n == 2, "또 간격을 기다린다");

    mk_railctl_tick(&RC, 1, 1, 500, 1000);       /* 24V */
    CHECK(LG.n == 3 && LG.rail[2] == MK_RAIL_24V && LG.on[2], "마지막이 24V");

    mk_railctl_tick(&RC, 1, 1, 500, 5000);
    CHECK(LG.n == 3, "다 올라가면 더 건드리지 않는다");
    if (failures) { dump(); }
}

static void test_five_volts_can_never_be_turned_off(void)
{
    /* 🔴 이 시험이 이 파일에서 가장 중요하다. 쿨링 팬(J34)이 5V 레일에
     *    직결이고 상시 동작이 요구사항이다 (CLAUDE.md §4).
     *
     *    모듈이 **구조적으로** 막는다 — 호출하는 쪽의 선의에 기대지 않는다.
     *    저전력 모드나 에러 처리에서 실수로 내리려 드는 것이 정확히
     *    문서가 경고하는 상황이다. */
    setup();
    mk_railctl_tick(&RC, 1, 1, 0, 0);
    mk_railctl_tick(&RC, 1, 1, 0, 1);
    mk_railctl_tick(&RC, 1, 1, 0, 2);
    int before = LG.n;

    /* 전부 끄라고 해 본다. */
    for (int64_t t = 10; t < 20; t++) {
        mk_railctl_tick(&RC, 0, 0, 0, t);
    }
    CHECK(mk_railctl_is_on(&RC, MK_RAIL_5V), "5V 는 여전히 켜져 있다");

    int lowered_5v = 0;
    for (int i = before; i < LG.n; i++) {
        if (LG.rail[i] == MK_RAIL_5V && !LG.on[i]) { lowered_5v = 1; }
    }
    CHECK(!lowered_5v, "5V 를 내리는 동작 자체가 나가지 않는다");
}

static void test_others_do_turn_off(void)
{
    setup();
    for (int64_t t = 0; t < 5; t++) {
        mk_railctl_tick(&RC, 1, 1, 0, t);
    }
    CHECK(mk_railctl_is_on(&RC, MK_RAIL_24V), "일단 다 올라갔다");

    mk_railctl_tick(&RC, 0, 0, 0, 10);
    CHECK(!mk_railctl_is_on(&RC, MK_RAIL_24V), "24V 는 꺼진다");
    CHECK(!mk_railctl_is_on(&RC, MK_RAIL_14V9), "14.9V 도 꺼진다");
}

static void test_turning_off_is_immediate_and_high_rail_first(void)
{
    /* 🔴 내릴 때는 기다리지 않는다. 사용자가 껐는데 seq_delay 만큼 더
     *    켜져 있으면 놀란다. 그리고 높은 레일부터 내린다 — 24V 를 켜 둔
     *    채 14.9V 를 먼저 내리면 중간 단이 빈다. */
    setup();
    for (int64_t t = 0; t < 5; t++) {
        mk_railctl_tick(&RC, 1, 1, 0, t);
    }
    int before = LG.n;
    mk_railctl_tick(&RC, 0, 0, 5000, 10);       /* 간격이 길어도 */

    CHECK(LG.n == before + 2, "한 번에 둘 다 내린다 (간격을 기다리지 않는다)");
    if (LG.n == before + 2) {
        CHECK(LG.rail[before] == MK_RAIL_24V, "24V 를 먼저");
        CHECK(LG.rail[before + 1] == MK_RAIL_14V9, "그 다음 14.9V");
    }
}

static void test_24v_waits_for_14v9(void)
{
    /* 둘 다 원하는데 14.9V 가 아직이면 24V 는 기다린다. 순서가 뒤집히면
     * 안 된다. */
    setup();
    mk_railctl_tick(&RC, 1, 1, 500, 0);          /* 5V */
    mk_railctl_tick(&RC, 1, 1, 500, 500);        /* 14.9V */
    int n_after_14v9 = LG.n;
    CHECK(n_after_14v9 == 2, "여기까지 두 단계");
    CHECK(!mk_railctl_is_on(&RC, MK_RAIL_24V), "24V 는 아직 안 올라갔다");
}

static void test_24v_alone_skips_the_middle_step(void)
{
    /* 14.9V 를 원하지 않으면 그 단계를 건너뛴다 — 쓰지도 않는 레일 때문에
     * 24V 가 늦어질 이유가 없다. */
    setup();
    mk_railctl_tick(&RC, 0, 1, 500, 0);          /* 5V */
    mk_railctl_tick(&RC, 0, 1, 500, 500);
    CHECK(mk_railctl_is_on(&RC, MK_RAIL_24V), "14.9V 없이 24V 가 올라간다");
    CHECK(!mk_railctl_is_on(&RC, MK_RAIL_14V9), "14.9V 는 안 올린다");
}

static void test_zero_delay_still_keeps_the_order(void)
{
    setup();
    for (int64_t t = 0; t < 4; t++) {
        mk_railctl_tick(&RC, 1, 1, 0, t);
    }
    CHECK(LG.n == 3, "간격이 0 이어도 세 단계를 밟는다");
    if (LG.n == 3) {
        CHECK(LG.rail[0] == MK_RAIL_5V && LG.rail[1] == MK_RAIL_14V9
              && LG.rail[2] == MK_RAIL_24V, "순서는 그대로");
    }
}

static void test_idempotent(void)
{
    /* 슈퍼루프가 매 바퀴 부른다. 같은 상태면 핀을 흔들지 않아야 한다 —
     * 흔들면 레일이 순간적으로 끊긴다. */
    setup();
    for (int64_t t = 0; t < 100; t++) {
        mk_railctl_tick(&RC, 1, 1, 10, t);
    }
    int n = LG.n;
    for (int64_t t = 100; t < 500; t++) {
        mk_railctl_tick(&RC, 1, 1, 10, t);
    }
    CHECK(LG.n == n, "안정된 뒤에는 아무 동작도 안 나간다");
}

static void test_settling_reports_progress(void)
{
    /* 화면이 "기동 중" 을 표시할 수 있어야 한다 — 설정은 ON 인데 아직
     * 안 올라간 구간이 존재하기 때문이다. */
    setup();
    CHECK(mk_railctl_settling(&RC, 1, 1), "시작 전에는 기동 중");
    mk_railctl_tick(&RC, 1, 1, 500, 0);
    CHECK(mk_railctl_settling(&RC, 1, 1), "5V 만 올라간 상태도 기동 중");
    mk_railctl_tick(&RC, 1, 1, 500, 500);
    mk_railctl_tick(&RC, 1, 1, 500, 1000);
    CHECK(!mk_railctl_settling(&RC, 1, 1), "다 올라가면 끝");
}

static void test_null_set_does_not_crash(void)
{
    MkRailCtl rc;
    mk_railctl_init(&rc, NULL, NULL);
    for (int64_t t = 0; t < 5; t++) {
        mk_railctl_tick(&rc, 1, 1, 0, t);
    }
    CHECK(mk_railctl_is_on(&rc, MK_RAIL_5V), "콜백이 없어도 상태는 센다");
}

int main(void)
{
    printf("mk_railctl\n");
    test_five_volts_comes_up_first_and_alone();
    test_sequence_order_and_spacing();
    test_five_volts_can_never_be_turned_off();
    test_others_do_turn_off();
    test_turning_off_is_immediate_and_high_rail_first();
    test_24v_waits_for_14v9();
    test_24v_alone_skips_the_middle_step();
    test_zero_delay_still_keeps_the_order();
    test_idempotent();
    test_settling_reports_progress();
    test_null_set_does_not_crash();
    printf(failures ? "FAILED (%d)\n" : "PASSED\n", failures);
    return failures ? 1 : 0;
}
