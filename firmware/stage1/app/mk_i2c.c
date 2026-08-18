#include "mk_i2c.h"

#include <string.h>

#include "mk_i2c_drivers.h"      /* 드라이버 표 — Task 5 에서 채운다 */

/* J10·J11 = I2C3 · J12·J13 = I2C5 · J14·J15 = I2C1 (넷리스트 확인) */
static const uint8_t BUS_OF[MK_I2C_COUNT] = { 3u, 3u, 5u, 5u, 1u, 1u };

uint8_t mk_i2c_bus_of(unsigned port)
{
    return port < MK_I2C_COUNT ? BUS_OF[port] : 0u;
}

unsigned mk_i2c_connector_of(unsigned port)
{
    return port < MK_I2C_COUNT ? 10u + port : 0u;
}

/* ---- 상태기계 -------------------------------------------------------------
 *
 * 🔴 여기서 지키는 것은 "언제 두드리는가" 다. 칩이 무엇인지는 드라이버가
 *    알고, 이 층은 순서·주기·격리만 안다 (Task 4).
 */

/* 설정 키 "i2c1N.<suffix>" 를 손으로 만든다. app/ 은 snprintf 를 안 쓴다. */
static MkCfgItem *port_item(MkConfig *cfg, unsigned port, const char *suffix)
{
    char key[20];
    int n = 0;
    unsigned jack = mk_i2c_connector_of(port);
    key[n++] = 'i'; key[n++] = '2'; key[n++] = 'c';
    key[n++] = (char)('0' + jack / 10u);
    key[n++] = (char)('0' + jack % 10u);
    for (const char *q = suffix; *q; q++) { key[n++] = *q; }
    key[n] = '\0';
    return mk_cfg_find(cfg, key);
}

static uint32_t port_u(MkConfig *cfg, unsigned port, const char *suffix,
                       uint32_t fallback)
{
    MkCfgItem *it = port_item(cfg, port, suffix);
    return it != NULL ? it->cur.u : fallback;
}

static void push_out(MkI2c *i, unsigned connector_id, const char *quantity,
                     float value, int have_value, uint16_t status, int64_t t_ms)
{
    if (i->n_out >= MK_I2C_OUT_MAX) {
        return;                  /* 한 바퀴에 포트 하나라 여기 오지 않는다 */
    }
    MkI2cOut *o = &i->out[i->n_out++];
    o->connector_id = connector_id;
    o->quantity = quantity;
    o->value = value;
    o->have_value = have_value;
    o->status = status;
    o->t_ms = t_ms;
}

void mk_i2c_init(MkI2c *i, const MkI2cIo *io)
{
    memset(i, 0, sizeof *i);
    if (io != NULL) { i->io = *io; }
}

int mk_i2c_take(MkI2c *i, MkI2cOut *out)
{
    if (i->out_head >= i->n_out) {
        i->out_head = 0;
        i->n_out = 0;
        return 0;
    }
    *out = i->out[i->out_head++];
    return 1;
}

/* 한 포트를 한 걸음 나아가게 한다. 버스를 건드렸으면 1 을 돌려준다. */
static int step_port(MkI2c *i, MkConfig *cfg, unsigned p, int64_t now)
{
    MkI2cPort *st = &i->port[p];
    uint8_t bus = mk_i2c_bus_of(p);
    unsigned jack = mk_i2c_connector_of(p);

    int enabled = port_u(cfg, p, ".enabled", 0u) != 0u;
    uint8_t kind = (uint8_t)port_u(cfg, p, ".kind", 0u);
    uint8_t addr = (uint8_t)port_u(cfg, p, ".addr", 0u);

    /* 🔴 꺼졌거나 종류가 "없음" 이면 아무것도 안 한다 — 버스도, 레코드도.
     *    미연결은 정상 상태다 (설계 원칙 3, 규격 §7.5). */
    if (!enabled || kind == (uint8_t)MK_I2C_KIND_NONE) {
        st->state = MK_I2C_OFF;
        return 0;
    }

    /* 설정이 바뀌면 그 포트만 처음으로 되돌린다. */
    if (st->state != MK_I2C_OFF && (st->kind != kind || st->addr != addr)) {
        st->state = MK_I2C_OFF;
    }

    const MkI2cDriver *drv = mk_i2c_driver_for(kind);
    if (drv == NULL) {
        /* 🔴 카탈로그에는 있는데 드라이버가 없다. 조용히 빈칸으로 두지
         *    않는다 — 규격 §7.5 의 status=3. 주기마다 한 번씩만 말한다. */
        if (st->state != MK_I2C_FAULT ||
            now - st->last_read_ms >= (int64_t)port_u(cfg, p, ".period_ms", 200u)) {
            st->state = MK_I2C_FAULT;
            st->last_read_ms = now;
            push_out(i, jack, "", 0.0f, 0, 3u, now);
        }
        return 0;
    }

    uint8_t use_addr = addr != 0u ? addr : drv->default_addr;

    switch (st->state) {
    case MK_I2C_OFF:
        st->kind = kind;
        st->addr = addr;
        st->state = MK_I2C_START;
        st->step_ms = now;
        return 0;                /* 다음 바퀴에 시작 명령을 낸다 */

    case MK_I2C_START: {
        int rc = 0;
        if (drv->start != NULL) {
            rc = drv->start(&i->io, bus, use_addr);
        }
        st->step_ms = now;
        if (rc != 0) {
            /* 시작부터 대답이 없다. 알리고 다음 주기에 다시 시도한다. */
            st->state = MK_I2C_OFF;
            st->last_read_ms = now;
            push_out(i, jack, "", 0.0f, 0, rc == -1 ? 1u : 2u, now);
            return 1;
        }
        st->state = MK_I2C_WARMUP;
        return 1;
    }

    case MK_I2C_WARMUP:
        if (now - st->step_ms < (int64_t)drv->warmup_ms) {
            return 0;            /* 🔴 여기서 기다리지 않는다. 그냥 돌아간다 */
        }
        st->state = MK_I2C_READY;
        st->last_read_ms = now - (int64_t)drv->warmup_ms;   /* 곧 읽는다 */
        return 0;

    case MK_I2C_READY: {
        /* 🔴 실효 주기 = max(period_ms, warmup_ms). 변환보다 빨리 읽으면
         *    같은 값이 여러 줄 나가고 화면이 멈춘 값을 갱신처럼 보여 준다. */
        uint32_t period = port_u(cfg, p, ".period_ms", 200u);
        if (period < drv->warmup_ms) { period = drv->warmup_ms; }
        if (now - st->last_read_ms < (int64_t)period) {
            return 0;
        }
        st->last_read_ms = now;

        MkI2cValue v[MK_I2C_VALUES_MAX];
        int n = 0;
        int rc = drv->read(&i->io, bus, use_addr, v, &n);
        if (rc != 0) {
            push_out(i, jack, "", 0.0f, 0, rc == -1 ? 1u : 2u, now);
            return 1;
        }
        for (int k = 0; k < n && k < MK_I2C_VALUES_MAX; k++) {
            /* 🔴 타임스탬프는 읽기가 끝난 지금이다 (설계 원칙 2). */
            push_out(i, jack, v[k].quantity, v[k].value, 1, 0u, now);
        }
        return 1;
    }

    case MK_I2C_FAULT:
    default:
        st->state = MK_I2C_OFF;
        return 0;
    }
}

void mk_i2c_tick(MkI2c *i, MkConfig *cfg, int64_t now_ms)
{
    if (cfg == NULL) { return; }

    /* 🔴 한 바퀴에 포트 하나. 버스를 건드린 포트가 나오면 거기서 멈춘다.
     *    출발점을 매번 옮겨 한 포트가 나머지를 굶기지 않게 한다. */
    for (unsigned n = 0; n < MK_I2C_COUNT; n++) {
        unsigned p = (i->next_port + n) % MK_I2C_COUNT;
        int touched = step_port(i, cfg, p, now_ms);
        if (touched) {
            i->next_port = (p + 1u) % MK_I2C_COUNT;
            return;
        }
    }
    i->next_port = (i->next_port + 1u) % MK_I2C_COUNT;
}
