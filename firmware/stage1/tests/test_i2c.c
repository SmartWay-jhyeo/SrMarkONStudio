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
 *    겹쳐 슈퍼루프가 길어진다.
 *
 * 🔴 [이가 없는 시험, 검토 지적 2026-08-18] 이 Task 의 드라이버 표
 *    (mk_i2c_drivers.c)는 항상 NULL 을 돌려주므로 LUX 도 지금은 지원 안
 *    하는 종류다 — 모든 포트가 FAULT 경로로 빠진다. FAULT 는 버스를
 *    건드리지 않는 채로 한 바퀴에 6개가 한꺼번에 처리되므로(mk_i2c_tick
 *    의 계약 — "버스를 건드린 포트가 나오면 거기서 멈춘다"는 FAULT 에는
 *    적용되지 않는다), BUS.n 은 드라이버가 없는 한 항상 0 이고
 *    `BUS.n <= 1` 은 라운드로빈이 실제로 동작하든 안 하든 참이다. 진짜로
 *    한 포트만 버스를 건드리는지는 Task 5 에서 BH1750 이 붙어야 물 수
 *    있다 — 그때 이 시험을 다시 본다. */
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

int main(void)
{
    printf("-- 꺼진 포트 --\n");        test_disabled_ports_never_touch_the_bus();
    printf("-- 라운드로빈 --\n");       test_one_port_per_tick();
    printf("-- 지원 안 하는 종류 --\n"); test_unsupported_kind_reports_status_three();

    printf(failures ? "\nFAILED (%d)\n" : "\nPASSED\n", failures);
    return failures ? 1 : 0;
}
