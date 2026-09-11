---
name: k8s-scoring-fix
description: "Analyzes and fixes Kubernetes resource-efficiency score issues on GKE / Kubernetes (helm and kustomize stacks). Covers: (1) Reading performance data (Dynatrace dashboard export, Prometheus/Grafana, kubectl top / metrics-server) to identify CPU over-provisioning, missing probes, restarts, and memory risks, (2) Diagnosing pod errors via kubectl — restarts, Exit Code 1, cloudsql-proxy race conditions, readiness probe 503s caused by the Spring Boot Elasticsearch health indicator, frontend probes failing due to OIDC redirects on Apache httpd with mod_auth_openidc, (3) Calculating CPU/memory actual vs requested vs namespace quota, (4) Implementing fixes in helm values.yaml or kustomize overlays (probes, CPU requests, memory requests, securityContext) and Spring Boot application.yml (hikari initialization-fail-timeout: -1, management.health.elasticsearch.enabled: false), (5) cloudsql-proxy exec probe with nc on alpine image (tcpSocket does not work on distroless), (6) Generating three output reports after fixes: technical reference, executive before/after summary with traffic-light KPIs, and plain-English status update, (7) Creating fix/k8s-scoring branch, kubectl patch to validate in cluster before committing. Use when user mentions 'k8s scoring', 'resource-efficiency score', 'performance score', 'resource score', 'right-size requests', 'pod restarts', 'missing probes', 'CPU over-provisioned', 'probe terminated redirects', 'actuator health 503', 'cloudsql-proxy probe', 'elasticsearch health check failed', 'securityContext PodSecurity warning', or wants to improve the resource efficiency of workloads on GKE or any Kubernetes cluster."
---

You are a Kubernetes Resource Optimization Expert. Your goal is to diagnose and fix resource-efficiency issues on Kubernetes clusters (validated on GKE), following a workflow used in production.

## WORKFLOW — 6 Phases

---

### PHASE 1 — Collect Performance Data

The skill is tool-agnostic. Use whatever the cluster already has, over a representative window (7 days recommended):

| Source | How |
|--------|-----|
| Dynatrace | Export the Kubernetes performance/workload dashboard to text (e.g. `performance-score-k8s.txt`) |
| Prometheus / Grafana | PromQL below (needs cAdvisor + kube-state-metrics) |
| metrics-server | `kubectl top pods -n <namespace> --containers` — point-in-time snapshot only: use it to confirm, not to size |

If your platform team publishes its own scoring formula, read it — its thresholds decide the reported number; `references/efficiency-metrics.md` gives the method behind the fix.

**Key data to extract (whatever the source):**
1. **pod_count** and namespace
2. **CPU Requests Utilization** (used mCores / total requested mCores / percentage)
3. **CPU Quota per container** — each service: actual usage vs request vs limit
4. **Health probes per container** — liveness / readiness / startup (0 = missing probe)
5. **Restarts per workload** — which pods restarted and how many times

**Prometheus equivalents (7-day window):**
```promql
# CPU used (cores) per container, 7-day average
avg_over_time((sum by (container) (rate(container_cpu_usage_seconds_total{namespace="<namespace>",container!="",container!="POD"}[5m])))[7d:5m])
# CPU requested (cores) per container
sum by (container) (kube_pod_container_resource_requests{namespace="<namespace>",resource="cpu"})
# Memory working set (bytes) per container, 7-day average (use max_over_time for OOM risk)
avg_over_time((sum by (container) (container_memory_working_set_bytes{namespace="<namespace>",container!="",container!="POD"}))[7d:5m])
# Memory requested (bytes) per container
sum by (container) (kube_pod_container_resource_requests{namespace="<namespace>",resource="memory"})
# Restarts over the window
sum by (pod, container) (increase(kube_pod_container_status_restarts_total{namespace="<namespace>"}[7d]))
```

**Probe table from the live cluster (same 0/1 layout as a dashboard export):**
```bash
kubectl get deploy -n <namespace> -o json | jq -r '.items[] | .metadata.name as $d
  | .spec.template.spec.containers[]
  | [$d, .name, (if .livenessProbe then 1 else 0 end), (if .readinessProbe then 1 else 0 end), (if .startupProbe then 1 else 0 end)]
  | @tsv'
```

---

### PHASE 2 — Kubernetes Investigation

Switch to the correct cluster and namespace first:
```bash
kubectl config use-context <cluster-context>
kubectl config set-context --current --namespace=<namespace>
# or, with kubectx/kubens: kubectx <cluster-context>; kubens <namespace>
```

Run these commands in parallel:
```bash
kubectl get pods -n <namespace>
kubectl get events -n <namespace> --sort-by='.lastTimestamp' | tail -30
kubectl top pods -n <namespace> --containers
kubectl describe pods -n <namespace> | grep -B5 "Exit Code\|OOMKilled\|Error\|Restart"
```

**For pods with restarts — get previous container logs:**
```bash
kubectl logs -n <namespace> <pod-name> -c <container-name> --previous | tail -30
```

**Common errors and their meaning:**
| Error | Root Cause |
|-------|-----------|
| `PSQLException: Connection to 127.0.0.1:5432 refused` | Race condition: app started before cloudsql-proxy sidecar was ready |
| `OOMKilled` | Memory limit too low — need to increase `resources.limits.memory` |
| `CrashLoopBackOff` | Repeated startup failure — check logs for root cause |
| Exit Code 1 | Application error at startup (DB connection, config, missing env var) |
| `Liveness probe failed` | App started but health endpoint not responding in time — add a `startupProbe` (preferred) or increase `initialDelaySeconds` |

**Check namespace quota:**
```bash
kubectl describe resourcequota -n <namespace>
```

---

### PHASE 3 — Resource Calculation

Use the metrics and sizing method from `references/efficiency-metrics.md`.

**Calculate per-service:**

```python
cpu_request_utilization = actual_cpu / cpu_requested        # < 10 % → over-provisioned
mem_request_utilization = actual_mem / mem_requested        # > 85 % → raise request, < 30 % → reduce

new_cpu_request = max(floor, p95_cpu * 1.5)                 # floor: 100m for JVM, 50m otherwise
new_mem_request = max_working_set * 1.2
freed_cpu       = sum(cpu_requested) - sum(new_cpu_request) # quota + node capacity given back
```

**Recommended CPU request values** (based on observed patterns):
- Java Spring Boot services (low-traffic prod): actual 5-10m → request `100m`
- Java Spring Boot services (higher traffic): actual 15-30m → request `200m`
- Frontend (nginx/angular): actual 2-5m → request `50m`
- Liquibase (migration only): actual 3m → request `50m`
- cloudsql-proxy: keep as-is (already small: 10-25m)

**Memory thresholds to watch:**
- > 85% of request → increase request (the pod is an early eviction candidate under node memory pressure; OOMKilled itself is governed by the **limit**)
- < 30% of request → consider reducing (wasteful)

---

### PHASE 4 — Implement Fixes

#### Fix A — Race condition: cloudsql-proxy not ready (Spring Boot + Hikari)

Add to `application.yml` in each Java service that uses cloudsql-proxy:

```yaml
spring:
  datasource:
    hikari:
      initialization-fail-timeout: -1   # retry indefinitely instead of crashing at startup
```

**Services to check:** any service with a `cloudsql-proxy` sidecar that had Exit Code 1 on startup.

**Indentation note:** some projects use 2-space indent (standard), others use 4-space indent (older projects). Match existing indentation exactly.

#### Fix B — Enable liveness and readiness probes

**Applies to: main application containers only.**

> ⚠️ **NEVER add probes to `cloudsql-proxy` sidecar** — see cloudsql-proxy section below.

**For Java Spring Boot services (4-space indent in `apis[]` list — shared helm chart stack):**
```yaml
    livenessProbe:
      httpGet:
        path: /actuator/health
        port: 8080
      initialDelaySeconds: 60
      periodSeconds: 30
      failureThreshold: 3
      timeoutSeconds: 5

    readinessProbe:
      httpGet:
        path: /actuator/health
        port: 8080
      initialDelaySeconds: 30
      periodSeconds: 10
      failureThreshold: 3
      timeoutSeconds: 5
```

**For Angular/React frontend (nginx/Apache, 2-space indent in `app:` block):**
```yaml
  livenessProbe:
    httpGet:
      path: /health-check   # NOT "/" — see note below
      port: 80
    initialDelaySeconds: 30
    periodSeconds: 30
    failureThreshold: 3
    timeoutSeconds: 5

  readinessProbe:
    httpGet:
      path: /health-check   # NOT "/" — see note below
      port: 80
    initialDelaySeconds: 15
    periodSeconds: 10
    failureThreshold: 3
    timeoutSeconds: 5
```

> ⚠️ **Frontend probe path**: Do NOT use `/` for Apache httpd + mod_auth_openidc frontends. The root path is protected by OIDC — kubelet probe gets a 302 redirect → `ProbeWarning: Probe terminated redirects`.
> Check `proxy.conf` for a dedicated health path:
> ```apache
> RewriteRule ^/health-check - [R=200,L]   # ← use this path for probes
> ```
> If the project has this rule, use `/health-check`. Otherwise look for any path with `OIDCUnAuthAction pass` or `RewriteRule` returning 200.

> 💡 **Spring Boot 2.3+:** prefer the probe groups — `/actuator/health/liveness` for liveness and `/actuator/health/readiness` for readiness (enabled automatically on Kubernetes, or via `management.endpoint.health.probes.enabled: true`). Liveness then ignores external dependencies (DB, Elasticsearch), so a down dependency cannot restart the pod. Use plain `/actuator/health` only on older Spring Boot versions.

**Verify `/actuator/health` is accessible:** check `application.yml` for security whitelist:
```yaml
# Should have one of these patterns:
- /actuator/**
- /health
```

#### ⚠️ Elasticsearch health indicator — CAUSES 503 ON PROBES

**Problem**: When adding `health` to `management.endpoints.web.exposure.include`, the `ElasticsearchHealthIndicator` is automatically included. If ES is not reachable (common in dev), `/actuator/health` returns HTTP 503 (DOWN) → readiness probe fails → liveness probe kills pod → restart loop.

**Symptom in logs:**
```
WARN | elasticsearch-rest-client-0-thread-1 | Elasticsearch health check failed
java.net.ConnectException: Connection refused
```
**Readiness probe event:** `Readiness probe failed: HTTP probe failed with statuscode: 503`

**Fix**: disable ES health indicator in each service's `application.yml`:
```yaml
management:
  health:
    elasticsearch:
      enabled: false
```

> ⚠️ **Check develop vs fix branch**: In `develop`, `management.endpoints.web.exposure.include` may be `"quartz"` only (health not exposed). When the fix branch adds `"quartz,health"` for probes, it automatically exposes ALL health indicators including ES. Always add `management.health.elasticsearch.enabled: false` alongside enabling the health endpoint.

> **Requires rebuild** when `application.yml` is baked into the image. Without a rebuild, the same settings can be passed as env vars in the deployment (Spring relaxed binding): `MANAGEMENT_HEALTH_ELASTICSEARCH_ENABLED=false`, `SPRING_DATASOURCE_HIKARI_INITIALIZATIONFAILTIMEOUT=-1`.

#### ⚠️ cloudsql-proxy probes — KNOWN LIMITATION

**DO NOT add probes to `cloudsql-proxy` sidecar** in projects using `gcr.io/cloudsql-docker/gce-proxy:1.17` (distroless image).

**Why it fails:**
- cloudsql-proxy binds to `127.0.0.1:5432` (loopback only)
- Kubelet `tcpSocket` probes connect to the **pod IP** (e.g. `<pod-ip>:5432`), NOT to `127.0.0.1`
- Connection is always refused → probe always fails
- `livenessProbe` would kill the container in a restart loop
- `readinessProbe` blocks RollingUpdate (new pods stuck at 1/2 Ready forever)

**What works with the alpine image:**
Projects using `gce-proxy:1.17-alpine` have `nc` in the image. For alpine images, an `exec` probe works:
```yaml
livenessProbe:
  exec:
    command: [nc, "-z", "127.0.0.1", "5432"]
```
This works because `exec` runs **inside** the container's network namespace (loopback accessible).

**For distroless images (`gce-proxy:1.17` without `-alpine`): do not add any probe.**

**Cloud SQL Auth Proxy v2** (`cloud-sql-proxy`, replaces the deprecated v1 `gce-proxy`) has built-in health endpoints: start it with `--health-check --http-address=0.0.0.0` and use `httpGet` probes on port `9090`, paths `/startup`, `/liveness`, `/readiness`. Prefer migrating to v2 over probing v1.

**Emergency fix if wrong probe was deployed:**
```bash
# Remove readiness probe from cloudsql-proxy (container index 1)
kubectl patch deployment <deployment> -n <namespace> --type=json \
  -p='[{"op":"remove","path":"/spec/template/spec/containers/1/readinessProbe"}]'

# Remove liveness probe from cloudsql-proxy
kubectl patch deployment <deployment> -n <namespace> --type=json \
  -p='[{"op":"remove","path":"/spec/template/spec/containers/1/livenessProbe"}]'
```

#### Fix E — Frontend securityContext: FULL non-root on Apache httpd + mod_auth_openidc (validated)

> ✅ **VERSION-DEPENDENT — check your image first (verified empirically in dev on an httpd +
> mod_auth_openidc base image):** On a recent image the frontend can run **fully non-root WITHOUT
> any Dockerfile/image rebuild**.
>
> The older "requires image change" guidance was correct *for older builds* of such images —
> those hard-coded `Listen 80`, so non-root genuinely needed a rebuild (Apache on 8080 +
> setcap/USER). **Do not assume either way — confirm the image first** (see check below).

**Why it works on recent images:** the image's `httpd.conf` listens on `Listen ${PORT}` (a variable).
Set env `PORT=8080` (a non-privileged port) and Apache no longer needs `CAP_NET_BIND_SERVICE` → you can
drop ALL capabilities and run as an arbitrary non-root uid. No `USER` line, no `Listen 8080`
override, no `NET_BIND_SERVICE` needed.

**Confirm the image supports `${PORT}` before relying on this** (exec into the running pod):
```bash
kubectl exec -n <namespace> <pod> -- grep -rn "^Listen" /usr/local/apache2/conf/httpd.conf
# "Listen ${PORT}"  → PORT-env trick works, full non-root without rebuild (this section)
# "Listen 80"       → older image: non-root needs a Dockerfile rebuild (Apache on 8080 + setcap/USER)
```

**Validated full hardening** (kustomize base `deployment-app.yaml`, 2-space indent):
```yaml
spec:
  template:
    metadata:
      annotations:
        container.apparmor.security.beta.kubernetes.io/<container>: runtime/default   # Kubernetes < 1.30 only
    spec:
      automountServiceAccountToken: false
      securityContext:
        runAsNonRoot: true
        runAsUser: 1001
        seccompProfile:
          type: RuntimeDefault
      volumes:
        - name: tmp
          emptyDir: {}
        - name: apache-logs
          emptyDir: {}
      containers:
        - name: <container>
          env:
            - name: PORT
              value: "8080"          # ← Apache listens here; enables non-root
          securityContext:
            runAsNonRoot: true
            runAsUser: 1001
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities:
              drop: ["ALL"]
            seccompProfile:
              type: RuntimeDefault
          volumeMounts:
            - { name: tmp, mountPath: /tmp }
            - { name: apache-logs, mountPath: /usr/local/apache2/logs }
          ports:
            - containerPort: 8080    # was 80
          livenessProbe:  { httpGet: { path: /health-check, port: 8080 } }
          readinessProbe: { httpGet: { path: /health-check, port: 8080 } }
```
On Kubernetes ≥ 1.30 drop the annotation and set `appArmorProfile: { type: RuntimeDefault }` in the pod or container `securityContext` (the annotation is deprecated).

Then in the **Service**: `targetPort: 8080` (keep `port: 80` so the Ingress backend is unchanged).

**`readOnlyRootFilesystem: true` is safe** with mod_auth_openidc when it uses an
**shm (shared-memory) cache**, not a file cache. Only writable paths Apache needs are emptyDir on
`/tmp` and `/usr/local/apache2/logs`.

**Benign warnings (not errors):**
- `[ssl:warn] AH01873: Session Cache is not configured` — pre-existing, TLS is terminated at the Ingress.
- `[auth_openidc:warn] ... X-Forwarded-Host/Proto not found in request` — only appears when you hit
  the pod directly (curl/kubelet probe). Real traffic via the ingress controller adds those headers. Test artifact.

**Mandatory validation BEFORE writing repo YAML** — deploy a throwaway clone (do NOT patch the live
app), watch rollout, and READ THE FULL POD LOGS for real errors (`[*:error]/[*:crit]/[*:emerg]`,
`permission denied`, `read-only file system`, `address already in use`):
```bash
# clone the live deployment spec into a test deploy named <app>-sctest with the hardening + PORT=8080,
# point probes at 8080, apply, then:
kubectl rollout status deploy/<app>-sctest -n <namespace> --timeout=70s
kubectl exec -n <namespace> <pod> -- sh -c 'id; echo PORT=$PORT; curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8080/health-check'
kubectl logs -n <namespace> <pod> | grep -iE "\[[a-z0-9_]+:(error|crit|emerg|alert)\]|permission denied|read-only file system"
kubectl delete deploy/<app>-sctest -n <namespace>   # clean up
```
If the namespace enforces **PodSecurity `restricted` + ResourceQuota**, the test pod must set the
full securityContext AND cpu/memory requests, else it is rejected.

> ⚠️ **Base-image bump (e.g. `<old-tag>` → `<new-tag>`) may ONLY be validatable via CI**, never by
> pulling the image in-cluster: if an admission policy **blocks Docker Hub registries** (only an
> internal registry such as `<region>-docker.pkg.dev/<project-id>/...` allowed), change the `FROM`,
> let CI build the app image (it pulls Docker Hub at build time) and push to the internal registry,
> then deploy to dev.

> ⚠️ **Never trust a sibling repo's manifest as a non-root reference.** A local repo may show
> `runAsNonRoot:true` while the *deployed* pod runs as root (stale/unmerged repo). Always check the
> live state: `kubectl get deploy <d> -n <namespace> -o jsonpath='{.spec.template.spec.securityContext}'`.

#### Fix C — Reduce CPU requests (values.yaml)

In `resources.requests.cpu` for each main app container. Use the Python script in `assets/patch-values.py` as a template.

#### Fix D — Adjust memory requests (values.yaml)

- If actual memory > 85% of request: increase request by 50% (e.g., 1024Mi → 1536Mi)
- Keep limits unchanged

---

### PHASE 5 — Branch, Diff, Commit

#### Create dedicated fix branch

Branch name: **always `fix/k8s-scoring`** (no env suffix) — one branch per repo, all envs included.

```bash
cd <project-stack-directory>
git checkout develop
git pull origin develop
git checkout -b fix/k8s-scoring
```

If changes were accidentally made on wrong branch:
```bash
git stash
git checkout develop && git pull origin develop
git checkout -b fix/k8s-scoring
git stash pop
```

> ⚠️ Do NOT run `git pull --rebase origin develop` while on the fix branch — it may drop your commits if they conflict with develop.

#### Apply changes

**Option A — Using the patch script (recommended for values.yaml):**
```bash
python3 assets/patch-values.py helm/values-<env>.yaml --dry-run   # preview
python3 assets/patch-values.py helm/values-<env>.yaml             # write
```

**Option B — Manual Edit tool** for `application.yml` changes (use Edit, not Write).

#### Preview with kubectl diff

```bash
# Save current deployments
kubectl get deployment -n <namespace> <svc1> <svc2> ... -o yaml > /tmp/current.yaml

# Generate patched manifests (use assets/generate-patched-manifests.py)
python3 assets/generate-patched-manifests.py /tmp/current.yaml /tmp/patched.yaml

# Preview changes
kubectl diff -n <namespace> -f /tmp/patched.yaml
```

Expected diff output — for each service:
```diff
+        livenessProbe:
+          httpGet: {path: /actuator/health, port: 8080}
+          initialDelaySeconds: 60
         resources:
           requests:
-            cpu: 1000m
+            cpu: 100m
```

#### Commit and push

```bash
git add helm/values-<env>.yaml <service>/src/main/resources/config/application.yml
git commit -m "fix: k8s scoring"
git push origin fix/k8s-scoring
```

**Note:** fix all environments in the same branch (`fix/k8s-scoring`) — dev, staging, preprod, prod all in the same commit or separate commits per env.

#### Stack: Kustomize (not helm)

For kustomize projects, changes go in `k8s/overlays/<env>/deployment-*.yaml`:
```bash
# Resources go in overlay (env-specific patch)
k8s/overlays/prod/deployment-api.yaml  ← cpu/memory requests
k8s/overlays/preprod/deployment-api.yaml

# Probes go in base (all envs inherit)
k8s/base/deployment-api.yaml  ← livenessProbe / readinessProbe on main container
```

Each repo is independent — create `fix/k8s-scoring` branch in each:
```bash
for repo in <app>-api <app>-worker <app>-admin; do
  cd /path/to/$repo
  git checkout -b fix/k8s-scoring
done
```

---

## COMMON PATTERNS BY STACK TYPE

### Stack: Shared helm chart (`apis[]` + `app:` layout)

Typical `values-*.yaml` structure:
```yaml
apis:           # list of backend services (4-space indent)
  - name: <service>
    resources:
      limits: { cpu: 4000m, memory: 4Gi }
      requests: { cpu: 1000m, memory: 1Gi }   # ← fix here
    livenessProbe: { }                          # ← fix here
    readinessProbe: { }                         # ← fix here

app:            # frontend (2-space indent)
  resources:
    requests: { cpu: 500m, memory: 512Mi }      # ← fix here
  livenessProbe: { }                            # ← fix here
  readinessProbe: { }                           # ← fix here
```

### Pipeline structure (shared CI/CD deployment template)

```
stages: build → check_manifests_dev → deploy_dev → check_manifests_staging → deploy_staging → promote_images → check_manifests_prod → deploy_prod
```

- `check_manifests_*`: `helm upgrade --install --dry-run` (auto, validates YAML)
- `deploy_*`: `helm upgrade --install` (manual trigger)
- `deploy_prod` only on `master` branch

### Spring Boot Hikari datasource (application.yml)

Look for the `hikari:` block under `spring.datasource`:
```yaml
spring:
  datasource:
    hikari:
      initialization-fail-timeout: -1   # ADD THIS
      maximum-pool-size: ${...}
      data-source-properties: ...
```

---

## PHASE 6 — Generate Reports

After completing fixes, produce three output files in the project root directory:

### File 1: `k8s-scoring-fixes-technical.md`
Full technical reference for the team. Include:
- Per-service CPU before/after table (requested vs actual)
- Exact code changes made (file paths, YAML snippets)
- Root cause explanation for each fix
- Known limitations table (what cannot be fixed without image rebuild, and why)
- Known noise table (log messages that are not actionable, with explanation)

### File 2: `k8s-scoring-executive-summary.md`
Business owner presentation. Structure:
1. **One-paragraph context** — what the KPIs measure and why they matter (no jargon)
2. **Before/After table per project** — 4–5 rows, traffic-light status (🔴🟡🟢), no raw numbers in the table body
3. **Numbered footnotes below each table** — for technical nuances only (keep table clean)
4. **Open Items table** — what remains and what is needed to fix it
5. **Monitoring Noise table** — pre-existing log messages that look like errors but are not

**Traffic light rules:**
- 🔴 = issue present, no mitigation
- 🟡 = partial / acceptable / risk present
- 🟢 = resolved or within target

### File 3: `k8s-scoring-status.md`
Executive one-pager ("two words" summary). ~150 words max. Plain English, no KPI names, no numbers, no acronyms. Structure:
- One sentence stating which project(s) have been reviewed
- 3–4 bullet points: what was wrong, now fixed (in business language)
- One closing sentence: current state is healthy, no open risks
- One sentence for any backlog items (if any): "minor, no user impact, planned for next sprint"

---

## EXPECTED IMPACT

| Fix | Before | After |
|-----|--------|-------|
| CPU requests 500m→100m (actual 5-10m) | ~1–2 % request utilization | ~5–10 % (at the JVM floor), ~80 % of requested CPU freed |
| Enable probes | 0 % probe coverage (ops risk) | 100 % of main containers |
| Fix race condition | 1 restart/deploy | 0 restarts |
| Memory request aligned | < 30 % or > 85 % utilization | 50–85 % |

---

## IMPORTANT CONSTRAINTS

- Never push directly to `master` or `develop` — always use `fix/k8s-scoring` branch
- Always test in `dev` first before merging to `prod`
- `initialization-fail-timeout: -1` in `application.yml` requires a full image rebuild (CI/CD) — or set `SPRING_DATASOURCE_HIKARI_INITIALIZATIONFAILTIMEOUT=-1` as a deployment env var
- `kubectl diff` warnings about PodSecurity are pre-existing, not caused by resource changes — ignore them
- Short commit messages: `fix: k8s scoring` (no env suffix)
- **`cloudsql-proxy` probe**: only add if image is alpine-based (has `nc`). Distroless `gce-proxy:1.17` cannot be probed — tcpSocket connects to pod IP, not to 127.0.0.1 where proxy listens
- **`git pull --rebase origin develop`** while on fix branch may drop commits if there are conflicts — avoid it
- **kubectl patch for emergency fix** (without waiting for pipeline):
  ```bash
  kubectl patch deployment <name> -n <namespace> --type=json \
    -p='[{"op":"remove","path":"/spec/template/spec/containers/1/livenessProbe"}]'
  ```
- **ExternalSecret with empty `secretStoreRef.name`**: fix by adding `name: <secret-store-name>` (the SecretStore/ClusterSecretStore it should read from) to `spec.secretStoreRef` in the ExternalSecret manifest
