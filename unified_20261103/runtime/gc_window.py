"""Bounded, process-local CPython GC timing; no hardware or device imports.

Disabling automatic GC is explicit and window-scoped. Reference counting still
reclaims acyclic objects. Thresholds never change. Explicit post-window collect
requires the caller to attest both camera and mailbox resources are closed.
"""
from collections import deque
import gc
import threading
import time


def _rss_snapshot():
    try:
        import resource
        import sys
        usage = resource.getrusage(resource.RUSAGE_SELF)
        unit = 'bytes' if sys.platform == 'darwin' else 'KiB'
        return dict(status='AVAILABLE',maximum_rss=usage.ru_maxrss,unit=unit,
                    source='getrusage(RUSAGE_SELF).ru_maxrss',
                    semantics='Process lifetime peak; not current RSS or a per-window peak reset')
    except (ImportError, OSError) as exc:
        return dict(status='UNAVAILABLE',reason=repr(exc),maximum_rss=None,unit=None)


class GCTimeline:
    def __init__(self, *, disable_during_window=False, max_events=4096):
        if type(disable_during_window) is not bool:
            raise TypeError('disable_during_window must be an explicit bool')
        if type(max_events) is not int or not 2 <= max_events <= 100000:
            raise ValueError('max_events must be 2..100000')
        self.disable_during_window = disable_during_window
        self.max_events = max_events
        self.events = deque(maxlen=max_events)
        self.dropped_events = 0
        self.callback_errors = 0
        self.active = {}
        self.entered = self.closed = False
        self.enabled_before = self.enabled_after = None
        self.thresholds_before = self.thresholds_after = None
        self.begin_ns = self.end_ns = None
        self.rss_before = self.rss_after = None
        self.post_close_collection = None
        # Keep exactly this bound method so removal cannot touch other hooks.
        self.callback = self._callback

    def _callback(self, phase, info):
        try:
            now = time.monotonic_ns()
            cpu = time.thread_time_ns()
            tid = threading.get_native_id()
            generation = int(info.get('generation', -1))
            key = (tid, generation)
            event = dict(phase=str(phase), monotonic_ns=now,
                         thread_cpu_ns=cpu, native_tid=tid,
                         generation=generation,
                         collected=int(info.get('collected', 0)),
                         uncollectable=int(info.get('uncollectable', 0)))
            if phase == 'start':
                self.active[key] = (now, cpu)
            elif phase == 'stop':
                start = self.active.pop(key, None)
                if start is not None:
                    event.update(start_ns=start[0], wall_ns=now-start[0],
                                 collector_thread_cpu_ns=cpu-start[1])
                else:
                    event['start_not_recorded'] = True
            if len(self.events) == self.max_events:
                self.dropped_events += 1
            self.events.append(event)
        except Exception:
            # An observer must never escape into Python's collector callback.
            self.callback_errors += 1

    def __enter__(self):
        if self.entered:
            raise RuntimeError('GCTimeline instances cannot be reused')
        self.entered = True
        self.enabled_before = gc.isenabled()
        self.thresholds_before = gc.get_threshold()
        self.rss_before = _rss_snapshot()
        self.begin_ns = time.monotonic_ns()
        gc.callbacks.append(self.callback)
        if self.disable_during_window:
            gc.disable()
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.end_ns = time.monotonic_ns()
        try:
            # Identity match only; preserve any pre-existing user callbacks.
            for index in range(len(gc.callbacks)-1, -1, -1):
                if gc.callbacks[index] is self.callback:
                    del gc.callbacks[index]
                    break
        finally:
            if self.enabled_before:
                gc.enable()
            else:
                gc.disable()
            self.enabled_after = gc.isenabled()
            self.thresholds_after = gc.get_threshold()
            self.rss_after = _rss_snapshot()
            self.closed = True
        return False

    def collect_after_close(self, *, hardware_closed=False, generation=2):
        """Never called automatically. Caller must close camera and UIO first."""
        if not self.closed or not hardware_closed:
            raise RuntimeError('Explicit collection requires exited window AND camera/UIO closed')
        if type(generation) is not int or generation not in (0, 1, 2):
            raise ValueError('invalid GC generation')
        start = time.monotonic_ns()
        cpu = time.thread_time_ns()
        collected = gc.collect(generation)
        self.post_close_collection = dict(
            generation=generation,collected=collected,
            wall_ns=time.monotonic_ns()-start,
            collector_thread_cpu_ns=time.thread_time_ns()-cpu,
            gc_enabled=gc.isenabled(),thresholds_unchanged=gc.get_threshold()==self.thresholds_before,
            hardware_closed_asserted=True,
            semantics='Explicit post-window collection after caller attested camera/UIO closed')
        return dict(self.post_close_collection)

    def report(self):
        events = list(self.events)
        stops = [event for event in events if event['phase']=='stop']
        return dict(schema='gc_timeline_v1',
            disable_during_window=self.disable_during_window,
            enabled_before=self.enabled_before,enabled_after=self.enabled_after,
            thresholds_before=self.thresholds_before,thresholds_after=self.thresholds_after,
            thresholds_unchanged=(self.thresholds_before==self.thresholds_after) if self.closed else None,
            prior_enabled_state_restored=(self.enabled_before==self.enabled_after) if self.closed else None,
            begin_ns=self.begin_ns,end_ns=self.end_ns,closed=self.closed,
            maximum_rss_before=self.rss_before,maximum_rss_after=self.rss_after,
            max_events=self.max_events,recorded_events=len(events),dropped_events=self.dropped_events,
            callback_errors=self.callback_errors,unfinished_collections=len(self.active),
            collection_count_by_generation={str(g):sum(e['generation']==g for e in stops) for g in (0,1,2)},
            maximum_collection_wall_ns=max((e.get('wall_ns',0) for e in stops),default=0),
            events=events,post_close_collection=self.post_close_collection,
            scope='Process-local GC events from every thread; wall and per-collector-thread CPU. No algorithm or freshness threshold changed; not isolated scheduler measurement.')
