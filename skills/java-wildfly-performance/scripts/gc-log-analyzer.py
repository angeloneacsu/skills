#!/usr/bin/env python3
"""
GC Log Analyzer for Java applications on WildFly

Parses GC logs and reports pause statistics, GC frequency, GC overhead, heap
occupancy trends and collector-specific problems (evacuation failures, ZGC
allocation stalls, Shenandoah degenerated cycles, explicit System.gc()...).

Supported input
  * JDK 9+ unified logging (JDK 17 / 21 / 25), -Xlog:gc or -Xlog:gc*:
    G1, Parallel, Serial, ZGC (single-generation and generational) and
    Shenandoah. Decorators may be any set, in any order: time, utctime,
    uptime, uptimemillis, uptimenanos, timemillis, timenanos, level, tags,
    pid, tid (e.g. the JDK default "uptime,level,tags" or WildFly's default
    "time,uptimemillis").
  * JDK 8 legacy logs ("simple"): -XX:+PrintGC / -XX:+PrintGCDetails, with or
    without -XX:+PrintGCDateStamps (G1, Parallel, CMS, Serial).
  Plain text or .gz files. Several files (e.g. rotated gc.log.N) can be given;
  they are ordered by their first wall-clock timestamp when available.

Pause times are STW pauses only. For ZGC the pauses (Pause Mark Start /
Mark End / Relocate Start) are logged with the gc+phases tag, so use -Xlog:gc*
(an -Xlog:gc only ZGC log has cycles and stalls but no pause times).

Usage:
    python3 gc-log-analyzer.py --logfile gc.log
    python3 gc-log-analyzer.py --logfile gc.log --format simple      # force the JDK 8 parser
    python3 gc-log-analyzer.py --logfile gc-issue.log --compare gc-baseline.log
    python3 gc-log-analyzer.py --logfile gc.log --output analysis.json --pause-target 200
"""

import argparse
import gzip
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# --------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------

@dataclass
class GCEvent:
    """A single stop-the-world GC pause"""
    timestamp: Optional[float]  # seconds (JVM uptime, or seconds since first wall-clock stamp)
    event_type: str             # YoungGC, MixedGC, FullGC, Remark, Cleanup, ZGC, Shenandoah, Degenerated, InitialMark, OtherPause
    pause_time_ms: float
    heap_before_mb: Optional[float] = None
    heap_after_mb: Optional[float] = None
    heap_max_mb: Optional[float] = None
    cause: str = ''
    gc_id: Optional[int] = None
    wallclock: Optional[str] = None


@dataclass
class GCCycle:
    """A (mostly) concurrent GC cycle: ZGC collection, G1 concurrent mark, Shenandoah cycle"""
    timestamp: Optional[float]
    kind: str                   # 'ZGC Garbage Collection', 'ZGC Minor Collection', 'ZGC Major Collection', 'G1 Concurrent Mark Cycle', 'Shenandoah'
    duration_ms: Optional[float] = None
    heap_before_mb: Optional[float] = None
    heap_after_mb: Optional[float] = None
    heap_max_mb: Optional[float] = None
    cause: str = ''
    gc_id: Optional[int] = None


@dataclass
class ParseResult:
    fmt: str = 'unknown'
    collector: Optional[str] = None
    events: List[GCEvent] = field(default_factory=list)
    cycles: List[GCCycle] = field(default_factory=list)
    stalls: List[Tuple[Optional[float], float, str]] = field(default_factory=list)  # (ts, ms, kind)
    safepoints: List[Tuple[Optional[float], float, float]] = field(default_factory=list)  # (ts, total_ms, ttsp_ms)
    counters: Dict[str, int] = field(default_factory=dict)
    first_ts: Optional[float] = None
    last_ts: Optional[float] = None
    first_wallclock: Optional[str] = None
    last_wallclock: Optional[str] = None
    jvm_restarts: int = 0
    heap_capacity_mb: Optional[float] = None   # from "Heap Max Capacity" / ZGC "Max Capacity"
    files: List[str] = field(default_factory=list)

    def bump(self, key: str, n: int = 1):
        self.counters[key] = self.counters.get(key, 0) + n


UNIT_TO_MB = {'B': 1.0 / (1024 * 1024), 'K': 1.0 / 1024, 'M': 1.0, 'G': 1024.0, 'T': 1024.0 * 1024}


def _num(s: str) -> float:
    return float(s.replace(',', '.'))


def _to_mb(value: str, unit: str) -> float:
    return _num(value) * UNIT_TO_MB[unit.upper()]


def _dur_to_ms(value: str, unit: str) -> float:
    v = _num(value)
    return {'s': v * 1000.0, 'ms': v, 'us': v / 1000.0, 'µs': v / 1000.0, 'ns': v / 1e6}[unit]


def _open(path: str):
    if path.endswith('.gz'):
        return gzip.open(path, 'rt', encoding='utf-8', errors='replace')
    return open(path, 'r', encoding='utf-8', errors='replace')


def _parse_wallclock(s: str) -> Optional[datetime]:
    s = s.strip().replace(',', '.')
    for fmt in ('%Y-%m-%dT%H:%M:%S.%f%z', '%Y-%m-%dT%H:%M:%S%z', '%Y-%m-%dT%H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S'):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


# --------------------------------------------------------------------------
# Unified logging (JDK 9+)
# --------------------------------------------------------------------------

LEVELS = {'trace', 'debug', 'info', 'warning', 'error'}
RE_ISO = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:[+-]\d{2}:?\d{2}|Z)?$')
RE_UPTIME_S = re.compile(r'^(\d+(?:[.,]\d+)?)s$')
RE_MS = re.compile(r'^(\d+)ms$')
RE_NS = re.compile(r'^(\d+)ns$')
RE_PID_TID = re.compile(r'^\d+[pt]$')
RE_TAGS = re.compile(r'^[a-z0-9_]+(?:,[a-z0-9_]+)*$')
RE_DECOR = re.compile(r'\[([^\[\]]*)\]')

_GCID = r'(?:GC\((?P<id>\d+)\)\s+)'
_GEN = r'(?:(?P<gen>[YOyo]):\s+)?'
_HEAP = (r'(?P<hb>\d+(?:[.,]\d+)?)(?P<hbu>[BKMGT])->(?P<ha>\d+(?:[.,]\d+)?)(?P<hau>[BKMGT])'
         r'\((?P<ht>\d+(?:[.,]\d+)?)(?P<htu>[BKMGT])\)')
_DUR = r'(?P<dur>\d+(?:[.,]\d+)?)\s?(?P<du>ms|us|µs|ns|s)'

RE_U_PAUSE = re.compile(r'^' + _GCID + _GEN + r'Pause (?P<what>.+?)(?:\s+' + _HEAP + r')?\s+' + _DUR + r'\s*$')
RE_U_ZCYCLE = re.compile(
    r'^' + _GCID + r'(?P<kind>Garbage Collection|Major Collection|Minor Collection)\s+\((?P<cause>.*)\)\s+'
    r'(?P<hb>\d+(?:[.,]\d+)?)(?P<hbu>[KMGT])\((?P<hbp>\d+)%\)->(?P<ha>\d+(?:[.,]\d+)?)(?P<hau>[KMGT])\((?P<hap>\d+)%\)'
    r'(?:\s+' + _DUR + r')?\s*$')
RE_U_STALL = re.compile(r'^(?:GC\(\d+\)\s+)?(?:[YOyo]:\s+)?(?P<kind>Allocation Stall|Relocation Stall)\s+\((?P<thr>.*)\)\s+' + _DUR + r'\s*$')
RE_U_G1CONC = re.compile(r'^' + _GCID + r'Concurrent (?P<what>Mark Cycle|Undo Cycle)\s+' + _DUR + r'\s*$')
RE_U_SHEN_CLEANUP = re.compile(r'^' + _GCID + r'Concurrent cleanup\s+' + _HEAP + r'\s+' + _DUR + r'\s*$')
RE_U_SAFEPOINT = re.compile(
    r'^Safepoint "(?P<op>[^"]+)".*?Reaching safepoint: (?P<ttsp>\d+) ns.*?Total: (?P<total>\d+) ns')
RE_U_USING = re.compile(r'^Using (?P<gc>G1|Parallel|Serial|The Z Garbage Collector|Shenandoah)\b')
RE_U_MAXCAP = re.compile(r'^(?:GC\(\d+\) )?(?:[YOyo]: )?(?:Heap )?Max Capacity: (?P<v>\d+(?:[.,]\d+)?)(?P<u>[BKMGT])')

ZGC_PHASES = ('Mark Start', 'Mark End', 'Relocate Start')
SHEN_PAUSES = ('Init Mark', 'Final Mark', 'Init Update Refs', 'Final Update Refs', 'Final Evac',
               'Init Evac', 'Final Roots', 'Final Update')


def split_decorations(line: str):
    """Split a unified-logging line into (decorations, message).

    Returns (None, line) when the line carries no recognizable decoration."""
    decor = {'uptime': None, 'wall': None, 'level': None, 'tags': None}
    pos = 0
    found = False
    while pos < len(line) and line[pos] == '[':
        m = RE_DECOR.match(line, pos)
        if not m:
            break
        tok = m.group(1).strip()
        if RE_ISO.match(tok):
            decor['wall'] = tok
        elif RE_UPTIME_S.match(tok):
            decor['uptime'] = _num(RE_UPTIME_S.match(tok).group(1))
        elif RE_MS.match(tok):
            v = int(tok[:-2])
            if v > 100_000_000_000:          # timemillis (epoch)
                decor['wall'] = decor['wall'] or datetime.fromtimestamp(v / 1000.0).isoformat()
            else:                             # uptimemillis
                decor['uptime'] = v / 1000.0
        elif RE_NS.match(tok):
            v = int(tok[:-2])
            if v > 100_000_000_000_000_000:  # timenanos (epoch)
                decor['wall'] = decor['wall'] or datetime.fromtimestamp(v / 1e9).isoformat()
            else:                             # uptimenanos
                decor['uptime'] = v / 1e9
        elif tok in LEVELS:
            decor['level'] = tok
        elif RE_PID_TID.match(tok):
            pass
        elif RE_TAGS.match(tok):
            decor['tags'] = tok               # last tag-like token wins (hostname precedes tags)
        else:
            break
        found = True
        pos = m.end()
    if not found:
        return None, line
    msg = line[pos:]
    if msg.startswith(' '):
        msg = msg[1:]
    return decor, msg


def classify_pause(what: str) -> str:
    if what.startswith('Young'):
        return 'MixedGC' if '(Mixed)' in what else 'YoungGC'
    if what.startswith('Full'):
        return 'FullGC'
    if what.startswith('Remark'):
        return 'Remark'
    if what.startswith('Cleanup'):
        return 'Cleanup'
    if what.startswith(ZGC_PHASES):
        return 'ZGC'
    if what.startswith('Degenerated'):
        return 'Degenerated'
    if what.startswith(SHEN_PAUSES):
        return 'Shenandoah'
    return 'OtherPause'


def pause_cause(what: str) -> str:
    """'Young (Normal) (G1 Evacuation Pause)' -> 'G1 Evacuation Pause'; 'Full (System.gc())' -> 'System.gc()'"""
    groups = re.findall(r'\(((?:[^()]|\([^()]*\))*)\)', what)
    if what.startswith('Degenerated') and groups:
        return f'Degenerated GC ({groups[0]})'
    ignore = {'Normal', 'Concurrent Start', 'Prepare Mixed', 'Mixed', 'Evacuation Failure', 'Major',
              'unload classes', 'process weakrefs', 'update refs'}
    for g in groups:
        if g not in ignore and not g.startswith('Evacuation Failure'):
            return g
    return ''


class _TimeTracker:
    """Turns decorations into a monotonic timeline (handles JVM restarts in one stream)"""

    def __init__(self, res: ParseResult):
        self.res = res
        self.offset = 0.0
        self.last_raw = None
        self.wall0: Optional[datetime] = None

    def ts(self, uptime: Optional[float], wall: Optional[str]) -> Optional[float]:
        if wall:
            if self.res.first_wallclock is None:
                self.res.first_wallclock = wall
            self.res.last_wallclock = wall
        raw = uptime
        if raw is None and wall:
            dt = _parse_wallclock(wall)
            if dt is not None:
                if self.wall0 is None:
                    self.wall0 = dt
                try:
                    raw = (dt - self.wall0).total_seconds()
                except TypeError:  # naive vs aware mix
                    raw = None
        if raw is None:
            return None
        if self.last_raw is not None and raw < self.last_raw - 1.0 and uptime is not None:
            # uptime went backwards: a new JVM started writing to the same stream
            self.offset += self.last_raw
            self.res.jvm_restarts += 1
        self.last_raw = raw
        t = raw + self.offset
        if self.res.first_ts is None:
            self.res.first_ts = t
        self.res.last_ts = t
        return t


class UnifiedGCLogParser:
    """Parser for unified GC logs (JDK 9+, -Xlog:gc / -Xlog:gc*)"""

    def __init__(self, logfile, result: Optional[ParseResult] = None):
        self.logfiles = [logfile] if isinstance(logfile, str) else list(logfile)
        self.res = result or ParseResult(fmt='unified')

    @property
    def events(self) -> List[GCEvent]:
        return self.res.events

    def parse(self) -> List[GCEvent]:
        clock = _TimeTracker(self.res)
        for path in self.logfiles:
            self.res.files.append(path)
            with _open(path) as f:
                for line in f:
                    self.parse_line(line.rstrip('\r\n'), clock)
        return self.res.events

    def parse_line(self, line: str, clock: _TimeTracker):
        decor, msg = split_decorations(line)
        if decor is None:
            decor = {'uptime': None, 'wall': None, 'level': None, 'tags': None}
        ts = clock.ts(decor['uptime'], decor['wall'])
        tags = decor['tags']
        tagset = set(tags.split(',')) if tags else None
        res = self.res

        m = RE_U_USING.match(msg)
        if m and (tagset is None or 'gc' in tagset):
            name = m.group('gc')
            res.collector = {'The Z Garbage Collector': 'ZGC'}.get(name, name)
            return

        if res.heap_capacity_mb is None and 'Max Capacity' in msg:
            m = RE_U_MAXCAP.match(msg)
            if m:
                res.heap_capacity_mb = _to_mb(m.group('v'), m.group('u'))
                return

        if 'GC(' not in msg:
            m = RE_U_STALL.match(msg)
            if m and (tagset is None or tagset == {'gc'}):
                res.stalls.append((ts, _dur_to_ms(m.group('dur'), m.group('du')), m.group('kind')))
                return
            m = RE_U_SAFEPOINT.match(msg)
            if m and (tagset is None or 'safepoint' in tagset):
                res.safepoints.append((ts, int(m.group('total')) / 1e6, int(m.group('ttsp')) / 1e6))
            return

        m = RE_U_PAUSE.match(msg)
        if m:
            what = m.group('what')
            etype = classify_pause(what)
            # Only the summary line of a pause counts: tag "gc" (all collectors) or
            # "gc,phases" for the ZGC pause phases. gc,stats / gc,start lines never match.
            if tagset is not None and not (tagset == {'gc'} or (tagset == {'gc', 'phases'} and etype == 'ZGC')):
                return
            ev = GCEvent(timestamp=ts, event_type=etype,
                         pause_time_ms=_dur_to_ms(m.group('dur'), m.group('du')),
                         cause=pause_cause(what), gc_id=int(m.group('id')), wallclock=decor['wall'])
            if m.group('hb'):
                ev.heap_before_mb = _to_mb(m.group('hb'), m.group('hbu'))
                ev.heap_after_mb = _to_mb(m.group('ha'), m.group('hau'))
                ev.heap_max_mb = _to_mb(m.group('ht'), m.group('htu'))
            if 'Evacuation Failure' in what:
                res.bump('evacuation_failure')
            if etype == 'ZGC' and res.collector is None:
                res.collector = 'ZGC'
            res.events.append(ev)
            return

        if tagset is not None and tagset != {'gc'}:
            return

        m = RE_U_ZCYCLE.match(msg)
        if m:
            hb = _to_mb(m.group('hb'), m.group('hbu'))
            ha = _to_mb(m.group('ha'), m.group('hau'))
            pct = int(m.group('hbp')) or int(m.group('hap'))
            total = None
            if int(m.group('hbp')) > 0:
                total = hb * 100.0 / int(m.group('hbp'))
            elif int(m.group('hap')) > 0:
                total = ha * 100.0 / int(m.group('hap'))
            dur = _dur_to_ms(m.group('dur'), m.group('du')) if m.group('dur') else None
            if res.heap_capacity_mb:
                total = res.heap_capacity_mb
            res.cycles.append(GCCycle(timestamp=ts, kind='ZGC ' + m.group('kind'), duration_ms=dur,
                                      heap_before_mb=hb, heap_after_mb=ha,
                                      heap_max_mb=round(total) if total and pct else None,
                                      cause=m.group('cause'), gc_id=int(m.group('id'))))
            res.collector = res.collector or 'ZGC'
            return
        m = RE_U_G1CONC.match(msg)
        if m:
            res.cycles.append(GCCycle(timestamp=ts, kind='G1 Concurrent ' + m.group('what'),
                                      duration_ms=_dur_to_ms(m.group('dur'), m.group('du')),
                                      gc_id=int(m.group('id'))))
            return
        m = RE_U_SHEN_CLEANUP.match(msg)
        if m:
            res.cycles.append(GCCycle(timestamp=ts, kind='Shenandoah', duration_ms=None,
                                      heap_before_mb=_to_mb(m.group('hb'), m.group('hbu')),
                                      heap_after_mb=_to_mb(m.group('ha'), m.group('hau')),
                                      heap_max_mb=_to_mb(m.group('ht'), m.group('htu')),
                                      gc_id=int(m.group('id'))))
            return
        if 'To-space exhausted' in msg:          # G1, JDK 9-19
            res.bump('evacuation_failure')
        elif 'Concurrent Mark Abort' in msg:     # G1: Full GC interrupted marking
            res.bump('concurrent_mark_abort')
        elif re.search(r'GC\(\d+\) (?:[YOyo]: )?(?:Allocation|Relocation) Stall', msg):
            m = RE_U_STALL.match(msg)
            if m:
                res.stalls.append((ts, _dur_to_ms(m.group('dur'), m.group('du')), m.group('kind')))


# --------------------------------------------------------------------------
# JDK 8 legacy format ("simple")
# --------------------------------------------------------------------------

RE_L_HEADER = re.compile(r'\[(?P<kind>GC pause|GC remark|GC cleanup|Full GC|GC)(?=[\s(,])(?P<body>.*)$')
RE_L_PREFIX = re.compile(r'^(?:(?P<wall>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[.,]\d+[+-]\d{4}):\s*)?(?:(?P<up>\d+[.,]\d+):\s*)?$')
RE_L_SECS = re.compile(r'(\d+[.,]\d+)\s*secs\]')
RE_L_HEAP = re.compile(r'(\d+(?:[.,]\d+)?)([BKMG])->(\d+(?:[.,]\d+)?)([BKMG])\((\d+(?:[.,]\d+)?)([BKMG])\)')
RE_L_G1HEAP = re.compile(r'Heap:\s*(\d+(?:[.,]\d+)?)([BKMG])\((\d+(?:[.,]\d+)?)([BKMG])\)->(\d+(?:[.,]\d+)?)([BKMG])\((\d+(?:[.,]\d+)?)([BKMG])\)')
RE_L_NESTED = re.compile(r'\[(?:[A-Za-z0-9][A-Za-z0-9 \-]*:|Times:)[^\[\]]*\]')
RE_L_INNER_TS = re.compile(r'(?:\d{4}-\d{2}-\d{2}T\S+:\s*)?\d+[.,]\d+:\s*\[[^\[\]]*\]')
RE_L_STOPPED = re.compile(r'Total time for which application threads were stopped:\s*(\d+[.,]\d+) seconds'
                          r'(?:, Stopping threads took:\s*(\d+[.,]\d+) seconds)?')


class LegacyGCLogParser:
    """Parser for JDK 8 GC logs (-XX:+PrintGC / -XX:+PrintGCDetails [-XX:+PrintGCDateStamps])"""

    def __init__(self, logfile, result: Optional[ParseResult] = None):
        self.logfiles = [logfile] if isinstance(logfile, str) else list(logfile)
        self.res = result or ParseResult(fmt='simple')

    def parse(self) -> List[GCEvent]:
        clock = _TimeTracker(self.res)
        last: Optional[GCEvent] = None
        for path in self.logfiles:
            self.res.files.append(path)
            with _open(path) as f:
                for line in f:
                    line = line.rstrip('\r\n')
                    ev = self._parse_line(line, clock)
                    if ev is not None:
                        last = ev
                        continue
                    if last is not None and last.heap_after_mb is None:
                        m = RE_L_G1HEAP.search(line)  # PrintGCDetails G1: heap summary on a later line
                        if m:
                            last.heap_before_mb = _to_mb(m.group(1), m.group(2))
                            last.heap_after_mb = _to_mb(m.group(5), m.group(6))
                            last.heap_max_mb = _to_mb(m.group(7), m.group(8))
        return self.res.events

    def _parse_line(self, line: str, clock: _TimeTracker) -> Optional[GCEvent]:
        res = self.res
        m = RE_L_STOPPED.search(line)
        if m:
            up = re.search(r'(\d+[.,]\d+):\s*Total time', line)
            ts = clock.ts(_num(up.group(1)) if up else None, None)
            res.safepoints.append((ts, _num(m.group(1)) * 1000.0,
                                   _num(m.group(2)) * 1000.0 if m.group(2) else 0.0))
            return None
        if 'concurrent mode failure' in line:
            res.bump('concurrent_mode_failure')
        if 'to-space exhausted' in line or 'to-space overflow' in line:
            res.bump('evacuation_failure')
        m = RE_L_HEADER.search(line)
        if not m:
            return None
        kind, body = m.group('kind'), m.group('body')
        if body.lstrip().startswith('concurrent-'):
            return None                      # G1 concurrent phases are not pauses
        prefix = RE_L_PREFIX.match(line[:m.start()].strip())
        wall = up = None
        if prefix:
            wall = prefix.group('wall')
            up = _num(prefix.group('up')) if prefix.group('up') else None
        clean = re.sub(r'\[Times:[^\]]*\]', '', body)
        secs = RE_L_SECS.findall(clean)
        if not secs:
            return None                      # details continue on following lines; no total yet
        pause_ms = _num(secs[-1]) * 1000.0
        ts = clock.ts(up, wall)

        cause_m = re.match(r'\s*\(((?:[^()]|\([^()]*\))*)\)', body)
        cause = cause_m.group(1) if cause_m else ''
        if kind == 'Full GC':
            etype = 'FullGC'
        elif kind == 'GC remark' or 'CMS Final Remark' in cause:
            etype = 'Remark'
        elif kind == 'GC cleanup':
            etype = 'Cleanup'
        elif 'CMS Initial Mark' in cause:
            etype = 'InitialMark'
        elif '(mixed)' in body:
            etype = 'MixedGC'
        else:
            etype = 'YoungGC'

        ev = GCEvent(timestamp=ts, event_type=etype, pause_time_ms=pause_ms, cause=cause, wallclock=wall)
        top = clean
        for _ in range(4):                   # strip nested [Gen: ...] / [Metaspace: ...] / inner timings
            new = RE_L_INNER_TS.sub('', RE_L_NESTED.sub('', top))
            if new == top:
                break
            top = new
        hm = RE_L_HEAP.findall(top)
        if hm:
            hb, hbu, ha, hau, ht, htu = hm[-1]
            ev.heap_before_mb, ev.heap_after_mb, ev.heap_max_mb = _to_mb(hb, hbu), _to_mb(ha, hau), _to_mb(ht, htu)
        else:
            gm = RE_L_G1HEAP.search(body)
            if gm:
                ev.heap_before_mb = _to_mb(gm.group(1), gm.group(2))
                ev.heap_after_mb = _to_mb(gm.group(5), gm.group(6))
                ev.heap_max_mb = _to_mb(gm.group(7), gm.group(8))
        if res.collector is None:
            if kind == 'GC pause' or kind in ('GC remark', 'GC cleanup'):
                res.collector = 'G1'
            elif 'PSYoungGen' in body:
                res.collector = 'Parallel'
            elif 'ParNew' in body or 'CMS' in body:
                res.collector = 'CMS'
            elif 'DefNew' in body:
                res.collector = 'Serial'
        res.events.append(ev)
        return ev


# --------------------------------------------------------------------------
# Format detection + driver
# --------------------------------------------------------------------------

def detect_format(paths: List[str], max_lines: int = 5000) -> str:
    unified = legacy = 0
    for path in paths:
        with _open(path) as f:
            for i, line in enumerate(f):
                if i >= max_lines:
                    break
                decor, msg = split_decorations(line.rstrip('\r\n'))
                if decor is not None or re.match(r'^GC\(\d+\) ', msg):
                    unified += 1
                elif RE_L_HEADER.search(line) or 'Total time for which application threads' in line:
                    legacy += 1
    return 'simple' if legacy > unified else 'unified'


def order_files(paths: List[str]) -> List[str]:
    """Order rotated files by their first wall-clock stamp (unknown ones keep CLI order, first)"""
    keyed = []
    for idx, p in enumerate(paths):
        first = None
        with _open(p) as f:
            for i, line in enumerate(f):
                if i > 200:
                    break
                m = re.search(r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:[.,]\d+)?)', line)
                if m:
                    first = m.group(1).replace(',', '.')
                    break
        keyed.append((first or '', idx, p))
    return [p for _, _, p in sorted(keyed)]


def parse_logs(paths: List[str], fmt: str = 'auto') -> ParseResult:
    paths = order_files(paths) if len(paths) > 1 else paths
    if fmt == 'auto':
        fmt = detect_format(paths)
    if fmt == 'legacy':
        fmt = 'simple'
    res = ParseResult(fmt=fmt)
    parser = LegacyGCLogParser(paths, res) if fmt == 'simple' else UnifiedGCLogParser(paths, res)
    parser.parse()
    return res


# --------------------------------------------------------------------------
# Analysis
# --------------------------------------------------------------------------

class GCAnalyzer:
    """Analyzes GC events and generates metrics"""

    STW_OTHER = ('Remark', 'Cleanup', 'InitialMark', 'OtherPause')

    def __init__(self, events, pause_target_ms: float = 200.0):
        if isinstance(events, ParseResult):
            self.res = events
        else:
            self.res = ParseResult(events=list(events))
        self.events = self.res.events
        self.pause_target_ms = pause_target_ms
        self.young_gc_events = [e for e in self.events if e.event_type in ('YoungGC', 'MixedGC')]
        self.full_gc_events = [e for e in self.events if e.event_type == 'FullGC']
        self.zgc_events = [e for e in self.events if e.event_type == 'ZGC']
        self.shen_events = [e for e in self.events if e.event_type in ('Shenandoah', 'Degenerated')]
        self.other_stw_events = [e for e in self.events if e.event_type in self.STW_OTHER]

    # -- helpers -----------------------------------------------------------
    @staticmethod
    def calculate_percentile(data: List[float], percentile: float) -> float:
        """Nearest-rank percentile"""
        if not data:
            return 0.0
        s = sorted(data)
        k = max(0, min(len(s) - 1, int(-(-len(s) * percentile // 100)) - 1))
        return s[k]

    def _pause_stats(self, evs: List[GCEvent]) -> Dict:
        if not evs:
            return {'count': 0}
        p = [e.pause_time_ms for e in evs]
        return {
            'count': len(p),
            'avg_pause_ms': sum(p) / len(p),
            'min_pause_ms': min(p),
            'max_pause_ms': max(p),
            'p50_pause_ms': self.calculate_percentile(p, 50),
            'p95_pause_ms': self.calculate_percentile(p, 95),
            'p99_pause_ms': self.calculate_percentile(p, 99),
            'total_pause_time_ms': sum(p),
            'over_target_count': sum(1 for x in p if x > self.pause_target_ms),
        }

    def window_s(self) -> Optional[float]:
        if self.res.first_ts is None or self.res.last_ts is None:
            ts = [e.timestamp for e in self.events if e.timestamp is not None]
            return (max(ts) - min(ts)) if len(ts) > 1 else None
        w = self.res.last_ts - self.res.first_ts
        return w if w > 0 else None

    # -- stats -------------------------------------------------------------
    def get_young_gc_stats(self) -> Dict:
        return self._pause_stats(self.young_gc_events)

    def get_full_gc_stats(self) -> Dict:
        st = self._pause_stats(self.full_gc_events)
        if st['count']:
            st['explicit_system_gc'] = sum(1 for e in self.full_gc_events if 'System.gc()' in e.cause)
        return st

    def get_zgc_stats(self) -> Dict:
        st = self._pause_stats(self.zgc_events)
        cyc = [c for c in self.res.cycles if c.kind.startswith('ZGC')]
        st['cycles'] = len(cyc)
        st['minor_cycles'] = sum(1 for c in cyc if 'Minor' in c.kind)
        st['major_cycles'] = sum(1 for c in cyc if 'Major' in c.kind)
        st['allocation_stall_cycles'] = sum(1 for c in cyc if 'Allocation Stall' in c.cause)
        return st

    def get_shenandoah_stats(self) -> Dict:
        st = self._pause_stats(self.shen_events)
        st['degenerated'] = sum(1 for e in self.shen_events if e.event_type == 'Degenerated')
        return st

    def get_all_pause_stats(self) -> Dict:
        st = self._pause_stats(self.events)
        w = self.window_s()
        if st['count'] and w:
            st['gc_overhead_pct'] = st['total_pause_time_ms'] / (w * 1000.0) * 100.0
            st['pauses_per_minute'] = st['count'] / (w / 60.0)
        return st

    def get_stall_stats(self) -> Dict:
        if not self.res.stalls:
            return {'count': 0}
        ms = [s[1] for s in self.res.stalls]
        return {'count': len(ms), 'total_ms': sum(ms), 'max_ms': max(ms),
                'allocation_stalls': sum(1 for s in self.res.stalls if s[2] == 'Allocation Stall'),
                'relocation_stalls': sum(1 for s in self.res.stalls if s[2] == 'Relocation Stall')}

    def get_safepoint_stats(self) -> Dict:
        if not self.res.safepoints:
            return {'count': 0}
        tot = [s[1] for s in self.res.safepoints]
        ttsp = [s[2] for s in self.res.safepoints]
        return {'count': len(tot), 'total_stopped_ms': sum(tot), 'max_stopped_ms': max(tot),
                'max_time_to_safepoint_ms': max(ttsp),
                'p99_time_to_safepoint_ms': self.calculate_percentile(ttsp, 99)}

    def _heap_samples(self):
        """(ts, before, after, max) samples from pauses, or from cycles (ZGC/Shenandoah)"""
        samples = [(e.timestamp, e.heap_before_mb, e.heap_after_mb, e.heap_max_mb)
                   for e in self.events if e.heap_after_mb is not None]
        if not samples:
            samples = [(c.timestamp, c.heap_before_mb, c.heap_after_mb, c.heap_max_mb)
                       for c in self.res.cycles if c.heap_after_mb is not None]
        return samples

    def get_heap_stats(self) -> Dict:
        s = self._heap_samples()
        before = [x[1] for x in s if x[1] is not None]
        after = [x[2] for x in s if x[2] is not None]
        maxes = [x[3] for x in s if x[3]]
        out = {
            'heap_before_min_mb': min(before) if before else None,
            'heap_before_max_mb': max(before) if before else None,
            'heap_before_avg_mb': sum(before) / len(before) if before else None,
            'heap_after_min_mb': min(after) if after else None,
            'heap_after_max_mb': max(after) if after else None,
            'heap_after_avg_mb': sum(after) / len(after) if after else None,
            'heap_capacity_max_mb': max(maxes) if maxes else None,
        }
        # occupancy after GC relative to capacity, over the last 10% of samples
        occ = [x[2] / x[3] * 100.0 for x in s if x[2] is not None and x[3]]
        if occ:
            tail = occ[-max(1, len(occ) // 10):]
            out['occupancy_after_gc_last_pct'] = sum(tail) / len(tail)
            out['occupancy_after_gc_max_pct'] = max(occ)
        # allocation rate estimate (pause-based collectors): heap growth between pauses / window
        w = self.window_s()
        if len(s) > 1 and w and s[0][1] is not None:
            grown = 0.0
            for prev, cur in zip(s, s[1:]):
                if cur[1] is not None and prev[2] is not None:
                    grown += max(0.0, cur[1] - prev[2])
            out['allocation_rate_mb_s'] = grown / w
        out['leak_trend'] = self._leak_trend()
        return out

    def _leak_trend(self) -> Optional[Dict]:
        """Compare post-GC occupancy at the start vs the end of the log.

        Uses the heap after Full GCs when there are >= 4 of them (live set), otherwise the
        post-GC heap of every pause (noisier: includes old-gen garbage not yet collected)."""
        fulls = [e for e in self.full_gc_events if e.heap_after_mb is not None]
        basis = 'full-gc' if len(fulls) >= 4 else 'all-pauses'
        s = [(e.heap_after_mb, e.heap_max_mb) for e in fulls] if basis == 'full-gc' else \
            [(x[2], x[3]) for x in self._heap_samples() if x[2] is not None]
        if len(s) < 8:
            return None
        q = max(2, len(s) // 4)
        first = sum(a for a, _ in s[:q]) / q
        last = sum(a for a, _ in s[-q:]) / q
        cap = max((c for _, c in s if c), default=None)
        return {'basis': basis, 'first_quarter_avg_mb': first, 'last_quarter_avg_mb': last,
                'growth_mb': last - first, 'capacity_mb': cap}

    def get_causes(self) -> Dict[str, int]:
        causes: Dict[str, int] = {}
        for e in self.events:
            if e.cause:
                causes[e.cause] = causes.get(e.cause, 0) + 1
        for c in self.res.cycles:
            if c.cause:
                causes[c.cause] = causes.get(c.cause, 0) + 1
        return dict(sorted(causes.items(), key=lambda kv: -kv[1]))

    # -- issues ------------------------------------------------------------
    def detect_issues(self) -> List[str]:
        issues = []
        young = self.get_young_gc_stats()
        full = self.get_full_gc_stats()
        allp = self.get_all_pause_stats()
        heap = self.get_heap_stats()
        stalls = self.get_stall_stats()
        c = self.res.counters
        w = self.window_s()

        if young.get('max_pause_ms', 0) > 500:
            issues.append(f"WARNING: Young GC pause time > 500ms ({young['max_pause_ms']:.0f}ms). "
                          f"Heap may be too small or too many live objects to copy.")
        if young.get('count', 0) >= 20 and young['p95_pause_ms'] > self.pause_target_ms:
            issues.append(f"WARNING: Young GC p95 pause {young['p95_pause_ms']:.0f}ms exceeds the pause target "
                          f"{self.pause_target_ms:.0f}ms ({young['over_target_count']}/{young['count']} pauses over target).")

        if full.get('count', 0) > 0:
            explicit = full.get('explicit_system_gc', 0)
            real = full['count'] - explicit
            if real > 10:
                issues.append(f"ERROR: {real} full GCs detected (excluding System.gc()). Heap too small or memory leak.")
            elif real > 1:
                issues.append(f"WARNING: {real} full GCs detected (excluding System.gc()). Monitor for memory leak.")
            elif real == 1:
                issues.append("INFO: 1 full GC detected. Check its cause and whether it repeats.")
            if explicit:
                issues.append(f"WARNING: {explicit} explicit System.gc() full GCs. Find the caller (RMI DGC, "
                              f"application/library code); consider -XX:+ExplicitGCInvokesConcurrent (G1) "
                              f"or -XX:+DisableExplicitGC.")

        if len(self.young_gc_events) > 1:
            ts = [e.timestamp for e in self.young_gc_events if e.timestamp is not None]
            if len(ts) > 1 and ts[-1] > ts[0]:
                avg_interval = (ts[-1] - ts[0]) / (len(ts) - 1)
                if avg_interval < 1:
                    issues.append(f"WARNING: Young GC every {avg_interval:.2f}s (very frequent). "
                                  f"High allocation rate or young generation too small.")

        if allp.get('gc_overhead_pct') is not None:
            ov = allp['gc_overhead_pct']
            if ov > 25:
                issues.append(f"ERROR: {ov:.1f}% of wall time spent in GC pauses. Application throughput severely impacted.")
            elif ov > 10:
                issues.append(f"WARNING: {ov:.1f}% of wall time spent in GC pauses (target < 5-10%).")

        occ = heap.get('occupancy_after_gc_last_pct')
        if occ is not None and occ > 90:
            issues.append(f"WARNING: Heap occupancy after GC at {occ:.0f}% of capacity at the end of the log. "
                          f"Overflow / OutOfMemoryError risk.")
        elif occ is not None and occ > 80:
            issues.append(f"WARNING: Heap occupancy after GC at {occ:.0f}% of capacity (keep below ~80%).")

        trend = heap.get('leak_trend')
        if trend and trend['capacity_mb']:
            growth = trend['growth_mb']
            if growth > 0.2 * trend['capacity_mb'] and growth > 0.5 * max(trend['first_quarter_avg_mb'], 1.0):
                issues.append(f"WARNING: Heap occupancy growing ({trend['first_quarter_avg_mb']:.0f}MB -> "
                              f"{trend['last_quarter_avg_mb']:.0f}MB after GC, basis: {trend['basis']}). "
                              f"Possible memory leak; confirm over a longer window / with a heap dump.")

        if c.get('evacuation_failure'):
            issues.append(f"WARNING: {c['evacuation_failure']} G1 evacuation failures (to-space exhausted). "
                          f"Heap too full: increase heap or lower InitiatingHeapOccupancyPercent.")
        if c.get('concurrent_mark_abort'):
            issues.append(f"WARNING: {c['concurrent_mark_abort']} G1 concurrent mark aborts (marking did not finish "
                          f"before a Full GC). Start marking earlier (IHOP) or increase heap.")
        if c.get('concurrent_mode_failure'):
            issues.append(f"WARNING: {c['concurrent_mode_failure']} CMS concurrent mode failures.")

        if stalls.get('count'):
            level = 'ERROR' if (w and stalls['total_ms'] > w * 10.0) else 'WARNING'   # > 1% of wall time
            issues.append(f"{level}: {stalls['count']} ZGC stalls (allocation: {stalls['allocation_stalls']}, "
                          f"relocation: {stalls['relocation_stalls']}), total {stalls['total_ms']:.0f}ms, "
                          f"max {stalls['max_ms']:.1f}ms. Application threads waited for GC: increase heap "
                          f"(-Xmx / SoftMaxHeapSize headroom) or GC threads.")

        shen = self.get_shenandoah_stats()
        if shen.get('degenerated'):
            issues.append(f"WARNING: {shen['degenerated']} Shenandoah degenerated GCs (concurrent GC could not keep up).")

        if self.res.collector == 'ZGC' and not self.zgc_events:
            issues.append("INFO: ZGC log without pause phases; use -Xlog:gc*:... to get Pause Mark Start/End and "
                          "Relocate Start times.")
        sp = self.get_safepoint_stats()
        if sp.get('count') and sp['max_time_to_safepoint_ms'] > 100:
            issues.append(f"WARNING: max time-to-safepoint {sp['max_time_to_safepoint_ms']:.0f}ms. Threads are slow to "
                          f"reach safepoints (counted loops, large array copies, JNI).")
        if self.res.jvm_restarts:
            issues.append(f"INFO: the log contains {self.res.jvm_restarts} JVM restart(s); statistics span all runs.")

        if not any(i.startswith(('ERROR', 'WARNING')) for i in issues):
            issues.insert(0, "OK: No major issues detected. Performance appears acceptable.")
        return issues

    # -- output ------------------------------------------------------------
    def summary(self) -> Dict:
        return {
            'format': self.res.fmt,
            'collector': self.res.collector,
            'files': self.res.files,
            'window_s': self.window_s(),
            'first_wallclock': self.res.first_wallclock,
            'last_wallclock': self.res.last_wallclock,
            'total_events': len(self.events),
            'young_gc_count': len(self.young_gc_events),
            'mixed_gc_count': sum(1 for e in self.young_gc_events if e.event_type == 'MixedGC'),
            'full_gc_count': len(self.full_gc_events),
            'zgc_count': len(self.zgc_events),
            'shenandoah_pause_count': len(self.shen_events),
            'other_stw_count': len(self.other_stw_events),
            'concurrent_cycles': len(self.res.cycles),
            'counters': dict(self.res.counters),
        }

    def metrics(self) -> Dict:
        return {
            'summary': self.summary(),
            'all_pauses': self.get_all_pause_stats(),
            'young_gc_stats': self.get_young_gc_stats(),
            'full_gc_stats': self.get_full_gc_stats(),
            'zgc_stats': self.get_zgc_stats(),
            'shenandoah_stats': self.get_shenandoah_stats(),
            'other_stw_stats': self._pause_stats(self.other_stw_events),
            'stalls': self.get_stall_stats(),
            'safepoints': self.get_safepoint_stats(),
            'heap_stats': self.get_heap_stats(),
            'causes': self.get_causes(),
        }

    @staticmethod
    def _print_pause_block(title: str, st: Dict, digits: int = 1):
        if not st.get('count'):
            return
        f = f"{{:.{digits}f}}"
        print(f"\n{title}")
        print(f"  Count: {st['count']}")
        print(f"  Avg pause: {f.format(st['avg_pause_ms'])}ms   Min: {f.format(st['min_pause_ms'])}ms   "
              f"Max: {f.format(st['max_pause_ms'])}ms")
        print(f"  P50: {f.format(st['p50_pause_ms'])}ms   P95: {f.format(st['p95_pause_ms'])}ms   "
              f"P99: {f.format(st['p99_pause_ms'])}ms")
        print(f"  Total pause: {st['total_pause_time_ms']:.0f}ms")

    def print_report(self):
        m = self.metrics()
        s = m['summary']
        print("\n" + "=" * 60)
        print("GC LOG ANALYSIS REPORT")
        print("=" * 60)
        print(f"\nFormat: {s['format']}   Collector: {s['collector'] or 'unknown'}")
        if s['window_s']:
            w = s['window_s']
            span = f"{w:.1f}s" + (f" ({w / 3600:.2f}h)" if w >= 3600 else f" ({w / 60:.1f}min)" if w >= 60 else '')
            wc = f"  [{s['first_wallclock']} .. {s['last_wallclock']}]" if s['first_wallclock'] else ''
            print(f"Log window: {span}{wc}")

        print(f"\nTotal STW pauses: {s['total_events']}")
        print(f"  - Young GC: {s['young_gc_count']} (of which mixed: {s['mixed_gc_count']})")
        print(f"  - Full GC: {s['full_gc_count']}")
        print(f"  - ZGC pauses: {s['zgc_count']}")
        if s['shenandoah_pause_count']:
            print(f"  - Shenandoah pauses: {s['shenandoah_pause_count']}")
        if s['other_stw_count']:
            print(f"  - Remark/Cleanup/other: {s['other_stw_count']}")
        if s['concurrent_cycles']:
            print(f"  Concurrent cycles: {s['concurrent_cycles']}")

        ap = m['all_pauses']
        if ap.get('gc_overhead_pct') is not None:
            print(f"\nTHROUGHPUT")
            print(f"  Time in STW pauses: {ap['total_pause_time_ms']:.0f}ms = {ap['gc_overhead_pct']:.2f}% of the window")
            print(f"  Pauses per minute: {ap['pauses_per_minute']:.1f}   Longest pause: {ap['max_pause_ms']:.1f}ms")

        self._print_pause_block("YOUNG GC STATISTICS (young + mixed)", m['young_gc_stats'])
        yg = m['young_gc_stats']
        if yg.get('count'):
            print(f"  Over {self.pause_target_ms:.0f}ms target: {yg['over_target_count']}")
        fg = m['full_gc_stats']
        if fg.get('count'):
            self._print_pause_block("FULL GC STATISTICS", fg)
            print(f"  Explicit System.gc(): {fg.get('explicit_system_gc', 0)}")
        self._print_pause_block("REMARK / CLEANUP / OTHER STW PAUSES", m['other_stw_stats'])
        zs = m['zgc_stats']
        if zs.get('count') or zs.get('cycles'):
            self._print_pause_block("ZGC PAUSES (Mark Start / Mark End / Relocate Start)", zs, digits=3)
            mode = (f"minor: {zs['minor_cycles']}, major: {zs['major_cycles']}"
                    if zs['minor_cycles'] or zs['major_cycles'] else 'single-generation')
            print(f"\nZGC CYCLES: {zs['cycles']} ({mode}, "
                  f"triggered by allocation stall: {zs['allocation_stall_cycles']})")
        self._print_pause_block("SHENANDOAH PAUSES", m['shenandoah_stats'], digits=3)
        st = m['stalls']
        if st.get('count'):
            print(f"\nZGC STALLS: {st['count']} (total {st['total_ms']:.0f}ms, max {st['max_ms']:.1f}ms)")
        sp = m['safepoints']
        if sp.get('count'):
            print(f"\nSAFEPOINTS: {sp['count']} (total stopped {sp['total_stopped_ms']:.0f}ms, "
                  f"max TTSP {sp['max_time_to_safepoint_ms']:.2f}ms)")

        hs = m['heap_stats']
        if hs.get('heap_before_avg_mb') is not None:
            print(f"\nHEAP STATISTICS")
            print(f"  Before GC: {hs['heap_before_min_mb']:.0f}MB - {hs['heap_before_max_mb']:.0f}MB "
                  f"(avg: {hs['heap_before_avg_mb']:.0f}MB)")
            print(f"  After GC:  {hs['heap_after_min_mb']:.0f}MB - {hs['heap_after_max_mb']:.0f}MB "
                  f"(avg: {hs['heap_after_avg_mb']:.0f}MB)")
            if hs.get('heap_capacity_max_mb'):
                print(f"  Capacity:  {hs['heap_capacity_max_mb']:.0f}MB")
            if hs.get('occupancy_after_gc_last_pct') is not None:
                print(f"  Occupancy after GC (end of log): {hs['occupancy_after_gc_last_pct']:.0f}%")
            if hs.get('allocation_rate_mb_s') is not None:
                print(f"  Approx. allocation rate: {hs['allocation_rate_mb_s']:.0f} MB/s")

        causes = m['causes']
        if causes:
            print(f"\nTOP CAUSES")
            for cause, n in list(causes.items())[:6]:
                print(f"  {n:6d}  {cause}")

        print(f"\nISSUE DETECTION")
        for issue in self.detect_issues():
            print(f"  {issue}")
        print("\n" + "=" * 60)

    def export_json(self, outfile: str, extra: Optional[Dict] = None):
        data = self.metrics()
        data['issues'] = self.detect_issues()
        if extra:
            data.update(extra)
        with open(outfile, 'w') as f:
            json.dump(data, f, indent=2, default=str)
        print(f"Analysis exported to {outfile}")


def compare(current: GCAnalyzer, baseline: GCAnalyzer) -> Dict:
    """Compare key metrics; returns a dict and prints a table"""
    cm, bm = current.metrics(), baseline.metrics()

    def g(mm, path):
        cur = mm
        for p in path:
            cur = cur.get(p) if isinstance(cur, dict) else None
            if cur is None:
                return None
        return cur

    rows = [
        ('STW pauses (count)', ('all_pauses', 'count')),
        ('Pauses per minute', ('all_pauses', 'pauses_per_minute')),
        ('GC overhead %', ('all_pauses', 'gc_overhead_pct')),
        ('Longest pause ms', ('all_pauses', 'max_pause_ms')),
        ('Young avg pause ms', ('young_gc_stats', 'avg_pause_ms')),
        ('Young p95 pause ms', ('young_gc_stats', 'p95_pause_ms')),
        ('Young p99 pause ms', ('young_gc_stats', 'p99_pause_ms')),
        ('Full GCs', ('full_gc_stats', 'count')),
        ('ZGC avg pause ms', ('zgc_stats', 'avg_pause_ms')),
        ('ZGC stalls', ('stalls', 'count')),
        ('Heap after GC avg MB', ('heap_stats', 'heap_after_avg_mb')),
        ('Occupancy after GC %', ('heap_stats', 'occupancy_after_gc_last_pct')),
    ]
    out = {}
    print(f"\nCOMPARISON (current vs baseline)")
    print(f"  {'metric':24s} {'baseline':>12s} {'current':>12s} {'delta':>9s}")
    for label, path in rows:
        b, c = g(bm, path), g(cm, path)
        if b is None and c is None:
            continue
        delta = None
        if isinstance(b, (int, float)) and isinstance(c, (int, float)) and b:
            delta = (c - b) / b * 100.0
        out[label] = {'baseline': b, 'current': c, 'delta_pct': delta}
        def fmt(v):
            if v is None:
                return '-'
            if isinstance(v, float):
                return f"{v:.3f}" if abs(v) < 1 else f"{v:.2f}"
            return str(v)
        fb, fc = fmt(b), fmt(c)
        fd = '' if delta is None else f"{delta:+.1f}%"
        print(f"  {label:24s} {fb:>12s} {fc:>12s} {fd:>9s}")

    verdicts = []
    yd = out.get('Young avg pause ms', {}).get('delta_pct')
    if yd is not None:
        print(f"\nCOMPARISON: Young GC avg pause {yd:+.1f}% vs baseline")
        if yd > 20:
            verdicts.append("Pause time significantly increased (possible regression)")
        elif yd < -20:
            verdicts.append("Pause time significantly improved (possible optimization win)")
    fb, fc = g(bm, ('full_gc_stats', 'count')) or 0, g(cm, ('full_gc_stats', 'count')) or 0
    if fc > fb:
        verdicts.append(f"More full GCs than baseline ({fb} -> {fc})")
    ob, oc = g(bm, ('all_pauses', 'gc_overhead_pct')), g(cm, ('all_pauses', 'gc_overhead_pct'))
    if ob is not None and oc is not None and oc > ob * 1.5 and oc - ob > 1.0:
        verdicts.append(f"GC overhead up from {ob:.1f}% to {oc:.1f}%")
    sb, sc = g(bm, ('stalls', 'count')) or 0, g(cm, ('stalls', 'count')) or 0
    if sc > sb:
        verdicts.append(f"More ZGC stalls than baseline ({sb} -> {sc})")
    if bm['summary']['collector'] != cm['summary']['collector']:
        verdicts.append(f"Different collectors: baseline {bm['summary']['collector']}, current {cm['summary']['collector']}")
    for v in verdicts:
        print(f"  - {v}")
    return {'metrics': out, 'verdicts': verdicts}


def main():
    parser = argparse.ArgumentParser(
        description='Analyze GC logs (JDK 17/21/25 unified logging, or JDK 8 legacy) of a Java application on WildFly',
        epilog='Recommended logging: -Xlog:gc*:file=gc.log:time,uptime,level,tags:filecount=10,filesize=100M')
    parser.add_argument('--logfile', required=True, nargs='+',
                        help='GC log file(s); several rotated files may be given (plain or .gz)')
    parser.add_argument('--format', default='auto', choices=['auto', 'unified', 'simple', 'legacy'],
                        help='Log format: auto-detect (default), unified (JDK 9+ -Xlog), '
                             'simple/legacy (JDK 8 -XX:+PrintGC[Details])')
    parser.add_argument('--output', help='Output JSON file for results')
    parser.add_argument('--compare', nargs='+', help='Baseline log file(s) to compare against')
    parser.add_argument('--pause-target', type=float, default=200.0,
                        help='Pause-time goal in ms, e.g. your -XX:MaxGCPauseMillis (default: %(default)s)')

    args = parser.parse_args()

    for p in args.logfile + (args.compare or []):
        if not Path(p).exists():
            print(f"Error: Log file not found: {p}")
            sys.exit(1)

    print(f"Parsing {', '.join(args.logfile)}...")
    res = parse_logs(args.logfile, args.format)
    if not res.events and not res.cycles:
        print(f"Warning: No GC events parsed from log (format: {res.fmt}). "
              f"Check --format and that the log was written with -Xlog:gc or -Xlog:gc* "
              f"(or -XX:+PrintGC/-XX:+PrintGCDetails on JDK 8).")
        sys.exit(1)

    analyzer = GCAnalyzer(res, pause_target_ms=args.pause_target)
    analyzer.print_report()

    comparison = None
    if args.compare:
        print(f"\nComparing with baseline {', '.join(args.compare)}...")
        bres = parse_logs(args.compare, args.format)
        if not bres.events and not bres.cycles:
            print("Warning: No GC events parsed from the baseline; comparison skipped.")
        else:
            comparison = compare(analyzer, GCAnalyzer(bres, pause_target_ms=args.pause_target))

    if args.output:
        analyzer.export_json(args.output, {'comparison': comparison} if comparison else None)


if __name__ == '__main__':
    main()
