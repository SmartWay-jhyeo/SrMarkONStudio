/* mk_i2c 단위 시험 — 보드도 센서도 필요 없다.
 *
 * 🔴 여기서 지키는 것은 "언제 두드리는가" 다. 칩이 무엇인지는 드라이버가
 *    알고, 이 층은 순서·주기·격리만 안다.
 */
#include <stdio.h>
#include <string.h>
#include "../app/mk_i2c.h"
#include "../app/mk_cfgtable.h"

static int failures = 0;

#define CHECK(cond, msg) do {                                   \
    if (!(cond)) { printf("  FAIL %s\n", msg); failures++; }    \
    else         { printf("  ok   %s\n", msg); }                \
} while (0)

/* ---- 가짜 버스 ---------------------------------------------------------- */

#define MAX_EV 64
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
 *    겹쳐 슈퍼루프가 길어진다. */
static void test_one_port_per_tick(void)
{
    setup();
    for (unsigned jack = 10u; jack <= 15u; jack++) {
        enable_lux_port(jack, 0x23u, 200u);
    }
    BUS.n = 0;
    mk_i2c_tick(&I2C, &CFG, 0);
    CHECK(BUS.n <= 1, "한 바퀴에 전송은 많아야 한 번");
}

/* 🔴 드라이버가 없는 종류는 status=3 이다. 아무것도 안 보내면 값이 왜
 *    없는지 화면 어디에도 답이 없다. */
static void test_unsupported_kind_reports_status_three(void)
{
    setup();
    enable_lux_port(10u, 0x23u, 200u);
    MkCfgItem *k = mk_cfg_find(&CFG, "i2c10.kind");
    k->cur.u = (uint32_t)MK_I2C_KIND_HUMID;      /* 1차에는 드라이버가 없다 */

    for (int64_t t = 0; t < 400; t += 10) { mk_i2c_tick(&I2C, &CFG, t); }

    MkI2cOut out;
    CHECK(mk_i2c_take(&I2C, &out) == 1, "지원 안 하는 종류도 레코드를 낸다");
    CHECK(out.status == 3u, "status=3");
    CHECK(BUS.n == 0, "지원 안 하면 버스를 두드리지 않는다");
}

int main(void)
{
    printf("-- 꺼진 포트 --\n");        test_disabled_ports_never_touch_the_bus();
    printf("-- 라운드로빈 --\n");       test_one_port_per_tick();
    printf("-- 지원 안 하는 종류 --\n"); test_unsupported_kind_reports_status_three();

    printf(failures ? "\nFAILED (%d)\n" : "\nPASSED\n", failures);
    return failures ? 1 : 0;
}
