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

static void set_u(const char *key, uint32_t v)
{
    MkCfgItem *it = mk_cfg_find(&CFG, key);
    if (it) { it->cur.u = v; }
}

static void setup(void)
{
    memset(&BUS, 0, sizeof BUS);
    mk_cfgtable_init(&CFG);
    MkI2cIo io = { fake_xfer, &BUS };
    mk_i2c_init(&I2C, &io);
}

/* 포트를 켜고 종류를 고른다. 조도 = MK_I2C_KIND_LUX */
static void enable_lux_port(unsigned jack, uint32_t addr, uint32_t period_ms)
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
    KEY(".kind");      set_u(key, (uint32_t)MK_I2C_KIND_LUX);
    KEY(".addr");      set_u(key, addr);
    KEY(".period_ms"); set_u(key, period_ms);
    #undef KEY
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
    MkI2cOut out;
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
        /* 매 바퀴 뒤 완전히 비운다 — mk_i2c.h 의 계약. */
        while (mk_i2c_take(&I2C, &out) == 1) { /* 버린다 */ }
    }

    CHECK(!mixed_ports_in_one_tick,
          "한 바퀴에 두드리는 포트는 하나뿐이다 (주소가 안 섞인다)");
    CHECK(BUS.n > 6, "그러고도 실제로 여러 번 두드린다 — 이가 있는 시험");
    CHECK(I2C.dropped == 0u, "매 바퀴 비웠으면 버릴 것이 없다");
}

/* 🔴 드라이버가 없는 종류는 status=3 이다. 아무것도 안 보내면 값이 왜
 *    없는지 화면 어디에도 답이 없다.
 *
 * 🔴 [검토 지적 2026-08-18] 바퀴마다 완전히 비우지 않고 시험 끝에
 *    mk_i2c_take() 를 한 번만 부르면, "주기마다 한 번만 알리는가"를
 *    2칸짜리 out 버퍼가 가려 버린다 — step_port() 의 시간 가드를 통째로
 *    지워도(매 바퀴 push) 이 시험은 그대로 통과했다(버려지는 40개는 보지
 *    않고 살아남은 2개만 봤으므로). 그래서 이제 **매 바퀴 뒤** while 로
 *    완전히 비우고 나온 레코드 수를 세어, 400ms/주기 200ms 에서 나와야
 *    하는 개수(2 — t=0 즉시 한 번 + t=200 에 한 번)를 못박는다. */
static void test_unsupported_kind_reports_status_three(void)
{
    setup();
    enable_lux_port(10u, 0x23u, 200u);
    MkCfgItem *k = mk_cfg_find(&CFG, "i2c10.kind");
    k->cur.u = (uint32_t)MK_I2C_KIND_HUMID;      /* 1차에는 드라이버가 없다 */

    int n_records = 0;
    int all_status_three = 1;
    MkI2cOut out;
    for (int64_t t = 0; t < 400; t += 10) {
        mk_i2c_tick(&I2C, &CFG, t);
        while (mk_i2c_take(&I2C, &out) == 1) {
            n_records++;
            if (out.status != 3u) { all_status_three = 0; }
        }
    }

    CHECK(n_records == 2, "지원 안 하는 종류는 주기마다 한 번만 알린다 (400ms/200ms=2)");
    CHECK(all_status_three, "status=3");
    CHECK(BUS.n == 0, "지원 안 하면 버스를 두드리지 않는다");
    CHECK(I2C.dropped == 0u, "매 바퀴 다 비웠으면 버릴 것이 없다");
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

int main(void)
{
    printf("-- 꺼진 포트 --\n");        test_disabled_ports_never_touch_the_bus();
    printf("-- 라운드로빈 --\n");       test_one_port_per_tick();
    printf("-- 지원 안 하는 종류 --\n"); test_unsupported_kind_reports_status_three();
    printf("-- BH1750 환산 --\n");      test_bh1750_conversion_uses_the_implementation_constant();
    printf("-- BH1750 바이트순서 --\n"); test_bh1750_reads_two_bytes_msb_first();
    printf("-- BH1750 전원 순서 --\n"); test_bh1750_start_powers_on_before_selecting_the_mode();
    printf("-- BH1750 전원 실패 --\n"); test_bh1750_start_stops_if_power_on_fails();

    printf(failures ? "\nFAILED (%d)\n" : "\nPASSED\n", failures);
    return failures ? 1 : 0;
}
