#include <pebble.h>
#include <stdint.h>
#include <stdio.h>
#include "daily_schedule.h"

#define MINUTE_BATCH 60
/* 7 days x (~3 sleep + ~10 restful) = ~91, plus workouts; 256 x 12 B = 3 KB RAM. */
#define MAX_SESSIONS 256
#define MINUTES_7D (7U * 24U * 60U)
#define MINUTES_DAILY (48U * 60U)
#define ACK_OK 1
#define ACK_READY 10
#define DAILY_COOKIE 0x48495354
#define PERSIST_RUN_ID 91
#define PERSIST_DAILY_ENABLED 92
#define PERSIST_DAILY_RETRY 93

typedef struct { time_t start; uint32_t duration; uint8_t type; } StoredActivity;
static Window *window; static TextLayer *text; static char display[96];
static HealthMinuteData minutes[MINUTE_BATCH];
static StoredActivity activities[MAX_SESSIONS]; static uint16_t activity_count;
static time_t cursor, end_time, packet_next_cursor; static uint32_t seq, run_id; static uint16_t minute_count;
static bool paired, exporting, waiting_ack, sending_sessions, resume_pending, activity_overflow;
static bool daily_enabled, quiet_auto;
static WakeupId daily_wakeup = -1;
static AppTimer *ack_timer, *quiet_exit_timer, *run_watchdog, *pair_timer;
static bool auto_waiting_pair;
static void pause_export(void);

static void show(const char *s) { if (text) text_layer_set_text(text, s); }
static void quiet_exit(void *ctx) { (void)ctx; quiet_exit_timer=NULL; if (quiet_auto) window_stack_pop_all(false); }
static void finish_quiet(void) { if (quiet_auto) { if (quiet_exit_timer) app_timer_cancel(quiet_exit_timer); quiet_exit_timer=app_timer_register(1000,quiet_exit,NULL); } }
static void run_timeout(void *ctx) { (void)ctx; run_watchdog=NULL; if (quiet_auto && exporting) pause_export(); }
static void pair_timeout(void *ctx) { (void)ctx; pair_timer=NULL; if (quiet_auto && auto_waiting_pair) { auto_waiting_pair=false; show("Phone unavailable.\nDaily export will retry."); finish_quiet(); } }

/* One future wakeup keeps this app below the hourly HA :35 snapshot and lets
 * Pebble's clock_to_timestamp apply the watch timezone and DST rules. */
static void schedule_daily(void) {
  time_t now,target; struct tm local;
  /* Wakeup IDs are not durable across launches; wakeups are scoped to this
   * app, so cancel the app's prior slot before arming its single next slot. */
  wakeup_cancel_all(); daily_wakeup=-1;
  if (!daily_enabled || !clock_is_timezone_set()) return;
  now=time(NULL); local=*localtime(&now);
  /* Select the local calendar day before converting.  Pebble's explicit
   * weekday conversion can interpret a past local time as next week's slot. */
  int day=history_daily_weekday(local.tm_wday,local.tm_hour*3600+local.tm_min*60+local.tm_sec);
  /* An explicit weekday equal to today means next week on Pebble, even for a
   * later time today, so today's slot must use TODAY. */
  target=clock_to_timestamp(day==local.tm_wday?TODAY:(WeekDay)(day+1),5,15);
  daily_wakeup=wakeup_schedule(target,DAILY_COOKIE,false);
  for (int retry=0; daily_wakeup==E_RANGE && retry<4; retry++) { target+=120; daily_wakeup=wakeup_schedule(target,DAILY_COOKIE,false); }
  if (daily_wakeup<0) APP_LOG(APP_LOG_LEVEL_ERROR,"Daily history wakeup failed: %ld",(long)daily_wakeup);
}
static void pause_export(void) {
  if (ack_timer) { app_timer_cancel(ack_timer); ack_timer=NULL; }
  if (run_watchdog) { app_timer_cancel(run_watchdog); run_watchdog=NULL; }
  waiting_ack=false; exporting=false; resume_pending=true;
  persist_write_bool(PERSIST_DAILY_RETRY,true);
  show("Phone unavailable.\nSelect to retry."); finish_quiet();
}
static void ack_timeout(void *ctx) { (void)ctx; ack_timer=NULL; if (exporting && waiting_ack) pause_export(); }

static bool collect_activity(HealthActivity a,time_t start,time_t end,void *ctx) {
  (void)ctx;
  if (end<=start || start>=end_time || end<=cursor) return true;
  if (activity_count>=MAX_SESSIONS) { activity_overflow=true; return false; }
  uint8_t type=3;
  switch(a) { case HealthActivityWalk:type=1;break; case HealthActivityRun:type=2;break; case HealthActivityOpenWorkout:type=3;break; case HealthActivitySleep:type=4;break; case HealthActivityRestfulSleep:type=5;break; default:return true; }
  activities[activity_count++]=(StoredActivity){start,(uint32_t)(end-start),type}; return true;
}
static void put_u32(uint8_t *p,uint32_t n) { p[0]=n&255;p[1]=(n>>8)&255;p[2]=(n>>16)&255;p[3]=(n>>24)&255; }
static void put_u16(uint8_t *p,uint16_t n) { p[0]=n&255;p[1]=(n>>8)&255; }
static void send_sessions(void); static void continue_export(void *ctx);
static void send_minutes(void) {
  DictionaryIterator *it; uint8_t packed[MINUTE_BATCH*10]; uint16_t i,valid_count=0; time_t request_end,actual_start,actual_end;
  if (cursor>=end_time) { sending_sessions=true;send_sessions();return; }
  request_end=cursor+MINUTE_BATCH*60;if(request_end>end_time)request_end=end_time;actual_start=cursor;actual_end=request_end;
  minute_count=health_service_get_minute_history(minutes,MINUTE_BATCH,&actual_start,&actual_end);if(minute_count>MINUTE_BATCH)minute_count=MINUTE_BATCH;
  for(i=0;i<minute_count;i++) { uint8_t *p;if(minutes[i].is_invalid)continue;p=packed+valid_count*10;put_u32(p,(uint32_t)(actual_start+i*60));put_u16(p+4,minutes[i].heart_rate_bpm);put_u16(p+6,minutes[i].steps);put_u16(p+8,minutes[i].vmc);valid_count++; }
  packet_next_cursor=minute_count?actual_end:request_end;if(packet_next_cursor<=cursor||packet_next_cursor>end_time)packet_next_cursor=request_end;
  if(!valid_count){cursor=packet_next_cursor;app_timer_register(1,continue_export,NULL);return;}
  if(app_message_outbox_begin(&it)!=APP_MSG_OK){pause_export();return;}
  dict_write_data(it,MESSAGE_KEY_records,packed,valid_count*10);dict_write_uint16(it,MESSAGE_KEY_records_count,valid_count);dict_write_int32(it,MESSAGE_KEY_start,(int32_t)actual_start);dict_write_uint32(it,MESSAGE_KEY_seq,seq);dict_write_uint32(it,MESSAGE_KEY_run,run_id);
  if(app_message_outbox_send()!=APP_MSG_OK){pause_export();return;} waiting_ack=true;ack_timer=app_timer_register(25000,ack_timeout,NULL);
  snprintf(display,sizeof(display),"Exporting history\n%lu / %u minutes",(unsigned long)((cursor-(end_time-(quiet_auto?MINUTES_DAILY:MINUTES_7D)*60))/60),quiet_auto?MINUTES_DAILY:MINUTES_7D);show(display);
}
static void send_sessions(void) {
  DictionaryIterator *it;uint8_t packed[20*9];uint16_t n=activity_count>20?20:activity_count,i;
  if(!n){exporting=false;resume_pending=false;persist_write_bool(PERSIST_DAILY_RETRY,false);if(run_watchdog){app_timer_cancel(run_watchdog);run_watchdog=NULL;}show("History export complete.\nSelect to export again.");finish_quiet();return;}
  if(app_message_outbox_begin(&it)!=APP_MSG_OK){pause_export();return;}
  for(i=0;i<n;i++){uint8_t *p=packed+i*9;put_u32(p,(uint32_t)activities[i].start);put_u32(p+4,activities[i].duration);p[8]=activities[i].type;}
  dict_write_data(it,MESSAGE_KEY_session_data,packed,n*9);dict_write_uint16(it,MESSAGE_KEY_session_count,n);dict_write_int32(it,MESSAGE_KEY_session_start,(int32_t)activities[0].start);dict_write_uint32(it,MESSAGE_KEY_session_seq,seq);dict_write_uint32(it,MESSAGE_KEY_run,run_id);
  if(app_message_outbox_send()!=APP_MSG_OK){pause_export();return;}waiting_ack=true;ack_timer=app_timer_register(25000,ack_timeout,NULL);snprintf(display,sizeof(display),"Exporting sessions\n%u sessions left",activity_count);show(display);
}
static void begin_export(bool automatic) {
  if(exporting)return;
  quiet_auto=automatic;
  if(!automatic){auto_waiting_pair=false;if(pair_timer){app_timer_cancel(pair_timer);pair_timer=NULL;}}
  if(text){window_set_background_color(window,automatic?GColorBlack:GColorWhite);text_layer_set_background_color(text,automatic?GColorBlack:GColorWhite);text_layer_set_text_color(text,GColorBlack);if(automatic)light_enable(false);}
  if(!paired){DictionaryIterator *it;if(app_message_outbox_begin(&it)==APP_MSG_OK){dict_write_uint8(it,MESSAGE_KEY_command,1);app_message_outbox_send();}show("Pair in phone settings,\nthen press Select.");if(automatic){auto_waiting_pair=true;pair_timer=app_timer_register(12000,pair_timeout,NULL);}else finish_quiet();return;}
  if(activity_overflow){show(automatic?"Too many sessions in 48 hours.\nExport aborted.":"Too many sessions in 7 days.\nExport aborted.");finish_quiet();return;}
  if(resume_pending){if(automatic){if(run_watchdog)app_timer_cancel(run_watchdog);run_watchdog=app_timer_register(15*60*1000,run_timeout,NULL);}resume_pending=false;persist_write_bool(PERSIST_DAILY_RETRY,false);exporting=true;waiting_ack=false;if(sending_sessions)send_sessions();else send_minutes();return;}
  end_time=time(NULL);cursor=end_time-(automatic?MINUTES_DAILY:MINUTES_7D)*60;seq=0;run_id=(uint32_t)persist_read_int(PERSIST_RUN_ID)+1;persist_write_int(PERSIST_RUN_ID,(int32_t)run_id);activity_count=0;activity_overflow=false;waiting_ack=false;sending_sessions=false;exporting=true;if(automatic)run_watchdog=app_timer_register(15*60*1000,run_timeout,NULL);
  health_service_activities_iterate(HealthActivityWalk|HealthActivityRun|HealthActivityOpenWorkout|HealthActivitySleep|HealthActivityRestfulSleep,cursor,end_time,HealthIterationDirectionFuture,collect_activity,NULL);
  if(activity_overflow){exporting=false;if(run_watchdog){app_timer_cancel(run_watchdog);run_watchdog=NULL;}show(automatic?"Too many sessions in 48 hours.\nExport aborted.":"Too many sessions in 7 days.\nExport aborted.");finish_quiet();return;} send_minutes();
}
static void inbox(DictionaryIterator *it,void *ctx) {
  Tuple *ack=dict_find(it,MESSAGE_KEY_ack),*ack_seq=dict_find(it,MESSAGE_KEY_ack_seq),*ack_run=dict_find(it,MESSAGE_KEY_ack_run);uint32_t got,ackrun;(void)ctx;if(!ack)return;
  if(ack->value->int32==ACK_READY){paired=true;if(auto_waiting_pair){auto_waiting_pair=false;if(pair_timer){app_timer_cancel(pair_timer);pair_timer=NULL;}begin_export(true);return;}if(!quiet_auto)show("Phone paired.\nSelect: export 7 days.");return;}
  if(!exporting||!ack_seq||!ack_run)return;
  got=ack_seq->value->uint32;ackrun=ack_run->value->uint32;if(got!=seq||ackrun!=run_id)return;if(ack->value->int32!=ACK_OK){pause_export();return;}
  if(ack_timer){app_timer_cancel(ack_timer);ack_timer=NULL;}waiting_ack=false;
  if(sending_sessions){uint16_t n=activity_count>20?20:activity_count;if(n){uint16_t i;for(i=0;i+n<activity_count;i++)activities[i]=activities[i+n];activity_count-=n;}seq++;send_sessions();}else{cursor=packet_next_cursor;seq++;send_minutes();}
}
static void failed(DictionaryIterator *it,AppMessageResult r,void *ctx){(void)it;(void)r;(void)ctx;if(exporting)pause_export();}
static void continue_export(void *ctx){(void)ctx;if(exporting&&!waiting_ack)send_minutes();}
static void select_click(ClickRecognizerRef ref,void *ctx){(void)ref;(void)ctx;if(!exporting)begin_export(false);}
static void up_click(ClickRecognizerRef ref,void *ctx){(void)ref;(void)ctx;if(exporting)return;daily_enabled=!daily_enabled;persist_write_bool(PERSIST_DAILY_ENABLED,daily_enabled);schedule_daily();show(daily_enabled?"Daily export ON\n05:15 watch local\nSelect: export 7 days":"Daily export OFF\nUp: enable 05:15 export\nSelect: export 7 days");}
static void clicks(void *ctx){window_single_click_subscribe(BUTTON_ID_SELECT,select_click);window_single_click_subscribe(BUTTON_ID_UP,up_click);}
static void auto_begin(void *ctx){(void)ctx;if(!quiet_auto)return;if(!daily_enabled){finish_quiet();return;}/* A new run is safe: the phone queue retains old packets and the receiver is idempotent. */begin_export(true);}
static void woke(WakeupId id,int32_t cookie){(void)id;if(cookie!=DAILY_COOKIE||!daily_enabled)return;schedule_daily();if(exporting||auto_waiting_pair)return;quiet_auto=true;app_timer_register(1500,auto_begin,NULL);}
static void load(Window *w){GRect b=layer_get_bounds(window_get_root_layer(w));text=text_layer_create(GRect(4,20,b.size.w-8,b.size.h-30));text_layer_set_font(text,fonts_get_system_font(FONT_KEY_GOTHIC_24_BOLD));text_layer_set_text_alignment(text,GTextAlignmentCenter);text_layer_set_text(text,daily_enabled?"PebbleFit History 1.2\nDaily: 05:15 local\nSelect: export 7 days\nUp: disable daily":"PebbleFit History 1.2\nDaily export OFF\nSelect: export 7 days\nUp: enable daily");if(quiet_auto){window_set_background_color(w,GColorBlack);text_layer_set_background_color(text,GColorBlack);text_layer_set_text_color(text,GColorBlack);light_enable(false);}layer_add_child(window_get_root_layer(w),text_layer_get_layer(text));}
int main(void){daily_enabled=persist_exists(PERSIST_DAILY_ENABLED)?persist_read_bool(PERSIST_DAILY_ENABLED):true;quiet_auto=launch_reason()==APP_LAUNCH_WAKEUP;wakeup_service_subscribe(woke);schedule_daily();window=window_create();window_set_window_handlers(window,(WindowHandlers){.load=load});window_set_click_config_provider(window,clicks);window_stack_push(window,true);app_message_register_inbox_received(inbox);app_message_register_outbox_failed(failed);app_message_open(256,2048);if(quiet_auto)app_timer_register(1500,auto_begin,NULL);app_event_loop();return 0;}
