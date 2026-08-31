#include "mk_solctl.h"

#include <string.h>

/* 🔴 극성 반전은 여기 **한 곳**에서만 한다. mk_solctl_prime() 과
 *    mk_solctl_tick() 둘 다 이 함수를 거쳐서만 raw -> state 를 만든다.
 *    두 곳에서 각자 뒤집으면 이중 반전으로 원래대로 돌아오고, 화면이
 *    모든 것을 반대로 말하게 된다(mk_solctl.h 상단 주석 참고).
 *
 *    raw LOW(0) = 옵토 신호 있음 = state 1(켜짐)
 *    raw HIGH(1) = 신호 없음     = state 0(꺼짐) */
static uint8_t flip_polarity(uint8_t raw)
{
    return raw ? 0u : 1u;
}

void mk_solctl_init(MkSolCtl *sc, MkSolRead read, void *ctx)
{
    memset(sc, 0, sizeof *sc);
    for (int c = 0; c < MK_SOL_COUNT; c++) {
        mk_queue_init(&sc->q[c], sc->q_buf[c], MK_SOL_QUEUE_CAP);
    }
    sc->read = read;
    sc->ctx  = ctx;
}

unsigned mk_sol_connector_of(MkSolCh ch)
{
    return (ch < MK_SOL_COUNT) ? 18u + (unsigned)ch : 0u;
}

void mk_solctl_on_edge(MkSolCtl *sc, MkSolCh ch, int raw_level, int64_t t_ms)
{
    if (ch >= MK_SOL_COUNT) {
        return;
    }
    /* 🔴 판단하지 않는다 — 값과 시각을 큐에 넣기만 한다. 가득 차 있으면
     *    mk_queue_push() 가 가장 오래된 것을 버리고 drops 를 센다. */
    mk_queue_push(&sc->q[ch], t_ms, raw_level ? 1 : 0);
}

void mk_solctl_prime(MkSolCtl *sc, MkSolCh ch, int raw_level, int64_t t_ms)
{
    if (ch >= MK_SOL_COUNT) {
        return;
    }
    uint8_t raw = raw_level ? 1u : 0u;
    sc->has_candidate[ch]   = 1u;
    sc->candidate[ch]       = raw;
    sc->candidate_t_ms[ch]  = t_ms;
    sc->confirmed_valid[ch] = 1u;
    sc->confirmed_state[ch] = flip_polarity(raw);
    /* 🔴 레코드를 내지 않는다 — out 에 아무것도 안 쌓는다. 초기값은
     *    `$STAT` 으로만 전해진다(규격 §7.6, mk_solctl.h 주석). */
}

void mk_solctl_tick(MkSolCtl *sc, MkConfig *cfg, int64_t now_ms)
{
    MkCfgItem *dit = mk_cfg_find(cfg, "sol.debounce_ms");
    /* 못 찾으면 카탈로그의 기본값(5)을 그대로 쓴다. */
    uint32_t debounce_ms = (dit != NULL) ? dit->cur.u : 5u;

    for (int c = 0; c < MK_SOL_COUNT; c++) {
        MkSample s;
        while (mk_queue_pop(&sc->q[c], &s)) {
            uint8_t raw = (uint8_t)(s.raw != 0);
            if (!sc->has_candidate[c] || raw != sc->candidate[c]) {
                /* 후보가 바뀌었다 — 안정 구간을 이 엣지부터 다시 잰다.
                 * 흔들림이 반복돼도 매번 여기로 와 시작점이 밀리므로,
                 * 디바운스 시간 안의 잡음은 결코 확정되지 않는다. */
                sc->has_candidate[c]  = 1u;
                sc->candidate[c]      = raw;
                sc->candidate_t_ms[c] = s.t_ms;
            }
        }

        /* 🔴 상태의 근거는 레벨이다 — 큐에서 나온 후보를 실제 핀과
         *    대조한다. 엣지 큐가 정확했으면(정상 경로) 아래 raw 는 이미
         *    위에서 잡은 candidate 와 같아 아무 일도 안 한다. 다르면
         *    엣지를 놓친 것이다 — PRIMASK 경합, 인터럽트 지연, 잡음으로
         *    씹힌 엣지가 전부 여기로 모인다. 놓친 시각은 알 수 없으니
         *    이 tick 의 now_ms 로 새 후보를 연다: 정밀도는 잃어도 다음
         *    안정 구간에서 반드시 회복한다(사용자 확정 2026-08-18). */
        if (sc->read != NULL) {
            uint8_t raw = (uint8_t)(sc->read(sc->ctx, (MkSolCh)c) != 0);
            if (!sc->has_candidate[c] || raw != sc->candidate[c]) {
                sc->has_candidate[c]  = 1u;
                sc->candidate[c]      = raw;
                sc->candidate_t_ms[c] = now_ms;
            }
        }

        if (!sc->has_candidate[c]) {
            continue;   /* 엣지도 폴링도 prime 도 아직 없다 — 볼 것이 없다 */
        }
        if ((now_ms - sc->candidate_t_ms[c]) < (int64_t)debounce_ms) {
            continue;   /* 아직 안정되지 않았다 */
        }

        uint8_t state = flip_polarity(sc->candidate[c]);
        if (sc->confirmed_valid[c] && sc->confirmed_state[c] == state) {
            continue;   /* 바뀐 것이 없다 — 레코드를 내지 않는다 */
        }
        sc->confirmed_valid[c] = 1u;
        sc->confirmed_state[c] = state;

        if (sc->n_out >= MK_SOL_COUNT) {
            /* 🔴 정상 경로로는 오지 않는다 — 채널마다 한 바퀴에 최대
             *    하나만 확정되므로 out 은 절대 안 넘친다. 그래도
             *    mk_queue.c 의 drops 와 같은 이유로 조용히 버리지 않고
             *    센다: 세지 않으면 유실이 없었던 것으로 보인다. */
            sc->out_dropped++;
            continue;
        }
        sc->out[sc->n_out].connector_id = mk_sol_connector_of((MkSolCh)c);
        sc->out[sc->n_out].state        = state;
        sc->out[sc->n_out].t_ms         = sc->candidate_t_ms[c];
        sc->n_out++;
    }
}

int mk_solctl_take(MkSolCtl *sc, MkSolOut *out)
{
    if (sc->out_head >= sc->n_out) {
        sc->out_head = 0;
        sc->n_out = 0;
        return 0;
    }
    *out = sc->out[sc->out_head++];
    return 1;
}

int mk_solctl_is_on(const MkSolCtl *sc, MkSolCh ch)
{
    /* `ch >= 0` 을 쓰지 않는다 — 부호 없는 열거는 항상 참이라
     * -Wtype-limits 에 걸린다(mk_railctl_is_on·mk_solctl_is_on 의 옛
     * 출력 버전과 같은 관례). */
    return (ch < MK_SOL_COUNT) ? (int)(sc->confirmed_valid[ch]
                                       && sc->confirmed_state[ch] != 0u)
                               : 0;
}
