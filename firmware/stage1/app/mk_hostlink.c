#include "mk_hostlink.h"

#include "mk_ads1256.h"
#include "mk_railctl.h"
#include "mk_json.h"

#include <string.h>

static void emit_line(MkHostlink *h, const char *payload)
{
    int n = mk_build_line(h->out, sizeof h->out, payload);
    if (n > 0 && h->emit != NULL) {
        h->emit(h->ctx, h->out, (size_t)n);
    }
    /* n < 0 이면 보내지 않는다. 반쪽짜리 줄을 흘리지 않는다. */
}

static void emit_sack_ok(MkHostlink *h, const char *verb)
{
    char payload[MK_LINE_MAX + 1];
    size_t n = 0;
    const char *p;

    for (p = "SACK,"; *p && n + 1u < sizeof payload; p++) payload[n++] = *p;
    for (p = verb;    *p && n + 1u < sizeof payload; p++) payload[n++] = *p;
    for (p = ",OK";   *p && n + 1u < sizeof payload; p++) payload[n++] = *p;
    payload[n] = '\0';
    emit_line(h, payload);
}

static void emit_sack_err(MkHostlink *h, const char *verb, const char *reason)
{
    char payload[MK_LINE_MAX + 1];
    size_t n = 0;
    const char *p;

    for (p = "SACK,"; *p && n + 1u < sizeof payload; p++) payload[n++] = *p;
    for (p = verb;    *p && n + 1u < sizeof payload; p++) payload[n++] = *p;
    for (p = ",ERR,"; *p && n + 1u < sizeof payload; p++) payload[n++] = *p;
    for (p = reason;  *p && n + 1u < sizeof payload; p++) payload[n++] = *p;
    payload[n] = '\0';
    emit_line(h, payload);
}

/* 규격 §5.2 — `$ID` 는 id 레코드 한 줄 뒤에 $SACK 를 보낸다. */
/* 반환: 내보냈으면 1, 못 내보냈으면 0.
 *
 * 🔴 호출 쪽이 이 값을 봐야 한다. 레코드를 못 만들었는데 $SACK,ID,OK 를
 *    보내면 호스트는 데이터를 받았다고 믿는다. 그것이 아무 응답도 없는
 *    것보다 나쁘다 — 무응답은 타임아웃으로 드러나지만, 거짓 OK 는
 *    드러나지 않는다. */
static int emit_id_record(MkHostlink *h, int64_t now_ms)
{
    char body[MK_LINE_MAX + 8];
    MkJson j;

    mk_json_begin(&j, body, sizeof body);
    mk_json_u32(&j, "schema_ver", 3u);
    mk_json_u32(&j, "seq", 0u);          /* §5.2 — 명령 응답의 seq 는 항상 0 */
    mk_json_i64(&j, "t", now_ms);
    mk_json_str(&j, "type", "id");
    mk_json_str(&j, "device_id", h->device_id);
    mk_json_str(&j, "fw", h->fw);
    mk_json_str(&j, "board_rev", h->board_rev);

    int n = mk_json_end(&j);
    if (n <= 0 || h->emit == NULL) {
        return 0;                        /* 잘린 JSON 은 내보내지 않는다 */
    }
    /* NDJSON 은 줄바꿈으로 끝난다. 버퍼에 자리가 있는지 먼저 본다.
     * 🔴 `n + 2` 는 줄바꿈과 NUL 이다. 이 검사가 없으면 n 이 버퍼 끝에
     *    닿았을 때 body[n], body[n+1] 이 범위를 벗어난다. */
    if ((size_t)n + 2u > sizeof body) {
        return 0;
    }
    body[n]     = '\n';
    body[n + 1] = '\0';
    h->emit(h->ctx, body, (size_t)n + 1u);
    return 1;
}

/* NDJSON 한 줄을 내보낸다. 줄바꿈을 붙일 자리가 없으면 보내지 않는다. */
static int emit_json(MkHostlink *h, char *body, size_t cap, int n)
{
    if (n <= 0 || h->emit == NULL) {
        return 0;
    }
    if ((size_t)n + 2u > cap) {
        return 0;
    }
    body[n]     = '\n';
    body[n + 1] = '\0';
    h->emit(h->ctx, body, (size_t)n + 1u);
    return 1;
}

/* mk_cfgwire 가 카탈로그 한 줄을 만들 때마다 부른다. */
static void catalog_sink(void *ctx, const char *line, size_t len)
{
    MkHostlink *h = (MkHostlink *)ctx;
    char body[400];

    if (h->emit == NULL || len + 2u > sizeof body) {
        return;
    }
    memcpy(body, line, len);
    body[len]     = '\n';
    body[len + 1] = '\0';
    h->emit(h->ctx, body, len + 1u);
}

/* MkCfgResult 를 SACK 사유로. */
static void sack_cfg(MkHostlink *h, MkCfgResult r)
{
    if (r == MK_CFG_OK) {
        emit_sack_ok(h, "CFG");
    } else {
        emit_sack_err(h, "CFG", mk_cfg_reason_text(r));
    }
}

static void on_cfg(MkHostlink *h, const MkCommand *c, int64_t now_ms)
{
    if (h->cfg == NULL) {
        emit_sack_err(h, "CFG", "UNSUPPORTED");
        return;
    }
    if (c->argc < 1) {
        emit_sack_err(h, "CFG", "UNKNOWN_KEY");
        return;
    }

    const char *sub = c->args[0];

    if (strcmp(sub, "LIST") == 0) {
        /* 규격 §5.2 — 본문을 먼저 보내고 $SACK 로 끝낸다. */
        mk_cfgwire_list(h->cfg, h->fields, h->n_fields, now_ms,
                        catalog_sink, h);
        emit_sack_ok(h, "CFG");
        return;
    }

    if (strcmp(sub, "GET") == 0) {
        if (c->argc < 2) {
            emit_sack_err(h, "CFG", "UNKNOWN_KEY");
            return;
        }
        MkCfgItem *item = mk_cfg_find(h->cfg, c->args[1]);
        if (item == NULL) {
            emit_sack_err(h, "CFG", "UNKNOWN_KEY");
            return;
        }
        char body[256];
        int n = mk_cfgwire_value(item, now_ms, body, sizeof body);
        /* 🔴 본문을 못 만들었으면 OK 라고 하지 않는다. 거짓 OK 는 무응답보다
         *    나쁘다 — 호스트가 값을 받았다고 믿고 넘어간다. */
        if (!emit_json(h, body, sizeof body, n)) {
            emit_sack_err(h, "CFG", "BUSY");
            return;
        }
        emit_sack_ok(h, "CFG");
        return;
    }

    /* 아래는 전부 CONFIG 전용이다 (규격 §4).
     *
     * 🔴 모드 검사를 키 존재보다 **먼저** 한다. RUN 모드에서 온 요청은
     *    키가 맞든 틀리든 받지 않으므로, 키를 먼저 보면 없는 키에 대해
     *    UNKNOWN_KEY 를 돌려주게 된다 — 사용자는 키 이름을 고치려 들지만
     *    진짜 문제는 GUI 가 하트비트를 못 보내고 있다는 것이다. */
    if (mk_hostlink_mode(h, now_ms) != MK_MODE_CONFIG) {
        emit_sack_err(h, "CFG", "MODE");
        return;
    }

    if (strcmp(sub, "SET") == 0) {
        if (c->argc < 3) {
            emit_sack_err(h, "CFG", "UNKNOWN_KEY");
            return;
        }
        sack_cfg(h, mk_cfg_set(h->cfg, c->args[1], c->args[2]));
        return;
    }

    if (strcmp(sub, "SAVE") == 0) {
        if (h->save == NULL) {
            emit_sack_err(h, "CFG", "BUSY");
            return;
        }
        /* 🔴 바뀐 것이 없으면 쓰지 않는다. Flash 를 지웠다 쓰는 것은
         *    수명을 깎는 일이고, 아무것도 안 바뀐 저장은 순수한 손해다.
         *    사용자에게는 성공으로 보인다 — 실제로 저장된 상태와 같으므로
         *    거짓말이 아니다. */
        if (!mk_cfg_dirty(h->cfg)) {
            emit_sack_ok(h, "CFG");
            return;
        }
        if (h->save(h->save_ctx) != 0) {
            emit_sack_err(h, "CFG", "BUSY");
            return;
        }
        mk_cfg_mark_saved(h->cfg);
        emit_sack_ok(h, "CFG");
        return;
    }

    if (strcmp(sub, "RESET") == 0) {
        mk_cfg_reset(h->cfg);
        emit_sack_ok(h, "CFG");
        return;
    }

    emit_sack_err(h, "CFG", "UNSUPPORTED");
}

/* 규격 §7.4 — `$STAT` 은 stat 레코드 한 줄 뒤에 $SACK 를 보낸다.
 *
 * 🔴 CONFIG 전용이 아니다 (규격 §4). RUN 에서 상태를 못 보면 진단할 수
 *    없다 — 유실이 나는 것은 대개 RUN 일 때다. */
void mk_hostlink_attach_ads(MkHostlink *h, struct MkAds *ads)
{
    h->ads = ads;
}

void mk_hostlink_attach_rails(MkHostlink *h, struct MkRailCtl *rails)
{
    h->rails = rails;
}

static void on_stat(MkHostlink *h, int64_t now_ms)
{
    if (h->cfg == NULL) {
        emit_sack_err(h, "STAT", "UNSUPPORTED");
        return;
    }

    /* 🔴 수집기가 안 붙어 있으면 빈 배열이다. 0 을 채워 보내지 않는다 —
     *    "채널이 없다" 와 "채널이 있는데 유실이 0" 은 다른 말이고, 유실을
     *    찾으려고 이 창구를 보는 사람에게는 그 차이가 전부다.
     *
     * 🔴 켜진 채널만 싣는다. 그래서 `ch` 는 배열 첨자가 아니라 채널 번호다
     *    (규격 §7.4). 시뮬레이터도 같은 규칙이다. */
    MkQueueStat qs[MK_ADS_CHANNELS];
    size_t n_q = 0;
    if (h->ads != NULL) {
        for (int i = 0; i < MK_ADS_CHANNELS; i++) {
            MkQueue *q = mk_ads_queue(h->ads, i);
            if (q == NULL || !mk_ads_channel_enabled(h->ads, i)) {
                continue;
            }
            qs[n_q].ch = (uint8_t)i;
            qs[n_q].depth = mk_queue_count(q);
            qs[n_q].peak = mk_queue_peak(q);
            qs[n_q].drops = mk_queue_drops(q);
            n_q++;
        }
    }

    /* 🔴 7채널을 다 켰을 때의 최악 길이를 세어 잡은 값이다.
     *
     *      머리(schema_ver..uptime_ms, 최악값)      178
     *      rails                                     47
     *      queues 7개 (ch/depth/peak/drops 최댓값)   397
     *      + 줄바꿈 + NUL                              2
     *      ---------------------------------------------
     *                                               624
     *
     *    처음에 400 으로 잡아 두었다가 되돌림 검사에서 발견했다. 꺼진 채널을
     *    거르는 코드를 지웠더니 $STAT 이 ERR,BUSY 로 떨어졌는데, 그것은
     *    **7채널을 다 켜면 정상 동작에서도 그렇게 된다**는 뜻이었다.
     *    사용자가 채널을 다 켜는 순간 진단 창구가 닫히는 셈이다.
     *
     *    이 줄은 $ 프레임이 아니라 NDJSON 이라 MK_LINE_MAX(192)의 제약을
     *    받지 않는다. 그쪽은 명령·SACK 의 payload 한도다(규격 §3). */
    /* 🔴 설정표가 아니라 레일 제어기에서 읽는다. 설정은 "사용자가 원하는
     *    것" 이고 이것은 "보드가 낸 것" 이다 — 그 차이가 순차 기동 중에
     *    드러난다. 예전에는 mk_cfgwire_stat 이 설정표를 읽어서, 부팅 직후
     *    pwr.5v 기본값(true)이 그대로 나가 핀은 0 인데 5V ON 이라고 했다
     *    (실기기 확인 2026-08-14). */
    MkRailState rs = {0, 0, 0};
    if (h->rails != NULL) {
        rs.v24   = (uint8_t)mk_railctl_is_on(h->rails, MK_RAIL_24V);
        rs.v14v9 = (uint8_t)mk_railctl_is_on(h->rails, MK_RAIL_14V9);
        rs.v5    = (uint8_t)mk_railctl_is_on(h->rails, MK_RAIL_5V);
    }

    char body[768];
    int n = mk_cfgwire_stat(
        now_ms,
        mk_hostlink_mode(h, now_ms) == MK_MODE_CONFIG ? "CONFIG" : "RUN",
        h->fw, h->board_rev, (uint32_t)now_ms,
        /* 🔴 1단계에는 GNSS 도 PPS 도 없다. `t` 는 부팅 후 경과 ms 이고
         *    UTC 가 아니다 — 호스트가 이것을 시각으로 저장하면 안 된다.
         *    3단계에서 시간 소스가 붙으면 여기가 바뀐다. */
        "device_clock", 0u,
        &rs,
        n_q > 0 ? qs : NULL, n_q, body, sizeof body);

    if (!emit_json(h, body, sizeof body, n)) {
        emit_sack_err(h, "STAT", "BUSY");
        return;
    }
    emit_sack_ok(h, "STAT");
}

void mk_hostlink_attach_config(MkHostlink *h, MkConfig *cfg,
                               const MkFieldBit *fields, size_t n_fields,
                               MkCfgSave save, void *save_ctx)
{
    h->cfg = cfg;
    h->fields = fields;
    h->n_fields = n_fields;
    h->save = save;
    h->save_ctx = save_ctx;
}

void mk_hostlink_init(MkHostlink *h, MkEmit emit, void *ctx,
                      const char *device_id, const char *fw,
                      const char *board_rev)
{
    memset(h, 0, sizeof *h);
    h->emit = emit;
    h->ctx = ctx;
    h->device_id = device_id;
    h->fw = fw;
    h->board_rev = board_rev;
    h->last_hb_rx_ms = 0;
    h->last_hb_tx_ms = 0;
    h->hb_seen = 0;                      /* 부팅 직후는 RUN (§6.2) */
}

void mk_hostlink_feed(MkHostlink *h, const char *line, size_t len,
                      int64_t now_ms)
{
    MkCommand c;
    MkParseResult r = mk_parse_line(line, len, &c);

    if (r == MK_ERR_MALFORMED) {
        return;                          /* verb 를 못 읽었다 — 조용히 버린다 */
    }

    if (r == MK_ERR_CHECKSUM) {
        /* 🔴 $HB 는 예외다 (§3, §6.1). 체크섬이 틀려도 SACK 를 보내지
         *    않는다. 1 Hz 로 오므로 링크가 나빠지면 초당 하나씩 쌓여
         *    이미 나쁜 링크를 더 나쁘게 만든다. 알릴 내용은 어차피
         *    3초 뒤 RUN 전환으로 전달된다.
         *
         *    그리고 여기서 하트비트 시각을 갱신하지 **않는다**. 이것이
         *    §6.3 이 회귀 시험을 필수로 둔 바로 그 지점이다. */
        if (strcmp(c.verb, "HB") != 0) {
            emit_sack_err(h, c.verb, "CHECKSUM");
        }
        return;
    }

    /* 여기부터는 체크섬을 통과한 줄이다. */
    if (strcmp(c.verb, "HB") == 0) {
        /* §6.1 — 어느 방향이든 $HB 에는 $SACK 를 보내지 않는다. */
        h->last_hb_rx_ms = now_ms;
        h->hb_seen = 1;
        return;
    }

    if (strcmp(c.verb, "ID") == 0) {
        /* 레코드를 못 내보냈으면 OK 라고 하지 않는다. 거짓 OK 는 무응답보다
         * 나쁘다 — 호스트가 데이터를 받았다고 믿고 다음으로 넘어간다. */
        if (emit_id_record(h, now_ms)) {
            emit_sack_ok(h, "ID");
        }
        return;
    }

    if (strcmp(c.verb, "CFG") == 0) {
        on_cfg(h, &c, now_ms);
        return;
    }

    if (strcmp(c.verb, "STAT") == 0) {
        on_stat(h, now_ms);
        return;
    }

    /* 규격 §5 — 알아들었으나 이 펌웨어가 아직 구현하지 않은 명령.
     * 조용히 버리면 죽은 링크와 구분되지 않는다. */
    emit_sack_err(h, c.verb, "UNSUPPORTED");
}

void mk_hostlink_tick(MkHostlink *h, int64_t now_ms)
{
    /* 시뮬레이터(device_sim.tick)와 같은 조건이다 — `>=`. 부팅 직후
     * (now_ms 0, last_hb_tx_ms 0)에는 내보내지 않고 1000 ms 부터 시작한다. */
    if (now_ms - h->last_hb_tx_ms >= MK_HB_INTERVAL_MS) {
        h->last_hb_tx_ms = now_ms;
        emit_line(h, "HB");
    }
}

MkMode mk_hostlink_mode(const MkHostlink *h, int64_t now_ms)
{
    if (!h->hb_seen) {
        return MK_MODE_RUN;              /* 부팅 직후 기본값 */
    }
    /* 🔴 `>` 이지 `>=` 가 아니다. 정확히 3000 ms 가 지난 순간은 아직
     *    CONFIG 다. 시뮬레이터(device_sim.mode)와 같은 경계를 쓴다 —
     *    한쪽이 CONFIG 라고 보는 순간에 다른 쪽이 RUN 이면 설정 변경이
     *    간헐적으로 거부되고, 그 원인은 재현되지 않는다. */
    if (now_ms - h->last_hb_rx_ms > MK_HB_TIMEOUT_MS) {
        return MK_MODE_RUN;
    }
    return MK_MODE_CONFIG;
}
