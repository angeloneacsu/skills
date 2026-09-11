# WildFly Performance Configuration Guide

## Table of Contents
1. Overview
2. DataSource Configuration
3. Thread Pool Tuning
4. HTTP Connector Configuration
5. Session Management
6. Deployment Configuration
7. Monitoring & Metrics
8. Performance Checklist

## 1. Overview

This guide covers WildFly 26 through current releases (WildFly 27+ is Jakarta EE 10; WildFly 40+ is Jakarta EE 11). HTTP is served by the **Undertow** subsystem on threads of the **io** subsystem worker; datasources are **IronJacamar** pools; security is **Elytron**. It focuses on the most impactful configurations for database-intensive, batch- and report-heavy applications.

Subsystem namespaces in the shipped `standalone.xml` (use the ones your server version ships; the elements below are the same unless noted):

| Subsystem | WildFly 26.1 | WildFly 41 |
|---|---|---|
| io | `urn:jboss:domain:io:3.0` | `urn:jboss:domain:io:4.0` |
| undertow | `urn:jboss:domain:undertow:12.0` | `urn:jboss:domain:undertow:15.0` |
| datasources | `urn:jboss:domain:datasources:7.0` | `urn:jboss:domain:datasources:7.2` |
| ejb3 | `urn:jboss:domain:ejb3:9.0` | `urn:jboss:domain:ejb3:10.0` |
| deployment-scanner | `urn:jboss:domain:deployment-scanner:2.0` | `urn:jboss:domain:deployment-scanner:2.0` |

**Performance-critical components:**
- DataSource connection pooling (database access)
- io worker threads that run HTTP requests (request handling)
- Deployment settings (module loading and initialization)
- Session management (memory per user)
- Buffer pools (network I/O efficiency)

## 2. DataSource Configuration

DataSources manage database connections. Proper configuration is critical for database-heavy applications.

### Reference Configuration

Annotated example (MySQL; `datasources:7.1+` syntax, i.e. WildFly 30+; placeholders in `<...>`):

```xml
<datasources>
  <datasource jndi-name="java:/datasources/<datasource>" pool-name="<datasource>" statistics-enabled="true">
    <connection-url>jdbc:mysql://<db-host>:3306/<database></connection-url>

    <!-- MySQL Connector/J properties -->
    <connection-property name="cachePrepStmts">true</connection-property>
    <connection-property name="prepStmtCacheSize">500</connection-property>
    <connection-property name="prepStmtCacheSqlLimit">2048</connection-property>
    <connection-property name="useServerPrepStmts">true</connection-property>
    <connection-property name="rewriteBatchedStatements">true</connection-property>

    <driver>mysql</driver>

    <!-- CONNECTION POOL SETTINGS (CRITICAL) -->
    <pool>
      <!-- Minimum connections to maintain -->
      <min-pool-size>0</min-pool-size>

      <!-- Maximum connections (IMPORTANT: this node's share of the DB connection budget) -->
      <max-pool-size>100</max-pool-size>

      <!-- Don't prefill pool on startup (let it grow on demand) -->
      <prefill>false</prefill>

      <!-- Which connections to destroy when a connection error is detected (this is the default) -->
      <flush-strategy>FailingConnectionOnly</flush-strategy>
    </pool>

    <security user-name="${env.DB_USER}" password="${env.DB_PASSWORD}"/>

    <!-- CONNECTION VALIDATION -->
    <validation>
      <!-- MySQL-specific validation -->
      <valid-connection-checker class-name="org.jboss.jca.adapters.jdbc.extensions.mysql.MySQLValidConnectionChecker"/>

      <!-- Don't test every connection on checkout (overhead) ... -->
      <validate-on-match>false</validate-on-match>

      <!-- ... test idle connections in the background instead, every 10 minutes -->
      <background-validation>true</background-validation>
      <background-validation-millis>600000</background-validation-millis>

      <!-- MySQL-specific exception handling (marks broken connections as unusable) -->
      <exception-sorter class-name="org.jboss.jca.adapters.jdbc.extensions.mysql.MySQLExceptionSorter"/>
    </validation>

    <!-- TIMEOUT SETTINGS -->
    <timeout>
      <!-- How long a request waits for a free connection before failing (default 30000) -->
      <blocking-timeout-millis>30000</blocking-timeout-millis>

      <!-- Remove connections idle longer than this -->
      <idle-timeout-minutes>15</idle-timeout-minutes>

      <!-- Query timeout in seconds (kill long-running statements) -->
      <query-timeout>300</query-timeout>

      <!-- Retries when creating a physical connection fails -->
      <allocation-retry>3</allocation-retry>
      <allocation-retry-wait-millis>500</allocation-retry-wait-millis>
    </timeout>

    <!-- PERFORMANCE OPTIONS -->
    <statement>
      <!-- Cache prepared statements per connection in the pool -->
      <prepared-statement-cache-size>50</prepared-statement-cache-size>
      <share-prepared-statements>true</share-prepared-statements>
    </statement>
  </datasource>
</datasources>
```

**Version differences (verified against the shipped XSDs):**
- WildFly 26–29 (`datasources:7.0`) takes credentials as child elements, `<security><user-name>...</user-name><password>...</password></security>`, and boolean flags such as `share-prepared-statements` are empty presence elements (`<share-prepared-statements/>`). From `datasources:7.1` (WildFly 30+) credentials are attributes of `<security>` and those flags are `true`/`false`.
- The XML element is `<driver>`; `driver-name` is the name of the same setting in the management model / CLI. Likewise `<blocking-timeout-millis>` is the CLI attribute `blocking-timeout-wait-millis`.
- `<xa-pool>` exists only inside `<xa-datasource>`; a plain `<datasource>` takes `<pool>`.
- `${env.DB_PASSWORD}` reads an environment variable; prefer an Elytron credential store (`<credential-reference>`) for real secrets.

### DataSource Tuning Guide

#### Connection Pool Sizing

**Minimum pool size (min-pool-size):**
- Start with 0 (lazy initialization)
- Connections created on demand
- Reduces startup time and memory usage
- Suitable for variable load

**Maximum pool size (max-pool-size):**
- **CRITICAL: must fit the database connection budget.** `max_connections` is a limit of the database *instance*, shared by every pool on every app node (plus admin connections and any other client). The WildFly default is 20.
- Formula: `Σ (max-pool-size of every datasource on every node) ≤ max_connections − reserved/admin − other clients`
- Splitting one pool into several datasources does not add capacity; it only partitions the same budget (useful to keep reports from starving interactive traffic).
- The examples in this skill assume a share of 100 connections per WildFly node.

**Example: one node, 2 datasources:**
```xml
<!-- Primary datasource -->
<max-pool-size>50</max-pool-size>

<!-- Secondary datasource (e.g. reporting) -->
<max-pool-size>50</max-pool-size>

<!-- Total = 100 for this node; × number of nodes must fit max_connections -->
```

#### Idle Connection Handling

**Idle timeout (idle-timeout-minutes: 15):**
- Removes connections unused for 15 minutes
- Saves memory, reduces dormant connections
- 15 minutes is reasonable for staging/production
- Keep it below any idle-connection timeout of the database or of firewalls/proxies in between

**Validation settings:**
- `validate-on-match=false`: Don't test every connection on checkout (overhead)
- `background-validation=true` + `background-validation-millis=600000`: periodically test idle connections
- Detects broken connections due to network issues

#### Query Timeout

**Query timeout (query-timeout: 300 seconds):**
- Maximum 5 minutes per query execution
- Prevents long-running queries from holding resources
- Period-end report queries may approach this limit
- Consider 600s (10 min) during period-end if needed
- Too long = resource waste; too short = failed operations

#### Connection Leak Detection

**Blocking timeout behavior:**
- `blocking-timeout-millis: 30000` = wait 30 seconds for a connection
- If the wait expires, the application gets an SQLException wrapping IronJacamar's `IJ000453`
- Application must handle retry logic
- Indicates pool too small, connections held too long (slow queries), or a connection leak

**Pool exhaustion in `server.log`** (IronJacamar message IDs):
```
IJ000453: Unable to get managed connection for java:/datasources/<datasource>
IJ000655: No managed connections available within configured blocking timeout (30000 [ms])
```
Check for:
1. Too many concurrent requests
2. Slow database queries holding connections
3. Connection leaks (not returned after use)

A thread dump shows the waiting threads: they sit in `SemaphoreConcurrentLinkedDequeManagedConnectionPool.getConnection` (IronJacamar's default pool), parked in a timed semaphore acquire.

**Finding leaks:** switch the pool implementation to the leak dumper on a test system (then reload):
```bash
/subsystem=datasources/data-source=<datasource>:write-attribute(name=mcp, value=org.jboss.jca.core.connectionmanager.pool.mcp.LeakDumperManagedConnectionPool)
```
Leaked connections are then logged as `IJ000616: Leak detected in pool` with the stack trace of the code that obtained them (optionally also to a file with `-Dironjacamar.leaklog=<file>`). The `-Dironjacamar.mcp` system property has no effect on WildFly datasources; use the `mcp` attribute.

### DataSource Performance Properties

MySQL Connector/J connection properties (see the Connector/J documentation for your driver version):

```xml
<connection-property name="cachePrepStmts">true</connection-property>
<!-- Cache prepared statements in the driver -->

<connection-property name="prepStmtCacheSize">500</connection-property>
<!-- Cache up to 500 prepared statements per connection -->

<connection-property name="rewriteBatchedStatements">true</connection-property>
<!-- Rewrite JDBC batches into multi-row statements (bulk insert/update throughput) -->

<connection-property name="useServerPrepStmts">true</connection-property>
<!-- Use server-side prepared statements (reduces parsing overhead) -->
```

Enable the driver cache or the pool's `prepared-statement-cache-size`, not necessarily both; measure. Avoid Connector/J `autoReconnect`: with a pool, validation plus the exception sorter is the way to replace broken connections.

## 3. Thread Pool Tuning

WildFly uses thread pools for different purposes. Key pools:

### io Worker (HTTP request threads)

Undertow listeners run on an XNIO worker defined in the io subsystem (the listener's `worker` attribute, `default` by default). The worker has two pools:

- **`io-threads`** — non-blocking I/O threads (`default I/O-N` in thread dumps). Default: 2 × CPU count. Rarely changed.
- **`task-max-threads`** — blocking task threads that run servlet/JAX-RS code and everything it calls synchronously, including JDBC (`default task-N`). Default: 16 × CPU count (capped by the file-descriptor limit). This is the pool that caps concurrent requests; it replaces the `maxThreads` of the old JBoss Web connector, which does not exist in WildFly.

The values chosen at boot are logged as `WFLYIO001: Worker 'default' has auto-configured to <n> IO threads with <n> max task threads based on your <n> available processors`.

```xml
<subsystem xmlns="urn:jboss:domain:io:4.0">   <!-- io:3.0 on WildFly 26 -->
  <worker name="default" io-threads="16" task-max-threads="128"/>
</subsystem>
```

```bash
/subsystem=io/worker=default:write-attribute(name=task-max-threads, value=128)
/subsystem=io/worker=default:write-attribute(name=io-threads, value=16)
:reload
```

**Sizing guidance:**
- Start from the defaults (8 vCPU → 16 io threads, 128 task threads) and change only on evidence
- Request threads that need the database are capped by the datasource pool: with `max-pool-size=100`, 128 task threads mostly queue on the pool under load — raising `task-max-threads` then adds waiting threads, not throughput
- Watch `busy-task-thread-count` and `queue-size` (below); if busy ≈ `max-pool-size` of the worker and the queue grows while CPU and the DB pool have headroom, raise `task-max-threads`
- `jvm-flag-recommender.py` prints io worker and EJB thread-pool values derived from vCPU and the datasource pool size; its report states the rule it used

### EJB Thread Pool (if the application uses EJB)

The EJB3 `default` thread pool runs `@Asynchronous` methods, remote EJB invocations and timers. Local EJB calls made from a request run on the caller's (`default task-N`) thread.

```xml
<subsystem xmlns="urn:jboss:domain:ejb3:10.0">   <!-- ejb3:9.0 on WildFly 26 -->
  <!-- other ejb3 elements omitted -->
  <thread-pools>
    <thread-pool name="default">
      <max-threads count="10"/>   <!-- shipped value -->
      <keepalive-time time="60" unit="seconds"/>
    </thread-pool>
  </thread-pools>
</subsystem>
```

**Sizing:** raise `max-threads` only if many `@Asynchronous`/timer tasks queue. Stateless bean instances come from `slsb-strict-max-pool`, shipped with `derive-size="from-worker-pools"` (sized from the io worker's max threads) and `instance-acquisition-timeout="5"` minutes, so it follows `task-max-threads`.

Application-level async work should use the managed executors of the `ee` subsystem (Jakarta Concurrency) rather than unmanaged threads.

## 4. HTTP Connector Configuration

### Listener and Statistics

```xml
<subsystem xmlns="urn:jboss:domain:undertow:15.0" statistics-enabled="true">   <!-- undertow:12.0 on WildFly 26 -->
  <byte-buffer-pool name="default"/>
  <server name="default-server">
    <http-listener name="default" socket-binding="http" worker="default" buffer-pool="default"
                   no-request-timeout="60000" max-post-size="10485760"
                   record-request-start-time="true"/>
    <host name="default-host" alias="localhost">
      <filter-ref name="gzipFilter" predicate="exists['%{o,Content-Type}'] and regex[pattern='(?:application/javascript|text/css|text/html|text/xml|application/json)(;.*)?', value=%{o,Content-Type}, full-match=true] and max-content-size[10240]"/>
    </host>
  </server>
  <filters>
    <gzip name="gzipFilter"/>
  </filters>
</subsystem>
```

(Only the elements discussed here are shown; keep the rest of your subsystem as shipped.) `max-connections` is unset (unlimited) by default; setting it bounds concurrent connections per listener.

### Network Buffer Pools

- WildFly 27+ ships `<byte-buffer-pool name="default"/>` in the undertow subsystem; the listener's `buffer-pool` attribute (default `default`) refers to it. Attributes: `buffer-size`, `direct`, `max-pool-size`, `thread-local-cache-size`, `leak-detection-percent`.
- WildFly 26 ships `<buffer-pool name="default"/>` in the io subsystem instead (`buffer-size`, `buffers-per-slice`, `direct-buffers`); it is deprecated in favour of the undertow `byte-buffer-pool`.
- Left unset, buffer size and direct/heap are derived from the max heap (16KB direct buffers for any heap ≥ 128MB), which suits most servers.

**Tuning:**
- Direct buffers: Better for I/O, but consume off-heap memory (not bounded by `-Xmx`)
- Heap buffers: Managed by GC, more flexible
- Change buffer size only on evidence (e.g. very large responses and measured copy overhead)

### Keep-Alive Configuration

| Listener attribute | Meaning | Default |
|---|---|---|
| `no-request-timeout` | How long an idle (keep-alive) connection stays open, ms | 60000 |
| `read-timeout` / `write-timeout` | Socket read/write timeout, ms | unset on WildFly 26; 90000 from WildFly 28 |
| `tcp-keep-alive` | TCP `SO_KEEPALIVE` | unset |
| `always-set-keep-alive` | Always send the `Connection: keep-alive` header | true |

**Considerations:**
- Long timeout: Reduces connection overhead but holds resources
- Short timeout: Reduces resource usage but more reconnects
- Behind a load balancer, make `no-request-timeout` longer than the LB's idle timeout towards the backend, so the LB closes idle connections first and never reuses one WildFly has already closed
- Long-running report generation is limited by the load balancer's request timeout and `read-timeout`/`write-timeout`, not by the keep-alive timeout

### HTTP Compression

Undertow compresses through a `gzip` filter referenced from the host (see the XML above). The predicate decides which responses are compressed: here, text-like content types, and only when `Content-Length` is known and **greater** than 10KB (`max-content-size[N]` is true above N bytes; it is false for chunked responses without `Content-Length`).

**Performance impact:**
- Reduces bandwidth (good for network-bound)
- Adds CPU overhead (bad for CPU-bound)
- Enable if users are WAN-based, disable if same datacenter

## 5. Session Management

### Session Timeout

Session timeout and cookie settings are per application, in the application's `WEB-INF/web.xml` (Servlet spec), not in `standalone.xml`:

```xml
<session-config>
  <session-timeout>30</session-timeout>  <!-- minutes -->
  <cookie-config>
    <http-only>true</http-only>
    <secure>true</secure>  <!-- Only send over HTTPS -->
  </cookie-config>
  <tracking-mode>COOKIE</tracking-mode>
</session-config>
```

**Guidance:**
- 30 minutes: Standard user session
- Adjust based on typical user session length
- Shorter = less memory, more re-login disruption
- Longer = more memory, better user experience

### Distributed Session Replication

Clustering configuration (distributable sessions, Infinispan) is out of scope for this skill.

## 6. Deployment Configuration

### Deployment Timeout

The deployment timeout must accommodate large module loading:

```xml
<subsystem xmlns="urn:jboss:domain:deployment-scanner:2.0">
  <deployment-scanner path="deployments"
                      relative-to="jboss.server.base.dir"
                      scan-enabled="true"
                      scan-interval="5000"
                      deployment-timeout="900"/>  <!-- seconds; default 600 -->
</subsystem>
```

**Timeout considerations:**
- `deployment-timeout` (seconds, default **600**) applies to deployments picked up by the scanner
- Large EAR/WAR: ~300-500MB (can take minutes to load)
- WildFly needs time to parse, validate, initialize
- Too short: Deployment fails even though it would eventually succeed
- Separately, management operations (including deployments at boot) are bounded by the system property `jboss.as.management.blocking.timeout` (seconds, default **300**); raise it, e.g. `-Djboss.as.management.blocking.timeout=900`, when boot of a large deployment times out

**Recommended: keep ≥ 600 seconds; raise both values for very large deployments.** The validator's `--min-deployment-timeout` sets the floor it checks against.

## 7. Monitoring & Metrics

### Key Performance Metrics

**Collect regularly for baseline and comparison:**

1. **DataSource Metrics** (need `statistics-enabled=true` on the datasource; changing it needs a reload):
   ```
   /subsystem=datasources/data-source=<datasource>/statistics=pool:read-resource(include-runtime=true)
   JMX: jboss.as:subsystem=datasources,data-source=<datasource>,statistics=pool
   - ActiveCount: connections currently open (in use + idle)
   - InUseCount: connections currently in use
   - AvailableCount: connections that can still be obtained
   - MaxUsedCount: high-water mark of connections in use
   - WaitCount: requests that had to wait for a connection (should be 0)
   - AverageBlockingTime / MaxWaitTime: how long requests wait (ms)
   ```

2. **HTTP Listener Metrics** (need `statistics-enabled=true` on the undertow subsystem):
   ```
   /subsystem=undertow/server=default-server/http-listener=default:read-resource(include-runtime=true)
   - request-count: Total requests handled
   - processing-time / max-processing-time: ns; stay 0 unless record-request-start-time=true
   - error-count: responses with status 500
   - bytes-sent / bytes-received
   ```

3. **Worker Thread Pool Metrics:**
   ```
   /subsystem=io/worker=default:read-resource(include-runtime=true)
   - busy-task-thread-count: estimate of busy task threads
   - max-pool-size: maximum task threads (= task-max-threads)
   - queue-size: estimate of queued tasks
   - If busy-task-thread-count ≈ max-pool-size and queue-size > 0 → pool saturated
   ```

### Example Cloud Monitoring Queries

Example for GCP Cloud Monitoring with custom metrics; adapt the metric names to whatever exporter publishes them:

```
# Active connections per datasource
resource.type="gce_instance"
metric.type="custom.googleapis.com/wildfly/datasource/active_connections"

# HTTP request latency
resource.type="gce_instance"
metric.type="custom.googleapis.com/wildfly/http/request_latency"

# Thread pool utilization
resource.type="gce_instance"
metric.type="custom.googleapis.com/wildfly/thread_pool/utilization"
```

## 8. Performance Checklist

Before production deployment, verify:

### Configuration Validation
- [ ] Sum of max-pool-size over all datasources and nodes within the DB's `max_connections`
- [ ] Deployment timeout ≥ 600 seconds (the default); management blocking timeout adequate for boot
- [ ] io worker `task-max-threads` at the default or changed on evidence
- [ ] Session timeout appropriate for workload
- [ ] Prepared statement caching enabled
- [ ] Batch rewriting enabled for bulk operations
- [ ] Statistics enabled on datasources (and undertow, if its metrics are collected)

### Testing
- [ ] Connection pool sizing validated under load
- [ ] No IJ000453/IJ000655 in logs
- [ ] No datasource timeout warnings
- [ ] Deployment completes within timeout
- [ ] Session replication working (if clustered)
- [ ] Response times acceptable under peak load

### Monitoring Setup
- [ ] Monitoring dashboard created
- [ ] Alerts configured for:
  - Connections in use > 90% of max
  - Worker busy threads > 80% of max
  - Deployment time > 200 seconds
  - Database query timeout errors > 0/hour
- [ ] Log monitoring for datasource warnings
- [ ] Weekly review of performance metrics

### Documentation
- [ ] Baseline metrics recorded (before deployment)
- [ ] Configuration changes documented
- [ ] Runbook created for connection pool issues
- [ ] Team trained on WildFly console

## Performance Tuning Summary

**Key takeaway:** Database connection pooling is the biggest performance lever for database-heavy EE applications.

**Start with vendor/default settings, then adjust based on monitoring:**
1. Monitor connections in use (InUseCount, MaxUsedCount)
2. If consistently > 80% of max → increase max-pool-size (within the budget) or shorten hold times
3. If WaitCount > 0 → pool too small or queries too slow
4. If AverageBlockingTime > 5s → problematic

**Common patterns:**
- Period-end spike: Increase max-pool-size for the period
- Report generation: Configure query timeout carefully
- Batch import: Enable batch rewriting and statement caching

Further reading:
- The application vendor's installation / configuration guide, if any
- WildFly documentation: https://docs.wildfly.org/ (Admin Guide; Model Reference for every attribute and its default)
