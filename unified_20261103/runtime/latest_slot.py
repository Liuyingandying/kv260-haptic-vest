"""One waiting immutable frame and one in-flight consumer frame; never a FIFO."""
import threading, time

class LatestSlot:
    def __init__(self):
        self.condition = threading.Condition()
        self.item = None
        self.closed = False
        self.error = None
        self.published = self.taken = self.overwritten = 0
        self.boundary_discard = 0
        self.normal_boundary_closed = False

    def publish(self, item, complete_boundary=False):
        with self.condition:
            if self.closed:
                return False
            if self.item is not None:
                self.overwritten += 1
            self.item = item
            self.published += 1
            if complete_boundary:
                # Normal acquisition end admits no new processing. The current
                # in-flight object was already taken and is not touched here.
                self.closed = self.normal_boundary_closed = True
                self.item = None
                self.boundary_discard += 1
                self.condition.notify_all()
            else:self.condition.notify()
            return True

    def take(self, timeout=1.0):
        deadline = time.monotonic()+timeout
        with self.condition:
            while self.item is None and not self.closed:
                remaining = deadline-time.monotonic()
                if remaining <= 0:
                    return None
                self.condition.wait(remaining)
            item, self.item = self.item, None
            if item is not None:
                self.taken += 1
            return item

    def close(self, error=None):
        with self.condition:
            self.closed = True
            if error:
                self.error = error
                self.item = None
            self.condition.notify_all()

    def snapshot(self):
        with self.condition:
            return dict(published=self.published, taken=self.taken, overwritten=self.overwritten,
                        waiting=int(self.item is not None), closed=self.closed, error=self.error,
                        boundary_discard=self.boundary_discard,normal_boundary_closed=self.normal_boundary_closed,
                        accounting_consistent=(self.published==self.taken+self.overwritten+self.boundary_discard+
                                               int(self.item is not None)) if not self.error else None,
                        queue_capacity=1)
