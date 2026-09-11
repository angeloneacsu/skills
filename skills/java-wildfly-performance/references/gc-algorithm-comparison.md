# GC Algorithm Comparison: G1GC vs ZGC

## Executive Summary

**Recommendation for heavy-load EE applications:**
- **Default: G1GC** - What vendors typically test and certify; right for standard deployments
- **Alternative: ZGC** - If an extreme low-latency requirement exists

**Key metrics comparison:**

| Metric | G1GC | ZGC |
|---|---|---|
| **Pause Time** | tens to hundreds of ms (target `MaxGCPauseMillis`, default 200) | sub-millisecond |
| **Pause Variance** | Grows with live data to copy | Consistent |
| **Memory Overhead** | Low (compressed oops, remembered sets off-heap) | Higher (needs allocation headroom; no compressed oops) |
| **Throughput** | Good | Lower for throughput-bound batch work (concurrent work competes for CPU) |
| **Code Maturity** | Default collector since JDK 9 | Production since JDK 15; generational since JDK 21 |
| **Typically vendor-tested** | ✓ Yes (usual default) | ✗ Often limited |

## 1. G1GC (Garbage-First Garbage Collector)

### Overview

G1GC is the default collector since JDK 9 (on JDK 17, 21 and 25 alike) and the usual vendor-recommended collector. It trades some throughput for pause times that stay near a configurable target.

### How G1GC Works

**Heap organization:**
```
Heap is divided into equal-size regions (1-32MB, chosen from the heap size)

┌────────┬────────┬────────┬────────┬────────┐
│ Region │ Region │ Region │ Region │ Region │  ...
│  Eden  │  Eden  │Survivor│Old Gen │ Old Gen│
└────────┴────────┴────────┴────────┴────────┘
```

Objects of half a region or more are allocated directly as **humongous** objects in contiguous old regions; frequent humongous allocations show up as `(G1 Humongous Allocation)` GC causes.

**Collection phases:**

1. **Young GC (minor collection)** — log: `Pause Young (Normal)`
   - Evacuates all Eden and Survivor regions
   - Objects surviving GC moved to survivor regions or promoted to old generation
   - Pause time: depends on how much live data must be copied
   - Frequency: depends on allocation rate and young gen size

2. **Concurrent Marking Phase** — starts with `Pause Young (Concurrent Start)`, ends with short `Pause Remark` and `Pause Cleanup` pauses
   - Runs in background alongside the application
   - Computes live data per old region; completely empty regions are freed at once

3. **Mixed GC** — `Pause Young (Prepare Mixed)`, then `Pause Young (Mixed)`
   - Collects Eden + Survivor + the old regions with the most garbage ("garbage first")
   - More expensive than young GC but still incremental
   - Reclaims space from old generation without full GC

4. **Full GC (fallback)** — log: `Pause Full (G1 Compaction Pause)` or `Pause Full (System.gc())`
   - Compacts the entire heap in one stop-the-world pause
   - Triggered when concurrent marking/mixed GCs can't keep up or evacuation fails
   - Long pause time (seconds on multi-GB heaps)
   - Indicates heap too small, allocation rate too high, or a leak (or an explicit `System.gc()`)

### G1GC Tuning Parameters

```bash
# === CORE PARAMETERS ===

-XX:+UseG1GC                       # Enable G1GC (already the default)

-XX:MaxGCPauseMillis=200           # Target pause time (key tuning parameter, default 200)
                                   # G1GC tries to meet this target
                                   # If pauses run over, G1 uses a smaller young gen (smaller, more frequent GCs)

# === OPTIONAL TUNING ===

-XX:+UnlockExperimentalVMOptions   # required by the two G1*NewSizePercent flags
-XX:G1NewSizePercent=25            # Minimum young generation size (% of heap, default 5)
-XX:G1MaxNewSizePercent=40         # Maximum young gen size (% of heap, default 60)
                                   # Avoid -Xmn / NewSize: a fixed young gen disables pause-target adaptation

-XX:InitiatingHeapOccupancyPercent=35   # When to start concurrent marking (default 45;
                                        # adaptive IHOP treats it as the starting value)
                                        # Lower = start earlier, more CPU overhead
                                        # Higher = start later, risk full GC

-XX:G1HeapWastePercent=10          # Garbage % of heap G1 tolerates before stopping mixed GCs (default 5)
# -XX:G1HeapRegionSize             # Ergonomic from heap size; override only for many humongous objects
```

### G1GC Performance Characteristics

**Young GC:**
- Pause time: depends on live data and the -XX:MaxGCPauseMillis target
- Frequency: varies with allocation rate and young gen size
- Predictable: Good (G1GC adjusts young gen size to meet pause target)

**Full GC:**
- Pause time: seconds (whole-heap compaction; multi-threaded since JDK 10)
- Frequency: ideally < 1 per month (indicates good sizing)
- Problem: If occurs weekly → heap too small or memory leak

**Memory overhead:**
- Remembered sets and other G1 structures live in native memory outside `-Xmx`
- Measure instead of guessing: `-XX:NativeMemoryTracking=summary`, then `jcmd <pid> VM.native_memory summary`

### G1GC for Heavy-Load EE Apps: Strengths & Weaknesses

**Strengths:**
- Usually vendor-tested and recommended
- Predictable pause times (tunable with -XX:MaxGCPauseMillis)
- Handles 8-64GB heaps efficiently
- Mature, reliable (default since JDK 9)
- Good for period-end spikes (adjusts young gen automatically)

**Weaknesses:**
- Pause times can be unpredictable if target not met
- A Full GC can stall the application for seconds (bad if it happens during period-end)
- Mixed GCs and concurrent marking cost CPU
- Not suited to single-digit-millisecond pause SLAs

### When to Use G1GC

✓ **Use G1GC when:**
- Standard deployment (recommended)
- Pause time SLA is 100-500ms (acceptable for a typical business app)
- Heap size 8-16GB (standard environment)
- Team familiar with G1GC tuning
- Cost optimization important (lower memory overhead)
- Following the vendor recommendation

✗ **Don't use G1GC when:**
- Pause time SLA < 10ms (real-time requirement)
- Financial trading or real-time processing needed

## 2. ZGC (Z Garbage Collector)

### Overview

ZGC is a scalable low-latency garbage collector: experimental in JDK 11, production-ready since JDK 15 (JEP 377). Pause times are independent of heap size and sub-millisecond since JDK 16 (JEP 376: concurrent thread-stack processing).

**Which ZGC you get depends on the JDK:**

| JDK | ZGC mode |
|---|---|
| 17 | Non-generational only (`-XX:+ZGenerational` does not exist) |
| 21, 22 | Non-generational by default; generational with `-XX:+ZGenerational` (JEP 439) |
| 23 | Generational by default (JEP 474); the flag is deprecated, `-XX:-ZGenerational` still selects the old mode |
| 24, 25 | Generational only (JEP 490); `ZGenerational` is obsolete: ignored with a warning — remove it |
| 26+ | Generational only; `ZGenerational` is unrecognized and the JVM will not start |

### How ZGC Works

**Colored pointers:** ZGC keeps GC metadata ("colors") in bits of each 64-bit object reference and checks them in load barriers, so it can move objects while the application runs. This is also why ZGC cannot use compressed oops.

**Collection phases (one cycle, phase names as they appear under `gc,phases`; generational ZGC runs them per generation, prefixed `y:`/`Y:` young and `O:` old):**
1. **Pause Mark Start** (STW, sub-ms)
   - Start marking from roots
2. **Concurrent Mark** (runs while app runs)
   - Traverse object graph, mark reachable objects
3. **Pause Mark End** (STW, sub-ms)
   - Finalize marking
4. **Concurrent Process Non-Strong References / Select Relocation Set** (runs while app runs)
   - Reference processing, choose the pages to compact
5. **Pause Relocate Start** (STW, sub-ms)
   - Switch to relocation
6. **Concurrent Relocate** (runs while app runs)
   - Move live objects out of the selected pages; stale references are fixed by load barriers

**Key insight:** All heavy lifting is concurrent. STW pauses are minimal and bounded.

### ZGC Tuning Parameters

```bash
# === CORE PARAMETERS ===

-XX:+UseZGC                        # Enable ZGC
-Xmx<size>                         # The main tuning knob: enough headroom avoids allocation stalls

# === GENERATIONAL MODE (JDK 21-22 only) ===

-XX:+ZGenerational                 # Opt in to generational ZGC (JEP 439)
                                   # JDK 17: unrecognized, JVM will not start; JDK 23: default;
                                   # JDK 24-25: ignored with a warning; JDK 26+: unrecognized

# === CONCURRENT GC THREADS ===

# Ergonomic and adjusted dynamically by default; override only if measurements demand it
-XX:ConcGCThreads=4

# === MEMORY MANAGEMENT ===

-XX:ZUncommitDelay=300             # Delay before returning memory to OS (seconds)
                                   # Default: 300s (5 min)
                                   # Helps with spiky workloads (don't release memory too eagerly)
-XX:SoftMaxHeapSize=<size>         # Optional: soft limit below -Xmx that ZGC tries to stay under
```

ZGC has no flags for page size or generation sizes; generational ZGC sizes the young and old generations itself.

### ZGC Performance Characteristics

**Pause time:**
- Consistently sub-millisecond
- Independent of heap size
- Ideal for latency-sensitive applications

**Throughput:**
- Lower than G1GC for throughput-bound work (concurrent GC threads compete for CPU)
- Acceptable trade-off for latency guarantee
- Measure with the real workload before deciding

**Memory overhead:**
- Needs headroom to keep allocating while a cycle runs; too little → allocation stalls
- No compressed oops, so references are 8 bytes
- Rule of thumb used in this skill: 12GB G1GC heap → 15GB ZGC heap (+25%) as a starting point, then check for allocation stalls

### ZGC for Heavy-Load EE Apps: Strengths & Weaknesses

**Strengths:**
- Ultra-low, consistent pause times (< 1ms)
- No full GC (concurrent collection handles everything)
- Scales from small to massive heaps
- Minimal pause time variance (good for SLA compliance)
- Good for period-end spikes (no GC pauses, as long as there is headroom to avoid allocation stalls)

**Weaknesses:**
- Requires more memory (headroom + no compressed oops)
- Lower throughput than G1GC for throughput-bound work
- Less tested than G1GC for typical vendor workloads
- Behaviour differs by JDK (non-generational on 17, generational on 21+)
- Often not officially vendor-tested for commercial EE products

### When to Use ZGC

✓ **Use ZGC when:**
- Extreme low-latency requirement (< 10ms pause SLA)
- Period-end processing must have predictable response times
- User experience impacted by GC pauses (interactive dashboard)
- Machine has excess CPU capacity (to absorb concurrent overhead)
- Team willing to test and accept newer technology

✗ **Don't use ZGC when:**
- Following a vendor recommendation for G1GC
- Memory constrained (can't increase heap ~25%)
- Production stability prioritized over latency
- Team unfamiliar with ZGC behavior
- Throughput is the critical metric (G1GC better)

## 3. Migration Guide: G1GC to ZGC

If deciding to switch from G1GC (default) to ZGC:

### Step 1: Preparation

```bash
# 1. Baseline G1GC performance in test environment
# Collect GC logs for 1 week
-Xlog:gc*:file=gc-g1gc-%t.log:time,uptime,level,tags:filecount=10,filesize=100m

# 2. Analyze baseline metrics:
# - Young GC pause time (should stay under the MaxGCPauseMillis target)
# - Full GC frequency (ideally 0, acceptable < 1/month)
# - Heap usage pattern (% of max heap)
# - Period-end pause time spike pattern
```

### Step 2: Configure ZGC in Test Environment

```bash
# Disable G1GC
# -XX:+UseG1GC  ← REMOVE THIS

# Enable ZGC
-XX:+UseZGC
-XX:+ZGenerational   # JDK 21-22 only; omit on JDK 17 (unsupported) and 23+ (default / obsolete)

# Increase heap size for ZGC headroom
# G1GC: -Xmx12288m
# ZGC:  -Xmx15360m  (12GB × 1.25)

# Concurrent threads: leave ergonomic unless measurements show a need
# -XX:ConcGCThreads=4
```

### Step 3: Test & Validation

```bash
# Run for 1-2 weeks in test environment
# Collect metrics:

# A. GC pause times (should be < 1ms); pause lines end in "<n>ms", e.g.
#    [gc,phases] GC(0) Y: Pause Mark Start (Major) 0.015ms
grep -E "Pause (Mark Start|Mark End|Relocate Start).*ms$" gc-zgc.log | \
  awk '{v=$NF; sub(/ms$/,"",v); v+=0; if (v>max) max=v} END {print "max pause: " max " ms"}'

# A2. Allocation stalls (must be 0; each one is an application thread waiting for memory)
grep -c "Allocation Stall (" gc-zgc.log

# B. Overall throughput (compare to G1GC baseline)
# If similar throughput → ZGC is viable

# C. Memory usage (expect it to be higher)
# Expected: 15GB vs 12GB = 25% increase ✓

# D. Period-end processing
# Critical test: run with a representative period-end workload
# Verify pause times stay consistent and no allocation stalls appear
```

### Step 4: Comparison & Decision

Create a comparison table (illustrative numbers):

```
Metric                    G1GC        ZGC          Trade-off
─────────────────────────────────────────────────────────────
Pause time (avg)          100ms       < 1ms        Huge ZGC win
Pause time (max)          300ms       < 1ms        Huge ZGC win
Full GC (frequency)       < 1/month   0            ZGC advantage
Memory usage              12GB        15GB         3GB cost
CPU overhead              8%          13%          5% cost
Vendor tested             Yes         No           Risk to ZGC
─────────────────────────────────────────────────────────────

Decision: If latency improvement justifies 3GB memory + risk → use ZGC
          Otherwise, stay with G1GC (default)
```

### Step 5: Gradual Rollout

If adopting ZGC:

1. **Dev environment:** 2-4 weeks ZGC testing
2. **Test environment:** 4-8 weeks full workload testing
3. **Integration/staging environment:** 1-2 weeks with production-like data
4. **Production canary:** 1-2 instances running ZGC (monitoring)
5. **Production full:** Rollout after canary successful

### Step 6: Rollback Plan

Keep the G1GC configuration ready:

```bash
# If ZGC causes issues, roll back:
# Restore the GC flags (e.g. the IaC variable holding them):
wildfly_java_opts_gc = "-XX:+UseG1GC -XX:MaxGCPauseMillis=200"
# Change heap: -Xmx15360m → -Xmx12288m
# Redeploy through the normal change path
# Restore to G1GC baseline
```

## 4. Workload-Specific GC Considerations

### Period-End Impact

**G1GC during period-end:**
- Young GC pauses increase (more data = longer pauses)
- Risk of full GC if old generation fills
- Pause time target may not be met

**ZGC during period-end:**
- Pause times stay consistent (< 1ms)
- No full GC stall — but allocation stalls if the heap has too little headroom
- Predictable performance throughout period-end, provided the heap is sized for the peak

**Recommendation:** If period-end pause times are problematic with G1GC → ZGC is worth trying

### Concurrent User Impact

**G1GC during heavy load:**
```
100 concurrent users → 100 threads requesting data
GC pause 200ms → all 100 threads blocked for 200ms
User-perceivable: Visible slowdown
```

**ZGC during heavy load:**
```
100 concurrent users → 100 threads requesting data
GC pause < 1ms → imperceptible
User-perceivable: No slowdown
```

### Database Connection Pool Interaction

**G1GC full GC impact:**
```
If full GC occurs for 10 seconds:
- All threads blocked (can't make DB queries)
- DB connections remain held (can't be released)
- If connection timeout = 30s, connections stay busy for 40s total
- New requests hitting timeout
```

**ZGC interaction:**
```
Since no full GC, no connection stalling
DB connections remain responsive
Better connection pool utilization
```

**Recommendation:** If Full GCs coincide with pool exhaustion, fix the Full GCs first (heap sizing, leak); ZGC removes the pause, not a leak or a slow query

## 5. Configuration Summary

### G1GC Baseline

```bash
-Xmx12288m -Xms2048m
-XX:+UseG1GC
-XX:MaxGCPauseMillis=200
-XX:InitiatingHeapOccupancyPercent=35
```

### G1GC Optimized for Period-End

```bash
-Xmx16384m -Xms4096m
-XX:+UseG1GC
-XX:MaxGCPauseMillis=300        # More relaxed target
-XX:+UnlockExperimentalVMOptions
-XX:G1NewSizePercent=30         # Larger minimum young gen (default 5)
-XX:InitiatingHeapOccupancyPercent=30  # Start marking earlier
```

### ZGC Low-Latency Alternative

```bash
-Xmx15360m -Xms3072m
-XX:+UseZGC
-XX:+ZGenerational              # JDK 21-22 only; omit on JDK 17 and 23+
-XX:ZUncommitDelay=300          # the default, shown for clarity
```

## Conclusion

**For a standard deployment:** Use G1GC (vendor-tested, proven)

**For a low-latency requirement or period-end issues:** Evaluate ZGC after testing

**Key decision factor:** Is < 1ms pause time worth 3GB extra memory and adoption risk?

For most batch/report-heavy business applications: No. G1GC is the right choice.

For latency-critical instances: Yes, ZGC may be worth investigating.
