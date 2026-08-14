#include "mk_railctl.h"

#include <string.h>

static void drive(MkRailCtl *rc, MkRail rail, int on)
{
    if (rc->on[rail] == (uint8_t)(on != 0)) {
        return;                     /* 이미 그 상태다. 핀을 흔들지 않는다 */
    }
    rc->on[rail] = (uint8_t)(on != 0);
    if (rc->set != NULL) {
        rc->set(rc->ctx, rail, on);
    }
}

void mk_railctl_init(MkRailCtl *rc, MkRailSet set, void *ctx)
{
    memset(rc, 0, sizeof *rc);
    rc->set = set;
    rc->ctx = ctx;
}

void mk_railctl_tick(MkRailCtl *rc, int want_5v, int want_14v9, int want_24v,
                     uint16_t seq_delay_ms, int64_t now_ms)
{
    /* 내리는 것부터 본다. 부하를 줄이는 방향이라 기다릴 이유가 없고,
     * 사용자가 껐는데 seq_delay 만큼 더 켜져 있으면 놀란다.
     *
     * 🔴 높은 레일부터 내린다. 24V 를 켜 둔 채 14.9V 를 먼저 내리면
     *    중간 단이 빈 상태가 된다. */
    if (!want_24v) {
        drive(rc, MK_RAIL_24V, 0);
    }
    if (!want_14v9) {
        drive(rc, MK_RAIL_14V9, 0);
    }
    /* 🔴 5V 를 내리면 위의 둘도 함께 내린다.
     *
     *    셋이 독립된 레귤레이터이긴 하지만, 5V 만 끈 상태는 이 보드에서
     *    쓸 일이 없고 진단만 어렵게 만든다 — 팬이 멈춘 채 24V 만 살아
     *    있는 상태가 그것이다. 끄려면 다 끈다. */
    if (!want_5v) {
        drive(rc, MK_RAIL_24V, 0);
        drive(rc, MK_RAIL_14V9, 0);
        drive(rc, MK_RAIL_5V, 0);
        return;
    }

    /* 올리는 것은 한 바퀴에 한 단계씩. */
    if (!rc->started) {
        rc->started = 1u;
        rc->last_step_ms = now_ms;
        drive(rc, MK_RAIL_5V, 1);       /* 5V 가 언제나 첫 단계다 */
        return;
    }

    /* 🔴 5V 가 안 올라가 있으면 여기서 올린다. 다음 단계의 전제이므로
     *    건너뛰면 안 된다 — 껐다 켜는 경우도 여기로 돌아온다. */
    if (!rc->on[MK_RAIL_5V]) {
        rc->last_step_ms = now_ms;
        drive(rc, MK_RAIL_5V, 1);
        return;
    }

    if ((now_ms - rc->last_step_ms) < (int64_t)seq_delay_ms) {
        return;                          /* 아직 띄울 시간이 안 됐다 */
    }

    if (want_14v9 && !rc->on[MK_RAIL_14V9]) {
        rc->last_step_ms = now_ms;
        drive(rc, MK_RAIL_14V9, 1);
        return;                          /* 한 바퀴에 한 단계 */
    }

    /* 🔴 24V 는 14.9V 뒤에만 올린다. 14.9V 를 원하지 않는 설정이면
     *    그 단계를 건너뛰고 바로 올려도 되지만, 원하는데 아직 안 올라간
     *    상태에서는 기다린다 — 순서가 뒤집히면 안 된다. */
    if (want_14v9 && !rc->on[MK_RAIL_14V9]) {
        return;
    }
    if (want_24v && !rc->on[MK_RAIL_24V]) {
        rc->last_step_ms = now_ms;
        drive(rc, MK_RAIL_24V, 1);
    }
}

int mk_railctl_is_on(const MkRailCtl *rc, MkRail rail)
{
    /* 🔴 `rail >= 0` 을 쓰지 않는다. MkRail 은 부호 없는 열거형이라
     *    항상 참이고, -Werror 에서 컴파일이 서는 것으로 알았다. */
    return (rail < MK_RAIL_COUNT) ? (int)rc->on[rail] : 0;
}

int mk_railctl_settling(const MkRailCtl *rc, int want_14v9, int want_24v)
{
    if (!rc->on[MK_RAIL_5V]) {
        return 1;
    }
    if (want_14v9 && !rc->on[MK_RAIL_14V9]) {
        return 1;
    }
    if (want_24v && !rc->on[MK_RAIL_24V]) {
        return 1;
    }
    return 0;
}
