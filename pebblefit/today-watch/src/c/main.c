/* PebbleFit Today 1.0: read-only glance of the latest server recovery summary.
 * Values are experimental estimates computed by the PebbleFit receiver, not medical data.
 * The phone (Gravel PKJS) holds the scoped pairing token; the watch only caches numbers. */
#include <pebble.h>
#include <stdio.h>
#include <string.h>

#define NULL_VALUE (-1)
#define PAGE_COUNT 5
#define BASELINE_TARGET 14
#define STALE_SECONDS (12 * 60 * 60)
#define REPLY_TIMEOUT_MS 25000
#define PERSIST_VERSION_KEY 1
#define PERSIST_SUMMARY_KEY 2
#define PERSIST_PAGE_KEY 3
#define SUMMARY_VERSION 1

/* Phone status codes; same numbering as PebbleFit HRV / History. */
#define STATUS_OK 1
#define STATUS_UNPAIRED 2
#define STATUS_UNAUTHORIZED 3
#define STATUS_NETWORK 4
#define STATUS_TIMEOUT 5
#define STATUS_BAD_RESPONSE 6

typedef enum { LinkIdle, LinkRefreshing, LinkUnpaired, LinkPhoneUnreachable, LinkServerUnreachable, LinkServerError } LinkState;

typedef struct {
  int32_t ts, readiness, sleep_score, sleep_min, restful_min, hrv_status, hrv_ms10, weekly_hrv_ms10;
  int32_t baseline_nights, rhr, breathing10, load_acute, load_chronic, steps;
  char readiness_status[24];
} Summary;

static Window *window;
static Layer *canvas;
static Summary summary;
static bool have_summary;
static int page;
static LinkState link_state = LinkIdle;
static int32_t server_error;
static AppTimer *reply_timer;

static char big_buf[16], unit_buf[32], line1_buf[32], line2_buf[32], footer_buf[48], header_buf[24];

/* ---------- helpers ---------- */
static int32_t tuple_int(const Tuple *t, int32_t fallback) {
  if (!t) return fallback;
  if (t->type == TUPLE_INT) {
    if (t->length == 1) return t->value->int8;
    if (t->length == 2) return t->value->int16;
    if (t->length == 4) return t->value->int32;
  } else if (t->type == TUPLE_UINT) {
    if (t->length == 1) return t->value->uint8;
    if (t->length == 2) return t->value->uint16;
    if (t->length == 4) return (int32_t)t->value->uint32;
  }
  return fallback;
}
static int32_t find_int(DictionaryIterator *it, uint32_t key) { return tuple_int(dict_find(it, key), NULL_VALUE); }

static void fmt_int(char *buf, size_t n, int32_t v) {
  if (v < 0) snprintf(buf, n, "--"); else snprintf(buf, n, "%ld", (long)v);
}
static void fmt_tenths(char *buf, size_t n, int32_t v) {
  if (v < 0) snprintf(buf, n, "--"); else snprintf(buf, n, "%ld.%ld", (long)(v / 10), (long)(v % 10));
}
static void fmt_hm(char *buf, size_t n, int32_t minutes) {
  if (minutes < 0) snprintf(buf, n, "--"); else snprintf(buf, n, "%ld:%02ld", (long)(minutes / 60), (long)(minutes % 60));
}
static const char *hrv_word(int32_t s) {
  switch (s) {
    case 1: return "Balanced";
    case 2: return "Unbalanced (low)";
    case 3: return "Unbalanced (high)";
    case 4: return "Low";
    case 5: return "Not enough data";
    case 6: return "Baseline building";
    case NULL_VALUE: return "No status";
    default: return "Unknown status";
  }
}

/* ---------- persistence ---------- */
static void load_cache(void) {
  have_summary = false;
  if (persist_exists(PERSIST_VERSION_KEY) && persist_read_int(PERSIST_VERSION_KEY) == SUMMARY_VERSION &&
      persist_get_size(PERSIST_SUMMARY_KEY) == (int)sizeof(Summary)) {
    if (persist_read_data(PERSIST_SUMMARY_KEY, &summary, sizeof(Summary)) == (int)sizeof(Summary)) {
      summary.readiness_status[sizeof(summary.readiness_status) - 1] = '\0';
      have_summary = summary.ts > 0;
    }
  }
  if (persist_exists(PERSIST_PAGE_KEY)) {
    page = persist_read_int(PERSIST_PAGE_KEY);
    if (page < 0 || page >= PAGE_COUNT) page = 0;
  }
}
static void save_cache(void) {
  persist_write_data(PERSIST_SUMMARY_KEY, &summary, sizeof(Summary));
  persist_write_int(PERSIST_VERSION_KEY, SUMMARY_VERSION);
}

/* ---------- drawing ---------- */
static GColor page_color(int p) {
#ifdef PBL_COLOR
  switch (p) {
    case 0: return GColorJaegerGreen;
    case 1: return GColorIndigo;
    case 2: return GColorCobaltBlue;
    case 3: return GColorFolly;
    default: return GColorWindsorTan;
  }
#else
  (void)p;
  return GColorWhite;
#endif
}
/* Lighter tint of the page colour for text on black (legibility). */
static GColor page_tint(int p) {
#ifdef PBL_COLOR
  switch (p) {
    case 0: return GColorMintGreen;
    case 1: return GColorLavenderIndigo;
    case 2: return GColorPictonBlue;
    case 3: return GColorMelon;
    default: return GColorRajah;
  }
#else
  (void)p;
  return GColorWhite;
#endif
}
static const char *const page_titles[PAGE_COUNT] = {"READINESS", "SLEEP", "HRV", "RESTING", "LOAD"};

static void format_age(char *buf, size_t n, time_t ts) {
  time_t now = time(NULL);
  int32_t age = now > ts ? (int32_t)(now - ts) : 0;
  if (age < 60) snprintf(buf, n, "updated just now");
  else if (age < 3600) snprintf(buf, n, "updated %ldm ago", (long)(age / 60));
  else if (age < 48 * 3600) snprintf(buf, n, "updated %ldh ago", (long)(age / 3600));
  else snprintf(buf, n, "updated %ldd ago", (long)(age / 86400));
}

/* Footer: link problems take precedence over age, but age is always shown when cached. */
static GColor footer_text(char *buf, size_t n) {
  char age[24];
  bool stale;
  format_age(age, sizeof(age), (time_t)summary.ts);
  stale = time(NULL) - (time_t)summary.ts > STALE_SECONDS;
  switch (link_state) {
    case LinkRefreshing: snprintf(buf, n, "Refreshing...\n%s", age); return GColorWhite;
    case LinkUnpaired: snprintf(buf, n, "Pair in phone app\n%s", age); return PBL_IF_COLOR_ELSE(GColorChromeYellow, GColorWhite);
    case LinkPhoneUnreachable: snprintf(buf, n, "Phone unreachable\n%s", age); return PBL_IF_COLOR_ELSE(GColorSunsetOrange, GColorWhite);
    case LinkServerUnreachable: snprintf(buf, n, "Server offline\n%s", age); return PBL_IF_COLOR_ELSE(GColorSunsetOrange, GColorWhite);
    case LinkServerError: snprintf(buf, n, "Server error %ld\n%s", (long)server_error, age); return PBL_IF_COLOR_ELSE(GColorSunsetOrange, GColorWhite);
    default: break;
  }
  if (stale) { snprintf(buf, n, "Stale data\n%s", age); return PBL_IF_COLOR_ELSE(GColorChromeYellow, GColorWhite); }
  snprintf(buf, n, "%s", age);
  return PBL_IF_COLOR_ELSE(GColorLightGray, GColorWhite);
}

static void fill_page(void) {
  char a[16], b[16];
  unit_buf[0] = line1_buf[0] = line2_buf[0] = '\0';
  switch (page) {
    case 0:
      fmt_int(big_buf, sizeof(big_buf), summary.readiness);
      snprintf(unit_buf, sizeof(unit_buf), "%s", summary.readiness_status[0] ? summary.readiness_status : "no status");
      snprintf(line1_buf, sizeof(line1_buf), "readiness / 100");
      break;
    case 1:
      fmt_int(big_buf, sizeof(big_buf), summary.sleep_score);
      snprintf(unit_buf, sizeof(unit_buf), "sleep score");
      fmt_hm(a, sizeof(a), summary.sleep_min);
      fmt_hm(b, sizeof(b), summary.restful_min);
      snprintf(line1_buf, sizeof(line1_buf), "Asleep %s", a);
      snprintf(line2_buf, sizeof(line2_buf), "Restful %s", b);
      break;
    case 2:
      fmt_tenths(big_buf, sizeof(big_buf), summary.hrv_ms10);
      snprintf(unit_buf, sizeof(unit_buf), "ms last night");
      fmt_tenths(a, sizeof(a), summary.weekly_hrv_ms10);
      snprintf(line1_buf, sizeof(line1_buf), "7-night avg %s", a);
      if (summary.hrv_status == 6 || (summary.hrv_status == NULL_VALUE && summary.baseline_nights >= 0 && summary.baseline_nights < BASELINE_TARGET))
        snprintf(line2_buf, sizeof(line2_buf), "Baseline %ld/%d", (long)(summary.baseline_nights < 0 ? 0 : summary.baseline_nights), BASELINE_TARGET);
      else snprintf(line2_buf, sizeof(line2_buf), "%s", hrv_word(summary.hrv_status));
      break;
    case 3:
      fmt_int(big_buf, sizeof(big_buf), summary.rhr);
      snprintf(unit_buf, sizeof(unit_buf), "bpm resting HR");
      fmt_tenths(a, sizeof(a), summary.breathing10);
      snprintf(line1_buf, sizeof(line1_buf), "Breathing %s", a);
      snprintf(line2_buf, sizeof(line2_buf), "breaths / min");
      break;
    default:
      fmt_int(big_buf, sizeof(big_buf), summary.load_acute);
      snprintf(unit_buf, sizeof(unit_buf), "acute load");
      fmt_int(a, sizeof(a), summary.load_chronic);
      snprintf(line1_buf, sizeof(line1_buf), "Chronic %s", a);
      fmt_int(b, sizeof(b), summary.steps);
      snprintf(line2_buf, sizeof(line2_buf), "Steps %s", b);
      break;
  }
}

static void draw_text(GContext *ctx, const char *s, const char *font, GRect box, GColor color) {
  graphics_context_set_text_color(ctx, color);
  graphics_draw_text(ctx, s, fonts_get_system_font(font), box, GTextOverflowModeTrailingEllipsis, GTextAlignmentCenter, NULL);
}

/* Full-screen state when there is no cached summary to show. */
static void draw_message(GContext *ctx, GRect b) {
  const char *title, *body;
  GColor accent = PBL_IF_COLOR_ELSE(GColorChromeYellow, GColorWhite);
  switch (link_state) {
    case LinkUnpaired: title = "Pair in phone app"; body = "Gravel > PebbleFit Today > Settings. Then press Select."; break;
    case LinkPhoneUnreachable: title = "Phone unreachable"; body = "Check Bluetooth and Gravel. Select: retry"; accent = PBL_IF_COLOR_ELSE(GColorSunsetOrange, GColorWhite); break;
    case LinkServerUnreachable: title = "Server offline"; body = "Phone could not reach PebbleFit. Select: retry"; accent = PBL_IF_COLOR_ELSE(GColorSunsetOrange, GColorWhite); break;
    case LinkServerError: snprintf(footer_buf, sizeof(footer_buf), "Server error %ld. Select: retry", (long)server_error); title = "Error"; body = footer_buf; accent = PBL_IF_COLOR_ELSE(GColorSunsetOrange, GColorWhite); break;
    case LinkRefreshing: title = "PebbleFit Today"; body = "Loading from phone..."; accent = GColorWhite; break;
    default: title = "No data yet"; body = "Nothing summarised yet. Wear the watch overnight. Select: refresh"; break;
  }
  draw_text(ctx, title, FONT_KEY_GOTHIC_28_BOLD, GRect(6, b.size.h / 2 - 70, b.size.w - 12, 64), accent);
  draw_text(ctx, body, FONT_KEY_GOTHIC_24_BOLD, GRect(8, b.size.h / 2 - 4, b.size.w - 16, 100), GColorWhite);
}

static void canvas_update(Layer *layer, GContext *ctx) {
  GRect b = layer_get_bounds(layer);
  GColor footer_color;
  int16_t w = b.size.w, h = b.size.h, i, dot_x;
  graphics_context_set_fill_color(ctx, GColorBlack);
  graphics_fill_rect(ctx, b, 0, GCornerNone);
  if (!have_summary) { draw_message(ctx, b); return; }

  /* Header: coloured bar, page title, page position. */
  graphics_context_set_fill_color(ctx, page_color(page));
  graphics_fill_rect(ctx, GRect(0, 0, w, 30), 0, GCornerNone);
  snprintf(header_buf, sizeof(header_buf), "%s", page_titles[page]);
  draw_text(ctx, header_buf, FONT_KEY_GOTHIC_24_BOLD, GRect(0, -2, w, 30), PBL_IF_COLOR_ELSE(GColorWhite, GColorBlack));

  fill_page();
  draw_text(ctx, big_buf, FONT_KEY_BITHAM_42_BOLD, GRect(0, 28, w, 52), GColorWhite);
  draw_text(ctx, unit_buf, FONT_KEY_GOTHIC_24_BOLD, GRect(4, 80, w - 8, 28), page_tint(page));
  draw_text(ctx, line1_buf, FONT_KEY_GOTHIC_24_BOLD, GRect(4, 106, w - 8, 28), GColorWhite);
  draw_text(ctx, line2_buf, FONT_KEY_GOTHIC_24_BOLD, GRect(4, 132, w - 8, 28), GColorWhite);
  draw_text(ctx, "Experimental estimate", FONT_KEY_GOTHIC_14, GRect(0, 160, w, 16), PBL_IF_COLOR_ELSE(GColorLightGray, GColorWhite));

  /* Up to two footer lines (state, then age) between the disclaimer and the page dots. */
  footer_color = footer_text(footer_buf, sizeof(footer_buf));
  draw_text(ctx, footer_buf, FONT_KEY_GOTHIC_18_BOLD, GRect(2, h - 54, w - 4, 42), footer_color);

  /* Page dots. */
  dot_x = w / 2 - (PAGE_COUNT - 1) * 6;
  for (i = 0; i < PAGE_COUNT; i++) {
    GPoint c = GPoint(dot_x + i * 12, h - 9);
    graphics_context_set_fill_color(ctx, i == page ? GColorWhite : PBL_IF_COLOR_ELSE(GColorDarkGray, GColorBlack));
    graphics_context_set_stroke_color(ctx, GColorWhite);
    graphics_fill_circle(ctx, c, 3);
    if (i != page) graphics_draw_circle(ctx, c, 3);
  }
}

static void redraw(void) { if (canvas) layer_mark_dirty(canvas); }

/* ---------- messaging ---------- */
static void cancel_reply_timer(void) { if (reply_timer) { app_timer_cancel(reply_timer); reply_timer = NULL; } }
static void reply_timeout(void *ctx) {
  (void)ctx;
  reply_timer = NULL;
  if (link_state == LinkRefreshing) { link_state = LinkPhoneUnreachable; redraw(); }
}
static void await_reply(void) {
  cancel_reply_timer();
  link_state = LinkRefreshing;
  reply_timer = app_timer_register(REPLY_TIMEOUT_MS, reply_timeout, NULL);
  redraw();
}
static void request_refresh(void) {
  DictionaryIterator *it;
  if (!connection_service_peek_pebble_app_connection()) { cancel_reply_timer(); link_state = LinkPhoneUnreachable; redraw(); return; }
  if (app_message_outbox_begin(&it) != APP_MSG_OK) { cancel_reply_timer(); link_state = LinkPhoneUnreachable; redraw(); return; }
  dict_write_uint8(it, MESSAGE_KEY_command, 1);
  if (app_message_outbox_send() != APP_MSG_OK) { cancel_reply_timer(); link_state = LinkPhoneUnreachable; redraw(); return; }
  await_reply();
}

static void inbox(DictionaryIterator *it, void *ctx) {
  Tuple *st = dict_find(it, MESSAGE_KEY_status), *text;
  int32_t code;
  (void)ctx;
  if (!st) return;
  code = tuple_int(st, 0);
  cancel_reply_timer();
  if (code == STATUS_OK) {
    Summary s;
    memset(&s, 0, sizeof(s));
    s.ts = find_int(it, MESSAGE_KEY_ts);
    s.readiness = find_int(it, MESSAGE_KEY_readiness);
    s.sleep_score = find_int(it, MESSAGE_KEY_sleep_score);
    s.sleep_min = find_int(it, MESSAGE_KEY_sleep_min);
    s.restful_min = find_int(it, MESSAGE_KEY_restful_min);
    s.hrv_status = find_int(it, MESSAGE_KEY_hrv_status);
    s.hrv_ms10 = find_int(it, MESSAGE_KEY_hrv_ms10);
    s.weekly_hrv_ms10 = find_int(it, MESSAGE_KEY_weekly_hrv_ms10);
    s.baseline_nights = find_int(it, MESSAGE_KEY_baseline_nights);
    s.rhr = find_int(it, MESSAGE_KEY_rhr);
    s.breathing10 = find_int(it, MESSAGE_KEY_breathing10);
    s.load_acute = find_int(it, MESSAGE_KEY_load_acute);
    s.load_chronic = find_int(it, MESSAGE_KEY_load_chronic);
    s.steps = find_int(it, MESSAGE_KEY_steps);
    text = dict_find(it, MESSAGE_KEY_readiness_status);
    if (text && text->type == TUPLE_CSTRING) {
      strncpy(s.readiness_status, text->value->cstring, sizeof(s.readiness_status) - 1);
      s.readiness_status[sizeof(s.readiness_status) - 1] = '\0';
    }
    link_state = LinkIdle;
    if (s.ts > 0) { summary = s; have_summary = true; save_cache(); }
    /* ts sentinel: server has no summary yet. Keep any older cache rather than erase it. */
  } else if (code == STATUS_UNPAIRED || code == STATUS_UNAUTHORIZED) link_state = LinkUnpaired;
  else if (code == STATUS_NETWORK || code == STATUS_TIMEOUT) link_state = LinkServerUnreachable;
  else { link_state = LinkServerError; server_error = code >= 1000 ? code - 1000 : code; }
  redraw();
}
static void outbox_failed(DictionaryIterator *it, AppMessageResult reason, void *ctx) {
  (void)it; (void)reason; (void)ctx;
  cancel_reply_timer();
  link_state = LinkPhoneUnreachable;
  redraw();
}
static void app_connection(bool connected) {
  if (!connected) { cancel_reply_timer(); link_state = LinkPhoneUnreachable; redraw(); }
  else if (link_state == LinkPhoneUnreachable) request_refresh();
}

/* ---------- input / lifecycle ---------- */
static void up_click(ClickRecognizerRef ref, void *ctx) { (void)ref; (void)ctx; if (have_summary) { page = (page + PAGE_COUNT - 1) % PAGE_COUNT; redraw(); } }
static void down_click(ClickRecognizerRef ref, void *ctx) { (void)ref; (void)ctx; if (have_summary) { page = (page + 1) % PAGE_COUNT; redraw(); } }
static void select_click(ClickRecognizerRef ref, void *ctx) { (void)ref; (void)ctx; request_refresh(); }
static void clicks(void *ctx) {
  (void)ctx;
  window_single_click_subscribe(BUTTON_ID_UP, up_click);
  window_single_click_subscribe(BUTTON_ID_DOWN, down_click);
  window_single_click_subscribe(BUTTON_ID_SELECT, select_click);
}
static void minute_tick(struct tm *tm, TimeUnits changed) { (void)tm; (void)changed; redraw(); }

static void window_load(Window *w) {
  Layer *root = window_get_root_layer(w);
  canvas = layer_create(layer_get_bounds(root));
  layer_set_update_proc(canvas, canvas_update);
  layer_add_child(root, canvas);
}
static void window_unload(Window *w) { (void)w; layer_destroy(canvas); canvas = NULL; }

int main(void) {
  load_cache();
  window = window_create();
  window_set_background_color(window, GColorBlack);
  window_set_window_handlers(window, (WindowHandlers){.load = window_load, .unload = window_unload});
  window_set_click_config_provider(window, clicks);
  app_message_register_inbox_received(inbox);
  app_message_register_outbox_failed(outbox_failed);
  app_message_open(512, 64);
  connection_service_subscribe((ConnectionHandlers){.pebble_app_connection_handler = app_connection});
  tick_timer_service_subscribe(MINUTE_UNIT, minute_tick);
  /* The phone fetches on PKJS 'ready'; wait for that push instead of sending a duplicate request. */
  if (connection_service_peek_pebble_app_connection()) await_reply(); else link_state = LinkPhoneUnreachable;
  window_stack_push(window, true);
  app_event_loop();
  persist_write_int(PERSIST_PAGE_KEY, page);
  cancel_reply_timer();
  tick_timer_service_unsubscribe();
  connection_service_unsubscribe();
  window_destroy(window);
  return 0;
}
