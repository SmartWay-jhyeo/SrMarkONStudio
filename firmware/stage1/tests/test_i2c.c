/* mk_i2c 단위 시험 — 보드도 센서도 필요 없다.
 *
 * 🔴 여기서 지키는 것은 "언제 두드리는가" 다. 칩이 무엇인지는 드라이버가
 *    알고, 이 층은 순서·주기·격리만 안다.
 */
#include <stdio.h>
#include <string.h>
#include "../app/mk_i2c.h"
#include "../app/mk_cfgtable.h"
#include "../app/mk_i2c_bh1750.h"
#include "../app/mk_i2c_mlx90614.h"
#include "../app/mk_i2c_am2320.h"

static int failures = 0;

#define CHECK(cond, msg) do {                                   \
    if (!(cond)) { printf("  FAIL %s\n", msg); failures++; }    \
    else         { printf("  ok   %s\n", msg); }                \
} while (0)

/* ---- 가짜 버스 ---------------------------------------------------------- */

/* 🔴 BH1750 이 붙은 뒤로는 라운드로빈 시험이 3000ms 를 돌려 포트당 여러
 *    번 읽으므로 64 로는 모자란다 — 넘치면 이후 기록이 조용히 끊겨
 *    "한 바퀴에 하나" 위반을 못 잡을 수 있다. */
#define MAX_EV 256
typedef struct {
    uint8_t bus[MAX_EV];
    uint8_t addr[MAX_EV];
    uint8_t first_tx[MAX_EV];
    size_t  ntx[MAX_EV];
    size_t  nrx[MAX_EV];
    int     n;
    int     ret;                 /* xfer 가 돌려줄 값 */
} Bus;

static Bus     BUS;
static MkI2c   I2C;
static MkConfig CFG;

static int fake_xfer(void *ctx, uint8_t bus, uint8_t addr,
                     const uint8_t *tx, size_t ntx, uint8_t *rx, size_t nrx)
{
    Bus *b = (Bus *)ctx;
    if (b->n < MAX_EV) {
        b->bus[b->n] = bus;
        b->addr[b->n] = addr;
        b->first_tx[b->n] = ntx ? tx[0] : 0u;
        b->ntx[b->n] = ntx;
        b->nrx[b->n] = nrx;
        b->n++;
    }
    for (size_t k = 0; k < nrx; k++) { rx[k] = (uint8_t)(k + 1u); }
    return b->ret;
}

/* 🔴 [AM2320] MkI2cIo.delay_us 자리를 채운다 — 없으면(NULL) AM2320
 *    드라이버가 대기를 건너뛰지만(방어적으로 NULL 검사를 해 뒀다), 여기
 *    시험은 실제로 딜레이가 불렸는지·얼마를 기다렸는지도 보고 싶으므로
 *    채워 둔다. 부르는 횟수는 세지 않는다 — round-robin 시험들은 그것을
 *    안 본다. */
static void fake_delay(void *ctx, uint32_t us)
{
    (void)ctx;
    (void)us;
}

static void set_u(const char *key, uint32_t v)
{
    MkCfgItem *it = mk_cfg_find(&CFG, key);
    if (it) { it->cur.u = v; }
}

static void setup(void)
{
    memset(&BUS, 0, sizeof BUS);
    mk_cfgtable_init(&CFG);
    MkI2cIo io = { fake_xfer, &BUS, fake_delay };
    mk_i2c_init(&I2C, &io);
}

/* 포트를 켜고 종류를 고른다 — 임의 종류. */
static void enable_port_kind(unsigned jack, uint32_t kind, uint32_t addr,
                             uint32_t period_ms)
{
    char key[20];
    int n;
    #define KEY(suffix) do {                                        \
        n = 0;                                                      \
        key[n++]='i'; key[n++]='2'; key[n++]='c';                   \
        key[n++]=(char)('0'+jack/10u); key[n++]=(char)('0'+jack%10u);\
        for (const char *q = (suffix); *q; q++) { key[n++] = *q; }  \
        key[n] = '\0';                                              \
    } while (0)
    KEY(".enabled");   set_u(key, 1u);
    KEY(".kind");      set_u(key, kind);
    KEY(".addr");      set_u(key, addr);
    KEY(".period_ms"); set_u(key, period_ms);
    #undef KEY
}

/* 포트를 켜고 종류를 고른다. 조도 = MK_I2C_KIND_LUX */
static void enable_lux_port(unsigned jack, uint32_t addr, uint32_t period_ms)
{
    enable_port_kind(jack, (uint32_t)MK_I2C_KIND_LUX, addr, period_ms);
}

/* ---- 시험 ---------------------------------------------------------------- */

/* 🔴 꺼진 포트는 버스를 아예 안 건드린다. 안 꽂힌 센서를 두드리면 매 주기
 *    NACK 이 나고, 그것을 오류로 세면 "미연결은 정상" 이라는 원칙이 깨진다. */
static void test_disabled_ports_never_touch_the_bus(void)
{
    setup();
    for (int64_t t = 0; t < 2000; t += 10) { mk_i2c_tick(&I2C, &CFG, t); }
    CHECK(BUS.n == 0, "꺼진 포트는 버스를 안 건드린다");
}

/* 🔴 한 바퀴에 포트 하나. 여섯이 한 바퀴에 다 돌면 최악에 전송이 여섯 번
 *    겹쳐 슈퍼루프가 길어진다.
 *
 * 🔴 [Task 5 에서 이를 세웠다] 예전 판은 tick 을 딱 한 번만 불렀다 —
 *    첫 tick 에서는 여섯 포트가 전부 OFF→START 로만 넘어가고(버스를
 *    안 건드리는 전이라 계속 순회한다) 아무도 버스를 건드리지 않으므로
 *    `BUS.n <= 1` 은 드라이버 유무와 무관하게 항상 참이었다(검토 지적
 *    2026-08-18). BH1750 이 실제로 붙은 지금은 시간을 충분히 돌려서 본다.
 *
 * 🔴 "전송 횟수 <= 1" 로는 못 잰다. BH1750 의 start() 는 Power On·모드
 *    선택을 STOP 을 사이에 두고 **두 번** 보낸다(§브리프 계획 수정 1) —
 *    포트 하나가 START 를 마치는 한 바퀴에도 xfer 는 2번 잡힌다. 그래서
 *    포트마다 **서로 다른 주소**를 설정해 두고, 한 tick 호출 동안 늘어난
 *    구간의 주소가 전부 같은지를 본다 — 다르면 두 포트가 한 바퀴에
 *    섞였다는 뜻이다. */
static void test_one_port_per_tick(void)
{
    setup();
    unsigned idx = 0;
    for (unsigned jack = 10u; jack <= 15u; jack++) {
        enable_lux_port(jack, 0x20u + idx, 200u);   /* 포트마다 다른 주소 */
        idx++;
    }

    int mixed_ports_in_one_tick = 0;
    for (int64_t t = 0; t < 3000; t += 10) {
        int before = BUS.n;
        mk_i2c_tick(&I2C, &CFG, t);
        int after = BUS.n;
        if (after > before) {
            uint8_t first_addr = BUS.addr[before];
            for (int k = before; k < after; k++) {
                if (BUS.addr[k] != first_addr) { mixed_ports_in_one_tick = 1; }
            }
        }
    }

    CHECK(!mixed_ports_in_one_tick,
          "한 바퀴에 두드리는 포트는 하나뿐이다 (주소가 안 섞인다)");
    CHECK(BUS.n > 6, "그러고도 실제로 여러 번 두드린다 — 이가 있는 시험");
}

/* 🔴 드라이버가 없는 종류는 status=3 이다. 아무것도 안 보내면 값이 왜
 *    없는지 화면 어디에도 답이 없다.
 *
 * 🔴 [검토 지적 2026-08-18] 시험 끝에 딱 한 번만 확인하면, "주기마다
 *    한 번만 알리는가"를 놓친다 — step_port() 의 시간 가드를 통째로
 *    지워도(매 바퀴 알림) 마지막 값만 보면 그대로 통과했다(중간의 잘못된
 *    반복은 안 보고 마지막 한 개만 봤으므로). 그래서 **매 바퀴 뒤**
 *    알림이 새로 왔는지를 보고 그 횟수를 세어, 400ms/주기 200ms 에서
 *    나와야 하는 트리거 횟수(2 — t=0 즉시 한 번 + t=200 에 한 번)를
 *    못박는다.
 *
 * 🔴 [2026-08-19] 예전엔 out[] 배출 큐를 매 바퀴 mk_i2c_take() 로 비우며
 *    셌다. 그 큐가 없어진 뒤로는 mk_i2c_last() 의 t_ms 가 바뀔 때마다
 *    "새로 한 번 알렸다"로 센다 — 같은 "매 바퀴 확인한다"는 취지를
 *    last() 로 옮긴 것뿐, 위 교훈(끝에 한 번만 보면 안 된다)은 그대로다.
 *
 * 🔴 [AM2320·MLX90614 추가] 예전에는 여기서 IR_TEMP 를 "드라이버 없음"의
 *    본보기로 썼다. 이제 IR_TEMP(MLX90614) 도 HUMID(AM2320) 도 드라이버가
 *    있으므로, 카탈로그에서 아직 드라이버가 없는 유일한 종류
 *    WATER_TEMP(방수 온도, 양 하나)로 바꾼다 — "양 둘 인 종류가 미지원일
 *    때 두 줄이 나가는가"는 이제 실제 카탈로그로는 재현할 수 없다(양이
 *    둘인 종류는 온습도뿐인데 그것도 이제 드라이버가 있다). 그 자리는
 *    아래 AM2320 절의 test_am2320_real_read_failure_emits_both_
 *    quantities_as_status_via_the_state_machine 가 "미지원 경로"가 아니라
 *    "진짜 드라이버가 실패하는 경로"로 대신 메운다 — 실제로 더 현실적인
 *    시나리오다(배선된 AM2320 이 응답하지 않는 경우). */
static void test_unsupported_kind_reports_status_three(void)
{
    setup();
    enable_lux_port(10u, 0x23u, 200u);
    MkCfgItem *k = mk_cfg_find(&CFG, "i2c10.kind");
    /* WATER_TEMP 는 카탈로그엔 있지만 이 라운드에도 드라이버가 없으므로
     * 여전히 FAULT(status=3) 경로를 탄다. */
    k->cur.u = (uint32_t)MK_I2C_KIND_WATER_TEMP;

    /* 🔴 [2026-08-19] out[]/mk_i2c_take() 가 없어져 "몇 번 알렸는가"를
     *    큐에서 셀 수 없다 — 대신 mk_i2c_last() 가 매번 t_ms 를 갱신하는
     *    것을 이용해, 바뀔 때마다 "새로 한 번 알렸다"로 센다. */
    int n_records = 0;
    int all_status_three = 1;
    int all_quantity_temp = 1;
    int64_t seen_t_ms = -1;
    for (int64_t t = 0; t < 400; t += 10) {
        mk_i2c_tick(&I2C, &CFG, t);
        MkI2cOut out;
        if (mk_i2c_last(&I2C, 0u, 0u, &out) == 1 && out.t_ms != seen_t_ms) {
            seen_t_ms = out.t_ms;
            n_records++;
            if (out.status != 3u) { all_status_three = 0; }
            if (out.quantity == NULL || strcmp(out.quantity, "temp") != 0) {
                all_quantity_temp = 0;
            }
        }
    }

    CHECK(n_records == 2, "지원 안 하는 종류는 주기마다 한 번만 알린다 (400ms/200ms=2)");
    CHECK(all_status_three, "status=3");
    CHECK(all_quantity_temp,
          "quantity 가 실린다 — 없으면 host/gui/screen.py 가 레코드를 버린다 (C1)");
    CHECK(BUS.n == 0, "지원 안 하면 버스를 두드리지 않는다");
}

/* 🔴 [C1] 종류→양 표를 직접 확인한다. 각 종류가 규격 §7.5.1 표대로다. */
static void test_kind_quantities_table_matches_the_spec(void)
{
    const char *q[MK_I2C_VALUES_MAX];
    int n;

    n = mk_i2c_kind_quantities((uint8_t)MK_I2C_KIND_NONE, q);
    CHECK(n == 0, "없음 은 양이 없다");

    n = mk_i2c_kind_quantities((uint8_t)MK_I2C_KIND_LUX, q);
    CHECK(n == 1 && strcmp(q[0], "lux") == 0, "조도 = lux 하나");

    n = mk_i2c_kind_quantities((uint8_t)MK_I2C_KIND_HUMID, q);
    CHECK(n == 2 && strcmp(q[0], "temp") == 0 && strcmp(q[1], "humidity") == 0,
          "온습도 = temp, humidity 둘 (값이 둘인 유일한 종류)");

    n = mk_i2c_kind_quantities((uint8_t)MK_I2C_KIND_IR_TEMP, q);
    CHECK(n == 1 && strcmp(q[0], "temp_object") == 0, "적외 온도 = temp_object 하나");

    n = mk_i2c_kind_quantities((uint8_t)MK_I2C_KIND_WATER_TEMP, q);
    CHECK(n == 1 && strcmp(q[0], "temp") == 0, "방수 온도 = temp 하나");
}

/* 🔴 [I1, 2026-08-19 갱신] 여러 포트가 같은 바퀴에 값을 내도 서로 안
 *    덮어쓴다.
 *
 *    FAULT(드라이버 없음)는 버스를 안 건드려 순회가 안 멈춘다 — 포트
 *    여섯을 전부 같은 미지원 종류로 켜 두면 부팅 직후(모든
 *    last_read_ms=0) 한 바퀴 안에서 여섯 포트가 동시에 gate 를 통과한다.
 *
 *    예전에는 이것이 배출 큐(out[], MK_I2C_OUT_MAX 칸) 오버플로 시험이었다
 *    — 여섯 포트가 한 큐를 다투면 자리가 모자랄 수 있었다. 2026-08-19 에
 *    그 큐를 걷어내고 포트·슬롯마다 고정 자리(last[][])로 바꾸면서
 *    "넘친다"는 개념 자체가 없어졌다 — 이제 이 시험이 지키는 것은
 *    "포트 여섯이 같은 바퀴에 값을 내도 각자의 슬롯에 맞게 들어가고 서로
 *    섞이지 않는다"다. */
static void test_many_faulted_ports_in_one_tick_all_get_recorded(void)
{
    setup();
    for (unsigned jack = 10u; jack <= 15u; jack++) {
        enable_port_kind(jack, (uint32_t)MK_I2C_KIND_WATER_TEMP, 0x30u + jack, 1000u);
    }

    mk_i2c_tick(&I2C, &CFG, 0);      /* 여섯 포트가 전부 처음 트리거된다 */

    int n = 0;
    for (unsigned p = 0; p < MK_I2C_COUNT; p++) {
        MkI2cOut out;
        if (mk_i2c_last(&I2C, p, 0u, &out) == 1) {
            n++;
            CHECK(out.connector_id == mk_i2c_connector_of(p),
                  "제 포트의 커넥터 번호로 들어간다 — 다른 포트 것으로 안 섞인다");
            CHECK(out.status == 3u, "미지원 status=3");
            CHECK(out.quantity != NULL && strcmp(out.quantity, "temp") == 0,
                  "quantity 는 방수 온도의 temp");
        }
    }

    CHECK(n == (int)MK_I2C_COUNT,
          "포트 여섯 전부 같은 바퀴에 값을 냈고, 전부 읽힌다");
    CHECK(BUS.n == 0, "드라이버가 없으니 버스는 안 건드린다");
}

/* 🔴 [검토 지적 Important, 2026-08-18] 재시도 하한 — 미지원 종류(드라이버
 *    없음, drv==NULL 경로).
 *
 *    period_ms 를 카탈로그 하한(10ms)에 두고 안 꽂힌 포트를 돌렸을 때,
 *    재시도가 10ms 마다가 아니라 MK_I2C_UNSUPPORTED_RETRY_FLOOR_MS(200ms)
 *    마다여야 한다. 2000ms 창에서: t=0(즉시 첫 알림) 이후 200ms 마다
 *    한 번씩 — 0,200,400,...,1800 = 10회. 하한이 없으면(원값 10ms) 이
 *    창에서 199회가 나온다(검토가 지적한 "초당 100회"와 같은 크기).
 *
 *    되돌림 검사: retry_period_ms() 안의 MK_I2C_UNSUPPORTED_RETRY_FLOOR_MS
 *    분기를 지우고(그냥 period 를 돌려주게) 이 시험이 10 이 아니라 199 로
 *    깨지는 것을 확인한 뒤 되돌렸다. */
static void test_unsupported_kind_retry_has_a_floor_even_at_min_period(void)
{
    setup();
    /* IR_TEMP(MLX90614) 는 이제 드라이버가 있다 — 미지원 종류는
     * WATER_TEMP 로 시험한다. */
    enable_port_kind(10u, (uint32_t)MK_I2C_KIND_WATER_TEMP, 0x50u, 10u);  /* period_ms = 카탈로그 하한 */

    /* out[]/mk_i2c_take() 가 없어져 "몇 번 알렸는가"를 mk_i2c_last() 의
     * t_ms 변화로 센다(위 test_unsupported_kind_reports_status_three 와
     * 같은 요령). */
    int n_events = 0;
    int64_t seen_t_ms = -1;
    for (int64_t t = 0; t < 2000; t += 10) {
        mk_i2c_tick(&I2C, &CFG, t);
        MkI2cOut out;
        if (mk_i2c_last(&I2C, 0u, 0u, &out) == 1 && out.t_ms != seen_t_ms) {
            seen_t_ms = out.t_ms;
            CHECK(out.status == 3u, "미지원은 늘 status=3");
            n_events++;
        }
    }
    CHECK(n_events == 10,
          "period_ms=10 이어도 재시도는 200ms 하한을 따른다 (2000ms/200ms=10, "
          "하한이 없으면 199)");
}

/* 🔴 [검토 지적 Important, 2026-08-18] 재시도 하한 — 드라이버는 있는데
 *    시작이 실패하는 경우(FAULT, C2 경로). 이것이 사용자가 든 실제 예다
 *    — 지원하는 종류를 골라 놓고 센서를 안 꽂으면 START 가 NACK 으로
 *    실패해 이 길을 탄다.
 *
 *    period_ms 를 하한(10ms)에 두면, 재시도는 이제 drv->warmup_ms
 *    (BH1750=180ms) 를 따라야 한다 — READY 의 max(period_ms, warmup_ms)
 *    와 같은 규칙. 2000ms 창에서: 첫 시도 t=10(OFF→START 가 한 바퀴,
 *    START 시도가 다음 바퀴) 이후 180ms 마다 한 번씩 —
 *    10,190,370,...,1990 = 12회. 하한이 없으면(원값 10ms) 이 창에서
 *    199회에 가깝게 나온다.
 *
 *    되돌림 검사: MK_I2C_FAULT 케이스의 retry_period_ms(drv, period) 를
 *    period 로 되돌리면 12 가 아니라 199 근처로 깨지는 것을 확인한 뒤
 *    되돌렸다. */
static void test_fault_retry_has_a_floor_even_at_min_period(void)
{
    setup();
    enable_lux_port(10u, 0x23u, 10u);   /* period_ms = 카탈로그 하한 */
    BUS.ret = -1;                       /* start 가 항상 NACK */

    /* out[]/mk_i2c_take() 가 없어져 t_ms 변화로 "몇 번 알렸는가"를 센다
     * (위 미지원 종류 시험과 같은 요령). */
    int n_events = 0;
    int64_t seen_t_ms = -1;
    for (int64_t t = 0; t < 2000; t += 10) {
        mk_i2c_tick(&I2C, &CFG, t);
        MkI2cOut out;
        if (mk_i2c_last(&I2C, 0u, 0u, &out) == 1 && out.t_ms != seen_t_ms) {
            seen_t_ms = out.t_ms;
            CHECK(out.status == 1u, "NACK 은 status=1");
            n_events++;
        }
    }
    CHECK(n_events == 12,
          "period_ms=10 이어도 재시도는 warmup_ms(180ms) 하한을 따른다 "
          "(t=10 부터 180ms 마다, 2000ms 창에서 12회)");
}

/* ---- BH1750 드라이버 ------------------------------------------------------
 *
 * 🔴 상태기계를 거치지 않고 드라이버 구조체(MK_I2C_BH1750)를 직접 부른다.
 *    이 층이 못 박는 것은 "언제 두드리는가"(위)가 아니라 "무엇을
 *    보내고 어떻게 읽는가"다 — 드라이버 자체를 시험한다.
 */

/* 🔴 환산 상수를 시험이 손으로 다시 적지 않는다. 구현과 시험이 같은 오해를
 *    나눠 가지면 둘이 늘 일치하므로 영원히 통과한다 — 전류 환산이 정확히
 *    절반으로 틀렸는데 시험이 못 잡았던 것이 그것이다. */
static void test_bh1750_conversion_uses_the_implementation_constant(void)
{
    CHECK(mk_bh1750_lux(0u) == 0.0f, "0 은 0 lx");

    /* 1.2 로 나눈다는 사실 자체는 여기서 한 번만 못 박는다 */
    float one = mk_bh1750_lux(12u);
    CHECK(one > 9.9f && one < 10.1f, "raw 12 = 10 lx (raw / 1.2)");

    float full = mk_bh1750_lux(0xFFFFu);
    CHECK(full > 54611.0f && full < 54613.0f, "만재 65535 → 약 54612 lx");
}

/* 🔴 바이트 순서를 못 박는다. BH1750 은 MSB 먼저이고 MLX90614 는 LSB
 *    먼저다 — 순서가 뒤집혀도 값은 나오므로 눈으로는 못 가린다. */
static void test_bh1750_reads_two_bytes_msb_first(void)
{
    setup();
    /* fake_xfer 는 rx 를 1,2,... 로 채운다 → MSB=1 LSB=2 → raw=0x0102 */
    MkI2cIo io = { fake_xfer, &BUS };
    MkI2cValue v[MK_I2C_VALUES_MAX];
    int n = 0;
    int rc = MK_I2C_BH1750.read(&io, 3u, 0x23u, v, &n);

    CHECK(rc == 0, "읽기 성공");
    CHECK(n == 1, "값은 하나 (조도)");
    CHECK(n == 1 && strcmp(v[0].quantity, "lux") == 0, "quantity 는 lux");
    /* 🔴 258/1.2 는 수학적으로 정확히 215 지만, float32 로는 1.2 자체가
     *    정확히 표현되지 않아 214.99998... 로 내려온다 — 경계값 215.0f
     *    를 하한으로 쓰면 올바른 구현이 실패한다(실측 확인). 214.9 로
     *    낮춰 정밀도 오차를 흡수하되, 바이트 순서가 뒤집힌 427.5 는
     *    여전히 걸러낸다. */
    CHECK(n == 1 && v[0].value > 214.9f && v[0].value < 216.0f,
          "0x0102 = 258 → 215 lx (MSB 먼저)");
    CHECK(BUS.n == 1 && BUS.ntx[0] == 0u && BUS.nrx[0] == 2u,
          "명령 없이 2바이트만 읽는다");
}

/* 🔴 [C1] 드라이버가 내는 quantity 와 종류→양 표가 어긋나면 화면이 못
 *    그린다 — 표가 "lux"를 기대하는데 드라이버가 다른 문자열을 내면
 *    (오타 등) 정상값도 host/gui/screen.py 가 못 찾는다. */
static void test_bh1750_quantity_matches_the_kind_table(void)
{
    const char *q[MK_I2C_VALUES_MAX];
    int nq = mk_i2c_kind_quantities((uint8_t)MK_I2C_KIND_LUX, q);
    CHECK(nq == 1, "조도 종류 표는 양이 하나");

    memset(&BUS, 0, sizeof BUS);
    MkI2cIo io = { fake_xfer, &BUS };
    MkI2cValue v[MK_I2C_VALUES_MAX];
    int n = 0;
    MK_I2C_BH1750.read(&io, 3u, 0x23u, v, &n);
    CHECK(n == 1 && nq == 1 && strcmp(v[0].quantity, q[0]) == 0,
          "BH1750 이 내는 quantity 가 종류 표와 같다 (lux)");
}

/* 🔴 전원을 먼저 켠다. 칩은 전원 인가 직후 Power Down 이라(p.4) 모드 명령만
 *    보내면 받지 않을 수 있다. 순서가 뒤바뀌어도 "안 켜진다" 로만 보인다. */
static void test_bh1750_start_powers_on_before_selecting_the_mode(void)
{
    setup();
    MkI2cIo io = { fake_xfer, &BUS };
    int rc = MK_I2C_BH1750.start(&io, 3u, 0x23u);

    CHECK(rc == 0, "시작 성공");
    CHECK(BUS.n == 2, "명령을 두 번 나눠 보낸다 (STOP 이 사이에 든다)");
    CHECK(BUS.n == 2 && BUS.first_tx[0] == 0x01u, "먼저 Power On 0x01");
    CHECK(BUS.n == 2 && BUS.first_tx[1] == 0x10u, "그다음 연속 고해상도 0x10");
    for (int k = 0; k < BUS.n; k++) {
        CHECK(BUS.ntx[k] == 1u && BUS.nrx[k] == 0u, "각각 1바이트 쓰기");
    }
    /* 🔴 180 이다. 120(typ)이 아니라 첫 측정의 max 를 기다린다 (p.7). */
    CHECK(MK_I2C_BH1750.warmup_ms == 180u, "변환 대기 180 ms");
    CHECK(MK_I2C_BH1750.default_addr == 0x23u, "기본 주소 0x23");
}

/* 🔴 전원 켜기가 실패하면 모드를 보내지 않는다. 대답 없는 버스에 계속
 *    쓰면 실패가 어디서 났는지 흐려진다. */
static void test_bh1750_start_stops_if_power_on_fails(void)
{
    setup();
    BUS.ret = -1;
    MkI2cIo io = { fake_xfer, &BUS };
    int rc = MK_I2C_BH1750.start(&io, 3u, 0x23u);

    CHECK(rc == -1, "실패를 그대로 돌려준다");
    CHECK(BUS.n == 1, "두 번째 명령을 보내지 않는다");
}

/* ---- MLX90614 드라이버 ----------------------------------------------------
 *
 * 🔴 예제 값은 전부 docs/datasheet/MLX90614.pdf 원문에서 가져왔다 —
 *    Figure 6(p.20, PEC=0x30)과 4.1.8.2 절의 예제(p.30, 0x3AF7→28.75°C)다.
 *    시험이 손으로 CRC 를 다시 계산하지 않는다 — Python 으로 미리
 *    검증해 둔 상수를 그대로 못 박는다(주석에 계산 방법을 남긴다).
 */

/* 🔴 MLX90614 전용 가짜 버스. 공용 fake_xfer 는 rx 를 1,2,3... 으로만
 *    채워 PEC 가 맞는 응답을 표현할 수 없다 — 이 칩만 따로 둔다
 *    (test_telem.c 의 fake_xfer_ok/fake_xfer_nak 와 같은 관례). */
typedef struct {
    int     n;
    size_t  ntx[4];
    size_t  nrx[4];
    uint8_t first_tx[4];
    uint8_t resp[3];             /* LSB, MSB, PEC — read() 가 돌려줄 3바이트 */
    int     ret;
} MlxBus;

static MlxBus MLX;

static int mlx_fake_xfer(void *ctx, uint8_t bus, uint8_t addr,
                         const uint8_t *tx, size_t ntx, uint8_t *rx, size_t nrx)
{
    (void)bus; (void)addr;
    MlxBus *b = (MlxBus *)ctx;
    if (b->n < 4) {
        b->ntx[b->n] = ntx;
        b->nrx[b->n] = nrx;
        b->first_tx[b->n] = ntx ? tx[0] : 0u;
        b->n++;
    }
    if (b->ret == 0) {
        for (size_t k = 0; k < nrx && k < sizeof b->resp; k++) { rx[k] = b->resp[k]; }
    }
    return b->ret;
}

/* PEC 함수 자체를 데이터시트 Figure 6 예제로 직접 검증한다: SA=0x5A,
 * RAM 0x07(=cmd), result=0x3AD2(LSB=0xD2, MSB=0x3A), PEC=0x30. */
static void test_mlx90614_pec_matches_the_datasheet_example(void)
{
    uint8_t pec = mk_mlx90614_pec(0x5Au, 0x07u, 0xD2u, 0x3Au);
    CHECK(pec == 0x30u, "PEC(SA=0x5A,cmd=0x07,LSB=0xD2,MSB=0x3A) == 0x30 (Figure 6)");
}

/* 환산 상수를 시험이 손으로 다시 적지 않는다 — 구현 함수를 직접 빌려
 * 쓰고, 기대값은 데이터시트 p.30 의 예제로 못 박는다. */
static void test_mlx90614_conversion_matches_the_datasheet_examples(void)
{
    float t1 = mk_mlx90614_temp_c(0x27ADu);
    CHECK(t1 > -70.02f && t1 < -70.00f, "0x27AD → -70.01°C (p.30 예제 1)");

    float t2 = mk_mlx90614_temp_c(0x3AF7u);
    CHECK(t2 > 28.74f && t2 < 28.76f, "0x3AF7 → 28.75°C (p.30 예제 3)");

    float t3 = mk_mlx90614_temp_c(0x7FFFu);
    CHECK(t3 > 382.18f && t3 < 382.20f, "0x7FFF → 382.19°C (최댓값, p.30 예제 5)");
}

/* read() 가 명령을 정확히 내고(cmd=0x07, ntx=1,nrx=3) PEC 가 맞는 값을
 * 받아들이는지. 데이터시트 p.30 예제 3(0x3AF7=28.75°C)을 그대로 쓴다 —
 * PEC 는 위에서 미리 python 으로 검증한 0xDF. */
static void test_mlx90614_read_accepts_a_pec_valid_frame(void)
{
    memset(&MLX, 0, sizeof MLX);
    MLX.resp[0] = 0xF7u; MLX.resp[1] = 0x3Au; MLX.resp[2] = 0xDFu;   /* LSB,MSB,PEC */
    MkI2cIo io = { mlx_fake_xfer, &MLX };
    MkI2cValue v[MK_I2C_VALUES_MAX];
    int n = 0;
    int rc = MK_I2C_MLX90614.read(&io, 3u, 0x5Au, v, &n);

    CHECK(rc == 0, "PEC 가 맞으면 읽기가 성공한다");
    CHECK(n == 1 && strcmp(v[0].quantity, "temp_object") == 0, "quantity 는 temp_object");
    CHECK(n == 1 && v[0].value > 28.74f && v[0].value < 28.76f, "28.75°C 로 환산된다");
    /* 🔴 [2026-08-22] 주변 온도(Ta, RAM 0x06)도 이어 읽는다 — 젯슨 링크
     *    선택 필드 temp_ambient. 이 가짜 버스는 Tobj 프레임만 주므로 Ta
     *    쪽 PEC 는 어긋나고, 그때 본값(Tobj)이 그대로 살아남는 것까지
     *    위의 n==1 검사들이 함께 확인한다. */
    CHECK(MLX.n == 2 && MLX.ntx[0] == 1u && MLX.nrx[0] == 3u,
          "명령 1바이트 쓰고 repeated start 로 3바이트 읽는다 (Ta 포함 2회)");
    CHECK(MLX.first_tx[0] == 0x07u, "첫 명령은 RAM 0x07(Tobj1)");
}

/* 🔴 PEC 를 반드시 본다 — 한 바이트만 틀려도 거부해야 한다. */
static void test_mlx90614_rejects_a_bad_pec(void)
{
    memset(&MLX, 0, sizeof MLX);
    MLX.resp[0] = 0xF7u; MLX.resp[1] = 0x3Au; MLX.resp[2] = 0xDEu;   /* PEC 한 비트 틀림 */
    MkI2cIo io = { mlx_fake_xfer, &MLX };
    MkI2cValue v[MK_I2C_VALUES_MAX];
    int n = 0;
    int rc = MK_I2C_MLX90614.read(&io, 3u, 0x5Au, v, &n);

    CHECK(rc == -2, "PEC 가 어긋나면 데이터 오류(-2)다");
    CHECK(n == 0, "값을 내보내지 않는다");
}

/* 🔴 [되돌림 검사용 표적] bit15(오류 플래그)가 서면 값을 내보내지 않는다
 *    — p.19·p.30 "0x8XXX → flag error". raw=0x8000(LSB=0x00,MSB=0x80),
 *    PEC 는 이 데이터에 맞게 다시 계산한 0x8F(python 으로 미리 확인). */
static void test_mlx90614_rejects_the_error_flag(void)
{
    memset(&MLX, 0, sizeof MLX);
    MLX.resp[0] = 0x00u; MLX.resp[1] = 0x80u; MLX.resp[2] = 0x8Fu;
    MkI2cIo io = { mlx_fake_xfer, &MLX };
    MkI2cValue v[MK_I2C_VALUES_MAX];
    int n = 0;
    int rc = MK_I2C_MLX90614.read(&io, 3u, 0x5Au, v, &n);

    CHECK(rc == -2, "오류 플래그(bit15)가 서면 데이터 오류(-2)다");
    CHECK(n == 0, "값을 내보내지 않는다");
}

/* start() 가 없고, 기본값(주소·warmup)이 데이터시트와 같은지. */
static void test_mlx90614_needs_no_start_and_has_the_right_defaults(void)
{
    CHECK(MK_I2C_MLX90614.start == NULL,
          "전원 인가 직후 기본이 이미 연속 측정이라(p.10) 시작 명령이 없다");
    CHECK(MK_I2C_MLX90614.default_addr == 0x5Au, "기본 주소 0x5A (p.20, 0x5B 아니다)");
    CHECK(MK_I2C_MLX90614.warmup_ms == 250u, "Tvalid=250ms (p.8 Table 5, After POR)");
}

static void test_mlx90614_quantity_matches_the_kind_table(void)
{
    const char *q[MK_I2C_VALUES_MAX];
    int nq = mk_i2c_kind_quantities((uint8_t)MK_I2C_KIND_IR_TEMP, q);
    CHECK(nq == 1 && strcmp(q[0], "temp_object") == 0, "적외 온도 종류 표는 temp_object 하나");

    memset(&MLX, 0, sizeof MLX);
    MLX.resp[0] = 0xF7u; MLX.resp[1] = 0x3Au; MLX.resp[2] = 0xDFu;
    MkI2cIo io = { mlx_fake_xfer, &MLX };
    MkI2cValue v[MK_I2C_VALUES_MAX];
    int n = 0;
    MK_I2C_MLX90614.read(&io, 3u, 0x5Au, v, &n);
    CHECK(n == 1 && nq == 1 && strcmp(v[0].quantity, q[0]) == 0,
          "MLX90614 가 내는 quantity 가 종류 표와 같다 (temp_object)");
}

/* ---- AM2320 드라이버 -------------------------------------------------------
 *
 * 🔴 예제 값은 전부 docs/datasheet/AM2320.pdf 원문 p.13~14 다(습도
 *    0x01F4=50.0%RH, 온도 0x00FA=25.0°C). CRC 는 Python 으로 미리
 *    계산해 못박는다 — crc16([0x03,0x04,0x01,0xF4,0x00,0xFA]) 의 결과를
 *    저바이트 먼저 실으면 0x31,0xA5 인데, 데이터시트 표(p.14, "CRC 코드
 *    2 31A5")가 정확히 그 두 바이트를 보여준다.
 */

/* 🔴 AM2320 전용 가짜 버스. 세 번의 전송(깨우기·명령·응답)을 각각 다르게
 *    반응시켜야 해서(그리고 깨우기는 ntx=0&&nrx=0) 공용 fake_xfer 로는
 *    표현이 안 된다. */
#define AM_MAX_EV 4
typedef struct {
    int      n;
    size_t   ntx[AM_MAX_EV];
    size_t   nrx[AM_MAX_EV];
    uint8_t  first_tx[AM_MAX_EV];
    int      poke_ret;           /* 깨우기(ntx=0,nrx=0) 호출의 반환값 */
    int      cmd_ret;            /* 명령 프레임(ntx=3,nrx=0) 호출의 반환값 */
    int      resp_ret;           /* 응답(ntx=0,nrx=8) 호출의 반환값 */
    uint8_t  resp[8];
    uint32_t delay_us[AM_MAX_EV];
    int      n_delay;
} AmBus;

static AmBus AM;

static int am_fake_xfer(void *ctx, uint8_t bus, uint8_t addr,
                        const uint8_t *tx, size_t ntx, uint8_t *rx, size_t nrx)
{
    (void)bus; (void)addr;
    AmBus *b = (AmBus *)ctx;
    if (b->n < AM_MAX_EV) {
        b->ntx[b->n] = ntx;
        b->nrx[b->n] = nrx;
        b->first_tx[b->n] = ntx ? tx[0] : 0u;
        b->n++;
    }
    if (ntx == 0u && nrx == 0u) {
        return b->poke_ret;
    }
    if (nrx == 0u) {
        return b->cmd_ret;
    }
    if (b->resp_ret == 0) {
        for (size_t k = 0; k < nrx && k < sizeof b->resp; k++) { rx[k] = b->resp[k]; }
    }
    return b->resp_ret;
}

static void am_fake_delay(void *ctx, uint32_t us)
{
    AmBus *b = (AmBus *)ctx;
    if (b->n_delay < AM_MAX_EV) { b->delay_us[b->n_delay++] = us; }
}

/* 데이터시트 p.14 예제: 습도 0x01F4=500(50.0%RH), 온도 0x00FA=250(25.0°C),
 * CRC 저바이트 먼저 0x31,0xA5 (파일 위 주석의 근거 참고). */
static void am_setup_positive_example(void)
{
    memset(&AM, 0, sizeof AM);
    uint8_t resp[8] = { 0x03u, 0x04u, 0x01u, 0xF4u, 0x00u, 0xFAu, 0x31u, 0xA5u };
    memcpy(AM.resp, resp, sizeof resp);
}

/* 🔴 [핵심] 깨우기가 NACK 이어도 정상 읽기가 된다 — 이것이 이 드라이버가
 *    존재하는 이유다. read() 안에서 깨우기의 반환값을 삼킨다. */
static void test_am2320_ignores_the_wake_nack(void)
{
    am_setup_positive_example();
    AM.poke_ret = -1;             /* 깨우기는 NACK — 정상이다 */
    MkI2cIo io = { am_fake_xfer, &AM, am_fake_delay };
    MkI2cValue v[MK_I2C_VALUES_MAX];
    int n = 0;
    int rc = MK_I2C_AM2320.read(&io, 1u, 0x5Cu, v, &n);

    CHECK(rc == 0, "깨우기가 NACK 이어도 읽기는 성공한다");
    CHECK(n == 2, "값은 둘(temp, humidity)");
}

/* 세 번의 전송이 데이터시트 순서·모양대로인지: 깨우기(주소만) →
 * 명령(0x03,0x00,0x04) → 응답(8바이트). 그 사이에 실제로 기다리는지도
 * 본다. */
static void test_am2320_wakes_then_waits_then_sends_the_command(void)
{
    am_setup_positive_example();
    MkI2cIo io = { am_fake_xfer, &AM, am_fake_delay };
    MkI2cValue v[MK_I2C_VALUES_MAX];
    int n = 0;
    int rc = MK_I2C_AM2320.read(&io, 1u, 0x5Cu, v, &n);

    CHECK(rc == 0, "읽기 성공");
    CHECK(AM.n == 3, "전송이 셋 — 깨우기·명령·응답");
    CHECK(AM.n == 3 && AM.ntx[0] == 0u && AM.nrx[0] == 0u,
          "첫 전송은 깨우기(주소만, ntx=0&&nrx=0)");
    CHECK(AM.n == 3 && AM.ntx[1] == 3u && AM.nrx[1] == 0u && AM.first_tx[1] == 0x03u,
          "둘째 전송은 명령 프레임(함수코드 0x03)");
    CHECK(AM.n == 3 && AM.ntx[2] == 0u && AM.nrx[2] == 8u,
          "셋째 전송은 응답 8바이트");
    CHECK(AM.n_delay == 2, "두 번 기다린다(깨우기 뒤·명령 뒤)");
    CHECK(AM.n_delay == 2 && AM.delay_us[0] >= 800u,
          "깨우기 뒤 최소 800us (p.17)");
    CHECK(AM.n_delay == 2 && AM.delay_us[1] >= 1500u,
          "명령 뒤 최소 1.5ms (p.17)");
}

/* 환산·순서: temp 가 out[0], humidity 가 out[1] — 종류 표(§7.5.1)와
 * 같아야 한다. */
static void test_am2320_conversion_and_order_match_the_datasheet_example(void)
{
    am_setup_positive_example();
    MkI2cIo io = { am_fake_xfer, &AM, am_fake_delay };
    MkI2cValue v[MK_I2C_VALUES_MAX];
    int n = 0;
    int rc = MK_I2C_AM2320.read(&io, 1u, 0x5Cu, v, &n);

    CHECK(rc == 0, "읽기 성공");
    CHECK(n == 2 && strcmp(v[0].quantity, "temp") == 0, "out[0] 은 temp");
    CHECK(n == 2 && v[0].value > 24.9f && v[0].value < 25.1f, "0x00FA → 25.0°C");
    CHECK(n == 2 && strcmp(v[1].quantity, "humidity") == 0, "out[1] 은 humidity");
    CHECK(n == 2 && v[1].value > 49.9f && v[1].value < 50.1f, "0x01F4 → 50.0%RH");
}

/* 🔴 음수 온도는 2의 보수가 아니라 부호+크기다(p.13). -10.0°C 는
 * magnitude=100(0x0064) 에 부호비트(raw=0x8064) — CRC 는 이 데이터로
 * python 으로 다시 계산했다(파일 위 주석). */
static void test_am2320_negative_temperature_uses_sign_and_magnitude(void)
{
    memset(&AM, 0, sizeof AM);
    uint8_t resp[8] = { 0x03u, 0x04u, 0x01u, 0xF4u, 0x80u, 0x64u, 0xD1u, 0xCDu };
    memcpy(AM.resp, resp, sizeof resp);
    MkI2cIo io = { am_fake_xfer, &AM, am_fake_delay };
    MkI2cValue v[MK_I2C_VALUES_MAX];
    int n = 0;
    int rc = MK_I2C_AM2320.read(&io, 1u, 0x5Cu, v, &n);

    CHECK(rc == 0, "CRC 가 맞으면 읽기가 성공한다");
    CHECK(n == 2 && v[0].value > -10.1f && v[0].value < -9.9f,
          "raw=0x8064 → -10.0°C (부호+크기, 2의 보수가 아니다)");
}

/* 🔴 CRC 를 반드시 본다. */
static void test_am2320_rejects_a_bad_crc(void)
{
    am_setup_positive_example();
    AM.resp[7] ^= 0xFFu;          /* CRC 상위 바이트를 망가뜨린다 */
    MkI2cIo io = { am_fake_xfer, &AM, am_fake_delay };
    MkI2cValue v[MK_I2C_VALUES_MAX];
    int n = 0;
    int rc = MK_I2C_AM2320.read(&io, 1u, 0x5Cu, v, &n);

    CHECK(rc == -2, "CRC 가 어긋나면 데이터 오류(-2)다");
    CHECK(n == 0, "값을 내보내지 않는다");
}

/* 명령 프레임(2번째 전송) 자체가 실패하면(=진짜 무응답) 오류를 그대로
 * 돌려준다 — 깨우기와 달리 이건 삼키면 안 된다. */
static void test_am2320_command_failure_is_a_real_error(void)
{
    am_setup_positive_example();
    AM.cmd_ret = -1;
    MkI2cIo io = { am_fake_xfer, &AM, am_fake_delay };
    MkI2cValue v[MK_I2C_VALUES_MAX];
    int n = 0;
    int rc = MK_I2C_AM2320.read(&io, 1u, 0x5Cu, v, &n);

    CHECK(rc == -1, "명령 프레임의 NACK 은 진짜 오류다 — 깨우기와 다르다");
    CHECK(n == 0, "값을 내보내지 않는다");
    CHECK(AM.n == 2, "응답을 읽으러 가지 않는다(명령이 실패했으므로)");
}

static void test_am2320_needs_no_start_and_has_the_right_defaults(void)
{
    CHECK(MK_I2C_AM2320.start == NULL,
          "read() 안에서 매번 깨운다 — 한 번만 켜는 start 가 없다(p.10 §8.2.1)");
    CHECK(MK_I2C_AM2320.default_addr == 0x5Cu, "기본 주소 0x5C (0xB8 의 7비트, p.10)");
    CHECK(MK_I2C_AM2320.warmup_ms == 2000u, "최소 읽기 간격 2초 (p.10 §8.2.1)");
}

static void test_am2320_quantity_matches_the_kind_table(void)
{
    const char *q[MK_I2C_VALUES_MAX];
    int nq = mk_i2c_kind_quantities((uint8_t)MK_I2C_KIND_HUMID, q);
    CHECK(nq == 2 && strcmp(q[0], "temp") == 0 && strcmp(q[1], "humidity") == 0,
          "온습도 종류 표는 temp, humidity 순서");

    am_setup_positive_example();
    MkI2cIo io = { am_fake_xfer, &AM, am_fake_delay };
    MkI2cValue v[MK_I2C_VALUES_MAX];
    int n = 0;
    MK_I2C_AM2320.read(&io, 1u, 0x5Cu, v, &n);
    CHECK(n == 2 && nq == 2 && strcmp(v[0].quantity, q[0]) == 0
          && strcmp(v[1].quantity, q[1]) == 0,
          "AM2320 이 내는 순서가 종류 표와 같다 (temp, humidity)");
}

/* 🔴 [C1 자리를 대신 채운다] 예전엔 "미지원(드라이버 없음) 종류가 양
 *    둘이면 두 줄이 나가는가"를 HUMID 로 시험했다. 이제 HUMID 는
 *    드라이버가 있어 그 경로를 못 탄다 — 대신 **진짜 드라이버가 실패하는
 *    경로**로 같은 push_status() 다중 라인 로직을 시험한다. 배선된
 *    AM2320 이 실제로 응답하지 않는(CRC 안 맞는 쓰레기 응답) 상황이라,
 *    미지원 시나리오보다 오히려 더 현실적이다. */
static void test_am2320_real_read_failure_emits_both_quantities_as_status(void)
{
    setup();       /* 공용 BUS/I2C/CFG — fake_xfer 는 rx 를 1,2,3... 으로
                    * 채우므로 AM2320 응답의 CRC 는 항상 안 맞는다 */
    enable_port_kind(10u, (uint32_t)MK_I2C_KIND_HUMID, 0x5Cu, 2000u);

    /* out[]/mk_i2c_take() 가 없어져 매 바퀴 흘러가는 레코드를 셀 수 없다
     * — 대신 tick 을 다 돌린 뒤 last[][] 의 두 슬롯을 직접 본다(마지막
     * 값이 곧 이 시험이 보려는 사실이다). */
    for (int64_t t = 0; t <= 2100; t += 10) {
        mk_i2c_tick(&I2C, &CFG, t);
    }

    MkI2cOut temp, humidity;
    int have_temp = mk_i2c_last(&I2C, 0u, 0u, &temp);
    int have_humidity = mk_i2c_last(&I2C, 0u, 1u, &humidity);
    CHECK(have_temp && have_humidity,
          "실패해도 temp·humidity 두 슬롯 다 채워진다(§7.5.1 표를 따라간다)");
    CHECK(!have_temp || (!temp.have_value
          && temp.quantity != NULL && strcmp(temp.quantity, "temp") == 0),
          "temp 슬롯: 값은 비어 있어도 quantity 는 남는다");
    CHECK(!have_humidity || (!humidity.have_value
          && humidity.quantity != NULL && strcmp(humidity.quantity, "humidity") == 0),
          "humidity 슬롯: 값은 비어 있어도 quantity 는 남는다");
    CHECK((!have_temp || temp.status == 2u) && (!have_humidity || humidity.status == 2u),
          "CRC 가 안 맞으면 데이터 오류(status=2)");
}

/* ---- I3 — 설계 §11 이 요구한 상태기계 시험 다섯 --------------------------
 *
 * 🔴 원장(progress.md)에는 "Task 4 에서 Task 5 로 옮겼다"고 적혀 있었지만
 *    실제로는 어디에도 없었다(검토 지적 I3). 아래 다섯이 그 빈칸을 채운다.
 *    시간 경계는 MK_I2C_BH1750.warmup_ms 를 빌려 쓴다 — 손으로 다시 적지
 *    않는다.
 */

/* [I3-1] 변환 대기 동안 read 를 안 부른다. */
static void test_does_not_read_during_warmup(void)
{
    setup();
    enable_lux_port(10u, 0x23u, 10u);   /* period 최솟값 — warmup 이 실효 주기를 결정한다 */

    int start_done_at = -1;
    int read_seen_at = -1;
    for (int64_t t = 0; t < 400; t += 10) {
        int before = BUS.n;
        mk_i2c_tick(&I2C, &CFG, t);
        int after = BUS.n;
        if (start_done_at < 0 && after >= 2) { start_done_at = (int)t; }
        if (start_done_at >= 0 && read_seen_at < 0 && after > before) {
            /* read 는 명령 없이(ntx=0) 2바이트를 받는다 (§6) — start 의
             * 1바이트 쓰기와 모양이 다르다. */
            if (BUS.ntx[after - 1] == 0u && BUS.nrx[after - 1] == 2u) {
                read_seen_at = (int)t;
            }
        }
    }

    CHECK(start_done_at >= 0, "start 가 불렸다");
    CHECK(read_seen_at >= 0, "결국 read 가 불렸다");
    CHECK(read_seen_at - start_done_at >= (int)MK_I2C_BH1750.warmup_ms,
          "read 는 warmup_ms 가 다 찬 뒤에야 나간다 (변환 대기 동안은 안 읽는다)");
}

/* [I3-2] 실효 주기 = max(period_ms, warmup_ms). */
static void test_effective_period_is_the_longer_of_period_and_warmup(void)
{
    setup();
    enable_lux_port(10u, 0x23u, 10u);  /* period(10) < warmup(180) */

    int64_t read_times[8];
    int n_reads = 0;
    for (int64_t t = 0; t < 800 && n_reads < 8; t += 10) {
        int before = BUS.n;
        mk_i2c_tick(&I2C, &CFG, t);
        int after = BUS.n;
        if (after > before && BUS.ntx[after - 1] == 0u && BUS.nrx[after - 1] == 2u) {
            read_times[n_reads++] = t;
        }
    }

    CHECK(n_reads >= 2, "read 가 여러 번 있었다");
    if (n_reads >= 2) {
        int64_t gap = read_times[1] - read_times[0];
        CHECK(gap >= (int64_t)MK_I2C_BH1750.warmup_ms,
              "read 간격이 설정 주기(10ms)가 아니라 warmup_ms(180ms) 를 따른다");
    }
}

/* [I3-3] start 는 켤 때 한 번만 — 주기마다 다시 부르지 않는다. */
static void test_start_is_called_once_when_turned_on(void)
{
    setup();
    enable_lux_port(10u, 0x23u, 50u);
    for (int64_t t = 0; t < 1000; t += 10) {
        mk_i2c_tick(&I2C, &CFG, t);
    }
    int start_writes = 0;
    for (int k = 0; k < BUS.n; k++) {
        if (BUS.ntx[k] == 1u && BUS.nrx[k] == 0u) { start_writes++; }
    }
    CHECK(start_writes == 2,
          "start(전원+모드) 는 켤 때 한 번만 나간다 — 계속 성공하는 동안 주기마다 다시 안 부른다");
}

/* [I3-4] 격리 — 한 포트가 죽어도(드라이버 없음) 다른 포트는 계속 읽힌다. */
static void test_isolation_a_dead_port_does_not_stop_the_rest(void)
{
    setup();
    enable_lux_port(10u, 0x23u, 50u);                                  /* 정상 */
    /* HUMID(AM2320) 는 이제 드라이버가 있다 — 죽은 포트는 WATER_TEMP 로. */
    enable_port_kind(11u, (uint32_t)MK_I2C_KIND_WATER_TEMP, 0x40u, 50u);  /* 드라이버 없음 — 계속 죽어 있다 */

    /* out[]/mk_i2c_take() 가 없어져 두 포트를 각자의 t_ms 변화로 따로
     * 센다 — 포트 0(J10)은 살아 있고, 포트 1(J11)은 죽어 있다. */
    int ok_count = 0, fault_count = 0;
    int64_t ok_t_ms = -1, fault_t_ms = -1;
    for (int64_t t = 0; t < 500; t += 10) {
        mk_i2c_tick(&I2C, &CFG, t);
        MkI2cOut out;
        if (mk_i2c_last(&I2C, 0u, 0u, &out) == 1 && out.t_ms != ok_t_ms) {
            ok_t_ms = out.t_ms;
            if (out.connector_id == 10u && out.status == 0u) { ok_count++; }
        }
        if (mk_i2c_last(&I2C, 1u, 0u, &out) == 1 && out.t_ms != fault_t_ms) {
            fault_t_ms = out.t_ms;
            if (out.connector_id == 11u && out.status == 3u) { fault_count++; }
        }
    }
    CHECK(ok_count > 0, "죽은 포트(J11)가 있어도 다른 포트(J10)는 계속 읽힌다");
    CHECK(fault_count > 0, "죽은 포트는 계속 status=3 을 알린다");
}

/* [I3-5] status 판정 — io 반환 −1·−2 가 1·2 로. */
static void test_io_error_codes_map_to_status_one_and_two(void)
{
    setup();
    enable_lux_port(10u, 0x23u, 50u);
    BUS.ret = -1;
    mk_i2c_tick(&I2C, &CFG, 0);      /* OFF -> START (버스 안 건드림) */
    mk_i2c_tick(&I2C, &CFG, 10);     /* START 시도: 실패 */
    MkI2cOut out;
    CHECK(mk_i2c_last(&I2C, 0u, 0u, &out) == 1 && out.status == 1u,
          "io -1(응답 없음) 은 status=1");

    setup();
    enable_lux_port(10u, 0x23u, 50u);
    BUS.ret = -2;
    mk_i2c_tick(&I2C, &CFG, 0);
    mk_i2c_tick(&I2C, &CFG, 10);
    CHECK(mk_i2c_last(&I2C, 0u, 0u, &out) == 1 && out.status == 2u,
          "io -2(버스 오류) 는 status=2");
}

/* 🔴 [I2] 읽기 실패가 이어지면 OFF 로 되돌아가 start 부터 다시 한다.
 *    BH1750 은 전원이 끊기면 Power Down 으로 돌아가는데(p.4) READY 에
 *    머물며 read 만 재시도하면, 재삽입 뒤 주소는 ACK 하므로 read 가
 *    성공(status=0)해 버려 변환 안 된 값이 나간다. */
static void test_read_failure_forces_a_fresh_start(void)
{
    setup();
    enable_lux_port(10u, 0x23u, 10u);
    for (int64_t t = 0; t <= 200; t += 10) {
        mk_i2c_tick(&I2C, &CFG, t);
    }
    int start_writes_before = 0;
    for (int k = 0; k < BUS.n; k++) {
        if (BUS.ntx[k] == 1u && BUS.nrx[k] == 0u) { start_writes_before++; }
    }
    CHECK(start_writes_before == 2, "여기까지는 시작 명령이 두 번뿐 (선행 확인)");

    BUS.ret = -1;   /* 이제부터 다음 read 가 실패한다 */
    for (int64_t t = 210; t <= 600; t += 10) {
        mk_i2c_tick(&I2C, &CFG, t);
    }
    int start_writes_after = 0;
    for (int k = 0; k < BUS.n; k++) {
        if (BUS.ntx[k] == 1u && BUS.nrx[k] == 0u) { start_writes_after++; }
    }
    CHECK(start_writes_after > start_writes_before,
          "읽기가 실패하면 OFF 로 되돌아가 start 를 다시 부른다 (I2)");
}

/* ---- 마지막 값 (수집·송신 분리, 사용자 설계 2026-08-19) ------------------
 *
 * mk_telem 은 tx.period_ms 마다 mk_i2c_last(port, slot) 만 읽는다 — 배출
 * 큐가 없다(2026-08-19 에 out[]/mk_i2c_take() 를 걷어냈다, mk_i2c.h 의
 * MkI2c 주석 참고). 그래서 아래 시험들은 tick 을 돌린 뒤 last[][] 만 본다
 * — 예전처럼 매 바퀴 뒤 큐를 비울 필요가 없다. */

static void test_last_value_is_absent_before_any_read(void)
{
    setup();
    enable_lux_port(10u, 0x23u, 200u);
    /* 아직 한 번도 tick 을 안 돌렸다 — port 0(=J10), slot 0. */
    MkI2cOut out;
    CHECK(mk_i2c_last(&I2C, 0u, 0u, &out) == 0,
          "첫 읽기 전에는 마지막 값이 없다");
}

static void test_last_value_updates_on_a_successful_read(void)
{
    setup();
    enable_lux_port(10u, 0x23u, 200u);
    for (int64_t t = 0; t <= 400; t += 10) {
        mk_i2c_tick(&I2C, &CFG, t);
    }

    MkI2cOut last;
    CHECK(mk_i2c_last(&I2C, 0u, 0u, &last) == 1, "성공하면 마지막 값이 생긴다");
    CHECK(last.connector_id == 10u, "커넥터 번호");
    CHECK(last.quantity != NULL && strcmp(last.quantity, "lux") == 0, "양");
    CHECK(last.have_value == 1, "값이 있다");
    CHECK(last.status == 0u, "정상");
}

static void test_last_value_records_a_failure_too(void)
{
    /* 🔴 실패·미지원도 마지막 값이다 — null·status 를 그대로 반복해서
     *    내보내는 것이 규격 §7.5 의 취지다("살아 있는 척하지 않는다"의
     *    반대쪽: "죽었으면 죽었다고 계속 말한다"). quantity 도 함께
     *    남아야 한다 — 없으면 host/gui/screen.py 가 이 레코드를 통째로
     *    버린다(C1, mk_i2c.h 의 push_status 관련 주석과 같은 이유). */
    setup();
    BUS.ret = -1;                      /* 처음부터 응답 없음 */
    enable_lux_port(10u, 0x23u, 200u);
    for (int64_t t = 0; t <= 50; t += 10) {
        mk_i2c_tick(&I2C, &CFG, t);
    }

    MkI2cOut last;
    CHECK(mk_i2c_last(&I2C, 0u, 0u, &last) == 1, "실패도 마지막 값을 남긴다");
    CHECK(last.quantity != NULL && strcmp(last.quantity, "lux") == 0,
          "실패해도 quantity 를 잃지 않는다");
    CHECK(last.have_value == 0, "값 자리는 비어 있다(null)");
    CHECK(last.status == 1u, "응답 없음");
}

/* 🔴 값이 둘인 종류(온습도, AM2320)는 두 슬롯이 각각 채워져야 한다 — 하나가
 *    다른 하나를 덮어쓰면 host 는 온도와 습도 중 하나를 영영 못 받는다.
 *    일반 fake_xfer 는 AM2320 의 CRC 를 못 맞추므로(위 test_am2320_* 주석
 *    참고), AM2320 전용 가짜 버스(am_fake_xfer/AM)를 이 포트에 직접
 *    붙인다. */
static void test_last_value_stores_both_slots_of_a_two_quantity_kind(void)
{
    setup();
    am_setup_positive_example();
    I2C.io.xfer = am_fake_xfer;
    I2C.io.ctx = &AM;
    I2C.io.delay_us = am_fake_delay;
    enable_port_kind(10u, (uint32_t)MK_I2C_KIND_HUMID, 0x5Cu, 2000u);

    /* OFF->START(start 없음)->WARMUP(2000ms)->READY 진입 즉시 읽기. */
    for (int64_t t = 0; t <= 2100; t += 10) {
        mk_i2c_tick(&I2C, &CFG, t);
    }

    MkI2cOut temp, humidity;
    CHECK(mk_i2c_last(&I2C, 0u, 0u, &temp) == 1, "slot 0(temp) 이 채워진다");
    CHECK(temp.quantity != NULL && strcmp(temp.quantity, "temp") == 0,
          "slot 0 은 temp");
    CHECK(temp.have_value == 1 && temp.value > 24.9f && temp.value < 25.1f,
          "0x00FA → 25.0°C (p.13~14 예제)");

    CHECK(mk_i2c_last(&I2C, 0u, 1u, &humidity) == 1,
          "slot 1(humidity) 도 따로 채워진다 — slot 0 에 덮어써지지 않는다");
    CHECK(humidity.quantity != NULL && strcmp(humidity.quantity, "humidity") == 0,
          "slot 1 은 humidity");
    CHECK(humidity.have_value == 1
          && humidity.value > 49.9f && humidity.value < 50.1f,
          "0x01F4 → 50.0%RH (p.13~14 예제)");
}

static void test_last_value_is_cleared_when_the_kind_changes(void)
{
    /* 🔴 옛 종류가 내던 양(quantity)을 새 종류인 척 계속 내보내면 안 된다.
     *    LUX(양 하나, slot 0)로 값을 만든 뒤 HUMID(양 둘)로 바꾸면, 새
     *    값이 오기 전까지는 아무 슬롯도 안 나가야 한다. */
    setup();
    enable_lux_port(10u, 0x23u, 200u);
    for (int64_t t = 0; t <= 400; t += 10) {
        mk_i2c_tick(&I2C, &CFG, t);
    }
    MkI2cOut before;
    CHECK(mk_i2c_last(&I2C, 0u, 0u, &before) == 1, "선행 확인 — 값이 있다");

    MkCfgItem *k = mk_cfg_find(&CFG, "i2c10.kind");
    k->cur.u = (uint32_t)MK_I2C_KIND_HUMID;   /* AM2320 — 드라이버가 있다 */
    mk_i2c_tick(&I2C, &CFG, 410);              /* 설정이 바뀐 것을 알아챈다 */

    MkI2cOut after;
    CHECK(mk_i2c_last(&I2C, 0u, 0u, &after) == 0,
          "종류가 바뀌면 옛 값을 비운다 — 새 값이 올 때까지 안 나간다");
}

static void test_last_value_is_cleared_when_disabled(void)
{
    setup();
    enable_lux_port(10u, 0x23u, 200u);
    for (int64_t t = 0; t <= 400; t += 10) {
        mk_i2c_tick(&I2C, &CFG, t);
    }
    MkI2cOut before;
    CHECK(mk_i2c_last(&I2C, 0u, 0u, &before) == 1, "선행 확인 — 값이 있다");

    MkCfgItem *en = mk_cfg_find(&CFG, "i2c10.enabled");
    en->cur.u = 0u;
    mk_i2c_tick(&I2C, &CFG, 410);

    MkI2cOut after;
    CHECK(mk_i2c_last(&I2C, 0u, 0u, &after) == 0,
          "끄면 마지막 값도 비운다 — 죽은 센서가 계속 살아 있는 척하면 안 된다");
}

/* ---- 대조용 덤프 ---------------------------------------------------------- */

/* 🔴 종류 → 양 표를 찍는다. crosscheck_i2c_quantities.py 가 시뮬레이터의
 *    I2C_QUANTITIES 와 대조한다. 시뮬레이터의 I2C_KINDS 와 같은 범위
 *    (0~4)만 찍는다 — 더 찍으면 시뮬레이터엔 없는 키가 나와 거짓
 *    불일치가 난다. */
static void print_quantities(void)
{
    for (uint32_t k = 0; k <= 4u; k++) {
        const char *q[MK_I2C_VALUES_MAX];
        int n = mk_i2c_kind_quantities((uint8_t)k, q);
        printf("%u", (unsigned)k);
        for (int j = 0; j < n; j++) { printf(",%s", q[j]); }
        printf("\n");
    }
}

int main(int argc, char **argv)
{
    if (argc > 1 && strcmp(argv[1], "--print-quantities") == 0) {
        print_quantities();
        return 0;
    }

    printf("-- 꺼진 포트 --\n");        test_disabled_ports_never_touch_the_bus();
    printf("-- 라운드로빈 --\n");       test_one_port_per_tick();
    printf("-- 지원 안 하는 종류 --\n"); test_unsupported_kind_reports_status_three();
    printf("-- 종류→양 표 --\n");       test_kind_quantities_table_matches_the_spec();
    printf("-- [I1] 여러 포트 동시 FAULT --\n");
    test_many_faulted_ports_in_one_tick_all_get_recorded();
    printf("-- 재시도 하한(미지원) --\n");
    test_unsupported_kind_retry_has_a_floor_even_at_min_period();
    printf("-- 재시도 하한(FAULT) --\n");
    test_fault_retry_has_a_floor_even_at_min_period();
    printf("-- BH1750 환산 --\n");      test_bh1750_conversion_uses_the_implementation_constant();
    printf("-- BH1750 바이트순서 --\n"); test_bh1750_reads_two_bytes_msb_first();
    printf("-- BH1750 quantity 일치 --\n");
    test_bh1750_quantity_matches_the_kind_table();
    printf("-- BH1750 전원 순서 --\n"); test_bh1750_start_powers_on_before_selecting_the_mode();
    printf("-- BH1750 전원 실패 --\n"); test_bh1750_start_stops_if_power_on_fails();

    printf("-- MLX90614 PEC(데이터시트 예제) --\n");
    test_mlx90614_pec_matches_the_datasheet_example();
    printf("-- MLX90614 환산(데이터시트 예제) --\n");
    test_mlx90614_conversion_matches_the_datasheet_examples();
    printf("-- MLX90614 읽기(PEC 정상) --\n");
    test_mlx90614_read_accepts_a_pec_valid_frame();
    printf("-- MLX90614 PEC 불일치 거부 --\n");
    test_mlx90614_rejects_a_bad_pec();
    printf("-- MLX90614 오류 플래그 거부 --\n");
    test_mlx90614_rejects_the_error_flag();
    printf("-- MLX90614 start 없음·기본값 --\n");
    test_mlx90614_needs_no_start_and_has_the_right_defaults();
    printf("-- MLX90614 quantity 일치 --\n");
    test_mlx90614_quantity_matches_the_kind_table();

    printf("-- AM2320 깨우기 NACK 무시 --\n");
    test_am2320_ignores_the_wake_nack();
    printf("-- AM2320 깨우기→대기→명령 순서 --\n");
    test_am2320_wakes_then_waits_then_sends_the_command();
    printf("-- AM2320 환산·순서 --\n");
    test_am2320_conversion_and_order_match_the_datasheet_example();
    printf("-- AM2320 음수 온도(부호+크기) --\n");
    test_am2320_negative_temperature_uses_sign_and_magnitude();
    printf("-- AM2320 CRC 불일치 거부 --\n");
    test_am2320_rejects_a_bad_crc();
    printf("-- AM2320 명령 실패는 진짜 오류 --\n");
    test_am2320_command_failure_is_a_real_error();
    printf("-- AM2320 start 없음·기본값 --\n");
    test_am2320_needs_no_start_and_has_the_right_defaults();
    printf("-- AM2320 quantity 일치 --\n");
    test_am2320_quantity_matches_the_kind_table();
    printf("-- AM2320 상태기계로 본 실패(양 둘) --\n");
    test_am2320_real_read_failure_emits_both_quantities_as_status();

    printf("-- [I3] 변환 대기 --\n");   test_does_not_read_during_warmup();
    printf("-- [I3] 실효 주기 --\n");   test_effective_period_is_the_longer_of_period_and_warmup();
    printf("-- [I3] start 한 번 --\n"); test_start_is_called_once_when_turned_on();
    printf("-- [I3] 격리 --\n");        test_isolation_a_dead_port_does_not_stop_the_rest();
    printf("-- [I3] status 매핑 --\n"); test_io_error_codes_map_to_status_one_and_two();
    printf("-- [I2] 읽기 실패 재시작 --\n");
    test_read_failure_forces_a_fresh_start();

    printf("-- 마지막 값 — 첫 읽기 전 --\n");
    test_last_value_is_absent_before_any_read();
    printf("-- 마지막 값 — 성공 --\n");
    test_last_value_updates_on_a_successful_read();
    printf("-- 마지막 값 — 실패도 남는다 --\n");
    test_last_value_records_a_failure_too();
    printf("-- 마지막 값 — 양이 둘인 종류는 두 슬롯 다 --\n");
    test_last_value_stores_both_slots_of_a_two_quantity_kind();
    printf("-- 마지막 값 — 종류 바뀌면 비운다 --\n");
    test_last_value_is_cleared_when_the_kind_changes();
    printf("-- 마지막 값 — 끄면 비운다 --\n");
    test_last_value_is_cleared_when_disabled();

    printf(failures ? "\nFAILED (%d)\n" : "\nPASSED\n", failures);
    return failures ? 1 : 0;
}
