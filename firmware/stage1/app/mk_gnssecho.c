#include "mk_gnssecho.h"

/* 한 틱 상한 — RTK 모듈은 초당 수십 줄을 낼 수 있다(구 규격 §7.7 의 대역폭
 * 경고). 진단 스위치가 본선을 통째로 먹지 못하게 막는다. */
#define MK_GNSSECHO_MAX_LINES 16

int mk_gnssecho_tick(MkGnss *g, MkConfig *cfg, MkGnssEchoEmit emit, void *ctx)
{
    if (g == NULL || cfg == NULL || emit == NULL) { return 0; }
    MkCfgItem *it = mk_cfg_find(cfg, "gnss.echo");
    if (it == NULL || !it->cur.u) { return 0; }

    int sent = 0;
    char raw[MK_GNSS_LINE_MAX + 2];
    while (sent < MK_GNSSECHO_MAX_LINES &&
           mk_gnss_take_raw(g, raw, sizeof raw, NULL)) {
        char line[sizeof "$GNSSRAW," + sizeof raw];
        size_t n = 0;
        const char *p;
        for (p = "$GNSSRAW,"; *p != '\0'; p++) { line[n++] = *p; }
        for (p = raw; *p != '\0' && n + 2u < sizeof line; p++) {
            line[n++] = *p;
        }
        line[n++] = '\n';
        line[n] = '\0';
        emit(ctx, line, n);
        sent++;
    }
    return sent;
}
