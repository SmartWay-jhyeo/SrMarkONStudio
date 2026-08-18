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

/* 🔴 종류 → 양 (규격 §7.5.1). 드라이버 유무와 무관하게 항상 있다 — mk_i2c.h
 *    의 선언 주석이 이유를 적는다. 시뮬레이터의 I2C_QUANTITIES 와 같은
 *    표여야 하고, crosscheck_i2c_quantities.py 가 대조한다. */
int mk_i2c_kind_quantities(uint8_t kind, const char *out[MK_I2C_VALUES_MAX])
{
    switch ((MkI2cKind)kind) {
    case MK_I2C_KIND_LUX:
        out[0] = "lux";
        return 1;
    case MK_I2C_KIND_HUMID:
        out[0] = "temp";
        out[1] = "humidity";
        return 2;
    case MK_I2C_KIND_IR_TEMP:
        out[0] = "temp_object";
        return 1;
    case MK_I2C_KIND_WATER_TEMP:
        out[0] = "temp";
        return 1;
    case MK_I2C_KIND_NONE:
    default:
        return 0;               /* 모르는 종류·"없음" — 낼 것이 없다 */
    }
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
        /* 🔴 정상 경로로는 오지 않는다 — FAULT 는 버스를 안 건드려 한
         *    바퀴에 여럿이 겹칠 수 있다(mk_i2c.h 의 mk_i2c_tick 계약).
         *    조용히 버리지 않고 센다 — mk_queue.c 의 drops 와 같은 이유:
         *    세지 않으면 유실이 없었던 것으로 보인다. */
        i->dropped++;
        return;
    }
    MkI2cOut *o = &i->out[i->n_out++];
    o->connector_id = connector_id;
    o->quantity = quantity;
    o->value = value;
    o->have_value = have_value;
    o->status = status;
    o->t_ms = t_ms;
}

/* 🔴 [C1] 실패(1·2)·미지원(3) 을 알릴 때 이 함수를 쓴다. 종류가 내는
 *    양마다 한 줄씩(값은 null) 내보낸다 — quantity 가 없는 레코드는
 *    host/gui/screen.py 가 통째로 버리므로, 여기서 비워 보내면 화면에
 *    아무 흔적도 안 남는다(검토 지적 C1). 온습도면 두 줄이 나간다. */
static void push_status(MkI2c *i, unsigned connector_id, uint8_t kind,
                        uint16_t status, int64_t t_ms)
{
    const char *q[MK_I2C_VALUES_MAX];
    int n = mk_i2c_kind_quantities(kind, q);
    for (int k = 0; k < n; k++) {
        push_out(i, connector_id, q[k], 0.0f, 0, status, t_ms);
    }
}

/* 🔴 [검토 지적 Important, 2026-08-18] 재시도 하한.
 *
 *    카탈로그 하한(i2cN.period_ms 최소 10ms)을 그대로 재시도 주기로 쓰면
 *    안 꽂힌 포트 하나가 초당 100회 재시도 + 레코드를 낸다 — 여섯 포트면
 *    초당 ~600줄, 링크의 약 90% 다. 방금 고친 C2(START 실패를 매 바퀴
 *    재시도해 링크를 채운 결함)와 같은 결의 문제라, 성공 경로처럼 여기도
 *    하한을 둔다.
 *
 *    드라이버가 있으면(FAULT: 시작은 실패했지만 종류는 지원됨) 그
 *    드라이버의 warmup_ms 를 그대로 floor 로 쓴다 — READY 의
 *    max(period_ms, warmup_ms) 와 정확히 같은 규칙이다(§ 아래
 *    "실효 주기" 주석 참고). 재시작이 성공하면 어차피 그 주기로
 *    안정되므로, 실패 중이라고 더 급하게 두드릴 이유가 없다.
 *
 *    드라이버가 없으면(미지원 종류, 절대 회복 안 됨) warmup_ms 를 모른다.
 *    "대답 없는 포트를 성공 경로보다 자주 두드릴 이유가 없다"가 요지라,
 *    카탈로그의 기본 주기(i2cN.period_ms 기본값 200ms, mk_cfgtable.c) 를
 *    floor 로 쓴다 — 실사용 대표값이지 임의로 지어낸 수가 아니다. */
#define MK_I2C_UNSUPPORTED_RETRY_FLOOR_MS 200u

static uint32_t retry_period_ms(const MkI2cDriver *drv, uint32_t period)
{
    uint32_t floor_ms = drv != NULL ? drv->warmup_ms
                                     : MK_I2C_UNSUPPORTED_RETRY_FLOOR_MS;
    return period < floor_ms ? floor_ms : period;
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
    /* 🔴 FAULT 로 빠지는 길에도 반드시 기록한다. 여기서 빠뜨리면 위 되돌림
     *    검사가 st->kind(옛값) != kind(방금 읽은 값) 를 매 바퀴 참으로
     *    보고 state 를 OFF 로 되돌려, 아래 FAULT 가드의 "state != FAULT"
     *    가 항상 참이 되어 "주기마다 한 번" 이 지켜지지 않는다
     *    (검토 지적 2026-08-18 — 시험을 고치다 드러났다). */
    st->kind = kind;
    st->addr = addr;

    const MkI2cDriver *drv = mk_i2c_driver_for(kind);
    uint32_t period = port_u(cfg, p, ".period_ms", 200u);

    if (drv == NULL) {
        /* 🔴 카탈로그에는 있는데 드라이버가 없다. 절대 회복되지 않는다 —
         *    종류를 바꾸기 전까지는 재시도해 봐야 늘 이 길이다. 조용히
         *    빈칸으로 두지 않는다 — 규격 §7.5 의 status=3. 주기마다 한
         *    번씩만 말한다.
         *
         *    🔴 버스를 안 건드리므로 0(안 건드림)을 돌려준다 — 한 바퀴
         *    안에서 다른 포트로 순회가 이어진다. 여러 포트가 동시에 이
         *    길을 타면 한 바퀴에 여럿이 몰릴 수 있다 — mk_i2c.h 의
         *    MK_I2C_OUT_MAX 가 그 최악(포트 수 × 종류당 양)을 감당한다
         *    (검토 지적 I1). */
        uint32_t retry = retry_period_ms(NULL, period);
        if (st->state != MK_I2C_FAULT || now - st->last_read_ms >= (int64_t)retry) {
            st->state = MK_I2C_FAULT;
            st->last_read_ms = now;
            push_status(i, jack, kind, 3u, now);
        }
        return 0;
    }

    uint8_t use_addr = addr != 0u ? addr : drv->default_addr;

    switch (st->state) {
    case MK_I2C_OFF:
        /* kind·addr 는 위에서 이미 기록했다. */
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
            /* 🔴 [C2] 시작부터 대답이 없다고 바로 OFF 로 돌리지 않는다.
             *    OFF 는 가드가 없어 다음 바퀴에 곧장 재시도되고, 안 꽂힌
             *    포트 하나가 슈퍼루프 두 바퀴마다 레코드를 만든다(실측
             *    초당 ~450줄, 링크의 약 2/3 — 검토 지적 C2). FAULT 로
             *    보내 아래 케이스가 같은 period_ms 가드로 재시도하게
             *    한다. */
            st->state = MK_I2C_FAULT;
            st->last_read_ms = now;
            push_status(i, jack, kind, rc == -1 ? 1u : 2u, now);
            return 1;             /* 실제로 버스를 두드렸다(NACK 포함) */
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
        uint32_t eff_period = retry_period_ms(drv, period);
        if (now - st->last_read_ms < (int64_t)eff_period) {
            return 0;
        }
        st->last_read_ms = now;

        MkI2cValue v[MK_I2C_VALUES_MAX];
        int n = 0;
        int rc = drv->read(&i->io, bus, use_addr, v, &n);
        if (rc != 0) {
            push_status(i, jack, kind, rc == -1 ? 1u : 2u, now);
            /* 🔴 [I2] READY 에 머물며 read 만 다시 시도하지 않는다. BH1750
             *    은 전원이 끊기면 Power Down 으로 돌아가는데(데이터시트
             *    p.4) 아무도 0x01/0x10 을 다시 안 보내면, 재삽입 뒤 주소는
             *    ACK 하니 read 는 성공(status=0)하고 변환 안 된 값이
             *    나간다 — "그럴듯하게 틀린 값"(검토 지적 I2). OFF 로
             *    되돌려 start 부터 다시 하게 한다. 즉시 되돌리는 이유:
             *    폭주는 C2 의 FAULT 주기 가드가 막는다 — start 가 다시
             *    실패해도 period_ms 마다 한 번만 재시도된다. */
            st->state = MK_I2C_OFF;
            return 1;
        }
        for (int k = 0; k < n && k < MK_I2C_VALUES_MAX; k++) {
            /* 🔴 타임스탬프는 읽기가 끝난 지금이다 (설계 원칙 2). */
            push_out(i, jack, v[k].quantity, v[k].value, 1, 0u, now);
        }
        return 1;
    }

    case MK_I2C_FAULT: {
        /* 🔴 [C2] 여기 오는 것은 시작 실패뿐이다 — 드라이버 없음은 위
         *    drv==NULL 검사에서 이미 걸러져 이 switch 에 안 온다(그래서
         *    drv 를 안전하게 그대로 쓴다). max(period_ms, warmup_ms) 마다
         *    한 번 다시 시도한다 — period_ms 원값만 쓰면 카탈로그 하한
         *    10ms 에서 초당 100회 재시도로 폭주한다(검토 지적 Important,
         *    retry_period_ms() 주석 참고). 즉시 재시도해도 START 실패와
         *    같은 폭주가 난다. */
        uint32_t retry = retry_period_ms(drv, period);
        if (now - st->last_read_ms < (int64_t)retry) {
            return 0;
        }
        int rc = drv->start != NULL ? drv->start(&i->io, bus, use_addr) : 0;
        st->step_ms = now;
        st->last_read_ms = now;
        if (rc != 0) {
            push_status(i, jack, kind, rc == -1 ? 1u : 2u, now);
            return 1;
        }
        st->state = MK_I2C_WARMUP;
        return 1;
    }

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
