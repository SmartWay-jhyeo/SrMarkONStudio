#include "mk_hostlink.h"

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
