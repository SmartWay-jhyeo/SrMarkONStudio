#include "mk_linkbaud.h"

/* 설정표에서 이 키 하나만 본다. 규격 §4.2 가 이름 지은 계약이다. */
static const char LINK_BAUD_KEY[] = "link.baud";

uint32_t mk_linkbaud_brr(uint32_t kernel_hz, uint32_t baud)
{
    if (baud == 0u) {
        return 0u;                    /* 나눗셈이 안 된다 */
    }
    return MK_LINKBAUD_BRR(kernel_hz, baud);
}

uint32_t mk_linkbaud_err_ppm(uint32_t kernel_hz, uint32_t baud)
{
    uint32_t brr = mk_linkbaud_brr(kernel_hz, baud);
    if (brr == 0u) {
        return 0xFFFFFFFFu;           /* 낼 수 없다 — 최댓값으로 걸리게 둔다 */
    }
    uint64_t got  = (uint64_t)baud * brr;
    uint64_t want = (uint64_t)kernel_hz;
    uint64_t diff = (want > got) ? (want - got) : (got - want);
    return (uint32_t)((diff * 1000000u) / got);
}

int mk_linkbaud_reachable(uint32_t kernel_hz, uint32_t baud)
{
    uint32_t brr = mk_linkbaud_brr(kernel_hz, baud);
    /* 🔴 BRR 하한 16 은 오버샘플 16 의 물리적 제약이다(RM0468 §54.5.4:
     *    "USARTDIV must be greater than or equal to 16"). 오차만 보고
     *    통과시키면 아주 높은 속도가 조용히 깨진 파형을 낸다. */
    if (brr < 16u || brr > 65535u) {
        return 0;
    }
    return mk_linkbaud_err_ppm(kernel_hz, baud) <= MK_LINKBAUD_MAX_ERR_PPM;
}

void mk_linkbaud_init(MkLinkBaud *lb, uint32_t kernel_hz, uint32_t boot_baud)
{
    lb->kernel_hz = kernel_hz;
    /* 🔴 Flash 에서 읽은 값이 이 클럭으로 못 내는 값이면 기본값으로 간다.
     *    확정된 값만 저장되므로 정상 경로에서는 일어나지 않지만, 저장이
     *    깨졌을 때 보드가 벽돌이 되지 않게 하는 마지막 방어선이다 —
     *    그 실패는 굽기로만 풀린다. */
    if (!mk_linkbaud_reachable(kernel_hz, boot_baud)) {
        boot_baud = MK_LINKBAUD_DEFAULT;
    }
    lb->active = boot_baud;
    lb->confirmed = boot_baud;
    lb->pending = 0u;
    lb->deadline_ms = 0;
    lb->state = (uint8_t)MK_LINKBAUD_IDLE;
    lb->applied = 0u;
    lb->confirmed_count = 0u;
    lb->reverted = 0u;
}

MkLinkBaudResult mk_linkbaud_request(MkLinkBaud *lb, uint32_t baud)
{
    if (lb->state != (uint8_t)MK_LINKBAUD_IDLE) {
        /* 🔴 하나씩만 처리한다. 겹치면 무엇으로 되돌아가야 하는지가
         *    흐려지고, 되돌아갈 곳이 불분명한 안전장치는 안전장치가
         *    아니다. */
        return MK_LINKBAUD_ERR_STATE;
    }
    if (!mk_linkbaud_reachable(lb->kernel_hz, baud)) {
        return MK_LINKBAUD_ERR_RANGE;
    }
    if (baud == lb->active) {
        return MK_LINKBAUD_OK;        /* 같은 값 — 바꿀 것이 없다 */
    }
    lb->pending = baud;
    lb->state = (uint8_t)MK_LINKBAUD_ARMED;
    return MK_LINKBAUD_OK;
}

MkLinkBaudResult mk_linkbaud_confirm(MkLinkBaud *lb, uint32_t baud)
{
    if (lb->state != (uint8_t)MK_LINKBAUD_PENDING) {
        return MK_LINKBAUD_ERR_STATE;
    }
    if (baud != lb->pending) {
        /* 🔴 시한을 늘려 주지 않는다. 틀린 확인은 확인이 아니다 —
         *    여기서 시한을 연장하면 잘못 짠 호스트가 영원히 되돌림을
         *    미룰 수 있다. */
        return MK_LINKBAUD_ERR_RANGE;
    }
    lb->confirmed = lb->pending;
    lb->pending = 0u;
    lb->state = (uint8_t)MK_LINKBAUD_IDLE;
    lb->confirmed_count++;
    return MK_LINKBAUD_OK;
}

void mk_linkbaud_tick(MkLinkBaud *lb, MkConfig *cfg, int64_t now_ms,
                      MkLinkBaudApply apply, void *ctx)
{
    MkCfgItem *item = (cfg != NULL) ? mk_cfg_find(cfg, LINK_BAUD_KEY) : NULL;

    /* 1) 설정표가 바뀌었으면 요청으로 본다. $CFG,SET 은 cur 만 건드리고
     *    응답을 내보낸 뒤 돌아갔다 — 그 응답이 **옛 속도로** 나갈 시간이
     *    여기서 생긴다(규격 §4.2.2 규칙 1). */
    if (item != NULL && lb->state == (uint8_t)MK_LINKBAUD_IDLE
            && item->cur.u != lb->active) {
        (void)mk_linkbaud_request(lb, item->cur.u);
        /* 실패했으면 아래에서 cur 를 active 로 되돌려 놓는다 — 화면에
         * 못 내는 값이 떠 있게 두지 않는다. */
    }

    /* 2) 실제로 바꾼다. 여기서부터 시한이 흐른다. */
    if (lb->state == (uint8_t)MK_LINKBAUD_ARMED && apply != NULL) {
        lb->active = lb->pending;
        lb->state = (uint8_t)MK_LINKBAUD_PENDING;
        lb->deadline_ms = now_ms + MK_LINKBAUD_CONFIRM_MS;
        lb->applied++;
        apply(ctx, lb->active);
    }

    /* 3) 시한이 지났으면 스스로 돌아간다. 사람이 아무것도 안 해도 링크가
     *    살아나는 것 — 이것이 이 모듈의 존재 이유다. */
    if (lb->state == (uint8_t)MK_LINKBAUD_PENDING && apply != NULL
            && now_ms >= lb->deadline_ms) {
        lb->active = lb->confirmed;
        lb->pending = 0u;
        lb->state = (uint8_t)MK_LINKBAUD_IDLE;
        lb->reverted++;
        apply(ctx, lb->active);
    }

    /* 4) 설정표를 전선의 사실에 맞춘다.
     *
     * 🔴 되돌아간 뒤 이것을 안 하면 화면에는 새 속도가 떠 있는데 전선은
     *    옛 속도로 도는 상태가 남고, 다음 바퀴가 그 차이를 **새 요청**으로
     *    오해해 영원히 되돌림을 되풀이한다. */
    if (item != NULL) {
        item->cur.u = lb->active;
    }
}

uint32_t mk_linkbaud_active(const MkLinkBaud *lb)    { return lb->active; }
uint32_t mk_linkbaud_confirmed(const MkLinkBaud *lb) { return lb->confirmed; }
uint32_t mk_linkbaud_pending(const MkLinkBaud *lb)   { return lb->pending; }

int mk_linkbaud_is_pending(const MkLinkBaud *lb)
{
    return lb->state != (uint8_t)MK_LINKBAUD_IDLE;
}

int64_t mk_linkbaud_remaining_ms(const MkLinkBaud *lb, int64_t now_ms)
{
    if (lb->state != (uint8_t)MK_LINKBAUD_PENDING) {
        return -1;
    }
    int64_t left = lb->deadline_ms - now_ms;
    return (left > 0) ? left : 0;
}
