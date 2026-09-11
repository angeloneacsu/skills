#!/usr/bin/env python3
"""
Thread Dump Analyzer for Java applications on WildFly

Analyzes HotSpot thread dumps (jstack [-l] <pid>, jcmd <pid> Thread.print [-l],
kill -3 output) to identify lock contention, deadlocks, database connection
pool exhaustion, threads stuck in database calls and busy/idle thread pools.

Supported: JDK 8 through 25 dump formats (including the JDK 19+ header
'"name" #N [os-id] ...'). Use -l (jstack -l / Thread.print -l) to also get
java.util.concurrent lock owners ("Locked ownable synchronizers").
A file with several consecutive dumps (e.g. 3 dumps taken 5-10 s apart) is
supported: one dump is analyzed in detail (--dump-index, default: the last)
and threads stuck in the same frames across all dumps are reported, together
with the CPU time they consumed between the first and last dump.

Usage:
    python3 thread-dump-analyzer.py --dump threaddump.txt
    python3 thread-dump-analyzer.py --dump threaddump.txt --focus blocked   # locks, contention, deadlocks
    python3 thread-dump-analyzer.py --dump threaddump.txt --focus waiting   # what WAITING threads wait for
    python3 thread-dump-analyzer.py --dump threaddump.txt --focus db        # datasource pool waits, JDBC calls
    python3 thread-dump-analyzer.py --dump threaddumps-3x.txt --dump-index 0 --output thread-analysis.json
"""

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional


RE_HEADER = re.compile(r'^"(?P<name>.*)"\s+(?:#(?P<num>\d+)\s+)?(?:\[(?P<osid>\d+)\]\s+)?(?P<rest>.*)$')
RE_STATE = re.compile(r'^\s+java\.lang\.Thread\.State:\s+(?P<state>[A-Z_]+)(?:\s+\((?P<detail>[^)]*)\))?')
RE_FRAME = re.compile(r'^\s+at\s+(?P<frame>.+)$')
RE_LOCKLINE = re.compile(
    r'^\s+- (?P<kind>waiting to lock|waiting to re-lock in wait\(\)|waiting on|parking to wait for|locked|eliminated)'
    r'\s+<(?P<addr>0x[0-9a-fA-F]+)>(?:\s+\(a (?P<cls>[^)]+)\))?')
RE_OWNABLE = re.compile(r'^\s+- <(?P<addr>0x[0-9a-fA-F]+)>\s+\(a (?P<cls>[^)]+)\)')
RE_CPU = re.compile(r'\bcpu=(?P<v>\d+(?:\.\d+)?)(?P<u>ms|s)\b')
RE_ELAPSED = re.compile(r'\belapsed=(?P<v>\d+(?:\.\d+)?)s\b')
RE_TID = re.compile(r'\btid=(?P<tid>0x[0-9a-fA-F]+)')
RE_NID = re.compile(r'\bnid=(?P<nid>0x[0-9a-fA-F]+|\d+)\s*(?P<what>[^\[]*)')
RE_PRIO = re.compile(r'(?<!os_)prio=(?P<prio>\d+)')
RE_DL_THREAD = re.compile(r'^"(?P<name>.*)":\s*$')
RE_DL_WAIT = re.compile(r'^\s+waiting (?:to lock monitor|for ownable synchronizer) (?P<mon>0x[0-9a-fA-F]+)'
                        r'(?:,?\s*\((?:object (?P<obj>0x[0-9a-fA-F]+), )?a (?P<cls>[^)]+)\))?')
RE_DL_HELD = re.compile(r'^\s+which is held by "(?P<name>.*)"')

SECTION_ENDS = ('JNI global refs', 'Found a total of', 'Java stack information for the threads', 'Threads class SMR info')

# Frames of threads that are idle (waiting for work) rather than blocked on something.
IDLE_FRAMES = (
    'java.util.concurrent.ThreadPoolExecutor.getTask',
    'java.util.concurrent.LinkedBlockingQueue.take', 'java.util.concurrent.LinkedBlockingQueue.poll',
    'java.util.concurrent.ArrayBlockingQueue.take', 'java.util.concurrent.SynchronousQueue',
    'java.util.concurrent.LinkedTransferQueue', 'java.util.concurrent.LinkedBlockingDeque.take',
    'java.util.concurrent.ScheduledThreadPoolExecutor$DelayedWorkQueue.take',
    'java.util.concurrent.ForkJoinPool.awaitWork', 'java.util.concurrent.ForkJoinPool.runWorker',
    'org.jboss.threads.EnhancedQueueExecutor$ThreadBody.run', 'org.jboss.threads.EnhancedQueueExecutor$PoolThreadNode.park',
    'org.jboss.threads.EnhancedQueueExecutor.park',
    'java.lang.ref.Reference.waitForReferencePendingList', 'java.lang.ref.ReferenceQueue.remove',
    'java.util.TimerThread.mainLoop', 'jdk.internal.ref.CleanerImpl.run',
    'sun.nio.ch.EPoll.wait', 'sun.nio.ch.KQueue.poll', 'sun.nio.ch.WEPoll.wait', 'sun.nio.ch.EPollArrayWrapper.epollWait',
    'sun.nio.ch.ServerSocketChannelImpl.accept', 'sun.nio.ch.Net.accept', 'java.net.PlainSocketImpl.socketAccept',
    'org.xnio.nio.WorkerThread.run', 'org.apache.activemq.artemis.utils.ActiveMQThreadPoolExecutor',
)
DB_POOL_FRAMES = re.compile(
    r'org\.jboss\.jca\.core\.connectionmanager\.pool\.|org\.jboss\.jca\.adapters\.jdbc\.WrapperDataSource\.getConnection|'
    r'com\.zaxxer\.hikari\.pool\.HikariPool\.getConnection|com\.zaxxer\.hikari\.util\.ConcurrentBag\.borrow|'
    r'org\.apache\.commons\.dbcp2?\.|org\.apache\.tomcat\.jdbc\.pool\.ConnectionPool|oracle\.ucp\.|'
    r'com\.mchange\.v2\.resourcepool\.')
JDBC_DRIVER_FRAMES = re.compile(
    r'^(?:org\.postgresql\.|oracle\.jdbc\.|oracle\.net\.|com\.microsoft\.sqlserver\.jdbc\.|com\.mysql\.|org\.mariadb\.jdbc\.|'
    r'com\.ibm\.db2\.|net\.sourceforge\.jtds\.|org\.h2\.|org\.hsqldb\.|com\.sap\.db\.)')
JVM_INTERNAL = re.compile(r'^(Reference Handler|Finalizer|Signal Dispatcher|Service Thread|Monitor Deflation Thread|'
                          r'C[12] CompilerThread\d+|Sweeper thread|Notification Thread|Common-Cleaner|Attach Listener|'
                          r'process reaper|JFR .*|Surrogate Locker Thread.*)$')
PLATFORM_PKG = ('java.', 'javax.', 'jdk.', 'sun.', 'com.sun.', 'jakarta.')
# container / pool / driver frames skipped when looking for the "application frame" of a thread
INFRA_PKG = ('org.jboss.jca.', 'com.zaxxer.hikari.', 'org.jboss.threads.', 'org.xnio.', 'io.undertow.',
             'org.jboss.invocation.', 'org.jboss.as.ee.component.', 'org.jboss.weld.proxy', 'org.postgresql.',
             'oracle.jdbc.', 'oracle.net.', 'com.microsoft.sqlserver.jdbc.', 'com.mysql.', 'org.mariadb.jdbc.',
             'com.ibm.db2.', 'net.sourceforge.jtds.')
LOCK_CLASSES = ('ReentrantLock', 'ReentrantReadWriteLock', 'StampedLock')


def pool_name(name: str) -> str:
    """'default task-12' -> 'default task'; 'EJB default - 3' -> 'EJB default'; 'pool-3-thread-7' -> 'pool-3-thread'"""
    base = re.sub(r'[\s\-#_:]*\d+$', '', name).rstrip(' -#_:')
    return base or name


class ThreadDumpAnalyzer:
    """Analyzes thread dump files"""

    def __init__(self, dump_file: str, dump_index: int = -1):
        self.dump_file = dump_file
        self.dump_index = dump_index
        self.dumps: List[Dict] = []           # all dumps in the file
        self.threads: List[Dict] = []         # threads of the selected dump
        self.vm_threads: List[Dict] = []
        self.jvm_deadlocks: List[List[Dict]] = []
        self.lock_holders: Dict[str, Dict] = {}
        self.lock_waiters: Dict[str, List[Dict]] = defaultdict(list)
        self.lock_class: Dict[str, str] = {}
        self.timestamp: Optional[str] = None

    # ------------------------------------------------------------------ parsing
    def parse(self) -> List[Dict]:
        with open(self.dump_file, 'r', encoding='utf-8', errors='replace') as f:
            lines = f.read().splitlines()

        starts = [i for i, l in enumerate(lines) if l.startswith('Full thread dump')]
        if not starts:
            chunks = [(None, lines)]
        else:
            chunks = []
            for n, s in enumerate(starts):
                end = starts[n + 1] if n + 1 < len(starts) else len(lines)
                ts = lines[s - 1].strip() if s > 0 and re.match(r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}', lines[s - 1]) else None
                chunks.append((ts, lines[s:end]))
        for ts, chunk in chunks:
            d = self._parse_dump(chunk)
            d['timestamp'] = ts
            if d['threads'] or d['vm_threads']:
                self.dumps.append(d)
        if not self.dumps:
            return []
        try:
            sel = self.dumps[self.dump_index]
        except IndexError:
            raise ValueError(f"--dump-index {self.dump_index} out of range: the file contains {len(self.dumps)} dump(s)")
        self.threads = sel['threads']
        self.vm_threads = sel['vm_threads']
        self.jvm_deadlocks = sel['deadlocks']
        self.timestamp = sel['timestamp']
        for t in self.threads:
            self._track_locks(t)
        return self.threads

    def _parse_dump(self, lines: List[str]) -> Dict:
        threads, vm_threads, deadlocks = [], [], []
        cur: Optional[Dict] = None
        in_ownable = False
        i = 0
        while i < len(lines):
            line = lines[i]
            if line.startswith('Found one Java-level deadlock'):
                cur = None
                i = self._parse_deadlock_section(lines, i + 1, deadlocks)
                continue
            if line.startswith(SECTION_ENDS) or line.strip() == 'Heap':   # kill -3 output ends with a heap summary
                cur = None
                i += 1
                continue
            hm = RE_HEADER.match(line) if line.startswith('"') else None
            if hm and ('tid=' in hm.group('rest') or 'prio=' in hm.group('rest') or 'nid=' in hm.group('rest')):
                cur = self._new_thread(hm)
                in_ownable = False
                (threads if cur['java_thread'] else vm_threads).append(cur)
                i += 1
                continue
            if cur is None:
                i += 1
                continue
            sm = RE_STATE.match(line)
            if sm:
                cur['state'] = sm.group('state')
                cur['state_detail'] = sm.group('detail') or ''
            elif line.strip() == 'Locked ownable synchronizers:':
                in_ownable = True
            else:
                fm = RE_FRAME.match(line)
                if fm:
                    cur['stack_trace'].append(fm.group('frame'))
                else:
                    lm = RE_LOCKLINE.match(line)
                    if lm:
                        self._lock_line(cur, lm)
                    elif in_ownable:
                        om = RE_OWNABLE.match(line)
                        if om:
                            cur['owned_synchronizers'].append(om.group('addr'))
                            cur['lock_classes'][om.group('addr')] = om.group('cls')
            i += 1
        for t in threads:
            # a monitor released by Object.wait() is still printed as "- locked" in lower frames
            t['locks_holding'] = [a for a in t['locks_holding'] if a not in t['waiting_on']]
            t['category'] = self._categorize(t)
        return {'threads': threads, 'vm_threads': vm_threads, 'deadlocks': deadlocks}

    @staticmethod
    def _new_thread(hm) -> Dict:
        rest = hm.group('rest')
        pm = RE_PRIO.search(rest)
        cpu = RE_CPU.search(rest)
        el = RE_ELAPSED.search(rest)
        tid = RE_TID.search(rest)
        nid = RE_NID.search(rest)
        name = hm.group('name')
        # VM-internal (non-Java) threads have no "#N" and no Java priority, e.g. "GC Thread#0" os_prio=0 ...
        java_thread = hm.group('num') is not None or bool(re.search(r'(^|\s)prio=', rest))
        return {
            'name': name,
            'num': int(hm.group('num')) if hm.group('num') else None,
            'prio': int(pm.group('prio')) if pm else None,
            'tid': tid.group('tid') if tid else None,
            'nid': nid.group('nid') if nid else None,
            'header_state': (nid.group('what').strip() if nid else ''),
            'cpu_ms': (float(cpu.group('v')) * (1000.0 if cpu.group('u') == 's' else 1.0)) if cpu else None,
            'elapsed_s': float(el.group('v')) if el else None,
            'is_daemon': bool(re.search(r'(^|\s)daemon(\s|$)', rest)),
            'java_thread': java_thread,
            'jvm_internal': bool(JVM_INTERNAL.match(name)),
            'state': 'UNKNOWN' if java_thread else 'VM',
            'state_detail': '',
            'stack_trace': [],
            'locks_waiting': [],      # monitors: waiting to lock / re-lock
            'waiting_on': [],         # Object.wait() monitors
            'parked_on': [],          # LockSupport.park blockers
            'locks_holding': [],      # monitors held
            'owned_synchronizers': [],
            'lock_classes': {},
        }

    @staticmethod
    def _lock_line(t: Dict, lm):
        kind, addr, cls = lm.group('kind'), lm.group('addr'), lm.group('cls') or ''
        t['lock_classes'][addr] = cls
        if kind.startswith('waiting to'):
            t['locks_waiting'].append(addr)
        elif kind == 'waiting on':
            t['waiting_on'].append(addr)
        elif kind == 'parking to wait for':
            t['parked_on'].append(addr)
        elif kind == 'locked':
            if addr not in t['locks_holding']:
                t['locks_holding'].append(addr)

    @staticmethod
    def _is_deadlock_count(line: str) -> bool:
        return bool(re.match(r'^Found (?:a total of )?\d+ deadlocks?\.', line))

    def _parse_deadlock_section(self, lines: List[str], i: int, out: List) -> int:
        """Parse one 'Found one Java-level deadlock:' block; return the index to resume at"""
        cycle, cur = [], None
        while i < len(lines):
            l = lines[i]
            if l.startswith(('Java stack information for the threads', 'Found one Java-level deadlock')) \
                    or self._is_deadlock_count(l):
                break
            m = RE_DL_THREAD.match(l)
            if m:
                cur = {'thread': m.group('name'), 'waiting_for': None, 'lock_class': None, 'held_by': None}
                cycle.append(cur)
            elif cur is not None:
                wm, hm = RE_DL_WAIT.match(l), RE_DL_HELD.match(l)
                if wm:
                    cur['waiting_for'] = wm.group('obj') or wm.group('mon')
                    cur['lock_class'] = wm.group('cls')
                elif hm:
                    cur['held_by'] = hm.group('name')
            i += 1
        if cycle:
            out.append(cycle)
        # skip the "Java stack information" block (it repeats the stacks under "name": headers)
        while i < len(lines):
            if lines[i].startswith('Found one Java-level deadlock'):
                return i
            if self._is_deadlock_count(lines[i]):
                return i + 1
            i += 1
        return i

    # ------------------------------------------------------------------ classification
    @staticmethod
    def _top(t: Dict, n: int) -> List[str]:
        return t['stack_trace'][:n]

    def _categorize(self, t: Dict) -> str:
        if not t['java_thread']:
            return 'vm'
        st, frames = t['state'], t['stack_trace']
        top12 = frames[:12]
        if st in ('WAITING', 'TIMED_WAITING', 'BLOCKED') and any(DB_POOL_FRAMES.search(f) for f in frames[:20]):
            return 'db_pool_wait'
        if any(JDBC_DRIVER_FRAMES.match(f) for f in top12):
            return 'db_call' if st == 'RUNNABLE' else ('db_blocked' if st == 'BLOCKED' else 'db_call_waiting')
        if st == 'BLOCKED':
            return 'blocked'
        if t['jvm_internal']:
            return 'jvm'
        if any(f.startswith(IDLE_FRAMES) for f in frames[:10]):
            return 'idle'
        if not frames and st == 'RUNNABLE':
            return 'jvm'
        if st == 'TIMED_WAITING' and t['state_detail'] == 'sleeping':
            return 'sleeping'
        if st in ('WAITING', 'TIMED_WAITING'):
            if t['parked_on'] and any(c in t['lock_classes'].get(a, '') for a in t['parked_on'] for c in LOCK_CLASSES):
                return 'lock_wait'
            return 'waiting'
        return 'running'

    def _track_locks(self, thread: Dict):
        for addr in thread['locks_holding'] + thread['owned_synchronizers']:
            self.lock_holders[addr] = thread
        for addr in thread['locks_waiting']:
            self.lock_waiters[addr].append(thread)
        for addr in thread['parked_on']:
            cls = thread['lock_classes'].get(addr, '')
            if 'ConditionObject' not in cls:
                self.lock_waiters[addr].append(thread)
        for addr, cls in thread['lock_classes'].items():
            self.lock_class.setdefault(addr, cls)

    # ------------------------------------------------------------------ queries
    def java_threads(self) -> List[Dict]:
        return self.threads

    def get_state_distribution(self) -> Dict[str, int]:
        distribution = defaultdict(int)
        for thread in self.threads:
            distribution[thread['state']] += 1
        return dict(distribution)

    def get_category_distribution(self) -> Dict[str, int]:
        d = defaultdict(int)
        for t in self.threads:
            d[t['category']] += 1
        return dict(d)

    def get_blocked_threads(self) -> List[Dict]:
        return [t for t in self.threads if t['state'] == 'BLOCKED']

    def get_waiting_threads(self) -> List[Dict]:
        return [t for t in self.threads if t['state'] in ('WAITING', 'TIMED_WAITING')]

    @staticmethod
    def app_frame(t: Dict) -> str:
        """First frame outside the JDK and container/pool/driver code (falls back to the top frame)"""
        for f in t['stack_trace']:
            if not f.startswith(PLATFORM_PKG) and not f.startswith(INFRA_PKG):
                return f
        for f in t['stack_trace']:
            if not f.startswith(PLATFORM_PKG):
                return f
        return t['stack_trace'][0] if t['stack_trace'] else '(no Java frames)'

    def find_contention_chains(self) -> List[Dict]:
        """Locks (monitors and j.u.c. locks) with waiting threads, most contended first"""
        chains = []
        for lock_id, waiters in self.lock_waiters.items():
            if not waiters:
                continue
            holder = self.lock_holders.get(lock_id)
            cls = self.lock_class.get(lock_id, '')
            if holder is None and not any(c in cls for c in LOCK_CLASSES) and not any(lock_id in w['locks_waiting'] for w in waiters):
                continue   # parked on something without an owner (semaphore, latch...): reported under waiting
            chains.append({
                'lock_id': lock_id,
                'lock_class': cls,
                'holder': holder['name'] if holder else None,
                'holder_state': holder['state'] if holder else None,
                'holder_frame': self.app_frame(holder) if holder else None,
                'waiters': [w['name'] for w in waiters],
                'waiter_count': len(waiters),
            })
        return sorted(chains, key=lambda x: x['waiter_count'], reverse=True)

    def find_deadlocks(self) -> List[List[str]]:
        """Deadlocks reported by the JVM, plus cycles found in the wait-for graph"""
        found = []
        seen = set()
        for cyc in self.jvm_deadlocks:
            names = [c['thread'] for c in cyc]
            key = frozenset(names)
            if key not in seen:
                seen.add(key)
                found.append(names)
        by_name = {t['name']: t for t in self.threads}
        waits_for = {}
        for t in self.threads:
            for addr in t['locks_waiting'] + [a for a in t['parked_on'] if 'ConditionObject' not in t['lock_classes'].get(a, '')]:
                h = self.lock_holders.get(addr)
                if h is not None and h is not t:
                    waits_for[t['name']] = h['name']
        for start in waits_for:
            path, cur = [], start
            while cur in waits_for and cur not in path:
                path.append(cur)
                cur = waits_for[cur]
            if cur in path:
                cyc = path[path.index(cur):]
                key = frozenset(cyc)
                if key not in seen and all(n in by_name for n in cyc):
                    seen.add(key)
                    found.append(cyc)
        return found

    def find_database_wait(self) -> List[Dict]:
        """Threads waiting for a connection from the datasource pool"""
        return [t for t in self.threads if t['category'] == 'db_pool_wait']

    def find_database_calls(self) -> List[Dict]:
        """Threads inside the JDBC driver (executing a statement / waiting for the database)"""
        return [t for t in self.threads if t['category'] in ('db_call', 'db_blocked', 'db_call_waiting')]

    def waiting_groups(self) -> List[Dict]:
        """Non-idle WAITING/TIMED_WAITING threads grouped by what they wait on"""
        groups: Dict[str, Dict] = {}
        for t in self.threads:
            if t['category'] not in ('waiting', 'lock_wait', 'db_pool_wait', 'db_call_waiting'):
                continue
            if t['parked_on']:
                addr = t['parked_on'][0]
                key = f"parked on {t['lock_classes'].get(addr, '?')} <{addr}>"
            elif t['waiting_on']:
                addr = t['waiting_on'][0]
                key = f"Object.wait() on {t['lock_classes'].get(addr, '?')} <{addr}>"
            else:
                key = f"at {self.app_frame(t)}"
            g = groups.setdefault(key, {'waiting_for': key, 'threads': [], 'app_frame': self.app_frame(t)})
            g['threads'].append(t['name'])
        return sorted(({**g, 'count': len(g['threads'])} for g in groups.values()), key=lambda g: -g['count'])

    def thread_pools(self) -> List[Dict]:
        pools: Dict[str, Dict] = {}
        for t in self.threads:
            if t['jvm_internal']:
                continue
            p = pools.setdefault(pool_name(t['name']), {'pool': pool_name(t['name']), 'threads': 0, 'busy': 0,
                                                        'states': defaultdict(int)})
            p['threads'] += 1
            p['states'][t['state']] += 1
            if t['category'] not in ('idle',):
                p['busy'] += 1
        out = [dict(p, states=dict(p['states'])) for p in pools.values() if p['threads'] > 1]
        return sorted(out, key=lambda p: -p['threads'])

    def hot_frames(self, n: int = 5) -> List[Dict]:
        """Application frames shared by several busy (non-idle) threads"""
        c = defaultdict(list)
        for t in self.threads:
            if t['category'] in ('idle', 'jvm', 'vm', 'sleeping'):
                continue
            c[self.app_frame(t)].append(t['name'])
        rows = [{'frame': f, 'count': len(v), 'threads': v} for f, v in c.items() if len(v) > 1]
        return sorted(rows, key=lambda r: -r['count'])[:n]

    def stuck_threads(self) -> List[Dict]:
        """Across several dumps: RUNNABLE or BLOCKED threads in the same top frames in every dump
        (RUNNABLE + CPU growing = hot loop / long computation; RUNNABLE in JDBC = long query; BLOCKED = long contention)"""
        if len(self.dumps) < 2:
            return []
        maps = [{t['name']: t for t in d['threads']} for d in self.dumps]
        out = []
        for name, t0 in maps[0].items():
            if t0['state'] not in ('RUNNABLE', 'BLOCKED') or t0['category'] in ('idle', 'jvm', 'vm') \
                    or not t0['stack_trace']:
                continue
            seq = [m.get(name) for m in maps]
            if any(x is None for x in seq):
                continue
            if all(x['stack_trace'][:5] == t0['stack_trace'][:5] and x['state'] == t0['state'] for x in seq):
                cpu = None
                if seq[0]['cpu_ms'] is not None and seq[-1]['cpu_ms'] is not None:
                    cpu = seq[-1]['cpu_ms'] - seq[0]['cpu_ms']
                out.append({'name': name, 'state': t0['state'], 'frame': self.app_frame(t0),
                            'dumps': len(seq), 'cpu_ms_delta': cpu})
        return sorted(out, key=lambda r: -(r['cpu_ms_delta'] or 0))

    def cpu_top(self, n: int = 5) -> List[Dict]:
        """Threads that used the most CPU between the first and the last dump (JDK 11+ 'cpu=')"""
        if len(self.dumps) < 2:
            return []
        first = {t['name']: t for t in self.dumps[0]['threads']}
        rows = []
        for t in self.dumps[-1]['threads']:
            f = first.get(t['name'])
            if f and t['cpu_ms'] is not None and f['cpu_ms'] is not None:
                rows.append({'name': t['name'], 'cpu_ms_delta': t['cpu_ms'] - f['cpu_ms'], 'state': t['state'],
                             'frame': self.app_frame(t)})
        return sorted(rows, key=lambda r: -r['cpu_ms_delta'])[:n]

    # ------------------------------------------------------------------ issues
    def detect_issues(self, focus: str = 'all') -> List[str]:
        issues = []
        blocked = self.get_blocked_threads()
        chains = self.find_contention_chains()
        deadlocks = self.find_deadlocks()
        cats = self.get_category_distribution()
        java = [t for t in self.threads if not t['jvm_internal']]

        if focus in ('all', 'blocked'):
            for cyc in deadlocks:
                issues.append(f"ERROR: Deadlock between {', '.join(cyc)}. These threads will never proceed; "
                              f"restart required, fix the lock ordering.")
            if len(blocked) > 10:
                issues.append(f"ERROR: {len(blocked)} threads blocked. Severe lock contention detected.")
            elif len(blocked) > 5:
                issues.append(f"WARNING: {len(blocked)} threads blocked. Lock contention may be affecting performance.")
            for ch in chains[:5]:
                if ch['waiter_count'] >= 3:
                    who = f"held by {ch['holder']} ({ch['holder_state']}, at {ch['holder_frame']})" if ch['holder'] \
                        else "owner unknown (capture with -l to see j.u.c. lock owners)"
                    issues.append(f"WARNING: Lock contention: {ch['waiter_count']} threads waiting for "
                                  f"{ch['lock_class'] or 'lock'} <{ch['lock_id']}> {who}")

        if focus in ('all', 'db'):
            db_wait = self.find_database_wait()
            db_calls = self.find_database_calls()
            if db_wait:
                issues.append(f"WARNING: {len(db_wait)} threads waiting for database connections from the pool. "
                              f"DataSource pool may be exhausted (check max-pool-size, connection leaks, slow queries; "
                              f"they fail after blocking-timeout-millis).")
            if db_calls:
                busy = sum(1 for t in java if t['category'] not in ('idle', 'jvm', 'vm'))
                lvl = 'WARNING' if (len(db_calls) >= 5 or (busy and len(db_calls) / busy >= 0.25)) else 'INFO'
                issues.append(f"{lvl}: {len(db_calls)} thread(s) inside JDBC driver calls (waiting for the database). "
                              f"Correlate with database-side active sessions / slow query log.")

        if focus in ('all', 'waiting'):
            for g in self.waiting_groups()[:3]:
                if g['count'] >= 5:
                    issues.append(f"WARNING: {g['count']} threads {g['waiting_for']} (at {g['app_frame']}).")
            nonidle_wait = cats.get('waiting', 0) + cats.get('lock_wait', 0)
            if java and nonidle_wait > 10 and nonidle_wait > 0.5 * len(java):
                issues.append(f"WARNING: {nonidle_wait}/{len(java)} threads waiting on something other than new work. "
                              f"Resource contention or a slow dependency.")
            if cats.get('idle'):
                issues.append(f"INFO: {cats['idle']} idle pool threads (waiting for work, normal).")

        if focus == 'all':
            runnable = [t for t in java if t['state'] == 'RUNNABLE']
            if java and not runnable:
                issues.append("INFO: No RUNNABLE application threads (idle, or everything is waiting).")
            stuck = self.stuck_threads()
            if stuck:
                by_state = defaultdict(int)
                for s_ in stuck:
                    by_state[s_['state']] += 1
                states = ', '.join(f"{v} {k}" for k, v in sorted(by_state.items()))
                ex = stuck[0]
                cpu = '' if ex['cpu_ms_delta'] is None else f", cpu +{ex['cpu_ms_delta']:.0f}ms"
                issues.append(f"WARNING: {len(stuck)} threads ({states}) in the same frames in all {ex['dumps']} dumps, "
                              f"e.g. {ex['name']} at {ex['frame']}{cpu}. Long-running or stuck work.")

        if not any(i.startswith(('ERROR', 'WARNING')) for i in issues):
            issues.insert(0, "OK: No critical issues detected.")
        return issues

    # ------------------------------------------------------------------ output
    def _print_thread(self, t: Dict, frames: int = 1):
        print(f"  - {t['name']}  [{t['state']}{' ' + t['state_detail'] if t['state_detail'] else ''}]")
        if t['locks_waiting']:
            a = t['locks_waiting'][0]
            h = self.lock_holders.get(a)
            print(f"    Waiting for lock: <{a}> ({t['lock_classes'].get(a, '?')})" + (f" held by {h['name']}" if h else ''))
        elif t['parked_on']:
            a = t['parked_on'][0]
            h = self.lock_holders.get(a)
            print(f"    Parked on: <{a}> ({t['lock_classes'].get(a, '?')})" + (f" owned by {h['name']}" if h else ''))
        for f in t['stack_trace'][:frames]:
            print(f"    At: {f}")
        af = self.app_frame(t)
        if t['stack_trace'] and af not in t['stack_trace'][:frames]:
            print(f"    App frame: {af}")

    def print_report(self, focus: str = 'all'):
        print("\n" + "=" * 60)
        print("THREAD DUMP ANALYSIS REPORT" + ('' if focus == 'all' else f" (focus: {focus})"))
        print("=" * 60)
        if len(self.dumps) > 1:
            idx = self.dump_index if self.dump_index >= 0 else len(self.dumps) + self.dump_index
            print(f"\nDumps in file: {len(self.dumps)}; analyzing dump #{idx} ({self.timestamp or 'no timestamp'})")
        elif self.timestamp:
            print(f"\nDump taken: {self.timestamp}")

        state_dist = self.get_state_distribution()
        print(f"\nTOTAL THREADS: {len(self.threads)} Java threads (+{len(self.vm_threads)} VM/GC threads)")
        print(f"\nTHREAD STATE DISTRIBUTION:")
        for state, count in sorted(state_dist.items()):
            percentage = (count / len(self.threads) * 100) if self.threads else 0
            print(f"  {state:15s}: {count:3d} ({percentage:5.1f}%)")
        cats = self.get_category_distribution()
        print(f"\nACTIVITY: " + ', '.join(f"{k}={v}" for k, v in sorted(cats.items(), key=lambda kv: -kv[1])))

        if focus in ('all', 'blocked'):
            deadlocks = self.find_deadlocks()
            if deadlocks:
                print(f"\nDEADLOCKS ({len(deadlocks)}):")
                for cyc in deadlocks:
                    print(f"  - {' -> '.join(cyc)} -> {cyc[0]}")
            blocked = self.get_blocked_threads()
            if blocked:
                print(f"\nBLOCKED THREADS ({len(blocked)}):")
                for t in blocked[:10 if focus == 'all' else 50]:
                    self._print_thread(t, frames=1 if focus == 'all' else 3)
            chains = self.find_contention_chains()
            if chains:
                print(f"\nLOCK CONTENTION CHAINS:")
                for ch in chains[:5 if focus == 'all' else 20]:
                    print(f"  Lock <{ch['lock_id']}> ({ch['lock_class'] or '?'}):")
                    if ch['holder']:
                        print(f"    Held by: {ch['holder']} (state: {ch['holder_state']}) at {ch['holder_frame']}")
                    else:
                        print(f"    Held by: unknown (use jstack -l / Thread.print -l for j.u.c. lock owners)")
                    print(f"    Waiting: {ch['waiter_count']} threads: {', '.join(ch['waiters'][:8])}"
                          f"{' ...' if ch['waiter_count'] > 8 else ''}")

        if focus in ('all', 'waiting'):
            groups = self.waiting_groups()
            if groups:
                print(f"\nWAITING THREADS BY WAIT TARGET (idle pool threads excluded):")
                for g in groups[:8 if focus == 'all' else 30]:
                    print(f"  {g['count']:3d}  {g['waiting_for']}")
                    print(f"       at {g['app_frame']}; e.g. {', '.join(g['threads'][:4])}")
            if focus == 'waiting':
                sleeping = [t for t in self.threads if t['category'] == 'sleeping']
                idle = [t for t in self.threads if t['category'] == 'idle']
                print(f"\n  Sleeping (Thread.sleep): {len(sleeping)}   Idle pool threads: {len(idle)}")

        if focus in ('all', 'db'):
            db_wait = self.find_database_wait()
            if db_wait:
                print(f"\nDATABASE CONNECTION POOL WAIT ({len(db_wait)} threads):")
                for t in db_wait[:5 if focus == 'all' else 50]:
                    pool_frame = next((f for f in t['stack_trace'] if DB_POOL_FRAMES.search(f)), '')
                    print(f"  - {t['name']} [{t['state']}] in {pool_frame}")
            db_calls = self.find_database_calls()
            if db_calls:
                print(f"\nIN JDBC DRIVER / DATABASE CALL ({len(db_calls)} threads):")
                for t in db_calls[:5 if focus == 'all' else 50]:
                    drv = next((f for f in t['stack_trace'] if JDBC_DRIVER_FRAMES.match(f)), '')
                    print(f"  - {t['name']} [{t['state']}] in {drv}; app frame: {self.app_frame(t)}")
            if focus == 'db' and not db_wait and not db_calls:
                print("\n  No threads waiting for a pooled connection or inside a JDBC driver.")

        if focus == 'all':
            pools = self.thread_pools()
            if pools:
                print(f"\nTHREAD POOLS (by name prefix):")
                for p in pools[:10]:
                    st = ', '.join(f"{k} {v}" for k, v in sorted(p['states'].items()))
                    print(f"  {p['pool'][:40]:40s} {p['threads']:4d} threads, {p['busy']:4d} busy  ({st})")
            hot = self.hot_frames()
            if hot:
                print(f"\nHOT APPLICATION FRAMES (busy threads sharing the same frame):")
                for h in hot:
                    print(f"  {h['count']:3d}  {h['frame']}")
            stuck = self.stuck_threads()
            if stuck:
                print(f"\nTHREADS IN THE SAME FRAMES IN ALL {len(self.dumps)} DUMPS:")
                for s in stuck[:10]:
                    cpu = '' if s['cpu_ms_delta'] is None else f"  cpu +{s['cpu_ms_delta']:.0f}ms"
                    print(f"  - {s['name']} [{s['state']}] at {s['frame']}{cpu}")
            top = [r for r in self.cpu_top() if r['cpu_ms_delta'] >= 1.0]
            if top:
                print(f"\nTOP CPU BETWEEN FIRST AND LAST DUMP:")
                for r in top:
                    print(f"  {r['cpu_ms_delta']:9.0f}ms  {r['name']} [{r['state']}] at {r['frame']}")

        print(f"\nISSUE DETECTION:")
        for issue in self.detect_issues(focus):
            print(f"  {issue}")
        print("\n" + "=" * 60)

    def export_json(self, outfile: str, focus: str = 'all'):
        def brief(t):
            return {'name': t['name'], 'state': t['state'], 'category': t['category'],
                    'waiting_for': t['locks_waiting'] + t['parked_on'], 'app_frame': self.app_frame(t),
                    'top_frame': t['stack_trace'][0] if t['stack_trace'] else None}
        data = {
            'focus': focus,
            'summary': {
                'dumps_in_file': len(self.dumps),
                'dump_timestamp': self.timestamp,
                'total_threads': len(self.threads),
                'vm_threads': len(self.vm_threads),
                'state_distribution': self.get_state_distribution(),
                'category_distribution': self.get_category_distribution(),
            },
            'deadlocks': self.find_deadlocks(),
            'blocked_threads': [{'name': t['name'], 'waiting_for': t['locks_waiting'],
                                 'app_frame': self.app_frame(t)} for t in self.get_blocked_threads()],
            'lock_contention_chains': self.find_contention_chains(),
            'waiting_groups': self.waiting_groups(),
            'db_wait_threads': [brief(t) for t in self.find_database_wait()],
            'db_call_threads': [brief(t) for t in self.find_database_calls()],
            'thread_pools': self.thread_pools(),
            'hot_frames': self.hot_frames(),
            'stuck_threads': self.stuck_threads(),
            'cpu_top': self.cpu_top(),
            'issues': self.detect_issues(focus),
        }
        with open(outfile, 'w') as f:
            json.dump(data, f, indent=2)
        print(f"Analysis exported to {outfile}")


def main():
    parser = argparse.ArgumentParser(
        description='Analyze thread dumps (jstack / jcmd Thread.print, JDK 8-25) of a Java application on WildFly')
    parser.add_argument('--dump', required=True, help='Path to thread dump file (may contain several dumps)')
    parser.add_argument('--output', help='Output JSON file for results (always contains all sections)')
    parser.add_argument('--focus', choices=['blocked', 'waiting', 'db', 'all'], default='all',
                        help='Report only one area: blocked = BLOCKED threads, lock contention, deadlocks; '
                             'waiting = what WAITING/TIMED_WAITING threads wait on (idle pool threads excluded); '
                             'db = datasource pool waits and threads inside JDBC calls; all = everything (default)')
    parser.add_argument('--dump-index', type=int, default=-1,
                        help='Which dump to analyze when the file holds several (0 = first, -1 = last; default: -1)')

    args = parser.parse_args()

    if not Path(args.dump).exists():
        print(f"Error: Dump file not found: {args.dump}")
        sys.exit(1)

    print(f"Parsing {args.dump}...")
    analyzer = ThreadDumpAnalyzer(args.dump, dump_index=args.dump_index)
    try:
        threads = analyzer.parse()
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)

    if not threads:
        print("Error: No threads parsed from dump (expected HotSpot jstack / jcmd Thread.print format)")
        sys.exit(1)

    print(f"Parsed {len(threads)} threads" + (f" (dump {len(analyzer.dumps)} of {len(analyzer.dumps)})"
                                              if len(analyzer.dumps) > 1 and args.dump_index == -1 else ''))
    analyzer.print_report(args.focus)

    if args.output:
        analyzer.export_json(args.output, args.focus)


if __name__ == '__main__':
    main()
