/* Three-minute raw PPI experiment. Never presents unvalidated RMSSD/stress. */
#include <pebble.h>
#include <stdlib.h>
#include "schedule.h"
#define CAPACITY 512
#define COLLECTOR_VERSION 151
static Window *window;
static TextLayer *text;
static uint16_t intervals[CAPACITY];
static uint32_t offsets[CAPACITY];
static uint16_t count;
static time_t start_sec;
static uint16_t start_ms;
static bool running, ready, paired;
static bool quiet, auto_pending, measurement_night;
static bool nights_enabled;
static AppTimer *night_watchdog;
static int32_t schedule_ok, schedule_error, next_wakeup, accel_rate_result;
static uint32_t accel_callbacks, vibration_samples;
static bool slot_started;
static int32_t schedule_ids[6],schedule_times[6];
static int32_t collector_phase=1;
static bool raw_motion;
static uint16_t measurement_version=COLLECTOR_VERSION;
static uint32_t hrv_events,zero_ppi_events,hr_events,hr_sum,hr_valid;
static char firmware[16];
static bool capacity_limited;
static void get_firmware(char *value,size_t size){WatchInfoVersion fw=watch_info_get_firmware_version();snprintf(value,size,"%u.%u.%u",fw.major,fw.minor,fw.patch);}
static void check_phone(void *context);
static void start(void);
static void send_result(void);
static void exit_night(void *context) { if(quiet) window_stack_pop_all(false); }
static void night_timeout(void *context){night_watchdog=NULL;collector_phase=4;check_phone(NULL);app_timer_register(1000,exit_night,NULL);}
static void close_soon(void) { if(quiet) app_timer_register(2500,exit_night,NULL); }
/* Closing the watch app also stops the phone script, which would abort an
 * upload still in flight; at night wait for its confirmation (bounded). */
static AppTimer *upload_wait;
static void upload_wait_over(void *context){upload_wait=NULL;exit_night(NULL);}
static void schedule_nights(void) {
  schedule_ok=0;schedule_error=0;next_wakeup=0;
  for(int i=0;i<6;i++){schedule_ids[i]=-1;schedule_times[i]=0;}
  wakeup_cancel_all();
  if(!nights_enabled)return;
  if(!clock_is_timezone_set()){schedule_error=E_INVALID_ARGUMENT;return;}
  time_t now=time(NULL);struct tm local=*localtime(&now);
  int weekdays[6],hours[6];
  int n=night_slots(local.tm_wday,local.tm_hour*3600+local.tm_min*60+local.tm_sec,weekdays,hours);
  for(int i=0;i<n;i++) {
    /* Pebble maps an explicit weekday equal to today to *next week*, even for
     * a later time today, so same-day slots must use TODAY. */
    time_t target=clock_to_timestamp(weekdays[i]==local.tm_wday?TODAY:(WeekDay)(weekdays[i]+1),hours[i],0);
    WakeupId id=wakeup_schedule(target,0x485256,false);
    for(int retry=0;id==E_RANGE && retry<4;retry++){target+=120;id=wakeup_schedule(target,0x485256,false);}
    schedule_ids[i]=id;schedule_times[i]=target;
    if(id>=0){schedule_ok++;if(!next_wakeup || target<next_wakeup)next_wakeup=target;}else schedule_error=id;
    APP_LOG(APP_LOG_LEVEL_INFO,"Night slot %d: %ld id=%ld",hours[i],(long)target,(long)id);
  }
}
static void begin_night(void *context) {
  if(running || slot_started)return;
  if(ready && count){send_result();return;}
  slot_started=true;start();
}
static void woke(WakeupId id,int32_t cookie) {
  schedule_nights();
  if(running)return;
  quiet=true;auto_pending=true;slot_started=false;begin_night(NULL);
}
static uint32_t motion, motion_samples;
static int16_t px, py, pz;
static bool previous;
static char display[160];

static uint32_t elapsed(void) {
  time_t seconds; uint16_t millis;
  time_ms(&seconds, &millis);
  return (uint32_t)((seconds-start_sec)*1000 + millis-start_ms);
}
static void send_result(void) {
  DictionaryIterator *iter;
  if (!count) { text_layer_set_text(text,"No intervals received.\nCheck firmware and fit.\nSelect to try again."); return; }
  if(app_message_outbox_begin(&iter)!=APP_MSG_OK) {text_layer_set_text(text,"Phone not ready.\nSelect to retry upload.");return;}
  dict_write_data(iter,MESSAGE_KEY_ppi,(uint8_t*)intervals,count*sizeof(uint16_t));
  dict_write_data(iter,MESSAGE_KEY_offsets,(uint8_t*)offsets,count*sizeof(uint32_t));
  int32_t ts=(int32_t)start_sec;
  dict_write_int(iter,MESSAGE_KEY_ts,&ts,sizeof(ts),true);
  dict_write_int(iter,MESSAGE_KEY_motion,&motion,sizeof(motion),false);
  int32_t night=measurement_night?1:0;
  dict_write_int(iter,MESSAGE_KEY_night,&night,sizeof(night),true);
  dict_write_int(iter,MESSAGE_KEY_motion_samples,&motion_samples,sizeof(motion_samples),false);
  dict_write_uint16(iter,MESSAGE_KEY_collector_version,measurement_version);
  dict_write_uint32(iter,MESSAGE_KEY_accel_callbacks,accel_callbacks);
  dict_write_uint32(iter,MESSAGE_KEY_vibration_samples,vibration_samples);
  dict_write_int32(iter,MESSAGE_KEY_accel_rate_result,accel_rate_result);
  dict_write_uint8(iter,MESSAGE_KEY_motion_source,raw_motion?1:0);
  dict_write_uint32(iter,MESSAGE_KEY_hrv_events,hrv_events);
  dict_write_uint32(iter,MESSAGE_KEY_zero_ppi_events,zero_ppi_events);
  dict_write_uint32(iter,MESSAGE_KEY_hr_events,hr_events);
  dict_write_uint32(iter,MESSAGE_KEY_hr_sum,hr_sum);
  dict_write_uint32(iter,MESSAGE_KEY_hr_valid,hr_valid);
  dict_write_cstring(iter,MESSAGE_KEY_firmware,firmware);
  dict_write_uint8(iter,MESSAGE_KEY_capacity_limited,capacity_limited);
  app_message_outbox_send();
  text_layer_set_text(text,"Uploading raw intervals…\nKeep this app open.");
}
static void save_measurement(void) {
  persist_delete(30);
  for(int i=0;i<CAPACITY*2/256;i++) persist_write_data(40+i,((uint8_t*)intervals)+i*256,256);
  for(int i=0;i<CAPACITY*4/256;i++) persist_write_data(50+i,((uint8_t*)offsets)+i*256,256);
  persist_write_int(31,(int32_t)start_sec);
  persist_write_int(32,(int32_t)motion);
  persist_write_bool(33,measurement_night);
  persist_write_int(34,motion_samples);
  persist_write_int(35,accel_callbacks);persist_write_int(36,vibration_samples);persist_write_int(37,accel_rate_result);persist_write_bool(38,raw_motion);persist_write_int(39,measurement_version);
  persist_write_int(61,hrv_events);persist_write_int(62,zero_ppi_events);persist_write_int(63,hr_events);persist_write_int(64,hr_sum);persist_write_int(65,hr_valid);persist_write_string(66,firmware);persist_write_bool(67,capacity_limited);
  persist_write_int(30,count);
}
static void check_phone(void *context) {
  DictionaryIterator *iter;char current_firmware[16];get_firmware(current_firmware,sizeof(current_firmware));
  if(app_message_outbox_begin(&iter)==APP_MSG_OK){
    dict_write_uint8(iter,MESSAGE_KEY_command,2);
    dict_write_int32(iter,MESSAGE_KEY_phase,collector_phase);
    dict_write_uint8(iter,MESSAGE_KEY_launch,quiet?1:0);
    dict_write_data(iter,MESSAGE_KEY_schedule_ids,(uint8_t*)schedule_ids,sizeof(schedule_ids));
    dict_write_data(iter,MESSAGE_KEY_schedule_times,(uint8_t*)schedule_times,sizeof(schedule_times));
    dict_write_uint16(iter,MESSAGE_KEY_collector_version,COLLECTOR_VERSION);
    dict_write_cstring(iter,MESSAGE_KEY_firmware,current_firmware);
    dict_write_int32(iter,MESSAGE_KEY_schedule_ok,schedule_ok);
    dict_write_int32(iter,MESSAGE_KEY_schedule_error,schedule_error);
    dict_write_int32(iter,MESSAGE_KEY_next_wakeup,next_wakeup);
    dict_write_uint8(iter,MESSAGE_KEY_night,nights_enabled?1:0);
    dict_write_int32(iter,MESSAGE_KEY_ts,time(NULL));app_message_outbox_send();}
}
static void finish(void) {
  capacity_limited=count==CAPACITY && elapsed()<180000;
  running=false; ready=true;collector_phase=3;
  health_service_set_hrv_sample_period(0);
  health_service_set_heart_rate_sample_period(0);
  accel_data_service_unsubscribe();
  save_measurement();
  send_result();
}
static void on_health(HealthEventType type, void *context) {
  if(!running)return;
  if(type==HealthEventHeartRateUpdate){hr_events++;int bpm=health_service_peek_current_value(HealthMetricHeartRateBPM);if(bpm>0 && bpm<=240){hr_sum+=bpm;hr_valid++;}return;}
  if(type!=HealthEventHRVUpdate)return;
  hrv_events++;
  uint16_t ppi=health_service_peek_hrv_ppi_ms();
  if(!ppi)zero_ppi_events++;
  if(ppi && count<CAPACITY) {intervals[count]=ppi;offsets[count]=elapsed();count++;}
  if(count==CAPACITY) finish();
}
static void on_motion(AccelRawData *data,uint32_t n,uint64_t timestamp) {
  if(!running)return;
  accel_callbacks++;
  for(uint32_t i=0;i<n;i++) {
    motion_samples++;
    if(previous) motion+=abs(data[i].x-px)+abs(data[i].y-py)+abs(data[i].z-pz);
    px=data[i].x;py=data[i].y;pz=data[i].z;previous=true;
  }
}
static void start(void) {
  if(running)return;
  if(ready && count){send_result();return;}
  if(!paired && !quiet){text_layer_set_text(text,"Pair in phone app:\nPebbleFit HRV settings\nEnable uploads.\nThen press Select.");check_phone(NULL);return;}
  get_firmware(firmware,sizeof(firmware));capacity_limited=false;
  measurement_version=COLLECTOR_VERSION;hrv_events=0;zero_ppi_events=0;hr_events=0;hr_sum=0;hr_valid=0;
  count=0; motion=0; motion_samples=0; previous=false;accel_callbacks=0;vibration_samples=0;
  persist_delete(30);measurement_night=quiet;auto_pending=false;
  time_ms(&start_sec,&start_ms);
  if(!health_service_set_hrv_sample_period(1)) {text_layer_set_text(text,"HRV request rejected.\nFirmware 4.32+ required.");close_soon();return;}
  health_service_set_heart_rate_sample_period(1);
  running=true;raw_motion=true;collector_phase=2;
  check_phone(NULL);
  accel_raw_data_service_subscribe(25,on_motion);
  accel_rate_result=accel_service_set_sampling_rate(ACCEL_SAMPLING_25HZ);
  if(quiet){if(night_watchdog)app_timer_cancel(night_watchdog);night_watchdog=app_timer_register(260000,night_timeout,NULL);}
}
static void tick(struct tm *tm,TimeUnits changed) {
  if(!running)return;
  uint32_t seconds=elapsed()/1000;
  if(seconds==8 && !accel_callbacks){accel_data_service_unsubscribe();accel_raw_data_service_subscribe(25,on_motion);accel_rate_result=accel_service_set_sampling_rate(ACCEL_SAMPLING_25HZ);}
  if(seconds>=180){finish();return;}
  if(quiet || (seconds>1 && seconds%30!=0))return;
  snprintf(display,sizeof(display),"Rest your arm\nBreathe normally\n%lu seconds left\n%u pulse intervals\nRaw diagnostic only",(unsigned long)(180-seconds),count);
  text_layer_set_text(text,display);
}
static void select_click(ClickRecognizerRef ref,void *context){quiet=false;window_set_background_color(window,GColorWhite);text_layer_set_background_color(text,GColorWhite);text_layer_set_text_color(text,GColorBlack);if(night_watchdog){app_timer_cancel(night_watchdog);night_watchdog=NULL;}start();}
static void down_click(ClickRecognizerRef ref,void *context){if(!running){ready=false;count=0;start();}}
static void up_click(ClickRecognizerRef ref,void *context){
 if(running)return;
 nights_enabled=!nights_enabled;persist_write_bool(80,nights_enabled);schedule_nights();
 char next[16]="none";
 if(next_wakeup){time_t target=next_wakeup;strftime(next,sizeof(next),"%a %H:%M",localtime(&target));}
 snprintf(display,sizeof(display),"Nights %s\n%ld slots armed\nNext: %s\nError: %ld",nights_enabled?"enabled":"disabled",(long)schedule_ok,next,(long)schedule_error);
 text_layer_set_text(text,display);check_phone(NULL);
}
static void clicks(void *context){window_single_click_subscribe(BUTTON_ID_UP,up_click);window_single_click_subscribe(BUTTON_ID_SELECT,select_click);window_single_click_subscribe(BUTTON_ID_DOWN,down_click);}
static void inbox(DictionaryIterator *iter,void *context){
  Tuple *ack=dict_find(iter,MESSAGE_KEY_ack);if(!ack)return;
  int code=ack->value->int32;
  if(code==10){paired=true;if(!ready && !running && !quiet)text_layer_set_text(text,nights_enabled?"Phone paired.\nSelect: manual test\nNight: 02 / 03 / 04\nUp: disable nights":"Phone paired.\nSelect: manual test\nUp: enable nights");return;}
  Tuple *ack_ts=dict_find(iter,MESSAGE_KEY_ack_ts);
  bool matches=ack_ts && ack_ts->value->int32==(int32_t)start_sec;
  if(code==1 || code==11){
    if(!matches || running)return;
    ready=false;count=0;persist_delete(30);
    text_layer_set_text(text,code==1?"Raw intervals saved.\nReview on Health.\nDown: new measurement":"Saved on phone.\nUpload retries later.\nDown: new measurement");
    if(quiet && auto_pending){auto_pending=false;slot_started=true;start();}
    else if(quiet && code==11){if(!upload_wait)upload_wait=app_timer_register(45000,upload_wait_over,NULL);}
    else{if(upload_wait){app_timer_cancel(upload_wait);upload_wait=NULL;}close_soon();}
  }
  else if(code==2 || code==3){paired=false;text_layer_set_text(text,"Pair in phone app:\nPebbleFit HRV settings.\nMeasurement kept.\nSelect: retry upload");}
  else{ready=count>0;snprintf(display,sizeof(display),"Upload error %d\nMeasurement kept.\nSelect: retry",code>=1000?code-1000:code);text_layer_set_text(text,display);}
}
static void failed(DictionaryIterator *iter,AppMessageResult reason,void *context){if(running)return;ready=count>0;text_layer_set_text(text,"Phone unreachable.\nSelect: retry upload");}
static void load(Window *w){
 GRect bounds=layer_get_bounds(window_get_root_layer(w));
 text=text_layer_create(GRect(8,24,bounds.size.w-16,bounds.size.h-32));
 text_layer_set_font(text,fonts_get_system_font(FONT_KEY_GOTHIC_24_BOLD));
 text_layer_set_text_alignment(text,GTextAlignmentCenter);
 if(quiet){window_set_background_color(w,GColorBlack);text_layer_set_background_color(text,GColorBlack);text_layer_set_text_color(text,GColorBlack);light_enable(false);}
 text_layer_set_text(text,nights_enabled?"PebbleFit HRV 1.5\nSelect: manual test\nNight: 02 / 03 / 04\nUp: disable nights":"PebbleFit HRV 1.5\nSelect: manual test\nUp: enable nights");
 layer_add_child(window_get_root_layer(w),text_layer_get_layer(text));
}
int main(void){
 get_firmware(firmware,sizeof(firmware));
 nights_enabled=persist_exists(80)?persist_read_bool(80):true;
 quiet=launch_reason()==APP_LAUNCH_WAKEUP;auto_pending=quiet;
 wakeup_service_subscribe(woke);schedule_nights();
 window=window_create();window_set_window_handlers(window,(WindowHandlers){.load=load});
 window_set_click_config_provider(window,clicks);window_stack_push(window,true);
 app_message_register_inbox_received(inbox);app_message_register_outbox_failed(failed);
 app_message_open(256,4096);
 int saved=persist_read_int(30);
 if(saved>0 && saved<=CAPACITY){
  measurement_version=persist_exists(39)?persist_read_int(39):0;
  hrv_events=persist_read_int(61);zero_ppi_events=persist_read_int(62);hr_events=persist_read_int(63);hr_sum=persist_read_int(64);hr_valid=persist_read_int(65);
  if(persist_exists(66))persist_read_string(66,firmware,sizeof(firmware));else snprintf(firmware,sizeof(firmware),"unknown");
  capacity_limited=persist_read_bool(67);
  raw_motion=persist_read_bool(38);
  accel_callbacks=persist_read_int(35);vibration_samples=persist_read_int(36);accel_rate_result=persist_read_int(37);
  count=(uint16_t)saved;ready=true;start_sec=persist_read_int(31);motion=(uint32_t)persist_read_int(32);measurement_night=persist_read_bool(33);motion_samples=persist_read_int(34);
  for(int i=0;i<CAPACITY*2/256;i++)persist_read_data(40+i,((uint8_t*)intervals)+i*256,256);
  for(int i=0;i<CAPACITY*4/256;i++)persist_read_data(50+i,((uint8_t*)offsets)+i*256,256);
  text_layer_set_text(text,"Saved measurement.\nSelect: retry upload\nDown: new test");
 }
 app_timer_register(1500,check_phone,NULL);
 if(quiet){app_timer_register(4000,begin_night,NULL);night_watchdog=app_timer_register(260000,night_timeout,NULL);}
 health_service_events_subscribe(on_health,NULL);tick_timer_service_subscribe(SECOND_UNIT,tick);
 app_event_loop();
 health_service_set_hrv_sample_period(0);health_service_set_heart_rate_sample_period(0);
 accel_data_service_unsubscribe();health_service_events_unsubscribe();tick_timer_service_unsubscribe();
 text_layer_destroy(text);window_destroy(window);
}
