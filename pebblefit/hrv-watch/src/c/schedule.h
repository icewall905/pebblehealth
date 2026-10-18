/* Platform-neutral slot planner. Conversion to UTC uses Pebble's timezone API. */
#pragma once
static int night_slots(int weekday, int second_of_day, int *weekdays, int *hours) {
  int count=0;
  for(int day=0;day<3 && count<6;day++) {
    for(int hour=2;hour<=4 && count<6;hour++) {
      if(day==0 && hour*3600<=second_of_day+5) continue;
      weekdays[count]=(weekday+day)%7; hours[count]=hour;count++;
    }
  }
  return count;
}
