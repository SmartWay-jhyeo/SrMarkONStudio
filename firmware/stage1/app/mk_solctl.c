#include "mk_solctl.h"

/* 🔴 키는 여기 한 벌만 둔다. 카탈로그(mk_cfgtable.c)가 같은 이름을 쓰고,
 *    어긋나면 스위치가 화면에서만 움직인다. tests/test_sol.c 가 진짜
 *    카탈로그로 시험하는 이유다. */
static const char *const SOL_KEYS[MK_SOL_COUNT] = {
    "sol.j18", "sol.j19", "sol.j20"
};

void mk_solctl_init(MkSolCtl *sc, MkSolSet set, void *ctx)
{
    sc->set = set;
    sc->ctx = ctx;
    for (int c = 0; c < MK_SOL_COUNT; c++) {
        sc->on[c] = 0u;
    }
    sc->primed = 0u;
}

void mk_solctl_tick(MkSolCtl *sc, MkConfig *cfg)
{
    for (int c = 0; c < MK_SOL_COUNT; c++) {
        MkCfgItem *it = mk_cfg_find(cfg, SOL_KEYS[c]);

        /* 못 찾으면 끈 것으로 본다 — 실수했을 때 출력이 켜지면 안 된다. */
        uint8_t want = (it != NULL && it->cur.u) ? 1u : 0u;

        /* 🔴 첫 바퀴에는 값이 같아도 한 번 낸다. 이 층은 핀의 시작 상태를
         *    모른다 — 한 번 내보내야 설정표와 핀이 어긋난 채로 시작하는
         *    일이 없다. 그 뒤로는 바뀐 채널만 쓴다. */
        if (sc->primed && sc->on[c] == want) {
            continue;
        }
        sc->on[c] = want;
        if (sc->set != NULL) {
            sc->set(sc->ctx, (MkSolCh)c, (int)want);
        }
    }
    sc->primed = 1u;
}

int mk_solctl_is_on(const MkSolCtl *sc, MkSolCh ch)
{
    /* 🔴 `ch >= 0` 을 쓰지 않는다. MkSolCh 는 부호 없는 열거형이라 항상
     *    참이고, arm-none-eabi-gcc 가 -Wtype-limits 로 짚는다. 레일에서
     *    이미 같은 것을 밟았다 (mk_railctl_is_on). */
    return (ch < MK_SOL_COUNT) ? (int)(sc->on[ch] != 0u) : 0;
}
