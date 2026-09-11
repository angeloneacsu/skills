# Kubernetes Resource-Efficiency Metrics

Generic metrics for judging how well workloads are sized, and a method for picking new
requests. If your platform team publishes its own score, use their formula for the number
they report and this file for the method behind the fix.

Inputs can come from any source (Dynatrace export, Prometheus/Grafana, `kubectl top` /
metrics-server) — see SKILL.md Phase 1. Use a representative window (7 days, including a
busy period); a point-in-time `kubectl top` is for confirming, not for sizing.

---

## 1. Metrics

```
cpu_request_utilization = actual_cpu_usage / cpu_requested        # per container and namespace total
mem_request_utilization = actual_mem_usage / mem_requested        # working set, not RSS/cache
probe_coverage          = main containers with liveness AND readiness / all main containers
restarts                = container restarts over the window                     # target: 0
quota_headroom          = (quota_hard - quota_used) / quota_hard  # from `kubectl describe resourcequota`
```

Exclude sidecars that cannot be probed (e.g. distroless cloudsql-proxy v1) from `probe_coverage`.

### Reading them

| Metric | Healthy | Act when |
|---|---|---|
| CPU request utilization | 30–80 % of request at p95 | < 10 % → over-provisioned; > 100 % sustained → throttling / noisy-neighbour risk |
| Memory request utilization | 50–85 % of request at max | > 85 % → raise request (eviction candidate under node pressure); < 30 % → reduce |
| Memory vs **limit** | max working set < ~90 % of limit | close to limit → OOMKilled risk: raise the **limit** (or tune JVM heap) |
| Probe coverage | 100 % of main containers | any main container without liveness + readiness |
| Restarts | 0 over the window | any — read `--previous` logs before touching resources |
| Quota headroom | enough for one extra replica of the largest workload during a rolling update | rollouts stuck on `exceeded quota` |

Over-requesting is the common failure: it reserves node capacity nobody uses and burns
namespace quota, so rolling updates and HPA scale-outs hit `exceeded quota` first.

---

## 2. Sizing method

```
cpu_request = max(floor, p95_cpu_usage × 1.5)            # floor: 50m, 100m for JVM apps
mem_request = max_working_set × 1.2                       # steady-state + margin
mem_limit   = max(mem_request, max_working_set × 1.5)     # room for spikes; OOMKilled is decided here
```

- Size per container, then sum per pod × replicas and check it fits the quota with room for
  the rolling-update surge (`maxSurge`).
- **JVM:** the container limit must cover heap + metaspace + code cache + thread stacks +
  direct buffers. Prefer `-XX:MaxRAMPercentage` (e.g. 70–75) over a fixed `-Xmx`, so heap
  follows the limit.
- CPU limits are optional; if set, keep them well above the request (JVM startup and GC are
  bursty — tight limits cause throttling and slow probes).
- Change one thing at a time and re-measure over the next window.

### Quick CPU request sizing guide

Based on observed usage in low-traffic production environments:

| Actual usage | Recommended request | Notes |
|-------------|--------------------|-|
| < 10m | 50–100m | Small buffer enough |
| 10–30m | 100–150m | Standard Java service |
| 30–100m | 150–250m | Medium load |
| > 100m | actual × 2 | Keep 2× buffer |

**Never set request < 50m for JVM apps** (JVM needs startup headroom even if steady-state is low).

### Worked example

```
6 pods, CPU requested 750m each (4500m total), actual p95 5m each (30m total)
cpu_request_utilization = 30 / 4500 ≈ 0.7 %   ← over-provisioned
new request per pod     = max(100m, 5m × 1.5) = 100m  → 600m total
cpu_request_utilization = 30 / 600 = 5 %      ← still low, but at the JVM floor
freed                   = 4500m − 600m = 3900m of requests (quota + node capacity)
```

---

## 3. Health probe table

Typical layout of a "health probes per container" table (dashboard export, or the `kubectl | jq`
command in SKILL.md Phase 1):
```
<deployment>  <container>  <liveness_count>  <readiness_count>  <startup_count>
```

- `0` = probe NOT configured → fix needed
- `1` = probe configured → OK
