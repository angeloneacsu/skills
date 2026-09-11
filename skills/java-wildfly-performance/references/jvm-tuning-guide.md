# JVM Tuning Guide for Heavy-Load Applications on WildFly

## Table of Contents
1. JVM Memory Model
2. Heap Sizing Methodology
3. Garbage Collection Algorithms
4. Code Cache Optimization
5. Metaspace Configuration
6. JVM Flag Reference

## 1. JVM Memory Model

The Java Virtual Machine divides memory into several regions:

```
┌─────────────────────────────────────────────────────────────┐
│                      Total JVM Memory                        │
├─────────────────────────────────────────────────────────────┤
│                      Heap (Young + Old)                      │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ Young Generation                                     │   │
│  │  ┌─────────────┐  ┌──────────┐  ┌──────────────┐   │   │
│  │  │  Eden Space │  │Survivor 0│  │  Survivor 1  │   │   │
│  │  └─────────────┘  └──────────┘  └──────────────┘   │   │
│  └──────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │           Old Generation (Tenured)                   │   │
│  └──────────────────────────────────────────────────────┘   │
├─────────────────────────────────────────────────────────────┤
│                      Metaspace (Java 8+)                    │
│  Class metadata, method bytecode, constant pools            │
├─────────────────────────────────────────────────────────────┤
│                    Code Cache (JIT Code)                    │
│  Native code produced by the JIT compilers                  │
├─────────────────────────────────────────────────────────────┤
│                  Other Native Memory                        │
│  Thread stacks, direct buffers, GC overhead                │
└─────────────────────────────────────────────────────────────┘
```

The diagram is the logical model. G1 and generational ZGC implement the generations as sets of heap regions, not as contiguous spaces.

### Memory Regions Explained

**Heap (Young Generation)**
- Where new objects are allocated
- Divided into Eden and Survivor spaces
- Objects surviving multiple GCs promoted to old generation
- G1 sizes it adaptively, between 5% and 60% of the heap by default, to meet the pause target

**Heap (Old Generation)**
- Long-lived objects and promoted objects
- Collected less frequently than young gen (G1: concurrent marking + mixed GCs)
- Whatever the young generation does not use
- A Full GC compacts the whole heap in one stop-the-world pause

**Metaspace (Java 8+)**
- Stores class metadata, method bytecode, constant pools
- Unlimited by default (bounded by available native memory)
- Ensure adequate space if the vendor baseline specifies it
- OutOfMemoryError: Metaspace indicates too many dynamic classes

**Code Cache**
- Stores native code compiled by the JIT (C1/C2) plus runtime stubs
- Size limits JIT compilation (more hot code = larger cache needed)
- Example vendor baseline: minimum 512m
- CodeCache full error stops JIT compilation (performance cliff)

**Native Memory (Other)**
- Thread stacks (1 MB reserved per thread by default on Linux x64, `-Xss`)
- Direct ByteBuffers (NIO buffers)
- GC overhead structures
- Socket/file buffers
- Not bounded by -Xmx flag

## 2. Heap Sizing Methodology

### Step 1: Determine System Resources

Identify available memory on the target machine (GCP machine types as examples):

| Machine Type | Total Memory | Heap ceiling (`jvm-flag-recommender.py`, `standard`) | Left for OS/native |
|---|---|---|---|
| e2-standard-4 | 16GB | 8GB (`-Xmx8192m`) | 8GB |
| e2-standard-8 | 32GB | 16GB (`-Xmx16384m`) | 16GB |
| custom-16-65536 | 64GB | 31GB (`-Xmx31744m`, compressed-oops cap) | 33GB |
| c3-standard-22 | 88GB | 31GB (cap; `peak-batch` gives 52.5GB) | 57GB |

The recommender computes `min(P% of RAM, RAM − headroom)` with P = 50 `standard` / 60 `peak-batch` / 70 `high-memory`; headroom = OS reserve (max(1GB, 10% of RAM)) + code cache + 512MB metaspace + 1MB per estimated thread + 10% of the heap for GC native structures. `-Xms` = `-Xmx`/4 by default. Treat it as a ceiling for the machine; the measured sizing below (Step 4) often justifies less.

**Compressed oops:** a heap of 32GB or more disables compressed object pointers (verified on JDK 21: `-Xmx31g` → `UseCompressedOops=true`, `-Xmx32g` → `false`), so every reference doubles in size. A 33GB heap holds less than a 31GB one; stay at ≤31GB or go well beyond 32GB (the recommender caps G1 heaps between 31GB and ~48GB at 31GB).

**Reserve sizing rules:**
- OS and processes: 2-4GB minimum
- GC overhead (G1GC, ZGC, heap management): 3-5GB
- Thread stacks (if many concurrent threads): 0.5GB per 500 threads
- Other native memory: 2-3GB

### Step 2: Analyze Current Workload

Collect metrics from production (or a production-like environment):

**From GC logs:**
```bash
# Young GC frequency tells us about Eden allocation rate
# If young GC every 5 seconds -> high allocation rate

# Peak heap occupancy after GC tells us how much long-lived data
# Heap occupancy after GC = minimum required heap size

# Full GC frequency indicates old generation pressure
# If full GC > once per day -> consider larger heap
```

**From thread dumps:**
```bash
# Count active threads
# Example for a busy EE app: 50-100 active threads during peak

# Each thread reserves stack space
# Default: 1MB per thread (can be reduced with -Xss)
# Total stack: 50 threads × 1MB = 50MB (usually negligible)
```

**From monitoring:**
- Peak requests per second
- Peak concurrent users
- Memory usage during period-end processing
- Memory usage during report generation

### Step 3: Size Young Generation

Young generation sizing affects GC pause times:

**Smaller young gen:**
- More frequent young GCs
- Shorter pause per young GC
- More short-lived objects promoted prematurely to old generation
- More old-generation pressure (more marking, mixed GCs, Full GC risk)

**Larger young gen:**
- Less frequent young GCs
- Longer pause per young GC if much of it survives (more to copy)
- Fewer objects promoted to old generation
- Less old-generation pressure

**Default sizing (G1):** adaptive, between `G1NewSizePercent` (5%) and `G1MaxNewSizePercent` (60%) of the heap, driven by `MaxGCPauseMillis`.

**For batch-heavy workloads (illustrative):**
- If period-end processing generates 10GB of temporary objects
- and G1 keeps the young gen small to meet the pause target,
- survivors overflow and are promoted prematurely
- Prefer relaxing `MaxGCPauseMillis` first; if you must force a minimum young size, raise the experimental `G1NewSizePercent` (needs `-XX:+UnlockExperimentalVMOptions`). Avoid `-Xmn` / `-XX:NewSize` with G1: a fixed young size disables pause-target adaptation.

### Step 4: Validate Sizing

Use GC logs to validate the chosen heap size:

**Good heap size indicators:**
- Young GC pause time < 200ms (for G1GC with -XX:MaxGCPauseMillis=200)
- Full GC < once per month under normal load
- Heap occupancy stays relatively flat after each GC
- No premature promotion (check the age distribution with `-Xlog:gc+age*=trace`)

**Bad heap size indicators:**
- Young GC pause times > 500ms (heap too small)
- Full GC multiple times per day (old gen pressure)
- Heap occupancy grows after each GC (memory leak)
- OutOfMemory: Java heap space (heap definitely too small)

### Heap Sizing Formula

```
Heap Size = Peak Memory Usage × 1.5 (safety margin)

Where:
- Peak Memory Usage = heap occupancy after GC during peak load
- 1.5x = ensures buffer for unexpected spikes + young gen growth
```

**Example calculation (illustrative):**
- Peak heap occupancy observed: 8GB (during period-end processing)
- Recommended heap: 8GB × 1.5 = 12GB
- Selected heap on a 32GB machine: 12GB ✓ (below the recommender's 16GB ceiling)
- Leave 20GB for OS, GC overhead, native memory ✓

## 3. Garbage Collection Algorithms

### G1GC (Garbage-First Garbage Collector) - Default

**How it works:**
1. Divides the heap into equal-size regions; the size is chosen ergonomically from the heap size (about heap/2048 rounded up to a power of two, 1–32MB; on JDK 21: 2MB for a 4GB heap, 8MB for 12–16GB, 16MB for 17–32GB, 32MB above). `-XX:G1HeapRegionSize` overrides it (max 32MB on JDK 17, 512MB on JDK 18+)
2. Young GC evacuates all Eden and Survivor regions
3. Concurrent mark phase traces live objects (short STW Remark/Cleanup pauses)
4. Mixed GC reclaims empty/low-live regions from old gen
5. Full GC fallback when concurrent marking can't keep up or evacuation fails

**Pause time target:**
- -XX:MaxGCPauseMillis=200 (default)
- G1GC tries to keep pauses ≤ 200ms
- Cannot guarantee (depends on live set size)
- G1 re-sizes the young generation after each young GC from observed pause times: when pauses run over the target it uses a smaller young gen (smaller, more frequent collections), not a larger one

**Advantages:**
- Predictable latency (tunable pause times)
- Scales from small to very large heaps
- Incremental collection reduces full GC frequency
- Mature, well-tested for enterprise applications
- Usually the collector application vendors test and certify

**Disadvantages:**
- Lower raw throughput than Parallel GC (barriers and concurrent work cost CPU)
- More tuning surface than Parallel GC
- Pause time not guaranteed if workload exceeds assumptions

**Tuning:**

```bash
# Baseline
-XX:+UseG1GC
-XX:MaxGCPauseMillis=200

# For period-end heavy load, may need to relax pause target
-XX:MaxGCPauseMillis=300

# For low-latency requirements
-XX:MaxGCPauseMillis=100

# Optional: bound the adaptive young gen (experimental flags, defaults 5 / 60)
-XX:+UnlockExperimentalVMOptions
-XX:G1NewSizePercent=25     # young gen never below 25% of heap
-XX:G1MaxNewSizePercent=40  # young gen never above 40% of heap
# Do not use -Xmn / -XX:NewSize with G1: a fixed young gen disables pause-target adaptation

# Initiating heap occupancy (when concurrent mark starts), default 45
-XX:InitiatingHeapOccupancyPercent=35
# With adaptive IHOP (default on) this is only the starting value; G1 then adjusts it
# Lower value = start earlier, more concurrent marking
# Higher value = wait longer, risk full GC if can't keep up
```

### ZGC (Z Garbage Collector) - Ultra-Low Latency Alternative

**How it works:**
1. Concurrent mark phase
2. Concurrent relocate phase (move objects, update references)
3. Three short STW pauses per cycle (Pause Mark Start, Pause Mark End, Pause Relocate Start)
4. Generations by JDK: JDK 17 non-generational only; JDK 21–22 generational opt-in (`-XX:+ZGenerational`); JDK 23 generational by default; JDK 24+ generational only (JEP 490)

**Pause characteristics:**
- Sub-millisecond pauses on JDK 17+ (since JDK 16, JEP 376)
- No pause-time target to tune
- Pause time independent of heap size
- No Full GC; the failure mode is an **allocation stall** (threads wait for memory when the concurrent cycle falls behind; logged as `Allocation Stall`)

**Advantages:**
- Dramatically lower pause times (sub-ms)
- Pause time independent of heap size
- No full GC pauses
- Better for applications requiring < 10ms GC pause SLA
- Excellent for interactive real-time requirements

**Disadvantages:**
- Needs heap headroom to allocate while collecting concurrently; no compressed oops. Rule of thumb in this skill: start from +25% heap vs G1, then confirm there are no allocation stalls
- Lower throughput than G1GC (concurrent work competes for CPU)
- Production-ready since JDK 15; generational only from JDK 21 (check the application's supported JDK)
- Less battle-tested in production than G1GC, and rarely vendor-certified

**Tuning (if latency critical):**

```bash
# Baseline ZGC configuration (all JDKs)
-XX:+UseZGC

# JDK 21-22 only: opt in to generational mode (JEP 439)
-XX:+ZGenerational
# JDK 17: do not add it (unrecognized option, the JVM will not start)
# JDK 23: already the default (flag deprecated); JDK 24-25: obsolete, ignored with a warning;
# JDK 26+: unrecognized again — remove it

# Concurrent GC threads: ergonomic and dynamic by default; set only if measurements demand it
-XX:ConcGCThreads=4

# Uncommit delay (return memory to OS after collection); 300 s is the default
-XX:ZUncommitDelay=300

# Heap sizing must account for the headroom
# Rule of thumb: ZGC heap = G1GC heap × 1.25
# Example: if G1GC = 12GB, then ZGC = 15GB
```

### Choosing Between G1GC and ZGC

**Use G1GC when:**
- Pause time target 100-300ms is acceptable
- Heap size 8-16GB (standard deployment)
- Cost optimization important (lower memory overhead)
- Following the vendor recommendation (G1GC is the usual default)
- Team familiar with G1GC tuning
- Throughput is priority over latency

**Use ZGC when:**
- Pause time must be < 10ms (real-time requirement)
- Heap size > 16GB (ZGC efficiency advantage)
- Interactive/UI workload where latency jitter is unacceptable
- Performance testing confirms acceptable throughput
- Memory budget allows a ~25% heap increase
- Willing to accept slightly lower throughput for latency gains

## 4. Code Cache Optimization

Code cache stores native code produced by the JIT compilers. If full, JIT compilation stops (severe performance impact).

### Code Cache Sizing

Example vendor baseline: **minimum 512m**. JVM defaults (verified on JDK 21): 240MB with tiered compilation, **48MB with `-XX:-TieredCompilation`** — so a baseline that disables tiered compilation must set `ReservedCodeCacheSize` explicitly.

Size it from measured usage, not from heap size: the amount of hot code depends on the application, not on `-Xmx`. Start from the baseline's 512m; raise it (768m, 1024m; the JVM limit is 2GB) only when measured usage approaches the limit.

**Monitoring code cache:**
```bash
# Live usage (size / used / max_used / free, and whether compilation is still enabled)
jcmd <pid> Compiler.codecache

# Usage summary printed at JVM exit
-XX:+PrintCodeCache

# Code cache events (e.g. full, flushing) in the unified log
-Xlog:codecache=info:file=<logs-dir>/codecache.log

# If max_used > ~90% of size -> increase ReservedCodeCacheSize
```

### Tiered Compilation Configuration

Some vendors require tiered compilation to be **disabled**. Only do this when the vendor mandates it:

```bash
# Vendor-required in the example baseline: disable tiered compilation
-XX:-TieredCompilation

# Rationale given for this:
# - Consistent performance (no C1 vs C2 switching)
# - Predictable compilation
# - Stable code cache usage
# - Matches the vendor's performance test conditions
# Cost: slower warm-up (methods are interpreted until C2 compiles them)
```

### Full Code Cache Configuration

```bash
# Example vendor baseline
-XX:ReservedCodeCacheSize=512m

# Additional code cache tuning (rarely needed)
-XX:CodeCacheExpansionSize=64m      # Expansion increment
-XX:InitialCodeCacheSize=256m       # Initial committed size (must not exceed ReservedCodeCacheSize)
-XX:+UseCodeCacheFlushing           # Default on; keep it (evicts cold code when the cache fills)
```

## 5. Metaspace Configuration

Metaspace stores class definitions, method metadata and constant pools.

### Metaspace Sizing

Java 8+ uses metaspace (native memory, not heap):

```bash
# Default: unlimited (bounded by system memory)
# Usually fine, but a maximum can be set:
-XX:MetaspaceSize=256m              # Initial high-water mark: first metadata-triggered GC happens here
-XX:MaxMetaspaceSize=1024m          # Hard cap (OutOfMemoryError: Metaspace beyond it)
```

### Monitoring for Metaspace Issues

**OutOfMemoryError: Metaspace indicates:**
- Too many class loaders not being garbage collected
- Dynamic class generation without cleanup
- Application class leak (common in some frameworks)

**Solutions:**
1. Increase -XX:MaxMetaspaceSize (temporary fix)
2. Find and fix class loader leak (permanent fix)
3. Disable dynamic class generation if possible
4. Use WeakHashMap for class loader caches

## 6. JVM Flag Reference

### Example Vendor-Style Baseline Flags

```bash
# == Baseline flags (example vendor baseline) ==

# Basic tuning (only because the example vendor mandates them)
-Xrs                                # Reduce JVM use of OS signals, see caveat below
-server                             # No-op on 64-bit JDKs (the server VM is the only VM)

# Memory sizing (adjust Xmx per environment)
-Xmx12288m                          # Heap max (example: 12GB)
-Xms2048m                           # Heap initial (can be much smaller, GC will grow)

# Code cache (example vendor minimum 512m)
-XX:ReservedCodeCacheSize=512m      # Code cache for JIT compilation

# Tiered compilation (disabled only if the vendor requires it)
-XX:-TieredCompilation              # Disable tiered compilation

# == GC Algorithm Selection ==

# G1GC (default)
-XX:+UseG1GC
-XX:MaxGCPauseMillis=200            # Target pause time (adjust to 100-300 based on SLA)

# OR ZGC (if low latency required, requires careful testing)
-XX:+UseZGC
-XX:+ZGenerational                  # JDK 21-22 only (see ZGC section)

# == GC Tuning Parameters ==

# Young generation bounds (if needed; experimental flags)
-XX:+UnlockExperimentalVMOptions
-XX:G1NewSizePercent=25
-XX:G1MaxNewSizePercent=40

# Concurrent mark initiation
-XX:InitiatingHeapOccupancyPercent=35

# == Logging & Diagnostics ==

# Unified GC logging (recommended for analysis)
-Xlog:gc*:file=logs/gc-%t.log:time,uptime,level,tags:filecount=5,filesize=20m

# Less verbose GC logging (if space constrained): one line per GC
-Xlog:gc:file=logs/gc-%t.log:time,uptime,level,tags:filecount=5,filesize=20m

# The JDK 8 flags (-Xloggc, -XX:+PrintGCDetails, -XX:+PrintGCTimeStamps,
# -XX:+PrintTenuringDistribution) are gone or deprecated since JDK 9; see the mapping below
```

> **`-Xrs` caveat.** With `-Xrs` the JVM installs no handlers for SIGQUIT, SIGINT, SIGTERM and SIGHUP.
> Verified on JDK 21: `kill -3 <pid>` no longer prints a thread dump (use `jcmd <pid> Thread.print`,
> which still works), and SIGTERM no longer runs shutdown hooks, so a plain `kill` / `systemctl stop`
> terminates WildFly without a graceful shutdown — stop it with `jboss-cli.sh --connect :shutdown`
> instead. Keep `-Xrs` only where a vendor requires it.

> **`%t` in the GC log file name is not cosmetic.** Without it the JVM always writes the same
> `gc.log`, and **a restart overwrites the previous set**. On any node with automatic restart
> (`-XX:+ExitOnOutOfMemoryError` plus systemd `Restart=always`), the restart follows an OOM within
> minutes — so the log that contained the GC death spiral before the crash is exactly the one you
> lose. `%t` (JVM start timestamp) gives one file set per JVM start and nothing is overwritten.
>
> The trade-off: the JVM rotates only **within** one run and never cleans up older runs. Bound it
> from outside — e.g. `find <logs-dir> -name 'gc-*.log*' -mtime +14 -delete` in a daily cron job.
> `logrotate` is **not** the right tool here: the JVM keeps the file descriptor open and
> `copytruncate` fights with the JVM's own rotation.

### Performance Monitoring Flags

JDK 17+ uses unified logging (`-Xlog`); the JDK 8 flags in the comments are rejected or only deprecated aliases. Tag sets can be combined in one `-Xlog` option (comma-separated) and sent to a file with `:file=...`.

```bash
# Heap usage before/after GC            (was -XX:+PrintHeapAtGC)
-Xlog:gc+heap=trace

# Promotion / survivor age distribution (was -XX:+PrintTenuringDistribution)
-Xlog:gc+age*=trace

# GC ergonomics decisions               (was -XX:+PrintAdaptiveSizePolicy)
-Xlog:gc+ergo*=debug

# Class loading tracking (if metaspace issues) (was -XX:+TraceClassLoading / TraceClassUnloading)
-Xlog:class+load=info,class+unload=info:file=<logs-dir>/classload.log

# JIT compilation tracking
-XX:+PrintCompilation                            # product flag
-XX:+UnlockDiagnosticVMOptions -XX:+PrintInlining # diagnostic flag, needs the unlock first

# Everything beyond gc* at info adds overhead and volume
# Use sparingly in production, more freely in test environments
```

### Safe Tuning Limits

**Do not exceed these limits without careful testing:**

| Parameter | Recommended Range | Dangerous Beyond |
|---|---|---|
| Xmx (heap max) | 4GB - 31GB, or well above 32GB (≥32GB loses compressed oops) | > System physical memory - 4GB |
| Xms (heap initial) | 0.5GB - 25% of Xmx | > Xmx (invalid) |
| ReservedCodeCacheSize | 256m - 1024m | > 2GB (rejected: JVM limit) |
| MaxGCPauseMillis | 50ms - 500ms | > 1000ms (too loose) |
| ConcGCThreads (ZGC) | 1 - vCPU count | > vCPU count (resource waste) |

## Summary: Recommended Configurations

Examples assume the illustrative vendor baseline; drop `-Xrs` (see caveat above), `-server` (no-op) and `-XX:-TieredCompilation` if your application has no such requirement.

### Staging / Integration (Standard Deployment)

```bash
JAVA_OPTS="-Xrs -server"
JAVA_OPTS="${JAVA_OPTS} -Xmx12288m -Xms2048m"
JAVA_OPTS="${JAVA_OPTS} -XX:ReservedCodeCacheSize=512m"
JAVA_OPTS="${JAVA_OPTS} -XX:-TieredCompilation"
JAVA_OPTS="${JAVA_OPTS} -XX:+UseG1GC -XX:MaxGCPauseMillis=200"
JAVA_OPTS="${JAVA_OPTS} -XX:InitiatingHeapOccupancyPercent=35"
JAVA_OPTS="${JAVA_OPTS} -Xlog:gc*:file=logs/gc-%t.log:time,uptime,level,tags:filecount=5,filesize=20m"
```

### Production (same as staging unless a specific optimization is needed)

Start with the staging configuration; adjust only if monitoring shows issues.

### Low-Latency Requirement (ZGC)

```bash
JAVA_OPTS="-Xrs -server"
JAVA_OPTS="${JAVA_OPTS} -Xmx15360m -Xms3072m"     # 20% more for ZGC overhead
JAVA_OPTS="${JAVA_OPTS} -XX:ReservedCodeCacheSize=512m"
JAVA_OPTS="${JAVA_OPTS} -XX:-TieredCompilation"
JAVA_OPTS="${JAVA_OPTS} -XX:+UseZGC"
JAVA_OPTS="${JAVA_OPTS} -XX:+ZGenerational"   # JDK 21-22 only; omit on JDK 17 and 23+
JAVA_OPTS="${JAVA_OPTS} -Xlog:gc*:file=logs/gc-%t.log:time,uptime,level,tags:filecount=5,filesize=20m"
```

## Testing & Validation Methodology

1. **Set baseline metrics** (before any changes)
2. **Apply one parameter change** at a time
3. **Run representative workload** (an hour of typical traffic)
4. **Collect GC logs** and compare metrics
5. **Validate improvement** (not regression)
6. **Document rationale** for each change
7. **Keep rollback plan** ready if issues arise
