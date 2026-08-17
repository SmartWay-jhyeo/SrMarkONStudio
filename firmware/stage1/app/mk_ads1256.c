#include "mk_ads1256.h"

#include <string.h>

/* 단일 종단 입력: PSEL = AINn, NSEL = AINCOM(8). 데이터시트 MUX 레지스터. */
#define AINCOM   0x08u

static void io_cs(MkAds *a, int low)
{
    if (a->io.cs) {
        a->io.cs(a->io.ctx, low);
    }
}

static void io_transfer(MkAds *a, size_t n)
{
    if (a->io.transfer) {
        a->io.transfer(a->io.ctx, a->tx, a->rx, n);
    }
}

static void io_delay(MkAds *a, uint32_t us)
{
    if (a->io.delay_us) {
        a->io.delay_us(a->io.ctx, us);
    }
}

void mk_ads_init(MkAds *a, const MkAdsIo *io,
                 uint8_t *tx, uint8_t *rx, size_t buf_cap,
                 int64_t drdy_timeout_ms)
{
    memset(a, 0, sizeof *a);
    if (io != NULL) {
        a->io = *io;
    }
    a->tx = tx;
    a->rx = rx;
    a->buf_cap = buf_cap;
    a->drdy_timeout_ms = drdy_timeout_ms;
    a->state = MK_ADS_IDLE;
    a->cur = -1;
    for (int i = 0; i < MK_ADS_CHANNELS; i++) {
        mk_queue_init(&a->ch[i].q, NULL, 0);
    }
}

void mk_ads_attach_queue(MkAds *a, int ch, MkSample *buf, uint16_t cap)
{
    if (ch < 0 || ch >= MK_ADS_CHANNELS) {
        return;
    }
    mk_queue_init(&a->ch[ch].q, buf, cap);
}

void mk_ads_configure(MkAds *a, int ch, int enabled, uint16_t period_ms,
                      int64_t now_ms)
{
    if (ch < 0 || ch >= MK_ADS_CHANNELS) {
        return;
    }
    MkAdsChannel *c = &a->ch[ch];
    uint8_t want = (uint8_t)(enabled && period_ms > 0u);

    /* 🔴 바뀐 것이 없으면 손대지 않는다.
     *
     *    설정은 슈퍼루프가 매 바퀴 이 함수로 밀어 넣는다 — GUI 에서 값을
     *    바꾼 것을 알아채는 다른 통로가 없기 때문이다. 그때마다
     *    next_due_ms 를 앞으로 밀면 예정이 영원히 도착하지 않고 채널이
     *    한 번도 읽히지 않는다. 같은 값이면 그냥 돌아간다. */
    if (c->enabled == want && c->period_ms == period_ms) {
        return;
    }

    c->enabled = want;
    c->period_ms = period_ms;
    /* 지금부터 한 주기 뒤에 처음 읽는다. 0 으로 두면 설정을 바꾼 순간
     * 모든 채널이 한꺼번에 몰린다. */
    c->next_due_ms = now_ms + (int64_t)period_ms;
}

/* 다음에 읽을 채널. 때가 된 것 중 **예정이 가장 오래된** 것을 고른다.
 *
 * 🔴 이것은 굶주림 방지가 아니다. 굶지 않는 것은 finish() 의 따라잡기
 *    포기 덕분이다 — 방금 읽은 채널은 next_due 가 앞으로 밀리므로 곧바로
 *    다시 이기지 못한다. 첨자 순서로 골라도 굶지는 않는다.
 *
 *    (처음엔 이 정렬이 굶주림을 막는다고 주석에 적었다가, 되돌림 검사에서
 *     정렬을 지워도 아무것도 안 깨지는 것을 보고 알았다.)
 *
 *    이 정렬이 실제로 하는 일은 **최악 지연을 줄이는 것**이다. 여러 채널이
 *    한꺼번에 밀렸을 때 가장 오래 기다린 것부터 처리하면 어느 한 채널만
 *    유독 늦어지는 일이 없다. 첨자 순서로 고르면 늘 뒤 채널이 더 기다린다. */
static int pick_next(const MkAds *a, int64_t now_ms)
{
    int best = -1;
    int64_t oldest = 0;
    for (int i = 0; i < MK_ADS_CHANNELS; i++) {
        const MkAdsChannel *c = &a->ch[i];
        if (!c->enabled || now_ms < c->next_due_ms) {
            continue;
        }
        if (best < 0 || c->next_due_ms < oldest) {
            best = i;
            oldest = c->next_due_ms;
        }
    }
    return best;
}

/* SETUP 의 한 걸음을 보낸다. 데이터시트의 "멀티플렉서 사이클링" 절차:
 *   MUX 갱신 -> SYNC -> WAKEUP -> (변환) -> RDATA */
static void send_setup_step(MkAds *a)
{
    if (a->buf_cap < 3u || a->tx == NULL) {
        return;
    }
    switch (a->setup_step) {
    case 0:                                  /* WREG MUX */
        a->tx[0] = (uint8_t)(MK_ADS_CMD_WREG | MK_ADS_REG_MUX);
        a->tx[1] = 0x00u;                    /* n-1 = 0 -> 1개 */
        a->tx[2] = (uint8_t)(((uint8_t)a->cur << 4) | AINCOM);
        io_transfer(a, 3u);
        break;
    case 1:
        a->tx[0] = MK_ADS_CMD_SYNC;
        io_transfer(a, 1u);
        break;
    default:
        /* 🔴 SYNC 뒤에는 t11(24 tCLKIN = 3.13us)을 쉬어야 다음 명령이
         *    받아들여진다. 붙여 보내면 SYNC 가 먹지 않아, 변환이 방금 고른
         *    채널이 아니라 이전 채널 것으로 남는다. */
        io_delay(a, MK_ADS_T11_SYNC_US);
        a->tx[0] = MK_ADS_CMD_WAKEUP;
        io_transfer(a, 1u);
        break;
    }
}

static void begin_channel(MkAds *a, int ch, int64_t now_ms)
{
    a->cur = ch;
    a->state = MK_ADS_SETUP;
    a->setup_step = 0u;
    a->started_ms = now_ms;
    io_cs(a, 1);
    send_setup_step(a);
}

/* 한 바퀴를 끝내고 쉬는 자리로 돌아간다. */
static void finish(MkAds *a, int64_t now_ms)
{
    MkAdsChannel *c = (a->cur >= 0) ? &a->ch[a->cur] : NULL;
    if (c != NULL) {
        /* 🔴 다음 예정을 `now + period` 가 아니라 `예정 + period` 로 민다.
         *    now 기준으로 하면 한 바퀴에 걸린 시간만큼 주기가 늘어나
         *    조금씩 뒤로 흐른다. 100 ms 주기가 실제로는 117 ms 가 된다.
         *
         *    단, 너무 밀렸으면 따라잡기를 포기하고 지금 기준으로 다시 잡는다.
         *    안 그러면 밀린 만큼 몰아서 읽으려 들며 큐를 터뜨린다. */
        c->next_due_ms += (int64_t)c->period_ms;
        if (c->next_due_ms <= now_ms) {
            c->next_due_ms = now_ms + (int64_t)c->period_ms;
        }
    }
    io_cs(a, 0);
    a->state = MK_ADS_IDLE;
    a->cur = -1;
}

void mk_ads_tick(MkAds *a, int64_t now_ms)
{
    if (a->state == MK_ADS_IDLE) {
        int ch = pick_next(a, now_ms);
        if (ch >= 0) {
            begin_channel(a, ch, now_ms);
        }
        return;
    }

    /* 🔴 DRDY 가 영영 안 올 수 있다 — 배선이 빠졌거나 칩이 죽었거나.
     *    그때 상태머신이 CONVERTING 에 갇히면 **다른 채널도 전부 멈춘다.**
     *    한 채널의 고장이 전체를 세우는 것이 정확히 설계 원칙이 막으려는
     *    것이므로, 시간이 지나면 그 채널만 접고 다음으로 넘어간다. */
    if (a->drdy_timeout_ms > 0
        && (now_ms - a->started_ms) > a->drdy_timeout_ms) {
        if (a->cur >= 0) {
            a->ch[a->cur].timeouts++;
        }
        finish(a, now_ms);
    }
}

void mk_ads_on_spi_done(MkAds *a, int64_t now_ms)
{
    switch (a->state) {
    case MK_ADS_SETUP:
        if (a->setup_step < 2u) {
            a->setup_step++;
            send_setup_step(a);
        } else {
            /* WAKEUP 까지 보냈다. 이제 변환이 돈다 — 여기서 기다리지
             * 않는다. DRDY 가 알려 줄 것이다. */
            a->state = MK_ADS_CONVERTING;
            a->started_ms = now_ms;
        }
        break;

    case MK_ADS_RDATA_SENT:
        /* 🔴 여기서 쉰다. RDATA 를 보낸 것과 데이터를 클럭하는 것 사이에
         *    t6(6.51us)가 필요하다 — 데이터시트 SBAS288K p.6.
         *
         *    한 번의 4바이트 전송으로 붙여 보내면 간격이 SCLK 한 주기(500
         *    kHz 에서 2us)뿐이라 3배 넘게 모자란다. 그러면 칩이 DOUT 을 아직
         *    안 실은 채로 클럭을 받고, ADS1256 의 7.68 MHz 는 우리와 비동기라
         *    얼마나 실렸는지가 매번 달라진다 — 값이 무작위로 튄다. 잡음처럼
         *    보이지만 잡음이 아니다 [실증 2026-08-17]. */
        io_delay(a, MK_ADS_T6_US);
        a->state = MK_ADS_READING;
        if (a->tx == NULL || a->buf_cap < 3u) {
            finish(a, now_ms);
            break;
        }
        a->tx[0] = 0u;
        a->tx[1] = 0u;
        a->tx[2] = 0u;
        io_transfer(a, 3u);
        break;

    case MK_ADS_READING: {
        /* 이제 rx[0..2] 가 그대로 24비트 2의 보수다 — 앞에 명령 바이트가
         * 섞이지 않는다. */
        int32_t code = 0;
        if (a->rx != NULL && a->buf_cap >= 3u) {
            code = ((int32_t)a->rx[0] << 16)
                 | ((int32_t)a->rx[1] << 8)
                 |  (int32_t)a->rx[2];
            if (code & 0x00800000) {         /* 부호 확장 */
                code |= (int32_t)0xFF000000;
            }
        }
        if (a->cur >= 0) {
            /* 🔴 큐에 넣는 시각은 **DRDY 시각**이지 지금이 아니다.
             *    지금은 SPI 전송이 끝난 시각이라 그만큼 늦다. 설계 원칙 2
             *    가 지키려는 것이 바로 이 차이다. */
            mk_queue_push(&a->ch[a->cur].q, a->started_ms, code);
        }
        finish(a, now_ms);
        break;
    }

    default:
        /* IDLE·CONVERTING 중의 완료는 우리가 시킨 것이 아니다. 무시한다 —
         * 여기서 상태를 바꾸면 늦게 도착한 인터럽트가 다음 채널의 전송을
         * 가로챈다. */
        break;
    }
}

void mk_ads_on_drdy(MkAds *a, int64_t now_ms)
{
    if (a->state != MK_ADS_CONVERTING) {
        /* 🔴 DRDY 는 우리가 안 시켜도 데이터율마다 계속 떨어진다.
         *    상태를 안 보고 반응하면 SETUP 도중에 읽기를 시작해 버린다. */
        return;
    }
    a->state = MK_ADS_RDATA_SENT;
    /* 🔴 획득 시각을 여기서 못박는다. 뒤로 갈수록 SPI·DMA·스케줄링 지연이
     *    섞이므로, 이 순간이 이 값이 가장 정확한 마지막 지점이다. */
    a->started_ms = now_ms;

    if (a->tx == NULL || a->buf_cap < 3u) {
        finish(a, now_ms);
        return;
    }
    /* 🔴 RDATA 만 홀로 보낸다. 데이터 3바이트를 이어 붙이면 그 사이에
     *    SCLK 이 쉬지 않아 t6 를 지킬 수 없다. */
    a->tx[0] = MK_ADS_CMD_RDATA;
    io_transfer(a, 1u);
}

MkAdsState mk_ads_state(const MkAds *a)         { return a->state; }
int        mk_ads_current_channel(const MkAds *a) { return a->cur; }

MkQueue *mk_ads_queue(MkAds *a, int ch)
{
    return (ch >= 0 && ch < MK_ADS_CHANNELS) ? &a->ch[ch].q : NULL;
}

int mk_ads_channel_enabled(const MkAds *a, int ch)
{
    return (ch >= 0 && ch < MK_ADS_CHANNELS) ? a->ch[ch].enabled : 0;
}

uint32_t mk_ads_timeouts(const MkAds *a, int ch)
{
    return (ch >= 0 && ch < MK_ADS_CHANNELS) ? a->ch[ch].timeouts : 0u;
}
