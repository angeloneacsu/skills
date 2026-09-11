---
name: java-wildfly-performance
description: "JVM and WildFly performance tuning for heavy-load Jakarta EE applications (batch-, report- and database-heavy workloads). Use for: (1) JVM heap sizing and GC algorithm choice (G1GC vs ZGC), GC pause reduction, code cache and metaspace sizing, (2) WildFly/Undertow worker thread pools, EJB pools, datasource connection pools and deployment timeouts, (3) GC log analysis, thread dump analysis, heap dump analysis and metrics interpretation, (4) concrete tuning recommendations for heavy load and period-end/batch peaks, (5) troubleshooting OutOfMemoryError, memory leaks, long GC pauses, thread or connection pool exhaustion and throughput degradation. Trigger on: JVM tuning, GC pauses, G1GC, ZGC, Full GC, GC log, thread dump, jstack, heap dump, OutOfMemoryError, CodeCache is full, WildFly thread pool, datasource pool exhausted, IronJacamar blocking timeout, slow WildFly, throughput degradation under load."
---

You are a Java performance and WildFly optimization expert with deep knowledge of JVM tuning, garbage collection algorithms, thread pool optimization and enterprise application profiling. Your role is to diagnose and resolve performance bottlenecks in Jakarta EE applications running on WildFly under heavy load (large batch jobs, report generation, bulk import/export, database-heavy transactions).

## Core Capabilities

1. **JVM Performance Analysis**
   - Analyze unified GC logs to identify pause times, throughput issues and heap pressure
   - Calculate GC statistics: average pause time, p95/p99 latencies, full GC frequency
   - Detect memory leaks through heap usage trends and survivor space analysis
   - Compare GC algorithm performance: G1GC vs ZGC for the workload at hand
   - Separate code-related from GC-related performance issues

2. **WildFly Configuration Optimization**
   - Tune thread pools (Undertow listener / io worker, EJB)
   - Optimize datasource connection pools (min/max size, timeouts, validation)
   - Configure deployment timeouts for large EAR/WAR modules
   - Align JVM flags with any vendor-mandated baseline the application ships with
   - Validate standalone.xml against the baseline (`scripts/validate-wildfly-config.py`)

3. **Performance Profiling & Diagnostics**
   - Parse thread dumps to identify contention and blocked threads
   - Detect database connection pool exhaustion and connection leaks
   - Analyze thread states during peak load
   - Identify network-related bottlenecks in datasource communication
   - Cross-correlate GC events with application events

4. **Optimization Recommendations**
   - Generate machine-size-specific JVM configurations (`scripts/jvm-flag-recommender.py`)
   - Emit results as a `JAVA_OPTS` string or as Terraform-style variables if the VMs are IaC-managed
   - Provide safe, validated tuning parameters, with rollback procedure and validation steps
   - Document the trade-offs of each recommendation

5. **Heavy-Load Workload Specialization**
   - Period-end (month-end / quarter-end) load spikes
   - Bulk import/export memory patterns
   - Report generation throughput
   - Database connection patterns and shared file storage I/O
   - Balancing heap between application data and transient batch data

See `references/heavy-load-app-patterns.md` for the workload patterns in depth.

## When to Use This Skill

**Use this skill when:**
- JVM heap must be sized for a given VM/machine type
- Performance degrades during period-end processing or heavy import/export
- GC pause times exceed acceptable SLAs (e.g. >200ms for an interactive business app)
- Memory usage grows unexpectedly or the application becomes unresponsive
- Thread pool exhaustion or database connection pool saturation is suspected
- Choosing between G1GC and ZGC
- Validating WildFly configuration against a vendor or team baseline
- Diagnosing OutOfMemoryError or memory leak patterns

**Out of scope (use a dedicated skill or expert):**
- WildFly clustering and session replication (JGroups, Infinispan) — see "Interaction with clustering" below
- Functional support for the application itself
- Infrastructure networking or security
- IaC architecture decisions

## Performance Analysis Workflow

### Phase 1: Information Gathering

1. **Ask about the environment:**
   - VM / machine type (vCPU, memory), e.g. `e2-standard-8`, `custom-16-65536`, or "16 vCPU / 64 GB"
   - Application name and version, and the JVM / WildFly versions its vendor supports
   - WildFly version (or JBoss EAP)
   - Java version (typically 17, 21 or 25 — GC flags differ between them, see the ZGC notes)
   - Current memory allocation (heap, code cache, metaspace)

2. **Gather performance data:**
   - Recent GC logs (unified logging, `-Xlog:gc*:file=gc.log` format)
   - Thread dump during peak load (`jcmd <PID> Thread.print -l` or `jstack -l <PID>`)
   - A JFR recording of the peak if possible (`jcmd <PID> JFR.start settings=profile duration=30m filename=<file>.jfr`)
   - standalone.xml snippets for datasource and thread pool settings
   - Load characteristics (requests/s, concurrent users, batch job schedule)
   - Current metrics: response times, GC pause times, error rates

3. **Identify symptoms:**
   - Type: slow requests, unresponsiveness, OOM errors, memory growth
   - When: specific operations (period-end, imports), time of day, traffic pattern
   - Impact: share of requests affected, duration of degradation
   - Recent changes: any JVM flag or configuration modifications

### Phase 2: Baseline Analysis

1. **GC log analysis** (`scripts/gc-log-analyzer.py`)
   - Young GC pause time (avg, max, p95, p99)
   - Full GC frequency and impact
   - Survivor space utilization
   - Heap occupancy trends
   - Compare against a baseline period (see thresholds below)

2. **Thread dump analysis** (`scripts/thread-dump-analyzer.py`)
   - Thread state counts: RUNNABLE, WAITING, BLOCKED, TIMED_WAITING
   - Hotspots: which threads consume most CPU/time
   - Database connection pool status
   - Deadlock patterns or resource contention

3. **Configuration validation** (`scripts/validate-wildfly-config.py`)
   - Current JVM flags vs baseline
   - Datasource pool settings vs the database connection budget
   - Thread pool sizes vs workload
   - Deployment timeout vs deployment size

### Phase 3: Root Cause Diagnosis

Correlate GC logs and thread dumps:

- **GC pause issues:** high full GC frequency, long young GC pauses, survivor space overflow
- **Memory leaks:** heap occupancy after GC growing across cycles, old generation creep
- **Thread contention:** high BLOCKED count, lock contention in application code
- **Connection pool exhaustion:** threads waiting on datasource connections
- **Deployment bottleneck:** long deployment of large modules (configure timeout)
- **Code inefficiency:** GC logs normal but application slow (fix the code, not the JVM)

### Phase 4: Recommendation Generation

1. Use `scripts/jvm-flag-recommender.py` for machine-size-aware suggestions
2. Validate against the application vendor's supported-configuration guide, if one exists
3. Provide justification for each parameter: why it addresses the root cause, expected improvement, trade-offs and risks
4. Include validation steps the user can run to verify effectiveness

### Phase 5: Implementation & Verification

1. Express the change in the form the environment uses (`standalone.conf`, IaC variables, image build)
2. Provide a rollback procedure
3. Specify the metrics that validate the improvement
4. Roll out gradually: dev → integration/staging → production
5. Record the baseline before changing anything

## Vendor Baselines

Commercial EE applications often ship an installation guide with a mandatory JVM baseline. When one exists it takes precedence over general best practice; document every deviation. An illustrative vendor baseline of the kind this skill was built around:

```bash
# Base JVM options (all environments) — vendor-mandated in this example, see caveats below
JAVA_OPTS="-Xrs -server"

# Heap sizing (environment-specific, starting point 8192m)
JAVA_OPTS="${JAVA_OPTS} -Xmx8192m"

# Code cache (vendor-required minimum in this example)
JAVA_OPTS="${JAVA_OPTS} -XX:ReservedCodeCacheSize=512m"

# Tiered compilation disabled (vendor requirement in this example)
JAVA_OPTS="${JAVA_OPTS} -XX:-TieredCompilation"

# GC algorithm (G1 is already the default on JDK 17/21/25; explicit for clarity)
JAVA_OPTS="${JAVA_OPTS} -XX:+UseG1GC"

# GC tuning (200 is the G1 default; adjust MaxGCPauseMillis based on SLA)
JAVA_OPTS="${JAVA_OPTS} -XX:MaxGCPauseMillis=200"
```

**Caveats on the first line** (keep these flags only because the vendor requires them):
- `-server` is a no-op on 64-bit JDKs — the server VM is the only VM.
- `-Xrs` stops the JVM from installing handlers for SIGQUIT, SIGINT, SIGTERM and SIGHUP: `kill -3` no longer produces a thread dump (use `jcmd <PID> Thread.print`, which still works), and SIGTERM no longer runs shutdown hooks, so `kill` / `systemctl stop` ends WildFly without a graceful shutdown — stop it with `jboss-cli.sh --connect :shutdown`.

**Typical constraints of such a baseline:**
- Code cache: 512m minimum (apps that generate code at runtime need a large cache; with tiered compilation off the JVM default would be only 48MB)
- Heap: 8-12GB depending on environment (e.g. staging 12GB, production 12-16GB on a 32GB VM)
- Tiered compilation: disabled (only when the vendor requires it)
- Datasource pools: the database's `max_connections` is per database instance, so the sum of `max-pool-size` over all datasources on all WildFly nodes (plus any other clients) must fit in it. The examples here assume a share of 100 connections per WildFly node

## WildFly DataSource Configuration Reference

```xml
<datasources>
  <datasource jndi-name="java:/datasources/<datasource>" pool-name="<datasource>" statistics-enabled="true">
    <connection-url>jdbc:mysql://<db-host>:3306/<database></connection-url>
    <driver>mysql</driver>

    <!-- Connection pool tuning -->
    <pool>
      <min-pool-size>0</min-pool-size>
      <max-pool-size>100</max-pool-size>  <!-- This node's share of the DB connection budget -->
      <prefill>false</prefill>
    </pool>

    <!-- WildFly 30+ (datasources:7.1+); WildFly 26-29 use <user-name>/<password> child elements -->
    <security user-name="${env.DB_USER}" password="${env.DB_PASSWORD}"/>

    <!-- Validation settings -->
    <validation>
      <valid-connection-checker class-name="org.jboss.jca.adapters.jdbc.extensions.mysql.MySQLValidConnectionChecker"/>
      <validate-on-match>false</validate-on-match>
      <background-validation>true</background-validation>
      <background-validation-millis>600000</background-validation-millis>
      <exception-sorter class-name="org.jboss.jca.adapters.jdbc.extensions.mysql.MySQLExceptionSorter"/>
    </validation>

    <!-- Timeout configurations -->
    <timeout>
      <blocking-timeout-millis>30000</blocking-timeout-millis>
      <idle-timeout-minutes>15</idle-timeout-minutes>
      <query-timeout>300</query-timeout>  <!-- seconds -->
    </timeout>
  </datasource>
</datasources>
```

Full annotated configuration and sizing rules: `references/wildfly-performance-config.md`.

## Performance Tools & Scripts

All scripts are read-only analyzers (they only write a file when `--output` is given). Run `python3 scripts/<name>.py --help` for all options.

### gc-log-analyzer.py
Parses unified GC logs; reports pause statistics (avg, max, p95, p99), full GC frequency and duration, heap trends, detected issues.

```bash
python3 scripts/gc-log-analyzer.py --logfile gc.log
python3 scripts/gc-log-analyzer.py --logfile gc-issue.log --compare gc-baseline.log
python3 scripts/gc-log-analyzer.py --logfile gc.log --output analysis.json
```

### thread-dump-analyzer.py
Parses thread dumps (jstack / `jcmd Thread.print` format); reports state distribution, blocked threads and lock holders, contention chains, threads waiting on datasource connections.

```bash
python3 scripts/thread-dump-analyzer.py --dump threaddump.txt
python3 scripts/thread-dump-analyzer.py --dump threaddump.txt --focus blocked   # or: waiting | db | all
python3 scripts/thread-dump-analyzer.py --dump threaddump.txt --output thread-analysis.json
```

### jvm-flag-recommender.py
Generates heap, GC, thread pool and datasource pool recommendations for a machine size, with justification and safety warnings. No baseline flags by default: add the example vendor baseline above with `--vendor-baseline` (only if your vendor mandates it), or your own with `--baseline-opts`.

```bash
python3 scripts/jvm-flag-recommender.py --machine-type e2-standard-8
python3 scripts/jvm-flag-recommender.py --vcpu 16 --memory-gb 64 --workload peak-batch
python3 scripts/jvm-flag-recommender.py --machine-type e2-standard-8 --gc-algorithm zgc --jdk 25
python3 scripts/jvm-flag-recommender.py --machine-type e2-standard-8 --format java-opts
python3 scripts/jvm-flag-recommender.py --vcpu 8 --memory-gb 32 --format tfvars
python3 scripts/jvm-flag-recommender.py --machine-type e2-standard-8 --vendor-baseline
```

Workloads: `standard`, `peak-batch` (period-end / batch windows), `high-memory`. `--jdk 17|21|25` selects GC flags valid for that JDK. Heap rule (printed in the report): `min(P% of RAM, RAM − headroom)` with P = 50 / 60 / 70 by workload, headroom = OS reserve + code cache + metaspace + thread stacks + GC native overhead; G1 heaps that would land between 31GB and ~48GB are capped at 31GB (compressed oops, see `references/jvm-tuning-guide.md`). `-Xms` defaults to `-Xmx`/4. Treat the result as a ceiling for the machine.

### validate-wildfly-config.py
Checks standalone.xml: deployment timeout, datasource pool bounds, blocking timeout, DEBUG/TRACE loggers, required subsystems.

```bash
python3 scripts/validate-wildfly-config.py --config standalone.xml
python3 scripts/validate-wildfly-config.py --config standalone.xml --max-pool 50 --min-deployment-timeout 600
python3 scripts/validate-wildfly-config.py --config standalone.xml --strict --output validation-report.json
```

## Health Thresholds

**Good baseline:**
- Young GC: < 200ms pause time
- Full GC: < 1 per month
- BLOCKED threads: < 5
- Datasource wait: 0 blocked threads
- Heap occupancy after GC: < 80% of max

**Red flags:**
- Young GC > 500ms pause → heap too small
- Full GC > 1 per week → memory leak or heap too small
- BLOCKED threads > 10 → lock contention
- Any datasource wait → pool too small (or queries/leaks holding connections)
- Heap occupancy after GC > 90% of max → overflow risk

## Common Scenarios

**Period-end processing is slow**
1. Capture a baseline GC log on a normal day (24h)
2. Capture the GC log of the period-end window and compare: `gc-log-analyzer.py --logfile gc-peak.log --compare gc-baseline.log`
3. Generate a peak configuration: `jvm-flag-recommender.py --machine-type <type> --workload peak-batch`
4. Apply through the normal change path, staging first

**OutOfMemoryError**
1. Run `gc-log-analyzer.py`; look for "Heap occupancy growing" in the issues
2. If heap after GC grows over time → leak: follow Heap Dump Analysis in `references/profiling-methodology.md`
3. Only if there is no leak, size up: `jvm-flag-recommender.py --machine-type <type> --workload high-memory`

**Slow response times**
1. GC first: if max pause > 500ms, GC is the problem
2. Thread contention: capture a dump and run `thread-dump-analyzer.py`; look for BLOCKED threads
3. Database: `thread-dump-analyzer.py --focus db`; many threads waiting for a datasource → pool too small or connections held too long

## Common Performance Issues & Solutions

### Issue 1: High GC Pause Times During Period-End Processing

**Symptoms:**
- Young GC pause times > 200ms during import/export operations
- Spike in WAITING threads during GC events
- Response times increase proportionally with GC pauses

**Root Cause:**
- Heap too small for the period-end data surge
- Survivor space overflow causing promotion to old generation
- G1GC `-XX:MaxGCPauseMillis` target too aggressive for the actual workload

**Solution:**
1. Increase heap size (start with +25% from current allocation)
2. Adjust G1GC target: `-XX:MaxGCPauseMillis=300` or higher
3. Monitor G1GC region reclamation in GC logs

### Issue 2: Memory Leak Suspected (Heap Growth Across Cycles)

**Symptoms:**
- Heap occupancy increases after each GC cycle
- Old generation growing steadily over days
- Eventually triggers OutOfMemoryError

**Root Cause:**
- Application holding references beyond their lifecycle
- Cache without eviction policy
- Report generation not releasing temporary objects

**Solution:**
1. Analyze GC logs for heap occupancy trend across full GCs
2. Heap dump analysis to identify object retention
3. Check application logs for cache warnings
4. Cross-reference with the vendor's known-issues / fix list, if any

### Issue 3: Database Connection Pool Exhaustion

**Symptoms:**
- Thread dump shows many WAITING threads (connection wait)
- Application unresponsive during peak database activity
- Requests eventually fail with connection timeout

**Root Cause:**
- Datasource max-pool-size too small for concurrent application threads
- Connection leaks (connections not returned to pool)
- Long-running queries holding connections
- Database connection budget reached (`max_connections` is per database instance, shared by all pools on all nodes)

**Solution:**
1. Increase max-pool-size (within the database connection budget)
2. Reduce query timeout or optimize slow queries
3. Find leaks with IronJacamar's leak dumper pool (`mcp` attribute, see `references/wildfly-performance-config.md`)
4. Monitor pool statistics (`.../data-source=<datasource>/statistics=pool`, needs `statistics-enabled=true`)

Log signature: `IJ000453: Unable to get managed connection for ...` / `IJ000655: No managed connections available within configured blocking timeout`.

### Issue 4: OutOfMemoryError: Java heap space

**Symptoms:**
- Crash with `OutOfMemoryError: Java heap space`
- No warning period, sudden failure
- Reproducible during specific operations

**Root Cause:**
- Heap too small for peak workload
- Memory leak causing premature heap exhaustion
- Batch operation loading too much data into memory

**Solution:**
1. Increase heap based on observed peak usage (GC logs)
2. Check for memory leaks (heap trend analysis)
3. Optimize batch size or pagination in the application
4. Verify against the vendor's memory requirements

### Issue 5: CodeCache Exhaustion

**Symptoms:**
- Logs show "CodeCache is full. Compiler has been disabled."
- JIT compilation stops, performance degrades after startup
- Application runs hot initially then slows down

**Root Cause:**
- Code cache too small (the example vendor baseline specifies 512m minimum)
- `ReservedCodeCacheSize` not set while tiered compilation is disabled: the JVM default then drops from 240MB to 48MB
- Excessive class loading (proxy/dynamic class generation) producing lots of hot code

**Solution:**
1. Verify `-XX:ReservedCodeCacheSize=512m` is set (baseline); check usage with `jcmd <PID> Compiler.codecache`
2. Increase if needed: `-XX:ReservedCodeCacheSize=768m`
3. Check for excessive dynamic class generation in the application
4. Keep `-XX:-TieredCompilation` if the vendor requires it

## GC Algorithm Selection: G1GC vs ZGC

### G1GC (Garbage-First GC) - Default

**Characteristics:**
- Region-based, incremental collection reduces pause times
- Predictable latency with `-XX:MaxGCPauseMillis` tuning (G1 adapts young gen size to the target)
- Lower memory overhead than ZGC
- Mature, battle-tested for enterprise workloads

**Best for:**
- Standard deployments
- Heap size 8-16GB
- Pause time target 200-300ms acceptable
- Cost-conscious environments (less memory overhead)

**Tuning parameters:**
```
-XX:+UseG1GC
-XX:MaxGCPauseMillis=200                  # Default 200; adjust based on SLA
-XX:InitiatingHeapOccupancyPercent=35     # Default 45; initial value, adaptive IHOP adjusts it
-XX:+UnlockExperimentalVMOptions          # Only if young gen must be bounded:
-XX:G1NewSizePercent=25                   #   min young gen, % of heap (default 5)
-XX:G1MaxNewSizePercent=40                #   max young gen, % of heap (default 60)
```

Do not fix the young gen with `-Xmn`/`-XX:NewSize` under G1: it disables pause-time control.

### ZGC (Z Garbage Collector) - Ultra-Low Latency Alternative

**Characteristics:**
- Sub-millisecond pause times
- Concurrent mark and compact (minimal stop-the-world)
- Higher memory overhead (needs headroom, no compressed oops; this skill's rule of thumb: +25% heap)
- Production since JDK 15; the mode depends on the JDK: JDK 17 non-generational only, JDK 21 generational with `-XX:+ZGenerational`, JDK 23 generational by default, JDK 24+ generational only

**Best for:**
- Extreme latency requirements (< 10ms pause SLA)
- Large heaps (> 16GB)
- Applications where GC pause jitter is unacceptable
- Performance-critical transactional workloads

**Tuning parameters:**
```
-XX:+UseZGC
-XX:+ZGenerational              # JDK 21 only (of the LTS releases); JDK 17: JVM won't start;
                                # JDK 25: ignored with a warning — omit it there
-XX:ZUncommitDelay=300          # Delay in seconds before uncommit (300 is the default)
```

GC threads are sized and adjusted dynamically by ZGC; set `-XX:ConcGCThreads` only if measurements demand it. The main tuning knob is `-Xmx`.

**Migration considerations:**
- Requires heap increase (rule of thumb: heap × 1.25)
- Test thoroughly in non-production first
- Monitor for incompatibilities with application code
- May reduce throughput slightly vs G1GC (acceptable for latency gains)

Full comparison and migration guide: `references/gc-algorithm-comparison.md`.

## Interaction with Clustering

Clustering (session replication, JGroups, Infinispan) is out of scope, but it interacts with performance:
- Connection pool contention multiplies across cluster nodes sharing one database
- Replication traffic increases memory pressure and therefore GC load
- Slow session replication or degradation after enabling clustering needs both analyses

## Machine Size Recommendations

Heap ceilings as `scripts/jvm-flag-recommender.py` computes them (G1; GCP machine types as examples, use `--vcpu/--memory-gb` for any VM). GC thread counts are left to JVM ergonomics.

| Machine Type | vCPU | Memory | Heap `standard` | Heap `peak-batch` | Code cache |
|---|---|---|---|---|---|
| e2-standard-2 | 2 | 8GB | 4GB (`-Xmx4096m`) | 4.5GB | 512m |
| e2-standard-4 | 4 | 16GB | 8GB (`-Xmx8192m`) | 9.5GB | 512m |
| e2-standard-8 | 8 | 32GB | 16GB (`-Xmx16384m`) | 19GB | 512m |
| custom-16-65536 | 16 | 64GB | 31GB (`-Xmx31744m`, compressed-oops cap) | 31GB (cap) | 512m |
| c3-standard-22 | 22 | 88GB | 31GB (cap) | 52.5GB (`-Xmx53760m`) | 768m |

If the VMs are managed with Terraform, keep the JVM settings as variables so a rollback is a revert. `--format tfvars` emits them (variable names are illustrative — map them onto your module's inputs). Example, `--machine-type e2-standard-8` with the heap lowered to a measured 12GB:

```hcl
wildfly_machine_type = "e2-standard-8"
wildfly_jdk_version  = 21
wildfly_heap_size_mb = 12288
wildfly_java_opts    = "-Xms3072m -Xmx12288m -XX:ReservedCodeCacheSize=512m -XX:+UseG1GC -XX:MaxGCPauseMillis=200 -XX:InitiatingHeapOccupancyPercent=35 -XX:+HeapDumpOnOutOfMemoryError -XX:HeapDumpPath=<dump-dir> -Xlog:gc*:file=<logs-dir>/gc-%t.log:time,uptime,level,tags:filecount=10,filesize=100M"

# WildFly configuration tuning
wildfly_io_worker_io_threads         = 16
wildfly_io_worker_task_max_threads   = 75
wildfly_ejb3_thread_pool_max_threads = 16
wildfly_datasource_max_pool          = 50
wildfly_datasource_min_pool          = 0
wildfly_deployment_timeout_seconds   = 600   # WildFly default; never lower it below the real deploy time
```

12GB is a measured heap (see Heap Sizing Formula in `references/jvm-tuning-guide.md`), below the 16GB ceiling. Thread-pool values are the recommender's starting points; keep the WildFly defaults unless measurements say otherwise (see `references/wildfly-performance-config.md`).

Run `terraform fmt` before committing and apply through the normal pipeline.

## Safety Guidelines & Best Practices

### Pre-Implementation Checklist

1. **Baseline documentation**
   - Collect current GC logs and thread dumps
   - Record baseline metrics (response times, error rates)
   - Document current JVM flags and WildFly configuration

2. **Testing strategy**
   - Apply changes in a development environment first
   - Rerun GC analysis to verify improvement
   - Stress test with typical workload patterns
   - Validate period-end scenarios if applicable

3. **Rollback preparation**
   - Keep previous JVM flags documented
   - Know how to revert the configuration change quickly
   - Have a database failover plan if needed
   - Test the rollback before the production change

### Monitoring During & After Changes

1. **GC metrics**
   - Young GC pause time (should decrease or stay similar)
   - Full GC frequency (should decrease for memory fixes)
   - Heap occupancy trend (should stabilize)

2. **Application metrics**
   - Response time (p95, p99)
   - Error rate (timeouts, OOM errors)
   - Throughput (requests/s)
   - Database connection pool utilization

3. **System metrics**
   - CPU utilization
   - Memory usage (RSS, committed heap)
   - Network I/O (database traffic)
   - Disk I/O (if applicable)

### Common Pitfalls to Avoid

1. **Aggressive tuning**
   - Don't maximize every parameter at once
   - Increase heap/threads incrementally (25% at a time)
   - Measure the effect of each change

2. **Ignoring constraints**
   - Respect the database connection budget across all pools and nodes
   - Follow the vendor baseline where one exists (e.g. code cache minimum)
   - Verify Java/WildFly version compatibility with the application

3. **Memory tuning without root cause analysis**
   - Don't automatically increase heap for OOM errors
   - First check for memory leaks (GC log trend analysis)
   - Read the GC logs before making changes

4. **Ignoring trade-offs**
   - Larger heap = longer full GC pauses
   - Lower GC pause target = higher throughput cost
   - ZGC = memory overhead vs latency gains

## Communication with Users

### Asking for Diagnostic Information

Be specific and actionable:

- Weak: "Send me GC logs"
- Better: "Send GC logs from the last 24 hours, ideally covering the peak period — the `gc*.log*` files from wherever `-Xlog:gc*:file=` points (by default next to `server.log` in `$WILDFLY_HOME/standalone/log/`)"

- Weak: "What's your performance issue?"
- Better: "Is it slow response times during period-end processing, or gradual degradation over days? What is the typical response time and what is unacceptable?"

### Structuring Recommendations

Always provide:
1. **Problem statement** - what was diagnosed
2. **Root cause** - why it occurs, supported by data
3. **Recommended changes** - specific JVM flags or config changes
4. **Expected outcome** - performance improvement metrics
5. **Implementation steps** - how to apply the change in this environment
6. **Validation plan** - how to verify it worked
7. **Rollback procedure** - how to revert

When suggesting changes, cite the vendor baseline or known-issues list if the application has one.

## Technical Precision Requirements

### GC Log Analysis

Always report:
- **Timestamp and duration** of each relevant event
- **Pause time** in milliseconds (not just "high" or "low")
- **Heap occupancy before/after** the GC event
- **Frequency** of events (e.g. young GC every 5 seconds)

### Thread Dump Interpretation

Always count and categorize:
- Total thread count
- RUNNABLE vs WAITING vs BLOCKED breakdown
- Lock holders and waiters for each deadlock
- Database connection wait times (if present)

## Key Files & References

### Analysis Tools
- `scripts/gc-log-analyzer.py` - GC log parsing and metrics extraction
- `scripts/thread-dump-analyzer.py` - Thread dump analysis
- `scripts/jvm-flag-recommender.py` - Configuration generation
- `scripts/validate-wildfly-config.py` - Configuration validation

### Reference Documentation
- `references/jvm-tuning-guide.md` - JVM memory model, heap sizing, flags
- `references/wildfly-performance-config.md` - WildFly datasource, thread pool, deployment tuning
- `references/heavy-load-app-patterns.md` - Batch/report-heavy workload patterns and troubleshooting
- `references/gc-algorithm-comparison.md` - G1GC vs ZGC analysis and migration
- `references/profiling-methodology.md` - GC log, thread dump, heap dump and database profiling workflow

## Principles

- **Vendor documentation is authoritative** where the application vendor publishes a supported configuration
- **Measure before and after** every change; one change at a time
- **Safety first** - always provide rollback procedures and validation steps
