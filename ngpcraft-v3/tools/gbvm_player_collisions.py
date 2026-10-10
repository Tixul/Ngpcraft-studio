"""GBVM actor.c player collision callback oracle."""
from tools.gbvm_actors import ScriptThread, COLLISION

class PlayerCollisions:
    def __init__(self,groups,scheduler=None,player_factory=None,actor_factories=None):
        self.groups=groups
        self.scheduler=scheduler
        self.player_factory=player_factory
        self.actor_factories=actor_factories or {}
        self.pending=None
        self.iframes=0
        self.calls=[]
        self.trace=[]

    def overlap(self,scene):
        p=scene.actors[0];l,r,t,b=p.bounds
        left,right,top,bottom=(p.x+l)&65535,(p.x+r)&65535,(p.y+t)&65535,(p.y+b)&65535
        actor=p.prev
        while actor:
            l,r,t,b=actor.bounds
            if actor.flags & COLLISION and (actor.x+l)&65535 <= right and (actor.x+r)&65535 >= left and (actor.y+t)&65535 <= bottom and (actor.y+b)&65535 >= top:
                return actor
            actor=actor.prev
        return None

    def finish(self,tick):
        if self.iframes==0 and self.pending is not None:
            group=self.groups[self.pending]&15
            if group:
                if self.player_factory:
                    self.calls.append((tick,0,group))
                    if self.scheduler:self.scheduler.threads.append(ScriptThread(self.player_factory(group),1,locked=False))
                if self.pending in self.actor_factories:
                    self.calls.append((tick,self.pending,0))
                    if self.scheduler:self.scheduler.threads.append(ScriptThread(self.actor_factories[self.pending](),1,locked=False))
                self.iframes=20
        elif self.iframes:
            self.iframes-=1
        self.trace.append((tick,self.pending,self.iframes))
        self.pending=None
