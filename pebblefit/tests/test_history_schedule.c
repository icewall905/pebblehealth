#include <assert.h>
#include <stdio.h>

#include "../history-watch/src/c/daily_schedule.h"
static int next_daily(int weekday,int second_of_day,int *day,int *hour,int *minute){*hour=5;*minute=15;*day=history_daily_weekday(weekday,second_of_day);return 1;}
int main(void){int day,hour,minute;for(int weekday=0;weekday<7;weekday++){next_daily(weekday,5*3600+15*60-1,&day,&hour,&minute);assert(day==weekday&&hour==5&&minute==15);next_daily(weekday,5*3600+15*60,&day,&hour,&minute);assert(day==(weekday+1)%7&&hour==5&&minute==15);next_daily(weekday,5*3600+15*60+1,&day,&hour,&minute);assert(day==(weekday+1)%7&&hour==5&&minute==15);}next_daily(6,23*3600,&day,&hour,&minute);assert(day==0&&hour==5&&minute==15);puts("Daily history schedule passed: future 05:15 local rollover across all weekdays");return 0;}
