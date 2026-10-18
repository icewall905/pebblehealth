#pragma once
/* Return a tm_wday (Sunday=0), selecting tomorrow before SDK UTC conversion.
 * Explicit same-day times in the past otherwise mean next week on Pebble. */
static int history_daily_weekday(int weekday,int second_of_day) {
  return (weekday+(second_of_day>=5*3600+15*60?1:0))%7;
}
