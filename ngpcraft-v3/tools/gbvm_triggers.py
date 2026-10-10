"""GBVM trigger.h/trigger.c oracle for top-down proofs."""
from tools.gbvm_actors import ScriptThread

class Triggers:
    def __init__(self, regions, scheduler=None, factory=None):
        self.regions = regions
        self.scheduler, self.factory = scheduler, factory
        self.last = 255
        self.calls = []

    def update(self, player, tick):
        l,r,t,b = player.bounds
        left, right = ((player.x+l)&65535)>>8, ((player.x+r)&65535)>>8
        top, bottom = ((player.y+t)&65535)>>8, ((player.y+b)&65535)>>8
        hit = 255
        for i, region in enumerate(self.regions):
            x,y,w,h,enter,leave = region
            if left <= x+w-1 and right >= x and top <= y+h-1 and bottom >= y:
                hit = i
                break
        if hit == self.last:
            return False
        calls = []
        if hit != 255 and self.regions[hit][4]:
            calls.append((hit,1))
        if self.last != 255 and self.regions[self.last][5]:
            calls.append((self.last,2))
        self.last = hit
        for i,arg in calls:
            self.calls.append((tick,i,arg))
            if self.scheduler:
                self.scheduler.threads.append(ScriptThread(self.factory(i,arg),1,locked=True))
        return bool(calls)
