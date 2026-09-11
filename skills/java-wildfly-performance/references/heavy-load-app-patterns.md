# Heavy-Load Application Patterns & Tuning

## Overview

Batch- and report-heavy Jakarta EE applications (financial planning and consolidation, ERP back-offices, analytics and reporting platforms) behave differently from request/response web apps: they hold large datasets in memory, run long transactions and big queries, and see sharp period-end peaks. This guide covers those workload patterns and how to tune WildFly and the JVM for them.

All numbers below are **illustrative** orders of magnitude — replace them with your own measurements.

## Table of Contents
1. Workload Characteristics
2. Period-End / Peak-Batch Processing
3. Bulk Import/Export
4. Report Generation
5. Memory Patterns
6. Database Access Patterns
7. Infrastructure Sizing
8. Troubleshooting Scenarios
9. Peak-Period Checklist

## 1. Workload Characteristics

### Workload Profile

**User behavior patterns:**
- Heavy morning usage (e.g. 30-50 concurrent users)
- Lighter afternoon usage (e.g. 10-20 concurrent users)
- Evening batch processes and reports (background)
- Period-end (month-end / quarter-end) spikes (500%+ normal load)

**Data characteristics:**
- Large dimensional datasets (hierarchies, attributes)
- In-memory data cubes / aggregates with multiple aggregation levels
- Significant memory footprint per loaded dataset
- Temporal data (multiple periods, versions, scenarios)

**Example performance targets:**
- Interactive response: < 2 seconds for navigation
- Report generation: < 5 minutes for standard reports
- Batch import: 1000+ records/second
- Aggregation / consolidation: completes within the business window

### Typical Architecture

```
User Interface Layer (Browser)
         ↓
Web Application Layer (WildFly)
         ↓
Business Logic (EJB / CDI / Spring)
         ↓
Calculation Engine (in-memory data structures)
         ↓
Database Layer (JDBC)
         ↓
File Storage (shared NFS / managed file service for documents, imports, exports)
```

**Performance implications:**
- UI latency ≤ 2s means backend response < 1.5s
- The in-memory calculation engine is memory-intensive (GC impact significant)
- The database is usually the primary bottleneck (connection pool critical)
- File operations (import/export) stress disk I/O and memory

## 2. Period-End / Peak-Batch Processing

### Workload Spike Characteristics

During period-end (e.g. last 5 business days of the month):

**Data volume increase (illustrative):**
- Standard daily processing: 50-100MB
- Period-end processing: 5-10GB
- Consolidation/reporting: 20-50GB of temporary objects
- Journal-type entries: 10-50x normal volume

**Typical daily sequence:**
- 10:00-12:00: Dataset population (heavy import)
- 12:00-14:00: Data validation (intensive queries)
- 14:00-17:00: Consolidation / aggregation (heavy computation)
- 17:00-19:00: Report generation (I/O intensive)

**Infrastructure load:**
- CPU: 80-95% utilization
- Memory: Peak heap usage up to 90%
- Disk I/O: Significant temporary file activity
- Network: Database connections at or near the limit

### Optimization Strategy for Peak Periods

#### 1. Prepare JVM Memory

**Before the peak period:**

```bash
# Increase heap size for the period
# Standard: Xmx12288m
# Peak:     Xmx16384m (if the machine has capacity)

# Or keep a peak-specific configuration
PEAK_PERIOD=true
if [ "$PEAK_PERIOD" = "true" ]; then
  JAVA_OPTS="${JAVA_OPTS} -Xmx16384m"
else
  JAVA_OPTS="${JAVA_OPTS} -Xmx12288m"
fi
```

**Memory calculation (illustrative):**
- Baseline heap: 12GB (from the JVM tuning guide)
- Period-end surge: +25-30% additional
- Result: 15-16GB for the peak period
- Requires a machine with 32GB+ total memory (the recommender's `peak-batch` ceiling on 32GB is 19GB)

`scripts/jvm-flag-recommender.py --machine-type <type> --workload peak-batch` produces the corresponding flags.

#### 2. Increase DataSource Connection Pool

```xml
<!-- Normal configuration -->
<max-pool-size>50</max-pool-size>

<!-- Peak configuration (if using multiple datasources) -->
<!-- Datasource 1 -->
<max-pool-size>60</max-pool-size>

<!-- Datasource 2 -->
<max-pool-size>40</max-pool-size>

<!-- Total: 100 per WildFly node; × number of nodes must fit the database's max_connections -->
```

Or use the WildFly CLI (the model marks `max-pool-size` as restart-required, so apply it with a reload in a quiet moment):

```bash
jboss-cli.sh --connect '/subsystem=datasources/data-source=<datasource>:write-attribute(name=max-pool-size, value=100)'
jboss-cli.sh --connect ':reload'
```

#### 3. Optimize GC Tuning

```bash
# Increase GC pause target during the peak
# Standard: MaxGCPauseMillis=200
# Peak:     MaxGCPauseMillis=300-400

# A larger minimum young generation can help the peak spike (experimental flags)
-XX:+UnlockExperimentalVMOptions
-XX:G1NewSizePercent=30      # default 5
-XX:G1MaxNewSizePercent=60   # the default; lower it only to bound young gen
```

#### 4. Batch Job Optimization

Inside the application (if it exposes tuning):

**Batch processing modes** — many batch-heavy applications offer some of these:
- A bulk/batch mode with larger internal buffers for import
- Deferring some real-time validations to a post-import step
- Chunked commits (records per batch) instead of one huge transaction
- Such modes can improve import speed 30-50%

**Monitor and track:**
```bash
# Track job progress in logs (adapt keywords to your application)
tail -f $WILDFLY_HOME/standalone/log/server.log | grep -i "import\|consolidation\|batch"

# Look for performance warnings
grep -i "slow\|timeout\|memory" server.log
```

#### 5. Database Optimization

**Connection pooling during the peak:**
- Monitor active connection count
- If approaching max → the database is the bottleneck
- Consider adding database capacity (if the architecture allows)

**Query optimization:**
- Run maintenance (ANALYZE, OPTIMIZE) before the peak
- Ensure indexes on frequently-used columns
- Check the slow query log for performance regressions

#### 6. Post-Peak Cleanup

After the peak completes:

```bash
# Reset JVM memory to normal (takes effect at the next restart)
JAVA_OPTS="${JAVA_OPTS} -Xmx12288m"

# Reduce datasource pool to normal (applies after :reload)
jboss-cli.sh --connect '/subsystem=datasources/data-source=<datasource>:write-attribute(name=max-pool-size, value=50)'

# No need to force a GC: temporary objects die young and are collected normally.
# (If ever required: jcmd <PID> GC.run triggers System.gc(), i.e. a Full GC pause.)

# Clear application caches if applicable
# (depends on the application's configuration)
```

## 3. Bulk Import/Export

### Import Performance Patterns

**Typical import scenarios (illustrative):**
- Ledger-type imports: 1000-5000 records
- Consolidation data: 10000-50000 records
- Period-end actuals: 100000-500000 records

**Performance characteristics by import size (illustrative):**

| Record Count | Typical Duration | Memory Usage | Database Impact |
|---|---|---|---|
| 1,000 | < 10 seconds | 50MB | Minimal |
| 10,000 | 30-60 seconds | 200MB | Light |
| 100,000 | 5-10 minutes | 1-2GB | Moderate |
| 500,000+ | 30+ minutes | 3-5GB | Heavy |

### Import Bottleneck Analysis

**Identifying the bottleneck:**

```bash
# If import slow and memory usage low → CPU bottleneck
# The application is calculating aggregations
# Solution: Defer aggregation to a post-import step

# If import slow and memory usage high → GC bottleneck
# Too many temporary objects
# Solution: Increase heap size, enable batch mode / smaller chunks

# If import slow and database connections at max → DB bottleneck
# Database can't handle the query rate
# Solution: Add connection pool capacity (if the architecture allows)

# If import slows progressively → possible memory leak
# Check heap trend in GC logs
# Solution: Investigate for connection/object leak
```

JDBC-level help for bulk writes: `rewriteBatchedStatements=true` and prepared statement caching (see `wildfly-performance-config.md`).

### Export Performance Optimization

**Report export bottlenecks:**

```bash
# If export slow and disk I/O high → shared storage bottleneck
# Solution: Increase storage IOPS/throughput tier, or use a larger instance

# If export slow and memory growing → Memory leak in export
# Check if temp files are being cleaned up
# Solution: Clear temp directory, check export settings

# If export fails with OutOfMemory → Report too large
# Solution: Reduce report scope, implement pagination
```

## 4. Report Generation

### Report Generation Workflow

```
User requests report
       ↓
Application queries database for raw data
       ↓
Apply formulas and calculations (CPU intensive)
       ↓
Format and style report (memory intensive)
       ↓
Write to file (I/O intensive)
       ↓
Return to user (network delivery)
```

### Common Report Performance Issues

#### Issue 1: Long Report Query Time

**Symptoms:**
- User waits 30+ seconds with no response
- Database connection held for an extended period
- Other users experience timeouts

**Root cause:**
- Query hitting non-indexed columns
- Data volume larger than expected
- Complex joins or aggregations in the report definition

**Solution:**
1. Verify database indexes on report filter/join fields
2. Check data volume in the relevant period
3. Consider narrowing report scope (fewer dimensions)
4. Add query result caching (if the application supports it)

#### Issue 2: Report Generation OutOfMemory

**Symptoms:**
- Report requested successfully
- Fails during formatting with OutOfMemory
- Log shows high heap usage

**Root cause:**
- Report too large for available heap
- Large number of rows × many columns
- Excel/PDF export overhead

**Solution:**
1. Increase heap size (if the machine supports it)
2. Narrow report scope (fewer rows/columns)
3. Implement pagination (multiple smaller reports)
4. Use CSV instead of Excel (lower memory)

#### Issue 3: Slow Report Delivery

**Symptoms:**
- Report generation completes quickly
- User waits a long time for the download
- Network traffic normal

**Root cause:**
- Large file being written to storage
- Slow disk I/O (shared storage performance)
- Network latency

**Solution:**
1. Enable compression (if not enabled)
2. Improve shared storage configuration (IOPS, throughput)
3. Use an SSD storage class if available
4. Distribute reports across multiple instances

## 5. Memory Patterns

### Common Memory Issues

#### Memory Pattern 1: Large Dataset Loading

**When a large dataset / cube is loaded into memory (illustrative):**

```
Heap before load: 4GB
Dataset size: 3GB
Heap after load: 7GB
Young GC frequency: Every 5 seconds
```

This is NORMAL. The dataset remains in heap until:
- User navigates away (unload)
- Timeout expires
- Explicit clear command

**Is it a leak?** Check:
1. Does heap drop when the user logs out? → Normal (not a leak)
2. Does heap remain high after timeout? → Possible leak

#### Memory Pattern 2: Report Temporary Objects

**During report generation (illustrative):**

```
Baseline: 5GB heap used
Report requested: 8GB (temporary report data)
During formatting: 10GB (peak)
After report complete: 5GB (cleanup)
```

This is NORMAL. Temporary objects are discarded after report generation.

**Watch for:** If heap doesn't drop back to baseline after GC → possible leak

#### Memory Pattern 3: Progressive Heap Growth

**Red flag pattern:**

```
Day 1: Baseline 6GB
Day 2: Baseline 7GB (grew 1GB)
Day 3: Baseline 8GB (grew 2GB)
...
Day 10: OutOfMemory (baseline = max heap)
```

**Root causes:**
1. Connection leak (connections held indefinitely)
2. Cache without eviction (unbounded growth)
3. Listener leak (event listeners not unregistered)
4. Thread-local leak (threads not cleaned up properly)

**Diagnosis:**
1. Capture a heap dump at day 5-6 (before OOM)
2. Analyze dominant object types
3. Cross-reference with the application documentation
4. Check the vendor's known-issues / fix list, if any

#### Session Memory

User sessions holding per-user state (loaded datasets, report drafts) scale heap with concurrent users. Keep session timeout aligned with real session length and avoid storing large objects in the HTTP session (see Session Management in `wildfly-performance-config.md`).

## 6. Database Access Patterns

### Connection Characteristics

**Typical database access (illustrative):**

```
Connection pool size: 50-100
Concurrent active: 20-40 during peak
Idle connections: cleaned up after 15 minutes
Busy connections: typically 0.5-2 seconds query time

Period-end peak:
Connection pool size: 100 (this node's share of the database connection budget)
Concurrent active: 80-100
Avg query time: 1-5 seconds (consolidation queries slow)
```

Long transactions and large result sets hold a connection for their whole duration; during a peak they, not request volume, usually drive pool exhaustion.

### Connection Pool Exhaustion Diagnosis

**Warning signs in logs:**

```
IJ000453: Unable to get managed connection for java:/datasources/<datasource>
IJ000655: No managed connections available within configured blocking timeout (30000 [ms])
```

(IronJacamar, the datasource pool of WildFly; the application sees them wrapped in an SQLException.)

**What this means:**
- Application thread waited 30 seconds (`blocking-timeout-millis`) for a database connection and gave up
- Pool at maximum capacity
- Need either:
  - More connections (if the database budget allows)
  - Better query performance (reduce connection hold time)
  - Connection leak fix (connections not being returned)

**Investigation steps:**

1. Check actual pool utilization (needs `statistics-enabled=true` on the datasource):
```bash
jboss-cli.sh --connect '/subsystem=datasources/data-source=<datasource>/statistics=pool:read-resource(include-runtime=true)' | grep -iE "InUseCount|AvailableCount|MaxUsedCount|WaitCount"
```

2. Monitor query execution time:
```sql
-- In the database slow query log, check query duration (MySQL, with log_output=TABLE)
SELECT query_time, sql_text FROM mysql.slow_log ORDER BY query_time DESC LIMIT 10;
```

3. Look for connection leaks:
```bash
# Monitor pool metrics over time
# InUseCount should fluctuate, not steadily increase
# If it stays near max even when load drops → likely leak
```

### Query Classes (illustrative)

```
Navigation query: < 100ms (dimension hierarchies)
Dataset load query: 1-10 seconds (fact table joins)
Consolidation query: 10-60 seconds (complex aggregations)
Report query: 5-30 seconds (depending on scope)
```

**Performance tuning:**
1. Verify indexes on the dimension, entity and period keys the queries filter on
2. Analyze slow queries (> 5 seconds)
3. Consider query result caching (if the application supports it)
4. Monitor database CPU/I/O during spikes

## 7. Infrastructure Sizing

### Machine Sizing by Environment (illustrative, GCP machine types as examples)

Heap figures stay at or below the `jvm-flag-recommender.py` ceiling for the machine (`standard` / `peak-batch` workload). Pool figures are per WildFly node; the sum over all nodes and datasources must fit the database's `max_connections`.

**Development/Testing:**
```
e2-standard-4 (4 vCPU, 16GB)
Heap: 8GB (recommender ceiling)
Pool: 50 connections
Suitable for 5-10 concurrent users
```

**Integration Testing:**
```
e2-standard-8 (8 vCPU, 32GB)
Heap: 12GB (measured; ceiling 16GB)
Pool: 100 connections
Suitable for 20-30 concurrent users
```

**Production:**
```
e2-standard-8 or larger (8+ vCPU, 32GB+)
Heap: 12-16GB on 32GB (peak-batch ceiling 19GB)
Pool: 100 connections per node, all datasources together
Suitable for 50+ concurrent users
Period-end capable with 32GB+ machine
```

**High-performance/Large-scale:**
```
c3-standard-22 (22 vCPU, 88GB)
Heap: 31GB (compressed-oops cap; the peak-batch ceiling is 52.5GB)
Pool: 100 connections per node, all datasources together
Suitable for 100+ concurrent users
Full period-end capability
```

### Shared File Storage

Import/export workloads typically use shared file storage (NFS or a managed file service).

**Standard configuration (illustrative):**
```
Storage capacity: 1TB
Performance tier: Standard
Suitable for: Import/export < 1GB files
Typical throughput: 200MB/s
```

**Peak-period configuration (illustrative):**
```
Storage capacity: 2TB+
Performance tier: Premium
Add: Dedicated NFS appliance (if available)
Throughput: 500MB/s+ capable
```

On many managed file services throughput scales with provisioned capacity or tier; watch read/write throughput and IOPS in your cloud monitoring during imports/exports, and add capacity or change tier when approaching the limits.

## 8. Troubleshooting Scenarios

### Scenario 1: Period-End Consolidation Hangs

**Symptoms:**
- Consolidation started at 14:00
- Still running at 18:00 (4 hours)
- No error in logs
- Database connections at max

**Diagnosis:**

```bash
# Check thread dump (3 dumps ~10 s apart show whether threads move)
jcmd <PID> Thread.print -l > threaddump-1.txt
python3 scripts/thread-dump-analyzer.py --dump threaddump-1.txt --focus db

# Look for many threads BLOCKED or WAITING
# Indicates contention or lock wait

# Check database slow log
# Look for consolidation queries running for hours
```

**Solution:**
1. Kill the consolidation process (if safe)
2. Check database indexes on consolidation tables
3. Review the application's consolidation configuration
4. Consider breaking consolidation into smaller chunks
5. Check the vendor's known-issues list for consolidation issues

### Scenario 2: Users Report Slow Navigation During the Peak

**Symptoms:**
- Normal navigation 1-2 seconds → 30+ seconds during period-end
- Some users unaffected, others impacted

**Diagnosis:**

```bash
# Check GC logs for pause times and their causes
grep -E "Pause (Young|Full).*ms$" gc.log | tail -20
grep -cE "G1 Humongous Allocation.*ms$" gc.log
# Many humongous-allocation GCs = many arrays >= half a G1 region (e.g. big result sets/exports);
# a larger -XX:G1HeapRegionSize or smaller chunks help

# Check thread dump
# Look for the user's request thread state
# If WAITING on database → DB locked
# If BLOCKED on lock → contention in application
```

**Solution:**
1. Reduce concurrent users during consolidation
2. Use a restricted/"batch mode" in the application during heavy runs (disable some features), if available
3. Increase GC pause target (`-XX:MaxGCPauseMillis=400`)
4. Add a separate connection pool for reporting operations

### Scenario 3: OutOfMemory After Running for 2 Weeks

**Symptoms:**
- System runs fine week 1
- Week 2: Occasional OutOfMemory during peak
- Week 3: OutOfMemory daily
- Memory leak suspected

**Diagnosis:**

```bash
# Capture heap dump (stops the JVM for the duration; see profiling-methodology.md)
jcmd <PID> GC.heap_dump /path/with/space/heapdump.hprof

# Download and analyze (Eclipse MAT)
# Look for large object clusters
# Check for accumulated data structures

# Review logs for:
# - Connection pool metrics
# - Cache sizes
# - Active user count
```

**Solution:**
1. Check the application version against the vendor's fix list (known leaks?)
2. Restart WildFly weekly (temporary mitigation)
3. Implement application cache eviction
4. Profile memory usage (heap dump analysis)
5. Contact vendor support with the heap dump

## 9. Peak-Period Checklist

### Before the Peak
- [ ] Verify JVM heap size adequate for +25-30% load
- [ ] Test DataSource pool under expected load
- [ ] Verify database capacity (CPU, connections)
- [ ] Check shared storage IOPS/throughput for large exports
- [ ] Review recent application release notes for known issues
- [ ] Create a backup before consolidation

### During the Peak
- [ ] Monitor GC pause times (should stay < 500ms)
- [ ] Monitor database active connections (should stay < 90% of max)
- [ ] Monitor heap usage (should stay < 90% of max)
- [ ] Monitor user response times (should stay < 5 seconds)
- [ ] Be ready to adjust GC pause target if needed

### After the Peak
- [ ] Review logs for warnings/errors
- [ ] Analyze GC patterns (compare to baseline)
- [ ] Reset JVM memory to standard size
- [ ] Reset DataSource pool to normal
- [ ] Document any issues encountered
- [ ] Plan improvements for the next peak
