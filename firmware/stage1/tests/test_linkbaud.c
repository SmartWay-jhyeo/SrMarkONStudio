/* mk_linkbaud 단위 시험 — 링크 속도 변경의 안전장치. HAL 비의존.
 *
 * 대상: firmware/stage1/app/mk_linkbaud.c
 * 규격: protocol/specification.md §4.2
 *
 * 🔴 여기서 보는 것은 기능이 아니라 **되돌아오는가**다. 이 모듈이 하는
 *    일은 "속도를 바꾼다" 가 아니라 "잘못 바꿨을 때 사람 없이 살아난다"
 *    이므로, 시험도 실패 경로가 본체다.
 */
#include <stdio.h>
#include <string.h>

#include "../app/mk_linkbaud.h"

static int failures = 0;

#define CHECK(cond, msg) do {                                   \
    if (!(cond)) { printf("  FAIL %s\n", msg); failures++; }    \
    else         { printf("  ok   %s\n", msg); }                \
} while (0)

#define CHECK_U(got, want, msg) do {                                        \
    unsigned long g_ = (unsigned long)(got), w_ = (unsigned long)(want);    \
    if (g_ != w_) {                                                         \
        printf("  FAIL %s\n        got  %lu\n        want %lu\n",           \
               msg, g_, w_);                                                \
        failures++;                                                         \
    } else { printf("  ok   %s\n", msg); }                                  \
} while (0)

/* 이 보드의 USART3 커널 클럭 (bsp/mk_clock.h 의 MK_USART3_KERNEL_HZ). */
#define KERN 64000000u

/* ---- 가짜 하드웨어 ------------------------------------------------------ */

static uint32_t WIRE;        /* "지금 전선에 서 있는 속도" */
static int      APPLIES;

static void fake_apply(void *ctx, uint32_t baud)
{
    (void)ctx;
    WIRE = baud;
    APPLIES++;
}

/* `link.baud` 하나짜리 설정표. 실제 표(mk_cfgtable.c)의 그 항목과 같은
 * 모양이면 충분하다 — 이 모듈은 키 하나만 본다. */
static const uint32_t CHOICES[] = { 115200u, 460800u, 921600u,
                                    1000000u, 1500000u, 2000000u };
static MkCfgItem ITEMS[1];
static MkConfig  CFG;

static void setup(MkLinkBaud *lb)
{
    memset(ITEMS, 0, sizeof ITEMS);
    ITEMS[0] = (MkCfgItem){ .key = "link.baud", .group = "link",
                            .vtype = MK_VT_ENUM,
                            .choices = CHOICES, .n_choices = 6,
                            .unit = "bps", .label = "호스트 링크 속도" };
    ITEMS[0].def.u = MK_LINKBAUD_DEFAULT;
    ITEMS[0].cur.u = MK_LINKBAUD_DEFAULT;
    CFG.items = ITEMS;
    CFG.count = 1;
    CFG.dirty = 0;

    WIRE = MK_LINKBAUD_DEFAULT;
    APPLIES = 0;
    mk_linkbaud_init(lb, KERN, MK_LINKBAUD_DEFAULT);
}

/* ---- 오차 계산 ---------------------------------------------------------- */

static void test_reachable_bauds_match_the_spec_table(void)
{
    /* 규격 §4.2.6 의 표. 손으로 적은 것이 아니라 계산이 그렇게 나온다.
     * BRR 은 반올림이다 — HAL 의 UART_DIV_SAMPLING16 과 같은 식. */
    CHECK_U(mk_linkbaud_brr(KERN, 115200u),  556u, "115200 -> BRR 556");
    CHECK_U(mk_linkbaud_brr(KERN, 460800u),  139u, "460800 -> BRR 139");
    CHECK_U(mk_linkbaud_brr(KERN, 921600u),   69u, "921600 -> BRR 69");
    CHECK_U(mk_linkbaud_brr(KERN, 1000000u),  64u, "1000000 -> BRR 64");
    CHECK_U(mk_linkbaud_brr(KERN, 1500000u),  43u, "1500000 -> BRR 43");
    CHECK_U(mk_linkbaud_brr(KERN, 2000000u),  32u, "2000000 -> BRR 32");

    CHECK_U(mk_linkbaud_err_ppm(KERN, 115200u),  799u, "115200 오차 799 ppm");
    CHECK_U(mk_linkbaud_err_ppm(KERN, 460800u),  799u, "460800 오차 799 ppm");
    CHECK_U(mk_linkbaud_err_ppm(KERN, 921600u), 6441u, "921600 오차 6441 ppm");
    CHECK_U(mk_linkbaud_err_ppm(KERN, 1000000u),   0u, "1 Mbaud 는 오차가 없다");
    CHECK_U(mk_linkbaud_err_ppm(KERN, 1500000u), 7751u, "1.5 Mbaud 오차 7751 ppm");
    CHECK_U(mk_linkbaud_err_ppm(KERN, 2000000u),   0u, "2 Mbaud 는 오차가 없다");

#define ONE(v) CHECK(mk_linkbaud_reachable(KERN, v), #v " 는 낼 수 있다");
    MK_LINKBAUD_CHOICE_LIST(ONE)
#undef ONE
}

static void test_unreachable_bauds_are_refused(void)
{
    /* 🔴 걸리는 자리는 둘이다 — 오차 2 % 초과, 그리고 BRR < 16.
     *    64 MHz 에서는 후자가 먼저 온다: 4 Mbaud 가 BRR 16 으로 딱
     *    경계이고, 그보다 빠르면 오버샘플 16 자체가 성립하지 않는다
     *    (RM0468 §54.5.4). */
    CHECK(mk_linkbaud_reachable(KERN, 4000000u), "4 Mbaud 는 BRR 16 으로 경계");
    CHECK(!mk_linkbaud_reachable(KERN, 5000000u),
          "5 Mbaud 는 BRR 이 13 이라 오버샘플 16 으로 못 낸다");
    CHECK(!mk_linkbaud_reachable(KERN, 0u), "0 은 나눗셈이 안 된다");

    /* 커널 클럭이 다르면 답도 달라진다 — 이 계산이 클럭에 매여 있다는 것
     * 자체가 이 시험의 요지다. 8 MHz 에서 921600 은 BRR 9 라 못 낸다. */
    CHECK(!mk_linkbaud_reachable(8000000u, 921600u),
          "8 MHz 에서는 921600 을 못 낸다 (BRR 9)");

    MkLinkBaud lb;
    setup(&lb);
    CHECK(mk_linkbaud_request(&lb, 5000000u) == MK_LINKBAUD_ERR_RANGE,
          "못 내는 속도는 요청 단계에서 거부된다");
    CHECK(mk_linkbaud_is_pending(&lb) == 0, "거부됐으니 대기 상태도 아니다");
}

/* ---- 순서: 응답이 먼저, 속도는 나중 ------------------------------------- */

static void test_request_does_not_touch_the_wire(void)
{
    /* 🔴 규격 §4.2.2 규칙 1. 요청을 받는 것과 속도를 바꾸는 것은 다른
     *    함수다 — 그래야 $SACK 가 옛 속도로 나갈 시간이 생긴다. */
    MkLinkBaud lb;
    setup(&lb);

    CHECK(mk_linkbaud_request(&lb, 1500000u) == MK_LINKBAUD_OK, "요청은 받아들여진다");
    CHECK_U(WIRE, MK_LINKBAUD_DEFAULT, "요청만으로는 전선이 안 바뀐다");
    CHECK_U(APPLIES, 0, "apply 도 안 불렸다");
    CHECK_U(mk_linkbaud_active(&lb), MK_LINKBAUD_DEFAULT, "active 도 그대로");
    CHECK(mk_linkbaud_is_pending(&lb), "다만 확정되지 않은 값이 대기 중이다");

    mk_linkbaud_tick(&lb, &CFG, 0, fake_apply, NULL);
    CHECK_U(WIRE, 1500000u, "tick 이 비로소 전선을 바꾼다");
    CHECK_U(APPLIES, 1, "apply 는 한 번만");
    CHECK_U(mk_linkbaud_active(&lb), 1500000u, "active 가 새 속도");
    CHECK_U(mk_linkbaud_confirmed(&lb), MK_LINKBAUD_DEFAULT,
            "확정된 값은 아직 옛 속도다");
}

static void test_config_table_change_is_a_request(void)
{
    /* $CFG,SET 은 설정표의 cur 만 바꾼다. tick 이 그것을 요청으로 읽는다. */
    MkLinkBaud lb;
    setup(&lb);

    ITEMS[0].cur.u = 460800u;                 /* $CFG,SET,link.baud,460800 */
    CHECK_U(WIRE, MK_LINKBAUD_DEFAULT, "설정표만 바뀐 시점에는 전선이 그대로");

    mk_linkbaud_tick(&lb, &CFG, 0, fake_apply, NULL);
    CHECK_U(WIRE, 460800u, "다음 tick 에 반영된다");
    CHECK(mk_linkbaud_is_pending(&lb), "확인 대기 상태");
    CHECK_U(mk_linkbaud_pending(&lb), 460800u, "대기 중인 값이 보인다");
}

/* ---- 확인 -------------------------------------------------------------- */

static void test_confirm_inside_the_deadline_commits(void)
{
    MkLinkBaud lb;
    setup(&lb);
    ITEMS[0].cur.u = 1500000u;
    mk_linkbaud_tick(&lb, &CFG, 0, fake_apply, NULL);

    CHECK(mk_linkbaud_confirm(&lb, 1500000u) == MK_LINKBAUD_OK, "확인이 받아들여진다");
    CHECK_U(mk_linkbaud_confirmed(&lb), 1500000u, "이제 확정된 값이다");
    CHECK(mk_linkbaud_is_pending(&lb) == 0, "대기 상태가 끝났다");
    CHECK_U(mk_linkbaud_pending(&lb), 0u, "대기 중인 값도 없다");

    /* 시한이 한참 지나도 되돌아가지 않는다. */
    mk_linkbaud_tick(&lb, &CFG, MK_LINKBAUD_CONFIRM_MS * 10, fake_apply, NULL);
    CHECK_U(WIRE, 1500000u, "확정된 뒤에는 시한이 지나도 그대로");
    CHECK_U(APPLIES, 1, "되돌리는 apply 도 없다");
}

static void test_confirm_with_a_different_value_is_refused(void)
{
    /* 🔴 값을 함께 받는 이유(규격 §4.2.3): 값이 되돌아온다는 것 자체가
     *    "호스트가 무엇을 확인하는지 알고 그 속도로 말할 수 있다" 는
     *    증거다. 값 없는 확인은 앞선 대화의 늦은 메아리와 구분되지 않는다. */
    MkLinkBaud lb;
    setup(&lb);
    ITEMS[0].cur.u = 1500000u;
    mk_linkbaud_tick(&lb, &CFG, 0, fake_apply, NULL);

    CHECK(mk_linkbaud_confirm(&lb, 921600u) == MK_LINKBAUD_ERR_RANGE,
          "다른 값으로는 확정되지 않는다");
    CHECK(mk_linkbaud_is_pending(&lb), "여전히 대기 중이다 — 시한은 계속 흐른다");
    CHECK_U(mk_linkbaud_confirmed(&lb), MK_LINKBAUD_DEFAULT, "확정값도 그대로");
}

static void test_confirm_without_a_pending_change_is_refused(void)
{
    MkLinkBaud lb;
    setup(&lb);
    CHECK(mk_linkbaud_confirm(&lb, MK_LINKBAUD_DEFAULT) == MK_LINKBAUD_ERR_STATE,
          "대기 중인 변경이 없으면 확인할 것도 없다");
}

/* ---- 시한 복귀 ---------------------------------------------------------- */

static void test_deadline_reverts_without_anyone_doing_anything(void)
{
    /* 🔴 이 시험이 이 모듈의 존재 이유다. */
    MkLinkBaud lb;
    setup(&lb);
    ITEMS[0].cur.u = 2000000u;
    mk_linkbaud_tick(&lb, &CFG, 1000, fake_apply, NULL);
    CHECK_U(WIRE, 2000000u, "일단 바뀌었다");

    mk_linkbaud_tick(&lb, &CFG, 1000 + MK_LINKBAUD_CONFIRM_MS - 1, fake_apply, NULL);
    CHECK_U(WIRE, 2000000u, "시한 직전까지는 기다린다");
    CHECK(mk_linkbaud_is_pending(&lb), "아직 대기 중");

    mk_linkbaud_tick(&lb, &CFG, 1000 + MK_LINKBAUD_CONFIRM_MS, fake_apply, NULL);
    CHECK_U(WIRE, MK_LINKBAUD_DEFAULT, "시한이 지나면 스스로 옛 속도로 돌아간다");
    CHECK(mk_linkbaud_is_pending(&lb) == 0, "대기 상태도 끝났다");
    CHECK_U(ITEMS[0].cur.u, MK_LINKBAUD_DEFAULT,
            "🔴 설정표의 현재값도 함께 되돌아간다 — 안 그러면 다음 tick 이 "
            "이것을 새 요청으로 오해한다");
}

static void test_revert_is_counted(void)
{
    /* 규격 §7.4 의 link.reverted — "그 속도로는 이 링크가 안 된다" 는
     * 유일한 누적 실측이다. */
    MkLinkBaud lb;
    setup(&lb);
    ITEMS[0].cur.u = 2000000u;
    mk_linkbaud_tick(&lb, &CFG, 0, fake_apply, NULL);
    mk_linkbaud_tick(&lb, &CFG, MK_LINKBAUD_CONFIRM_MS, fake_apply, NULL);

    CHECK_U(lb.applied, 1u, "바꾼 횟수 1");
    CHECK_U(lb.reverted, 1u, "되돌아간 횟수 1");
    CHECK_U(lb.confirmed_count, 0u, "확정된 적은 없다");

    ITEMS[0].cur.u = 1500000u;
    mk_linkbaud_tick(&lb, &CFG, MK_LINKBAUD_CONFIRM_MS + 1, fake_apply, NULL);
    mk_linkbaud_confirm(&lb, 1500000u);
    CHECK_U(lb.applied, 2u, "바꾼 횟수 2");
    CHECK_U(lb.reverted, 1u, "되돌아간 횟수는 그대로");
    CHECK_U(lb.confirmed_count, 1u, "확정 1");
}

static void test_no_second_request_while_one_is_pending(void)
{
    /* 🔴 겹치면 무엇으로 되돌아가야 하는지가 흐려진다. */
    MkLinkBaud lb;
    setup(&lb);
    ITEMS[0].cur.u = 1500000u;
    mk_linkbaud_tick(&lb, &CFG, 0, fake_apply, NULL);

    CHECK(mk_linkbaud_request(&lb, 2000000u) == MK_LINKBAUD_ERR_STATE,
          "대기 중에는 다음 요청을 받지 않는다");
    CHECK_U(mk_linkbaud_pending(&lb), 1500000u, "대기 중인 값도 안 바뀐다");
}

static void test_remaining_time_is_visible(void)
{
    MkLinkBaud lb;
    setup(&lb);
    CHECK(mk_linkbaud_remaining_ms(&lb, 0) < 0, "대기 중이 아니면 -1");

    ITEMS[0].cur.u = 460800u;
    mk_linkbaud_tick(&lb, &CFG, 500, fake_apply, NULL);
    CHECK(mk_linkbaud_remaining_ms(&lb, 500) == MK_LINKBAUD_CONFIRM_MS,
          "막 바꾼 순간에는 시한 전체가 남아 있다");
    CHECK(mk_linkbaud_remaining_ms(&lb, 500 + 4000) == MK_LINKBAUD_CONFIRM_MS - 4000,
          "시간이 흐른 만큼 줄어든다");
}

static void test_tick_without_apply_or_cfg_does_nothing(void)
{
    /* 링크 속도를 안 붙인 빌드에서 그대로 돌아야 한다. */
    MkLinkBaud lb;
    setup(&lb);
    mk_linkbaud_tick(&lb, NULL, 0, fake_apply, NULL);
    mk_linkbaud_tick(&lb, &CFG, 0, NULL, NULL);
    CHECK_U(APPLIES, 0, "아무 일도 안 일어난다");
    CHECK(mk_linkbaud_is_pending(&lb) == 0, "대기 상태도 안 생긴다");
}

static void test_boot_baud_out_of_the_list_falls_back(void)
{
    /* 🔴 Flash 에서 읽은 값이 이 클럭으로 못 내는 값이면 기본값으로 간다.
     *    확정된 값만 저장되므로 정상 경로에서는 일어나지 않지만, 저장이
     *    깨졌을 때 벽돌이 되지 않게 하는 마지막 방어선이다. */
    MkLinkBaud lb;
    mk_linkbaud_init(&lb, KERN, 5000000u);
    CHECK_U(mk_linkbaud_active(&lb), MK_LINKBAUD_DEFAULT,
            "못 내는 부팅 속도는 기본값으로 대체된다");
    CHECK_U(mk_linkbaud_confirmed(&lb), MK_LINKBAUD_DEFAULT, "확정값도 기본값");
}

int main(void)
{
    printf("mk_linkbaud\n");
    test_reachable_bauds_match_the_spec_table();
    test_unreachable_bauds_are_refused();
    test_request_does_not_touch_the_wire();
    test_config_table_change_is_a_request();
    test_confirm_inside_the_deadline_commits();
    test_confirm_with_a_different_value_is_refused();
    test_confirm_without_a_pending_change_is_refused();
    test_deadline_reverts_without_anyone_doing_anything();
    test_revert_is_counted();
    test_no_second_request_while_one_is_pending();
    test_remaining_time_is_visible();
    test_tick_without_apply_or_cfg_does_nothing();
    test_boot_baud_out_of_the_list_falls_back();
    printf(failures ? "\nFAILED (%d)\n" : "\nPASSED\n", failures);
    return failures ? 1 : 0;
}
