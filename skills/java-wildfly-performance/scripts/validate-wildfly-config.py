#!/usr/bin/env python3
"""
WildFly Configuration Validator for heavy-load Jakarta EE applications

Validates a WildFly 26+ standalone.xml (or domain.xml profiles) against a
performance baseline: deployment timeouts, datasource pools (size, validation,
timeouts, statistics), Undertow listeners and their io worker (task-max-threads /
io-threads), EJB3 bean-instance pools and thread pool, transaction timeout,
Infinispan second-level cache bounds and verbose loggers.

Subsystems are matched by local element name and by any namespace version
(urn:jboss:domain:<subsystem>:<version> or urn:wildfly:<subsystem>:<version>),
so the check does not depend on the WildFly release. ${property:default}
expressions are resolved from <system-properties> or their default value.

The baseline values below are defaults; override the environment-specific ones
(database connection budget, minimum deployment timeout, server vCPUs) from the
command line.

Usage:
    python3 validate-wildfly-config.py --config standalone.xml
    python3 validate-wildfly-config.py --config standalone.xml --strict
    python3 validate-wildfly-config.py --config standalone.xml --max-pool 50 --min-deployment-timeout 600 --vcpu 8
"""

import copy
import json
import re
import sys
import argparse
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, List, Optional, Tuple


RE_SUBSYSTEM_NS = re.compile(r'^urn:(?:jboss:domain|wildfly):(?P<name>[a-z0-9][a-z0-9\-]*)(?::(?P<ver>[\d.]+))?$')
RE_ROOT_NS = re.compile(r'^urn:jboss:domain:(?P<ver>[\d.]+)$')
VERBOSE_LEVELS = {'DEBUG', 'TRACE', 'ALL', 'FINER', 'FINEST', 'FINE'}


def local(tag) -> str:
    if not isinstance(tag, str):
        return ''
    return tag.rsplit('}', 1)[-1] if tag.startswith('{') else tag


def namespace(tag) -> str:
    return tag[1:].split('}', 1)[0] if isinstance(tag, str) and tag.startswith('{') else ''


def kids(el, name: str) -> List[ET.Element]:
    return [c for c in el if local(c.tag) == name]


def kid(el, *path: str) -> Optional[ET.Element]:
    cur = el
    for name in path:
        if cur is None:
            return None
        found = kids(cur, name)
        cur = found[0] if found else None
    return cur


def descendants(el, name: str) -> List[ET.Element]:
    return [c for c in el.iter() if local(c.tag) == name]


class WildFlyConfigValidator:
    """Validates WildFly configuration"""

    # Baseline requirements (defaults; see --max-pool / --min-deployment-timeout)
    VENDOR_REQUIREMENTS = {
        'deployment_timeout': {
            'min': 300,
            'reason': 'Large EAR/WAR deployments can take several minutes to deploy'
        },
        'datasource_max_pool': {
            'max': 100,
            'reason': 'Per-datasource share of the database connection budget (max_connections)'
        },
        'datasource_min_pool': {
            'recommended': 0,
            'reason': 'Lazy initialization saves startup time and memory'
        },
        'datasource_blocking_timeout': {
            'recommended': 30000,
            'reason': 'Reasonable wait time for connection availability'
        },
        'code_cache_size': {
            'min': 512,
            'reason': 'Example vendor-mandated minimum (not checked against XML)'
        },
    }

    # WildFly / IronJacamar defaults used when an attribute is not set
    DEFAULTS = {
        'deployment_timeout_s': 600,          # deployment-scanner deployment-timeout
        'management_blocking_timeout_s': 300,  # jboss.as.management.blocking.timeout
        'ds_max_pool': 20,                     # IronJacamar max-pool-size
        'ds_blocking_timeout_ms': 30000,       # IronJacamar blocking-timeout-millis
        'ds_idle_timeout_min': 30,             # IronJacamar idle-timeout-minutes
        'ejb_thread_pool_max': 10,
        'tx_default_timeout_s': 300,           # transactions coordinator-environment default-timeout
    }

    def __init__(self, config_file: str, requirements: Optional[Dict] = None, vcpu: Optional[int] = None):
        self.config_file = config_file
        self.req = copy.deepcopy(requirements or self.VENDOR_REQUIREMENTS)
        self.vcpu = vcpu
        self.tree = None
        self.root = None
        self.issues: List[Dict] = []
        self.warnings: List[Dict] = []
        self.notes: List[Dict] = []
        self.info: List[Dict] = []
        self.subsystems: Dict[str, List[Tuple[str, ET.Element, str]]] = {}   # name -> [(version, element, profile)]
        self.props: Dict[str, str] = {}
        self.worker_threads: Dict[str, Optional[int]] = {}
        self.max_ds_pool: Optional[int] = None

    # ------------------------------------------------------------------ helpers
    def parse(self) -> bool:
        try:
            self.tree = ET.parse(self.config_file)
            self.root = self.tree.getroot()
        except ET.ParseError as e:
            print(f"Error parsing XML: {e}")
            return False
        for sp in descendants(self.root, 'system-properties'):
            for prop in kids(sp, 'property'):
                if prop.get('name') is not None and prop.get('value') is not None:
                    self.props[prop.get('name')] = prop.get('value')
        # standalone.xml: one unnamed <profile>; domain.xml: several named profiles
        profiles = descendants(self.root, 'profile')
        pairs = [(sub, p.get('name', '')) for p in profiles for sub in kids(p, 'subsystem')] if profiles \
            else [(sub, '') for sub in descendants(self.root, 'subsystem')]
        for sub, pname in pairs:
            m = RE_SUBSYSTEM_NS.match(namespace(sub.tag))
            if m:
                self.subsystems.setdefault(m.group('name'), []).append((m.group('ver') or '', sub, pname))
        return True

    def get_namespace(self) -> str:
        return namespace(self.root.tag)

    def find_elements(self, tag: str, namespace_: Optional[str] = None) -> List[ET.Element]:
        """All elements with this local name, whatever their namespace"""
        return descendants(self.root, tag)

    def resolve(self, value: Optional[str]) -> Tuple[Optional[str], bool]:
        """Resolve ${a:b} / ${a:${b:c}} / ${env.X:d}; returns (value, is_expression)"""
        if value is None:
            return None, False
        value = value.strip()
        if '${' not in value:
            return value, False
        for _ in range(10):
            m = re.search(r'\$\{([^{}]*)\}', value)
            if not m:
                break
            body = m.group(1)
            names, sep, default = body.partition(':')
            rep = None
            for n in names.split(','):
                if n in self.props:
                    rep = self.props[n]
                    break
            if rep is None:
                rep = default if sep else None
            if rep is None:
                return None, True
            value = value[:m.start()] + rep + value[m.end():]
        return value, True

    def attr_int(self, el, name: str) -> Tuple[Optional[int], Optional[str]]:
        raw = el.get(name) if el is not None else None
        return self._to_int(raw)

    def text_int(self, el, *path) -> Tuple[Optional[int], Optional[str]]:
        node = kid(el, *path) if el is not None else None
        raw = node.text if node is not None else None
        return self._to_int(raw)

    def _to_int(self, raw: Optional[str]) -> Tuple[Optional[int], Optional[str]]:
        if raw is None:
            return None, None
        val, expr = self.resolve(raw)
        if val is None:
            return None, raw
        try:
            return int(float(val)), raw
        except ValueError:
            return None, raw

    def truthy(self, raw: Optional[str]) -> Optional[bool]:
        if raw is None:
            return None
        v, _ = self.resolve(raw)
        if v is None:
            return None
        return v.strip().lower() == 'true'

    def text_bool(self, el, *path) -> Optional[bool]:
        node = kid(el, *path) if el is not None else None
        return self.truthy(node.text) if node is not None and node.text is not None else None

    def sub(self, name: str) -> List[Tuple[str, ET.Element, str]]:
        return self.subsystems.get(name, [])

    @staticmethod
    def _label(component: str, profile: str) -> str:
        return f"{profile}/{component}" if profile else component

    def _issue(self, severity, component, message, fix=None):
        d = {'severity': severity, 'component': component, 'message': message}
        if fix:
            d['fix'] = fix
        self.issues.append(d)

    def _warn(self, component, message, fix=None):
        d = {'component': component, 'message': message}
        if fix:
            d['fix'] = fix
        self.warnings.append(d)

    def _note(self, component, message):
        self.notes.append({'component': component, 'message': message})

    def _ok(self, component, message):
        self.info.append({'component': component, 'message': message})

    # ------------------------------------------------------------------ checks
    def validate_subsystems(self):
        ver = RE_ROOT_NS.match(self.get_namespace())
        if ver:
            self._ok('server', f"configuration schema urn:jboss:domain:{ver.group('ver')}")
        found = ', '.join(f"{n} {v[0][0]}" for n, v in sorted(self.subsystems.items()))
        if found:
            self._note('subsystems', f"found: {found}")
        if self.sub('web'):
            self._issue('HIGH', 'subsystem', "Legacy 'web' (JBoss Web) subsystem found: this is a JBoss AS 7 / EAP 6 "
                                             "configuration. WildFly uses 'undertow' + 'io'; migrate before tuning.")
        for name, why in (('datasources', 'The application requires a configured datasource.'),
                          ('undertow', 'HTTP requests are served by Undertow.'),
                          ('io', 'Undertow listeners run on io subsystem workers.')):
            if self.sub(name):
                self._ok('subsystem', f"{name} subsystem configured (version {self.sub(name)[0][0] or '?'})")
            else:
                self._issue('MEDIUM', 'subsystem', f"{name} subsystem not found. {why}")
        if not self.subsystems:
            self._issue('HIGH', 'server', 'No <subsystem> elements with a urn:jboss:domain:* / urn:wildfly:* namespace '
                                          'found. Is this a WildFly standalone.xml / domain.xml?')

    def validate_deployment_timeout(self):
        min_t = self.req['deployment_timeout']['min']
        subs = self.sub('deployment-scanner')
        if not subs:
            self._note('deployment-scanner', 'deployment-scanner subsystem not configured (deployments only through '
                                             'the management API/CLI).')
        for _, s, prof in subs:
            scanners = kids(s, 'deployment-scanner')
            if not scanners:
                self._note(self._label('deployment-scanner', prof), 'no <deployment-scanner> element defined.')
            for sc in scanners:
                comp = self._label(f"deployment-scanner[{sc.get('name', 'default')}]", prof)
                t, raw = self.attr_int(sc, 'deployment-timeout')
                if raw is None:
                    t = self.DEFAULTS['deployment_timeout_s']
                    src = f"not set, WildFly default {t}s"
                elif t is None:
                    self._warn(comp, f"deployment-timeout '{raw}' cannot be evaluated.")
                    continue
                else:
                    src = f"{t}s" + (f" (from {raw})" if raw.startswith('${') else '')
                if t < min_t:
                    self._issue('HIGH', comp, f'deployment-timeout {src} < required {min_t}s',
                                f'<deployment-scanner ... deployment-timeout="{min_t}"/>')
                else:
                    self._ok(comp, f'deployment-timeout {src} meets the {min_t}s requirement')
        # service container stability timeout used while (re)deploying and at boot
        raw = self.props.get('jboss.as.management.blocking.timeout')
        t = self._to_int(raw)[0] if raw is not None else None
        if raw is None:
            self._note('system-properties', f"jboss.as.management.blocking.timeout not set (default "
                                            f"{self.DEFAULTS['management_blocking_timeout_s']}s). Large deployments that "
                                            f"fail with WFLYCTL0348 at boot need a higher value.")
        elif t is not None and t < min_t:
            self._warn('system-properties', f"jboss.as.management.blocking.timeout {t}s < {min_t}s",
                       f'<property name="jboss.as.management.blocking.timeout" value="{min_t}"/>')
        elif t is not None:
            self._ok('system-properties', f"jboss.as.management.blocking.timeout {t}s")

    def validate_datasources(self):
        subs = self.sub('datasources')
        dss = []
        for _, s, prof in subs:
            for ds in descendants(s, 'datasource') + descendants(s, 'xa-datasource'):
                dss.append((ds, prof))
        if not dss:
            self._warn('datasource', 'No datasources found. The application requires a configured datasource.')
            return
        max_allowed = self.req['datasource_max_pool']['max']
        for ds, prof in dss:
            xa = local(ds.tag) == 'xa-datasource'
            name = ds.get('pool-name') or ds.get('jndi-name', 'unknown')
            comp = self._label(f"{'xa-' if xa else ''}datasource[{name}]", prof)
            if self.truthy(ds.get('enabled')) is False:
                self._note(comp, 'disabled (enabled="false"); not checked.')
                continue
            jndi = ds.get('jndi-name', '')
            url_el = kid(ds, 'connection-url')
            url = (url_el.text or '') if url_el is not None else ''
            driver_el = kid(ds, 'driver')
            driver = (driver_el.text or '') if driver_el is not None else ''
            if 'ExampleDS' in (name + jndi) or url.startswith('jdbc:h2:mem') or driver.strip() == 'h2':
                self._warn(comp, 'H2 example datasource present. Remove it in production (it also keeps the H2 '
                                 'driver module and an in-memory database around). Pool checks skipped.')
                continue

            # statistics
            stats = self.truthy(ds.get('statistics-enabled'))
            if stats:
                self._ok(comp, 'statistics-enabled')
            else:
                self._warn(comp, 'statistics-enabled is not true: no pool metrics (ActiveCount, AvailableCount, '
                                 'WaitCount, MaxWaitTime) to diagnose pool exhaustion.',
                           f'<{local(ds.tag)} ... statistics-enabled="true">')

            # pool sizing
            pool = kid(ds, 'xa-pool' if xa else 'pool')
            max_v, max_raw = self.text_int(pool, 'max-pool-size')
            min_v, _ = self.text_int(pool, 'min-pool-size')
            init_v, _ = self.text_int(pool, 'initial-pool-size')
            if max_raw is None:
                max_v = self.DEFAULTS['ds_max_pool']
                self._warn(comp, f"max-pool-size not set: IronJacamar default {max_v} is used. Size it explicitly "
                                 f"from the request/batch concurrency and the database connection budget.",
                           f"<pool><max-pool-size>N</max-pool-size></pool>")
            elif max_v is None:
                self._warn(comp, f"max-pool-size '{max_raw}' cannot be evaluated.")
            elif max_v > max_allowed:
                self._issue('HIGH', comp, f'max-pool-size {max_v} > database connection budget {max_allowed}',
                            f'<max-pool-size>{max_allowed}</max-pool-size>')
            else:
                self._ok(comp, f'max-pool-size {max_v} OK (budget {max_allowed})')
            if max_v is not None:
                self.max_ds_pool = max(self.max_ds_pool or 0, max_v)
            if min_v is not None and max_v is not None and min_v > max_v:
                self._issue('HIGH', comp, f'min-pool-size {min_v} > max-pool-size {max_v}')
            if init_v is not None and max_v is not None and init_v > max_v:
                self._issue('HIGH', comp, f'initial-pool-size {init_v} > max-pool-size {max_v}')
            if min_v is not None and min_v > 0:
                self._warn(comp, f'min-pool-size {min_v} > 0. Consider 0 for lazy initialization '
                                 f'(or keep it deliberately to pre-warm connections).')

            # validation
            val = kid(ds, 'validation')
            vom = self.text_bool(val, 'validate-on-match')
            bg = self.text_bool(val, 'background-validation')
            checker = val is not None and (kid(val, 'valid-connection-checker') is not None
                                           or kid(val, 'check-valid-connection-sql') is not None)
            sorter = val is not None and kid(val, 'exception-sorter') is not None
            if not vom and not bg:
                self._warn(comp, 'No connection validation (neither validate-on-match nor background-validation). '
                                 'Broken connections after a database restart/failover are handed to the application.',
                           '<validation><valid-connection-checker class-name="..."/><validate-on-match>true'
                           '</validate-on-match></validation>')
            elif vom and bg:
                self._warn(comp, 'validate-on-match and background-validation are both true; they are typically '
                                 'exclusive (validating on every checkout makes background validation redundant).')
            else:
                mode = 'validate-on-match' if vom else 'background-validation'
                if not checker:
                    self._warn(comp, f'{mode} enabled but no valid-connection-checker / check-valid-connection-sql.')
                else:
                    self._ok(comp, f'connection validation: {mode}')
            if not sorter:
                self._warn(comp, 'No exception-sorter: connections that hit a fatal database error are not evicted '
                                 'from the pool. Use the vendor sorter (e.g. PostgreSQLExceptionSorter, '
                                 'OracleExceptionSorter).')

            # timeouts
            to = kid(ds, 'timeout')
            bt, bt_raw = self.text_int(to, 'blocking-timeout-millis')
            if bt_raw is None:
                self._ok(comp, f"blocking-timeout-millis not set (default {self.DEFAULTS['ds_blocking_timeout_ms']}ms)")
            elif bt is None:
                self._warn(comp, f"blocking-timeout-millis '{bt_raw}' cannot be evaluated.")
            elif bt < 10000:
                self._warn(comp, f'blocking-timeout-millis {bt}ms seems low. Recommended >= 30000ms.')
            else:
                self._ok(comp, f'blocking-timeout-millis {bt}ms OK')
            qt, _ = self.text_int(to, 'query-timeout')
            stq = self.text_bool(to, 'set-tx-query-timeout')
            if qt is None and not stq:
                self._note(comp, 'No query-timeout / set-tx-query-timeout: a runaway query holds its connection '
                                 'until it finishes.')
            it, it_raw = self.text_int(to, 'idle-timeout-minutes')
            if it_raw is not None and it is not None:
                self._ok(comp, f'idle-timeout-minutes {it}')
            else:
                self._ok(comp, f"idle-timeout-minutes not set (default {self.DEFAULTS['ds_idle_timeout_min']})")

            # statement cache
            st = kid(ds, 'statement')
            psc, _ = self.text_int(st, 'prepared-statement-cache-size')
            if not psc:
                self._note(comp, 'prepared-statement-cache-size not set (0): consider a per-connection cache (e.g. 32-100) '
                                 'for repeated statements, if the driver does not cache them itself.')

    def _worker_defaults(self) -> Tuple[Optional[int], Optional[int]]:
        if self.vcpu:
            return self.vcpu * 2, self.vcpu * 16
        return None, None

    def validate_io(self):
        dio, dtask = self._worker_defaults()
        for _, s, prof in self.sub('io'):
            workers = kids(s, 'worker')
            if not workers:
                self._warn(self._label('io', prof), 'io subsystem has no <worker>.')
            for w in workers:
                name = w.get('name', 'default')
                comp = self._label(f'io/worker[{name}]', prof)
                io_t, io_raw = self.attr_int(w, 'io-threads')
                task_t, task_raw = self.attr_int(w, 'task-max-threads')
                eff_io = io_t if io_raw is not None else dio
                eff_task = task_t if task_raw is not None else dtask
                self.worker_threads[name] = eff_task
                desc_io = f"{io_t}" if io_raw is not None else (f"default {dio} (2 x {self.vcpu} vCPU)" if dio else "default 2 x vCPU")
                desc_task = f"{task_t}" if task_raw is not None else (f"default {dtask} (16 x {self.vcpu} vCPU)" if dtask else "default 16 x vCPU")
                self._ok(comp, f"io-threads {desc_io}, task-max-threads {desc_task}")
                if task_raw is None and not self.vcpu:
                    self._note(comp, 'task-max-threads not set: the request thread pool scales with the CPU count of '
                                     'the server (pass --vcpu to evaluate it against the datasource pools).')
                if eff_io is not None and eff_task is not None and eff_io > eff_task:
                    self._warn(comp, f"io-threads {eff_io} > task-max-threads {eff_task}: request (task) threads "
                                     f"should outnumber I/O threads.")
                if self.vcpu and eff_task is not None and eff_task < 2 * self.vcpu:
                    self._warn(comp, f"task-max-threads {eff_task} < 2 x vCPU: blocking requests (JDBC, remote calls) "
                                     f"will queue.")
                if self.vcpu and eff_io is not None and eff_io > 4 * self.vcpu:
                    self._warn(comp, f"io-threads {eff_io} > 4 x vCPU: I/O threads must never block; more of them "
                                     f"only add context switches.")

    def validate_undertow(self):
        for _, s, prof in self.sub('undertow'):
            stats = self.truthy(s.get('statistics-enabled'))
            if not stats:
                self._note(self._label('undertow', prof), 'statistics-enabled is not true: no request count/processing '
                                                          'time metrics for the listeners.')
            for srv in kids(s, 'server'):
                for lst in kids(srv, 'http-listener') + kids(srv, 'https-listener') + kids(srv, 'ajp-listener'):
                    name = lst.get('name', '?')
                    comp = self._label(f"undertow/{srv.get('name', 'server')}/{local(lst.tag)}[{name}]", prof)
                    worker = lst.get('worker', 'default')
                    if self.worker_threads and worker not in self.worker_threads:
                        self._issue('HIGH', comp, f"references io worker '{worker}' that is not defined in the io subsystem")
                    task = self.worker_threads.get(worker)
                    msg = f"worker '{worker}'" + (f" (task-max-threads {task})" if task else '')
                    self._ok(comp, msg)
                    if task and self.max_ds_pool:
                        if task < self.max_ds_pool:
                            self._note(comp, f"worker task-max-threads {task} < largest datasource max-pool-size "
                                             f"{self.max_ds_pool}: HTTP requests alone can never use the whole pool "
                                             f"(fine if batch/EJB threads use the rest).")
                        elif task > 4 * self.max_ds_pool:
                            self._note(comp, f"worker task-max-threads {task} > 4 x largest datasource max-pool-size "
                                             f"{self.max_ds_pool}: under load most request threads may queue on the pool "
                                             f"(blocking-timeout-millis).")
                    if lst.get('max-connections') is None:
                        self._note(comp, 'max-connections not set (unlimited): consider a cap to shed load early.')
                    if local(lst.tag) != 'ajp-listener' and lst.get('record-request-start-time') is None:
                        self._note(comp, 'record-request-start-time not set: access-log response times (%D/%T) '
                                         'are not recorded.')
            for sc in kids(s, 'servlet-container'):
                tmo, raw = self.attr_int(sc, 'default-session-timeout')
                if raw is not None and tmo is not None and tmo > 60:
                    self._note(self._label(f"undertow/servlet-container[{sc.get('name', '?')}]", prof),
                               f"default-session-timeout {tmo} min: long-lived sessions keep their data on the heap.")

    def validate_thread_pools(self):
        """EJB3 bean-instance pools and thread pool"""
        worker_task = self.worker_threads.get('default')
        for _, s, prof in self.sub('ejb3'):
            base = self._label('ejb3', prof)
            slsb = kid(s, 'session-bean', 'stateless')
            if slsb is not None:
                ref = kid(slsb, 'bean-instance-pool-ref')
                if ref is not None:
                    self._ok(base, f"stateless beans pooled in '{ref.get('pool-name')}'")
                else:
                    self._ok(base, 'stateless bean pooling disabled (new instance per invocation)')
            long_wait = []
            for pool in descendants(s, 'strict-max-pool'):
                name = pool.get('name', '?')
                comp = f"{base}/strict-max-pool[{name}]"
                size, raw = self.attr_int(pool, 'max-pool-size')
                derive = self.resolve(pool.get('derive-size'))[0]
                tmo, _ = self.attr_int(pool, 'instance-acquisition-timeout')
                unit = (pool.get('instance-acquisition-timeout-unit') or 'MINUTES').upper()
                tdesc = f"{tmo} {unit.lower()}" if tmo is not None else 'default'
                if derive and derive != 'none':
                    self._ok(comp, f"size derived {derive}, acquisition timeout {tdesc}")
                elif size is not None:
                    self._ok(comp, f"max-pool-size {size}, acquisition timeout {tdesc}")
                    if 'slsb' in name and worker_task and size < worker_task:
                        self._warn(comp, f"max-pool-size {size} < io worker task-max-threads {worker_task}: request "
                                         f"threads can wait up to {tdesc} for a bean instance.")
                if tmo is not None and unit.startswith('MINUTE') and tmo >= 5:
                    long_wait.append(f"{name} ({tdesc})")
            if long_wait:
                self._note(f"{base}/strict-max-pool", f"instance-acquisition-timeout of {', '.join(long_wait)}: an "
                                                      f"invocation waits this long for a free bean instance before failing.")
            for tp in descendants(s, 'thread-pool'):
                comp = f"{base}/thread-pool[{tp.get('name', '?')}]"
                mt, raw = self.attr_int(kid(tp, 'max-threads'), 'count')
                if mt is None and raw is None:
                    mt = self.DEFAULTS['ejb_thread_pool_max']
                if mt is not None:
                    self._ok(comp, f"max-threads {mt} (used by @Asynchronous, timers and remote EJB calls)")
                    if mt <= 10 and self.vcpu and self.vcpu > 4:
                        self._note(comp, f"max-threads {mt}: raise it if the application relies on @Asynchronous "
                                         f"methods or many EJB timers.")

    def validate_transactions(self):
        for _, s, prof in self.sub('transactions'):
            ce = kid(s, 'coordinator-environment')
            t, raw = self.attr_int(ce, 'default-timeout') if ce is not None else (None, None)
            if raw is None:
                t = self.DEFAULTS['tx_default_timeout_s']
            if t is not None:
                self._ok(self._label('transactions', prof), f"default transaction timeout {t}s"
                                                            f"{' (default)' if raw is None else ''}")
                if t <= 300:
                    self._note(self._label('transactions', prof),
                               f"default-timeout {t}s: long batch transactions are rolled back after it; raise it "
                               f"per bean (@TransactionTimeout) or globally if needed.")

    def validate_infinispan(self):
        for _, s, prof in self.sub('infinispan'):
            for cc in kids(s, 'cache-container'):
                ccn = cc.get('name', '?')
                for cache in [c for c in cc if local(c.tag).endswith('-cache')]:
                    cname = cache.get('name', '?')
                    comp = self._label(f"infinispan/{ccn}/{local(cache.tag)}[{cname}]", prof)
                    # Hibernate 2LC entity / query caches must be bounded (the timestamps cache must not be)
                    if ccn == 'hibernate' and cname in ('entity', 'local-query', 'entity-query'):
                        mem = next((c for c in cache if local(c.tag) in ('heap-memory', 'object-memory', 'off-heap-memory',
                                                                         'binary-memory', 'memory')), None)
                        size = mem.get('size') if mem is not None else None
                        if size is None:
                            self._warn(comp, 'second-level cache without a memory bound (heap-memory size): it can grow '
                                             'until the heap is full.', '<heap-memory size="10000"/>')
                        else:
                            self._ok(comp, f"bounded ({local(mem.tag)} size {size})")
                    if local(cache.tag) in ('replicated-cache', 'distributed-cache'):
                        self._note(comp, 'clustered cache: replication traffic adds GC and network load.')

    def validate_logging(self):
        for _, s, prof in self.sub('logging'):
            for lg in kids(s, 'logger') + kids(s, 'root-logger'):
                lvl_el = kid(lg, 'level')
                level = (self.resolve(lvl_el.get('name'))[0] or '') if lvl_el is not None else (lg.get('level') or '')
                level = level.upper()
                category = lg.get('category', 'ROOT' if local(lg.tag) == 'root-logger' else 'unknown')
                if category == 'org.jboss.as.config':
                    continue   # shipped at DEBUG: only logs the boot-time configuration
                if level in VERBOSE_LEVELS:
                    self._warn(self._label(f'logger[{category}]', prof), f'Logger level is {level}. May impact performance.',
                               'Set to INFO or WARN for production')
            for h in kids(s, 'console-handler'):
                lvl_el = kid(h, 'level')
                level = (lvl_el.get('name') or '').upper() if lvl_el is not None else ''
                self._note(self._label(f"logging/console-handler[{h.get('name', '?')}]", prof),
                           f"console handler active{' at ' + level if level else ''}: in production stdout is often "
                           f"redirected to a file as well (duplicate, synchronous writes).")

    # ------------------------------------------------------------------ driver
    def run_validation(self):
        if not self.parse():
            return False
        self.validate_subsystems()
        self.validate_deployment_timeout()
        self.validate_datasources()
        self.validate_io()
        self.validate_undertow()
        self.validate_thread_pools()
        self.validate_transactions()
        self.validate_infinispan()
        self.validate_logging()
        return True

    def print_report(self, strict: bool = False):
        print("\n" + "=" * 70)
        print("WILDFLY CONFIGURATION VALIDATION REPORT")
        print("=" * 70)
        print(f"\nConfiguration file: {self.config_file}")

        print(f"\nSUMMARY:")
        print(f"  Issues (High severity): {len(self.issues)}")
        print(f"  Warnings: {len(self.warnings)}")
        print(f"  Notes: {len(self.notes)}")
        print(f"  OK: {len(self.info)}")

        if self.issues:
            print(f"\nCRITICAL ISSUES:")
            for issue in self.issues:
                print(f"  [{issue['severity']}] {issue['component']}: {issue['message']}")
                if 'fix' in issue:
                    print(f"         Fix: {issue['fix']}")
        if self.warnings:
            print(f"\nWARNINGS:")
            for warning in self.warnings:
                print(f"  [WARN] {warning['component']}: {warning['message']}")
                if 'fix' in warning:
                    print(f"         Fix: {warning['fix']}")
        if self.notes:
            print(f"\nNOTES:")
            for n in self.notes:
                print(f"  [NOTE] {n['component']}: {n['message']}")
        if self.info and not strict:
            print(f"\nOK:")
            for info in self.info:
                print(f"  [OK] {info['component']}: {info['message']}")

        print("\n" + "=" * 70)
        if strict:
            return len(self.issues) == 0 and len(self.warnings) == 0
        return len(self.issues) == 0

    def export_json(self, outfile: str):
        data = {
            'config_file': self.config_file,
            'summary': {'issues': len(self.issues), 'warnings': len(self.warnings),
                        'notes': len(self.notes), 'ok': len(self.info)},
            'subsystems': {n: [v for v, _, _ in lst] for n, lst in self.subsystems.items()},
            'issues': self.issues,
            'warnings': self.warnings,
            'notes': self.notes,
            'ok': self.info,
        }
        with open(outfile, 'w') as f:
            json.dump(data, f, indent=2)
        print(f"Report exported to {outfile}")


def main():
    parser = argparse.ArgumentParser(
        description='Validate a WildFly 26+ standalone.xml for a heavy-load Jakarta EE application')
    parser.add_argument('--config', required=True, help='Path to standalone.xml (or domain.xml)')
    parser.add_argument('--output', help='Output JSON file')
    parser.add_argument('--strict', action='store_true',
                        help='Strict mode: fail on warnings (notes never fail)')
    parser.add_argument('--max-pool', type=int,
                        default=WildFlyConfigValidator.VENDOR_REQUIREMENTS['datasource_max_pool']['max'],
                        help='Max allowed max-pool-size per datasource (your DB connection budget; default: %(default)s)')
    parser.add_argument('--min-deployment-timeout', type=int,
                        default=WildFlyConfigValidator.VENDOR_REQUIREMENTS['deployment_timeout']['min'],
                        help='Minimum deployment-timeout in seconds (default: %(default)s)')
    parser.add_argument('--vcpu', type=int,
                        help='vCPUs of the server running WildFly: evaluates CPU-derived defaults '
                             '(io worker io-threads = 2 x vCPU, task-max-threads = 16 x vCPU)')

    args = parser.parse_args()

    if not Path(args.config).exists():
        print(f"Error: Config file not found: {args.config}")
        sys.exit(1)

    req = copy.deepcopy(WildFlyConfigValidator.VENDOR_REQUIREMENTS)
    req['datasource_max_pool']['max'] = args.max_pool
    req['deployment_timeout']['min'] = args.min_deployment_timeout

    validator = WildFlyConfigValidator(args.config, requirements=req, vcpu=args.vcpu)
    if not validator.run_validation():
        print("Error: Failed to validate configuration")
        sys.exit(1)

    valid = validator.print_report(strict=args.strict)

    if args.output:
        validator.export_json(args.output)

    sys.exit(0 if valid else 1)


if __name__ == '__main__':
    main()
