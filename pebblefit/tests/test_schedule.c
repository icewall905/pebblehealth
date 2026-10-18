#include <assert.h>
#include <stdio.h>
#include "../hrv-watch/src/c/schedule.h"
int main(void){
 for(int day=0;day<7;day++)for(int now=0;now<86400;now+=59){
  int ds[6],hs[6];assert(night_slots(day,now,ds,hs)==6);int last=now+5;
  for(int i=0;i<6;i++){
   assert(hs[i]>=2 && hs[i]<=4);
   int t=((ds[i]-day+7)%7)*86400+hs[i]*3600;
   assert(t>last && t<3*86400);last=t;
  }
 }
 int ds[6],hs[6];night_slots(6,4*3600,ds,hs);
 assert(ds[0]==0 && hs[0]==2); /* Saturday after 04:00 -> Sunday 02:00 */
 night_slots(1,2*3600,ds,hs);assert(ds[0]==1 && hs[0]==3);
 puts("Night schedule passed: upcoming slots, midnight, week rollover, no past/duplicate events");
}
