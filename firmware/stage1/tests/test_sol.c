/* mk_solctl 단위 시험.
 *
 * 🔴 여기서 확인하는 것은 **설정이 핀까지 닿는가** 하나다.
 *
 *    이 프로젝트가 같은 빠짐을 네 번 밟았다 — ain*.enabled 가 수집기에
 *    안 닿았고, adc.pga·drate 가 칩에 안 닿았고, led.* 가 핀으로 안
 *    나갔고, sol.* 은 이 커밋 전까지 아무것도 안 했다. 전부 GUI 에는
 *    멀쩡히 뜨기 때문에 화면만 봐서는 알 수 없다.
 *
 *    그래서 시험이 설정표를 **진짜 카탈로그**(mk_cfgtable_init)로 만든다.
 *    가짜 키로 시험하면 카탈로그에서 이름이 바뀌어도 통과한다.
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

/* ---- 핀 동작 기록 -------------------------------------------------------- */

#define MAX_EV 32
typedef struct {
    MkSolCh ch[MAX_EV];
    int     on[MAX_EV];
    int     n;
} Log;

static Log      LG;
static MkSolCtl SC;
static MkConfig CFG;

static void log_set(void *ctx, MkSolCh ch, int on)
{
    Log *l = (Log *)ctx;
    if (l->n < MAX_EV) {
        l->ch[l->n] = ch;
        l->on[l->n] = on;
        l->n++;
    }
}

static void setup(void)
{
    memset(&LG, 0, sizeof LG);
    mk_cfgtable_init(&CFG);
    mk_solctl_init(&SC, log_set, &LG);
}

static void set_bool(const char *key, unsigned v)
{
    MkCfgItem *it = mk_cfg_find(&CFG, key);
    if (it) { it->cur.u = v; }
}

/* 기록에서 채널 ch 의 마지막 명령. 없으면 -1. */
static int last_for(MkSolCh ch)
{
    int seen = -1;
    for (int i = 0; i < LG.n; i++) {
        if (LG.ch[i] == ch) { seen = LG.on[i]; }
    }
    return seen;
}

static const char *name(MkSolCh ch)
{
    switch (ch) {
    case MK_SOL_J18: return "J18";
    case MK_SOL_J19: return "J19";
    default:         return "J20";
    }
}

static void dump(void)
{
    for (int i = 0; i < LG.n; i++) {
        printf("       %s -> %s\n", name(LG.ch[i]), LG.on[i] ? "ON" : "OFF");
    }
}

/* ---- 시험 ---------------------------------------------------------------- */

/* 🔴 첫 바퀴에 셋 다 끈다.
 *
 *    "기본값이 꺼짐이니 아무것도 안 해도 된다" 는 말이 맞으려면 핀의
 *    시작 상태를 코드가 알아야 하는데, 이 층은 그것을 모른다. 한 번
 *    내보내면 설정표와 핀이 어긋난 채로 시작하는 일이 없다. */
static void test_first_tick_commands_every_channel_off(void)
{
    setup();
    mk_solctl_tick(&SC, &CFG);

    CHECK(LG.n == MK_SOL_COUNT, "첫 바퀴에 세 채널을 모두 명령한다");
    CHECK(last_for(MK_SOL_J18) == 0, "J18 이 꺼짐으로 시작한다");
    CHECK(last_for(MK_SOL_J19) == 0, "J19 가 꺼짐으로 시작한다");
    CHECK(last_for(MK_SOL_J20) == 0, "J20 이 꺼짐으로 시작한다");
    if (failures) { dump(); }
}

/* 🔴 키와 커넥터의 대응. 이것이 어긋나면 J18 을 켰는데 J20 이 열린다 —
 *    화면·저장·통신이 전부 정상이라 실물로만 알 수 있고, 밸브가 붙어
 *    있으면 알게 되는 방식이 나쁘다. */
static void test_each_key_drives_its_own_connector(void)
{
    static const struct { const char *key; MkSolCh ch; } MAP[] = {
        { "sol.j18", MK_SOL_J18 },
        { "sol.j19", MK_SOL_J19 },
        { "sol.j20", MK_SOL_J20 },
    };

    for (size_t m = 0; m < sizeof MAP / sizeof MAP[0]; m++) {
        setup();
        mk_solctl_tick(&SC, &CFG);          /* 시작 상태를 내보낸다 */
        memset(&LG, 0, sizeof LG);

        set_bool(MAP[m].key, 1u);
        mk_solctl_tick(&SC, &CFG);

        CHECK(LG.n == 1, "켠 채널 하나만 명령한다");
        CHECK(LG.n == 1 && LG.ch[0] == MAP[m].ch && LG.on[0] == 1,
              MAP[m].key);
        for (int c = 0; c < MK_SOL_COUNT; c++) {
            if ((MkSolCh)c == MAP[m].ch) { continue; }
            CHECK(mk_solctl_is_on(&SC, (MkSolCh)c) == 0,
                  "나머지 채널은 꺼진 채로 남는다");
        }
        if (failures) { dump(); return; }
    }
}

/* 값이 그대로면 다시 쓰지 않는다. 매 바퀴 쓰면 기록이 넘쳐 무엇이
 * 언제 바뀌었는지 볼 수 없고, 이 층이 명령 상태를 들고 있을 이유도 없다. */
static void test_unchanged_settings_do_not_write_again(void)
{
    setup();
    set_bool("sol.j19", 1u);
    mk_solctl_tick(&SC, &CFG);
    int after_first = LG.n;

    for (int i = 0; i < 5; i++) { mk_solctl_tick(&SC, &CFG); }

    CHECK(LG.n == after_first, "설정이 그대로면 핀을 다시 쓰지 않는다");
    CHECK(mk_solctl_is_on(&SC, MK_SOL_J19) == 1, "명령 상태는 켜짐으로 남는다");
    if (failures) { dump(); }
}

static void test_turning_off_reaches_the_pin(void)
{
    setup();
    set_bool("sol.j20", 1u);
    mk_solctl_tick(&SC, &CFG);
    memset(&LG, 0, sizeof LG);

    set_bool("sol.j20", 0u);
    mk_solctl_tick(&SC, &CFG);

    CHECK(LG.n == 1 && LG.ch[0] == MK_SOL_J20 && LG.on[0] == 0,
          "끄면 끔이 핀까지 간다");
    CHECK(mk_solctl_is_on(&SC, MK_SOL_J20) == 0, "명령 상태도 꺼짐이 된다");
    if (failures) { dump(); }
}

/* 🔴 항목을 못 찾으면 **끈 것으로** 본다. sync_rails 와 같은 규칙이다 —
 *    설정표에서 항목이 사라지는 것은 실수이고, 실수했을 때 출력이 켜지면
 *    안 된다. */
static void test_missing_items_are_treated_as_off(void)
{
    memset(&LG, 0, sizeof LG);
    memset(&CFG, 0, sizeof CFG);        /* 항목이 하나도 없는 설정표 */
    mk_solctl_init(&SC, log_set, &LG);

    mk_solctl_tick(&SC, &CFG);

    CHECK(last_for(MK_SOL_J18) == 0, "없는 항목은 꺼짐이다 (J18)");
    CHECK(last_for(MK_SOL_J19) == 0, "없는 항목은 꺼짐이다 (J19)");
    CHECK(last_for(MK_SOL_J20) == 0, "없는 항목은 꺼짐이다 (J20)");
    for (int c = 0; c < MK_SOL_COUNT; c++) {
        CHECK(mk_solctl_is_on(&SC, (MkSolCh)c) == 0,
              "없는 항목으로는 명령 상태도 켜지지 않는다");
    }
    if (failures) { dump(); }
}

/* 🔴 카탈로그에 세 항목이 그대로 있는지. 키 이름이 바뀌면 위 시험들이
 *    "없는 항목" 경로로 빠져 **조용히 통과한다** — 그때는 보드에서
 *    스위치가 아무 일도 안 한다. */
static void test_the_catalog_still_has_the_three_keys(void)
{
    mk_cfgtable_init(&CFG);
    CHECK(mk_cfg_find(&CFG, "sol.j18") != NULL, "카탈로그에 sol.j18 이 있다");
    CHECK(mk_cfg_find(&CFG, "sol.j19") != NULL, "카탈로그에 sol.j19 가 있다");
    CHECK(mk_cfg_find(&CFG, "sol.j20") != NULL, "카탈로그에 sol.j20 이 있다");
}

int main(void)
{
    printf("-- 시작 상태 --\n");
    test_first_tick_commands_every_channel_off();
    printf("-- 키와 커넥터의 대응 --\n");
    test_each_key_drives_its_own_connector();
    printf("-- 바뀔 때만 쓴다 --\n");
    test_unchanged_settings_do_not_write_again();
    printf("-- 끄기 --\n");
    test_turning_off_reaches_the_pin();
    printf("-- 없는 항목 --\n");
    test_missing_items_are_treated_as_off();
    printf("-- 카탈로그 --\n");
    test_the_catalog_still_has_the_three_keys();

    printf(failures ? "\nFAILED (%d)\n" : "\nPASSED\n", failures);
    return failures ? 1 : 0;
}
