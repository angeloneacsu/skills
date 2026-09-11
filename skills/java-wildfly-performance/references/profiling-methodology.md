# Performance Profiling Methodology for WildFly Applications

## Overview

Performance profiling identifies and measures performance issues. This guide provides a step-by-step methodology for profiling heavy-load Jakarta EE applications running on WildFly.

## Table of Contents
1. GC Log Analysis Workflow
2. Thread Dump Analysis
3. Heap Dump Analysis
4. Network/Database Profiling
5. Production Profiling Safety
6. Tools & Commands Reference

## 1. GC Log Analysis Workflow

### Step 1: Collect GC Logs

**Enable unified GC logging in WildFly:**

```bash
# Add to $WILDFLY_HOME/bin/standalone.conf (or your IaC / image definition):

JAVA_OPTS="${JAVA_OPTS} -Xlog:gc*:file=<logs-dir>/gc-%t.log:time,uptime,level,tags:filecount=10,filesize=100m"
```

Use an absolute path: a relative `file=` is resolved against the JVM's working directory. Prefer `gc-%t.log` over `gc.log` on nodes that restart automatically — see the `%t` note in `jvm-tuning-guide.md`.

**Log file locations:**
- Wherever `file=` points; keep it next to `server.log` (`$WILDFLY_HOME/standalone/log/` by default)
- Production: ship to the central log platform, or keep on persistent/shared storage

**Rotation settings:**
- `filecount=10`: Keep last 10 files (1GB total)
- `filesize=100m`: Rotate each file at 100MB

### Step 2: Establish Baseline Metrics

**Capture baseline during normal operation:**

```bash
# Collect GC logs for 24 hours of normal operation
# Time period: Normal business hours (not period-end, not peak)

# Key baseline metrics to extract:
# 1. Young GC frequency (every X seconds)
# 2. Young GC pause time (min, avg, max, p95, p99)
# 3. Full GC frequency (how many per day)
# 4. Heap occupancy (min, max, after GC)
```

**Parse GC logs for metrics:**

JDK 17+ unified logs (G1) print one `[gc]` line per pause, ending in heap before→after(committed) and the duration, e.g.
`[...][info][gc          ] GC(212) Pause Young (Prepare Mixed) (G1 Evacuation Pause) 475M->343M(512M) 3.442ms`.
Pause types: `Pause Young (Normal)`, `Pause Young (Concurrent Start)`, `Pause Young (Prepare Mixed)`, `Pause Young (Mixed)`, `Pause Remark`, `Pause Cleanup`, `Pause Full (...)`. The matching `[gc,start]` line has no duration, hence the `ms$` anchor below.

```bash
# Young GC pause times (G1GC)
grep -E "Pause Young.*ms$" gc.log | \
  awk '{v=$NF; sub(/ms$/,"",v); v+=0; sum+=v; cnt++; if(v>max)max=v; if(!min || v<min)min=v} \
       END {print "Count: " cnt ", Avg: " sum/cnt "ms, Min: " min "ms, Max: " max "ms"}'

# Count full GCs (JDK 17+ logs say "Pause Full", never "Full GC")
grep -cE "Pause Full.*ms$" gc.log

# Heap occupancy before->after(committed) of the last pauses
grep -E "Pause (Young|Full).*ms$" gc.log | awk '{print $(NF-1)}' | tail -5

# Pauses caused by humongous allocations (large arrays)
grep -cE "G1 Humongous Allocation.*ms$" gc.log
```

`scripts/gc-log-analyzer.py --logfile gc.log` computes the same plus p95/p99.

### Step 3: Compare Problem Period to Baseline

**When investigating an issue, collect the same time period:**

```bash
# If issue occurs 14:00-17:00 daily
# Collect logs from problematic period

# Compare metrics:
# - Young GC pause time increased? How much?
# - Full GC frequency increased? How many more?
# - Heap occupancy pattern changed?

# Visualization (if using analytics tool):
# Plot baseline vs problem period
# Identify correlation between issue and GC behavior
```

### Step 4: Diagnose Root Cause from GC Pattern

**Pattern: Young GC pause time increasing over time**

```
Timeline:
14:00 - Young GC: 50ms
14:30 - Young GC: 75ms
15:00 - Young GC: 100ms
15:30 - Young GC: 150ms
16:00 - Young GC: 200ms

Diagnosis: Young generation filling faster than normal
Cause: Application allocating more objects than baseline
Solution: Investigate code for unusual allocations, or increase heap
```

**Pattern: Sudden full GC**

```
Timeline:
14:50 - Young GC: 80ms (normal)
14:51 - Full GC: 15000ms (PROBLEM!)
14:52 - Young GC: 60ms (resumed)

Diagnosis: Old generation became full
Cause: Many objects promoted from young gen to old gen
Solution: Increase heap size, or investigate for memory leak
```

**Pattern: Frequent full GCs**

```
Timeline (over 1 hour):
14:00 - Full GC: 5000ms
14:20 - Full GC: 5100ms
14:40 - Full GC: 5200ms
15:00 - Full GC: 5300ms

Diagnosis: Full GC every 20 minutes (abnormal)
Cause: Heap too small for workload, or memory leak
Solution: Increase heap size significantly (50%+)
```

**Pattern: Heap occupancy increasing (memory leak)**

```
Timeline (over 8 hours):
08:00 - Heap after GC: 3GB
12:00 - Heap after GC: 4GB
16:00 - Heap after GC: 5GB
20:00 - Heap after GC: 6GB

Diagnosis: Heap growing 1GB per 4 hours
Cause: Objects not being garbage collected
Solution: Memory leak in application or cache
```

### Step 5: Extract Actionable Metrics

**Create a summary report (illustrative numbers):**

```
=== GC ANALYSIS REPORT ===

Period: <day 2>, 14:00-17:00 (issue period)
vs
Baseline: <day 1>, 14:00-17:00 (normal period)

Metric                  Baseline    Problem     Delta
─────────────────────────────────────────────────────
Young GC frequency      Every 10s   Every 5s    2x (worse)
Young GC pause (avg)    80ms        150ms       +87% (worse)
Young GC pause (max)    150ms       300ms       +100% (worse)
Full GC count           0           3           3 additional
Heap after GC (min)     4GB         4.5GB       +12% (worse)
Heap after GC (max)     8GB         9.5GB       problem: exceeds max!

Conclusion: Heap too small during peak load. Increase Xmx.
```

## 2. Thread Dump Analysis

### Step 1: Capture Thread Dump

**Method 1: Via jcmd (preferred; run as the user that owns the WildFly process)**

```bash
# Find the WildFly JVM
jcmd -l | grep jboss-modules

# Thread dump with java.util.concurrent lock owners (-l), jstack format
jcmd <PID> Thread.print -l > threaddump-$(date +%H%M%S).txt
```

**Method 2: Via jstack**

```bash
jstack -l <PID> > threaddump.txt
```

**Method 3: Via the WildFly management model (no shell on the host needed)**

```bash
jboss-cli.sh --connect '/core-service=platform-mbean/type=threading:dump-all-threads(locked-monitors=true,locked-synchronizers=true)'
```

This returns the dump as a DMR structure, not jstack text; use Method 1 or 2 for `thread-dump-analyzer.py`. `kill -3 <PID>` also works (dump goes to the console log) unless the JVM runs with `-Xrs`.

**Best practice:**
- Capture during issue reproduction
- Capture multiple times (1-minute intervals) to show trend
- Capture during known slow period (if reproducible)

### Step 2: Analyze Thread States

**Count threads by state:**

```bash
grep "java.lang.Thread.State:" threaddump.txt | sort | uniq -c | sort -rn
```

**Expected output:**

```
     85 java.lang.Thread.State: RUNNABLE
     12 java.lang.Thread.State: WAITING
      3 java.lang.Thread.State: BLOCKED
      2 java.lang.Thread.State: TIMED_WAITING
```

**Interpretation:**
- RUNNABLE (85): Executing — or blocked in native I/O (socket reads, e.g. a JDBC call waiting for the database, and the Undertow `default I/O-*` threads in epoll). Check the top frames before calling them busy
- WAITING (12): Acceptable, threads waiting for resources (idle pool threads wait here too)
- BLOCKED (3): Problem indicator, threads blocked on locks
- TIMED_WAITING (2): Acceptable, threads waiting with timeout

**Red flags:**
- BLOCKED count > 10: Lock contention issue
- WAITING count > 50: Resource pool exhaustion
- RUNNABLE count = 0: Application frozen

### Step 3: Find Blocked Threads

**Identify lock contention:**

```bash
grep -A 30 "BLOCKED" threaddump.txt
```

**Example output:**

```
"default task-22" #245 [5678] prio=5 os_prio=0 cpu=812.40ms elapsed=3600.12s tid=0x00007f8b4d8c0000 nid=5678 waiting for monitor entry  [0x00007f8b3f5ff000]
   java.lang.Thread.State: BLOCKED (on object monitor)
        at com.example.app.CubeCache.get(CubeCache.java:42)
        - waiting to lock <0x00000000c1234567> (a java.lang.Object)
        at com.example.app.DataLoader.loadCube(DataLoader.java:156)

"default task-1" #224 [1234] prio=5 os_prio=0 cpu=95012.33ms elapsed=3600.40s tid=0x00007f8b4c0a0000 nid=1234 runnable  [0x00007f8b3f7ff000]
   java.lang.Thread.State: RUNNABLE
        at com.example.app.CubeCache.update(CubeCache.java:50)
        - locked <0x00000000c1234567> (a java.lang.Object)
```

(`default task-N` are Undertow/io worker threads that run requests; `default I/O-N` are the non-blocking I/O threads.)

**Interpretation:**
- `default task-22` blocked waiting for lock on CubeCache
- `default task-1` holds the lock (running CubeCache.update)
- Contention point: CubeCache.update is slow, blocking other threads

**Solution:**
1. Optimize CubeCache.update (reduce lock hold time)
2. Or break into finer-grained locks
3. Or use a concurrent data structure

### Step 4: Check Database Connection Pool Status

**Look for connection wait patterns:**

```bash
grep -i "datasource\|connection" threaddump.txt | head -20
```

**Example problematic pattern:**

```
"default task-42" #287 [9012] prio=5 os_prio=0 cpu=15.20ms elapsed=812.50s tid=0x00007f8b4e1a2000 nid=9012 waiting on condition  [0x00007f8b3e1fe000]
   java.lang.Thread.State: TIMED_WAITING (parking)
        at jdk.internal.misc.Unsafe.park(java.base@21/Native Method)
        ...
        at java.util.concurrent.Semaphore.tryAcquire(java.base@21/Semaphore.java)
        at org.jboss.jca.core.connectionmanager.pool.mcp.SemaphoreConcurrentLinkedDequeManagedConnectionPool.getConnection(...)
        ...
```

The pool waits in a timed semaphore acquire for up to `blocking-timeout-millis`; after that the request fails with `IJ000655` / `IJ000453` in `server.log`.

**What this means:**
- Thread waiting for datasource connection
- Connection pool has no available connections
- All connections either in use or not yet created

**Solutions:**
1. Increase datasource max-pool-size
2. Reduce query execution time (connections held less time)
3. Check for connection leaks

## 3. Heap Dump Analysis

### Step 1: Capture Heap Dump

**Only do in non-production or with caution:** the JVM is stopped for the whole dump, and by default the dump first runs a full GC (only live objects are written). Needs free disk space of about the live heap size.

```bash
# Via jcmd (live objects; add -all to include unreachable ones, -gz=1 to compress)
jcmd <PID> GC.heap_dump /path/with/space/heapdump.hprof

# Or via jmap
jmap -dump:live,format=b,file=heapdump.hprof <PID>

# Automatic dump on OOM (set it permanently, it costs nothing until an OOM)
-XX:+HeapDumpOnOutOfMemoryError -XX:HeapDumpPath=<dump-dir>
```

**Lighter first look (no dump file, but still a full GC):** `jcmd <PID> GC.class_histogram | head -30` lists the classes with the most instances/bytes; two histograms an hour apart often show what grows.

### Step 2: Analyze with Eclipse Memory Analyzer (MAT)

`jhat` was removed in JDK 9 (JEP 241). Use Eclipse MAT (https://eclipse.dev/mat/) or VisualVM.

```bash
# Open heapdump.hprof in MAT GUI (give MAT a heap of at least the dump size)
# Leak Suspects report: automatic leak detection
# Dominator tree + "Path to GC Roots": object retention analysis
```

### Step 3: Find Dominant Objects

**In MAT:**

```
1. Open the Dominator Tree, sort by Retained Heap
2. Or OQL (Query Browser), e.g.:

SELECT * FROM com.example.app.DataCache

3. Examine results:
   - How many instances?
   - What references prevent garbage collection? (Path to GC Roots)
   - Can the cache be bounded/cleared?
```

**Common findings (illustrative, for an app holding large in-memory datasets):**

```
Top memory consumers:
1. Object[]: 40% (arrays holding cube/dataset rows)
2. HashMap: 25% (various caches)
3. String: 20% (labels, dimension names)
4. ResultSet (from JDBC): 10% (not closed?)
5. byte[]: 5% (serialized data)
```

### Step 4: Investigate Leaks

**If a leak is suspected:**

```
1. Compare two heap dumps (one from after startup, one from a week later)
2. Use MAT diff analysis
3. Find objects that grew but shouldn't

Example leak:
   - Cache was 100MB after startup
   - Cache is 5GB one week later
   - Never cleared → memory leak confirmed
```

## 4. Network/Database Profiling

### Database Query Performance

**Enable the slow query log (MySQL):**

```sql
SET GLOBAL slow_query_log = 'ON';
SET GLOBAL long_query_time = 1;  -- Log queries > 1 second
SET GLOBAL log_queries_not_using_indexes = 'ON';
SET GLOBAL log_output = 'TABLE'; -- default is FILE; the query below needs TABLE
```

On managed databases these are usually set as instance flags/parameters rather than with `SET GLOBAL`.

**Retrieve slow queries:**

```sql
SELECT query_time, lock_time, rows_sent, rows_examined, sql_text
FROM mysql.slow_log
ORDER BY query_time DESC
LIMIT 20;
```

**Analyze query performance:**

```
Query: SELECT * FROM transactions WHERE account_id = ? AND date BETWEEN ? AND ?

Current execution:
- query_time: 2.5 seconds
- rows_examined: 500,000
- rows_sent: 100
- Ratio: 5000:1 (very inefficient!)

Diagnosis: Index missing on account_id or date columns

Solution: CREATE INDEX idx_account_date ON transactions(account_id, date);
Expected improvement: 2.5s → 50ms (50x faster)
```

### DataSource Connection Performance

**Monitor from WildFly CLI** (pool statistics are off by default):

```bash
# Once: enable statistics on the datasource (persisted; takes effect after :reload)
jboss-cli.sh --connect '/subsystem=datasources/data-source=<datasource>:write-attribute(name=statistics-enabled,value=true)'
jboss-cli.sh --connect ':reload'

# Read the pool statistics
jboss-cli.sh --connect '/subsystem=datasources/data-source=<datasource>/statistics=pool:read-resource(include-runtime=true)'
```

**Key metrics:**

```
Result:
{
    ...
    "ActiveCount" => 50,          # connections currently open (in use + idle)
    "InUseCount" => 45,           # connections currently handed out to the application
    "AvailableCount" => 55,       # connections that can still be obtained (max-pool-size - in use)
    "MaxUsedCount" => 60,         # high-water mark of connections in use
    "WaitCount" => 0,             # requests that had to wait for a connection
    "AverageBlockingTime" => 0,   # average ms spent waiting for a connection
    "MaxWaitTime" => 0,           # longest wait, ms
    "TimedOut" => 0               # connections timed out
}
```

**Interpretation:**

```
Healthy state:
- InUseCount: well below max-pool-size
- MaxUsedCount: < 80% of max-pool-size
- WaitCount: 0 (no one waiting)
- AverageBlockingTime: 0 (no contention)

Problem state:
- InUseCount = max-pool-size, AvailableCount: 0
- WaitCount rising (requests waiting)
- AverageBlockingTime / MaxWaitTime approaching blocking-timeout-millis

Action: shorten connection hold time (slow queries, leaks) or increase max-pool-size within the DB connection budget
```

## 5. Production Profiling Safety

### Pre-Production Profiling Requirements

**Before profiling production:**

1. **Impact assessment**
   - GC log: No impact (just writes to file)
   - JFR recording (default settings): Low impact, designed for production
   - Thread dump: Low impact (brief safepoint pause)
   - Heap dump: HIGH impact (full GC, then the JVM is stopped for the whole dump — minutes on large heaps)
   - Database slow log: Low impact (just logging)

2. **Timing**
   - Off-peak only (early morning, late night)
   - Not during critical batch processes
   - Not during period-end processing
   - With change management approval

3. **Monitoring during**
   - Have the team available to revert if needed
   - Monitor application responses (expect spike then recovery)
   - Monitor JVM metrics (memory, threads)

4. **Communication**
   - Notify users of possible slowness
   - Have a rollback plan if performance doesn't recover

### Safe Profiling Approach

**Recommended production profiling:**

1. **Always available (no production risk):**
   - GC logs (enabled continuously)
   - Application logs
   - Database slow logs
   - JMX metrics (read-only)

2. **Low-risk (minimal impact):**
   - Java Flight Recorder (preferred profiler: CPU hot methods, allocation, lock contention, GC, I/O — built into the JDK, started with `jcmd` without a restart)
   - Thread dumps (brief safepoint pause)
   - Per-thread CPU sampling

3. **Medium-risk (meaningful impact):**
   - Heap dump (full GC + JVM stopped for the dump)
   - JFR with `settings=profile` for long periods (more events, more overhead than the default settings)

4. **High-risk (avoid in production):**
   - Full profiler (> 30% overhead)
   - Invasive instrumentation
   - Debug mode

### When You Can't Reproduce in Test

**If the issue only happens in production:**

1. **Collect continuous data:**
   - Enable GC logs (always safe)
   - Monitor metrics (always safe)
   - Enable slow query logs (always safe)

2. **Correlate with user reports:**
   - When a user reports slowness at 14:30
   - Check GC logs for 14:25-14:35
   - Check database slow logs for the same period
   - Check JMX metrics for anomalies

3. **Capture a JFR recording and thread dumps:**
   - While the issue is actively occurring (see JFR commands below)
   - Thread dumps show exactly what blocked; take 3+ a few seconds apart to tell stuck from busy

4. **Mirror to test when possible:**
   - Copy production data to a test environment (respecting data-protection rules)
   - Attempt to reproduce with the same data volume
   - Profile in test (no production risk)

## 6. Tools & Commands Reference

### GC Log Analysis Tools

**Built-in JDK tools:**
```bash
# Live GC/heap counters
jstat -gc <PID> 1000  # Prints stats every second

# Real-time memory usage
jconsole <PID>        # Connect to running JVM

# Heap summary without a dump
jcmd <PID> GC.heap_info

# Native memory (needs -XX:NativeMemoryTracking=summary at JVM start)
jcmd <PID> VM.native_memory summary
```

### Java Flight Recorder (preferred profiler)

```bash
# Start a recording on the running JVM (no restart), stop automatically after 30 minutes
jcmd <PID> JFR.start name=perf settings=profile duration=30m filename=/path/with/space/perf-%p-%t.jfr

# Or dump what an always-on recording holds so far
jcmd <PID> JFR.dump name=perf filename=/path/with/space/perf-now.jfr
jcmd <PID> JFR.check          # list running recordings
jcmd <PID> JFR.stop name=perf
```

Open the `.jfr` file in JDK Mission Control (JMC): Method Profiling (CPU hot spots), Allocations, Lock Instances (contention), Garbage Collections, Socket I/O (database round trips). `settings=default` (the default) is the low-overhead set for always-on use; `settings=profile` records more detail.

### WildFly CLI Commands

```bash
# Connect to running WildFly
jboss-cli.sh --connect

# Thread dump (DMR format; for jstack format use jcmd <PID> Thread.print)
/core-service=platform-mbean/type=threading:dump-all-threads(locked-monitors=true,locked-synchronizers=true)

# Heap dump: no management operation; use jcmd <PID> GC.heap_dump <file>

# DataSource pool statistics (needs statistics-enabled=true on the datasource)
/subsystem=datasources/data-source=<datasource>/statistics=pool:read-resource(include-runtime=true)

# Undertow worker pool (io subsystem) runtime values
/subsystem=io/worker=default:read-resource(include-runtime=true)

# HTTP listener request statistics (needs statistics-enabled=true on the undertow subsystem)
/subsystem=undertow/server=default-server/http-listener=default:read-resource(include-runtime=true)

# Reload configuration (apply changes)
:reload
```

### Docker/Kubernetes Profiling

**If running in Kubernetes:**

```bash
# Port-forward to WildFly
kubectl port-forward <pod-name> 9990:9990

# Connect jboss-cli via port-forward
jboss-cli.sh --controller=localhost:9990 --connect

# Dump logs from container
kubectl logs <pod-name> > pod-logs.txt

# Exec into container for a thread dump (the image must contain a full JDK, not only a JRE)
kubectl exec <pod-name> -- jcmd <PID> Thread.print -l
```

## Summary Checklist

**When investigating a performance issue:**

- [ ] Collect baseline metrics (from normal operation)
- [ ] Collect problem period metrics (same timeframe)
- [ ] Compare baseline vs problem (identify delta)
- [ ] Diagnose from GC logs (memory/GC issue?)
- [ ] Diagnose from thread dumps (contention issue?)
- [ ] Diagnose from database logs (DB bottleneck?)
- [ ] Identify root cause (not just symptom)
- [ ] Recommend solution (specific change)
- [ ] Test in non-production first
- [ ] Measure improvement after fix
- [ ] Document findings and solution

For more details, see:
- `references/jvm-tuning-guide.md` - JVM tuning details
- `references/wildfly-performance-config.md` - WildFly configuration
- `references/heavy-load-app-patterns.md` - Batch/report-heavy workload patterns
