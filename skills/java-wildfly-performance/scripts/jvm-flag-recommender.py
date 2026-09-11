#!/usr/bin/env python3
"""
JVM Flag Recommender for heavy-load Jakarta EE applications on WildFly

Generates machine-size-specific JVM and WildFly pool settings on top of a
baseline flag set, with flags that are valid for the selected JDK (17, 21 or
25). No baseline by default; --vendor-baseline prepends an example of a
vendor-mandated one (-Xrs -server, 512m code cache, tiered compilation disabled),
--baseline-opts prepends your own. Output: human-readable report, JAVA_OPTS string, or
Terraform-style variables (HCL).

Heap rule (also printed in the report):
    heap = min(P% of RAM, RAM - headroom), rounded down to 512 MB (256 MB below 4 GB)
    P        = 50 (standard), 60 (peak-batch), 70 (high-memory), or --heap-percent
    headroom = OS reserve max(1 GB, 10% of RAM)
             + code cache + metaspace budget (512 MB) + thread stacks (1 MB per thread)
             + GC native overhead (10% of the heap)

Usage:
    python3 jvm-flag-recommender.py --machine-type e2-standard-8
    python3 jvm-flag-recommender.py --machine-type e2-standard-8 --workload peak-batch
    python3 jvm-flag-recommender.py --machine-type e2-standard-8 --gc-algorithm zgc --jdk 25
    python3 jvm-flag-recommender.py --vcpu 12 --memory-gb 48 --format java-opts
    python3 jvm-flag-recommender.py --vcpu 8 --memory-gb 32 --baseline-opts "-XX:ReservedCodeCacheSize=256m"
    python3 jvm-flag-recommender.py --machine-type e2-standard-8 --vendor-baseline
"""

import argparse
import re
import shlex
import sys
from typing import Dict, List, Optional, Tuple


# JDK 8-era flags that users often carry in a baseline. fatal=True: removed, the JVM refuses to
# start ("Unrecognized VM option", verified on JDK 21). fatal=False: deprecated but still mapped.
LEGACY_FLAGS = {
    'PrintGCDateStamps': (True, "use the 'time' decorator of -Xlog"),
    'PrintGCTimeStamps': (True, "use the 'uptime' decorator of -Xlog"),
    'UseGCLogFileRotation': (True, "use -Xlog output options filecount=N,filesize=S"),
    'NumberOfGCLogFiles': (True, "use -Xlog output option filecount=N"),
    'GCLogFileSize': (True, "use -Xlog output option filesize=S"),
    'PrintGCApplicationStoppedTime': (True, "use -Xlog:safepoint"),
    'PrintGCApplicationConcurrentTime': (True, "use -Xlog:safepoint"),
    'PrintTenuringDistribution': (True, "use -Xlog:gc+age=trace"),
    'PrintHeapAtGC': (True, "use -Xlog:gc+heap=debug"),
    'PrintAdaptiveSizePolicy': (True, "use -Xlog:gc+ergo*=trace"),
    'PrintGCCause': (True, "the GC cause is always logged by -Xlog:gc"),
    'PrintGCID': (True, "the GC id is always logged by -Xlog:gc"),
    'PrintReferenceGC': (True, "use -Xlog:gc+ref=debug"),
    'PermSize': (True, "PermGen no longer exists; use -XX:MetaspaceSize"),
    'MaxPermSize': (True, "PermGen no longer exists; use -XX:MaxMetaspaceSize"),
    'UseConcMarkSweepGC': (True, "CMS was removed in JDK 14; use G1 or ZGC"),
    'UseParNewGC': (True, "ParNew was removed; use G1 or ZGC"),
    'CMSInitiatingOccupancyFraction': (True, "CMS was removed; for G1 use -XX:InitiatingHeapOccupancyPercent"),
    'UseCMSInitiatingOccupancyOnly': (True, "CMS was removed"),
    'CMSClassUnloadingEnabled': (True, "CMS was removed"),
    'AggressiveOpts': (True, "removed; no replacement"),
    'PrintGCDetails': (False, "deprecated: use -Xlog:gc*"),
    'PrintGC': (False, "deprecated: use -Xlog:gc"),
}
GC_SELECT = re.compile(r'^-XX:[+-]Use(?:G1|Z|Parallel|ParallelOld|Serial|Shenandoah|ConcMarkSweep|Epsilon)GC$')


class JVMRecommender:
    """Recommends JVM configurations for a WildFly-hosted application"""

    # Machine type specifications (GCP machine types as examples;
    # use --vcpu/--memory-gb for any other VM size)
    MACHINES = {
        'e2-standard-2': {'vcpu': 2, 'memory_gb': 8, 'tier': 'dev'},
        'e2-standard-4': {'vcpu': 4, 'memory_gb': 16, 'tier': 'test'},
        'e2-standard-8': {'vcpu': 8, 'memory_gb': 32, 'tier': 'prod-small'},
        'e2-standard-16': {'vcpu': 16, 'memory_gb': 64, 'tier': 'prod-medium'},
        'e2-standard-32': {'vcpu': 32, 'memory_gb': 128, 'tier': 'prod-large'},
        'c3-standard-22': {'vcpu': 22, 'memory_gb': 88, 'tier': 'prod-large'},
        'custom-16-65536': {'vcpu': 16, 'memory_gb': 64, 'tier': 'prod-custom'},
    }

    # Example vendor-mandated baseline (only with --vendor-baseline)
    VENDOR_BASELINE = {
        'base': '-Xrs -server',
        'code_cache': '-XX:ReservedCodeCacheSize=512m',
        'tiered_compilation': '-XX:-TieredCompilation',
    }

    HEAP_PERCENT = {'standard': 50, 'peak-batch': 60, 'high-memory': 70}
    COMPRESSED_OOPS_MAX_MB = 31744      # G1: -Xmx >= 32767m disables compressed oops (checked on JDK 21)
    NO_OOPS_WORTH_IT_MB = 49152         # a heap without compressed oops only pays off from ~48 GB
    METASPACE_BUDGET_MB = 512
    GC_NATIVE_OVERHEAD = 0.10
    SUPPORTED_JDKS = (17, 21, 25)

    def __init__(self, machine_type: str, workload: str = 'standard', gc_algorithm: str = 'g1gc',
                 vcpu: Optional[int] = None, memory_gb: Optional[float] = None,
                 baseline_opts: Optional[str] = None, jdk: int = 21, heap_percent: Optional[float] = None,
                 gc_log_path: str = 'gc.log', xms: str = 'quarter', vendor_baseline: bool = False):
        self.machine_type = machine_type
        self.workload = workload
        self.gc_algorithm = gc_algorithm.lower()
        self.baseline_opts = baseline_opts
        self.vendor_baseline = vendor_baseline
        self.jdk = int(jdk)
        self.heap_percent = heap_percent
        self.gc_log_path = gc_log_path
        self.xms = xms
        self.notes: List[str] = []
        if self.jdk not in self.SUPPORTED_JDKS:
            raise ValueError(f"Unsupported JDK {jdk}: choose one of {self.SUPPORTED_JDKS}")
        if self.gc_algorithm not in ('g1gc', 'zgc'):
            raise ValueError(f"Unknown GC algorithm: {self.gc_algorithm}")
        if heap_percent is not None and not (10 <= heap_percent <= 85):
            raise ValueError("--heap-percent must be between 10 and 85")

        if vcpu is not None and memory_gb is not None:
            if vcpu < 1 or memory_gb < 2:
                raise ValueError("--vcpu must be >= 1 and --memory-gb >= 2")
            self.machine = {'vcpu': vcpu, 'memory_gb': memory_gb, 'tier': 'custom'}
        else:
            if machine_type not in self.MACHINES:
                raise ValueError(f"Unknown machine type: {machine_type}")
            self.machine = self.MACHINES[machine_type]

        self._baseline_tokens, self._baseline_notes = self._sanitize_baseline()

    # ------------------------------------------------------------------ baseline
    def _raw_baseline_tokens(self) -> List[str]:
        if self.baseline_opts is not None:
            return shlex.split(self.baseline_opts)
        if self.vendor_baseline:
            return (f"{self.VENDOR_BASELINE['base']} {self.VENDOR_BASELINE['code_cache']} "
                    f"{self.VENDOR_BASELINE['tiered_compilation']}").split()
        return []

    def _sanitize_baseline(self) -> Tuple[List[str], List[str]]:
        """Drop baseline flags that would break or be overridden (legacy JDK 8 flags, heap size,
        a second GC selector, ZGenerational on the wrong JDK, JDK 8 GC logging)"""
        kept, notes = [], []
        for tok in self._raw_baseline_tokens():
            m = re.match(r'^-XX:[+-]?(?P<name>[A-Za-z0-9_]+)(?:=.*)?$', tok)
            name = m.group('name') if m else None
            if tok.startswith(('-Xmx', '-Xms')):
                notes.append(f"Removed {tok} from the baseline: the heap is set by this recommendation.")
                continue
            if GC_SELECT.match(tok):
                notes.append(f"Removed {tok} from the baseline: a second collector would make the JVM exit with "
                             f"'Multiple garbage collectors selected'; the collector comes from --gc-algorithm.")
                continue
            if name == 'ZGenerational':
                notes.append(f"Removed {tok} from the baseline: set by --gc-algorithm/--jdk (invalid on JDK 17, "
                             f"obsolete since JDK 24).")
                continue
            if tok.startswith('-Xloggc') or tok.startswith('-verbose:gc'):
                notes.append(f"Removed {tok} from the baseline: GC logging is configured with -Xlog below.")
                continue
            if name in LEGACY_FLAGS:
                fatal, hint = LEGACY_FLAGS[name]
                notes.append(f"Removed {tok} from the baseline: "
                             f"{'removed flag, the JVM would refuse to start' if fatal else 'deprecated'} ({hint}).")
                continue
            if name == 'UseBiasedLocking':
                notes.append(f"Removed {tok}: biased locking was removed (unrecognized on JDK 21+, deprecated on 17).")
                continue
            kept.append(tok)
        return kept, notes

    def _baseline_code_cache_mb(self) -> Optional[int]:
        for tok in self._baseline_tokens:
            m = re.match(r'^-XX:ReservedCodeCacheSize=(\d+)([kKmMgG]?)$', tok)
            if m:
                v, u = int(m.group(1)), m.group(2).lower()
                return {'k': v // 1024, 'm': v, 'g': v * 1024, '': v // (1024 * 1024)}[u]
        return None

    # ------------------------------------------------------------------ sizing
    def estimated_threads(self) -> int:
        """Rough total thread count: io worker tasks + io threads + EJB pool + ~100 JVM/WildFly service threads"""
        tp = self.get_thread_pool_sizes()
        return tp['io_worker_task_max_threads'] + tp['io_worker_io_threads'] + tp['ejb3_thread_pool_max_threads'] + 100

    def heap_percent_used(self) -> float:
        return self.heap_percent if self.heap_percent is not None else self.HEAP_PERCENT[self.workload]

    def heap_breakdown(self) -> Dict:
        ram = self.machine['memory_gb'] * 1024
        os_reserve = max(1024, int(ram * 0.10))
        code_cache = self.calculate_code_cache()
        threads_mb = self.estimated_threads()  # 1 MB stack per thread (-Xss default on x64)
        fixed = os_reserve + code_cache + self.METASPACE_BUDGET_MB + threads_mb
        cap = (ram - fixed) / (1 + self.GC_NATIVE_OVERHEAD)
        target = ram * self.heap_percent_used() / 100.0
        raw = min(target, cap)
        step = 512 if raw >= 4096 else 256
        heap = max(512, int(raw // step) * step)
        limited_by = 'percent' if target <= cap else 'headroom'
        if self.gc_algorithm == 'g1gc' and self.COMPRESSED_OOPS_MAX_MB < heap < self.NO_OOPS_WORTH_IT_MB:
            heap, limited_by = self.COMPRESSED_OOPS_MAX_MB, 'compressed-oops (31 GB)'
        return {
            'ram_mb': int(ram), 'percent': self.heap_percent_used(), 'target_mb': int(target), 'cap_mb': int(cap),
            'os_reserve_mb': os_reserve, 'code_cache_mb': code_cache, 'metaspace_mb': self.METASPACE_BUDGET_MB,
            'thread_stacks_mb': threads_mb, 'gc_overhead_mb': int(heap * self.GC_NATIVE_OVERHEAD),
            'heap_mb': heap, 'limited_by': limited_by,
        }

    def calculate_heap_size(self) -> int:
        """Recommended max heap (-Xmx) in MB; see heap_breakdown() for the rule"""
        return self.heap_breakdown()['heap_mb']

    def calculate_initial_heap(self) -> int:
        heap = self.calculate_heap_size()
        return heap if self.xms == 'equal' else max(256, heap // 4)

    def recommended_code_cache(self) -> int:
        return 768 if self.machine['memory_gb'] > 64 else 512

    def calculate_code_cache(self) -> int:
        """Effective code cache in MB (the value that ends up in JAVA_OPTS).

        Example vendor baseline: its 512m is a minimum, raised to 768m above 64 GB RAM.
        Custom --baseline-opts that set ReservedCodeCacheSize: kept as given. Otherwise 512m / 768m."""
        from_baseline = self._baseline_code_cache_mb()
        if from_baseline and self.baseline_opts is not None:
            return from_baseline
        return max(from_baseline or 0, self.recommended_code_cache())

    def get_gc_flags(self) -> str:
        """GC flags valid for the selected JDK"""
        if self.gc_algorithm == 'g1gc':
            pause = 300 if self.workload == 'peak-batch' else 200
            return f'-XX:+UseG1GC -XX:MaxGCPauseMillis={pause} -XX:InitiatingHeapOccupancyPercent=35'
        # ZGC: JDK 17 = single-generation only; JDK 21 = generational is opt-in (-XX:+ZGenerational);
        # JDK 23 makes generational the default, JDK 24 removes single-generation mode and obsoletes
        # ZGenerational (warning on 24/25, "Unrecognized VM option" from JDK 26).
        if self.jdk == 21:
            return '-XX:+UseZGC -XX:+ZGenerational'
        return '-XX:+UseZGC'

    def get_logging_flags(self) -> str:
        return f"-Xlog:gc*:file={self.gc_log_path}:time,uptime,level,tags:filecount=10,filesize=100M"

    def get_thread_pool_sizes(self) -> Dict[str, int]:
        """WildFly pool sizes.

        io worker task-max-threads (Undertow request threads) = max(8 x vCPU, 1.5 x datasource pool),
        capped at the WildFly default 16 x vCPU: enough threads to use every pooled connection, not so many
        that most of them just queue on the pool. io-threads = WildFly default 2 x vCPU.
        EJB3 thread pool (async, timers, remote) = max(10, 2 x vCPU)."""
        vcpu = self.machine['vcpu']
        ds = self.get_datasource_pool_size()
        return {
            'io_worker_io_threads': vcpu * 2,
            'io_worker_task_max_threads': min(16 * vcpu, max(8 * vcpu, ds * 3 // 2)),
            'ejb3_thread_pool_max_threads': max(10, vcpu * 2),
        }

    def get_datasource_pool_size(self) -> int:
        """Datasource max-pool-size (reference database connection budget: 100 per datasource)"""
        return 100 if self.workload == 'peak-batch' else 50

    def get_recommendations(self) -> Dict:
        return {
            'machine_type': self.machine_type,
            'vcpu': self.machine['vcpu'],
            'memory_gb': self.machine['memory_gb'],
            'workload': self.workload,
            'jdk': self.jdk,
            'gc_algorithm': self.gc_algorithm,
            'heap_mb': self.calculate_heap_size(),
            'initial_heap_mb': self.calculate_initial_heap(),
            'code_cache_mb': self.calculate_code_cache(),
            'heap_breakdown': self.heap_breakdown(),
            'thread_pools': self.get_thread_pool_sizes(),
            'datasource_pool_size': self.get_datasource_pool_size(),
            'gc_flags': self.get_gc_flags(),
        }

    # ------------------------------------------------------------------ output
    def generate_java_opts(self) -> str:
        """Complete JAVA_OPTS string: baseline, heap, code cache, GC, GC logging"""
        heap_mb = self.calculate_heap_size()
        cc = f"-XX:ReservedCodeCacheSize={self.calculate_code_cache()}m"
        opts, cc_done = [], False
        for tok in self._baseline_tokens:
            if tok.startswith('-XX:ReservedCodeCacheSize='):
                tok, cc_done = cc, True          # one code cache flag, with the effective value
            opts.append(tok)
        opts += [f"-Xms{self.calculate_initial_heap()}m", f"-Xmx{heap_mb}m"]
        if not cc_done:
            opts.append(cc)
        opts.append(self.get_gc_flags())
        opts.append(self.get_logging_flags())
        return ' '.join(opts)

    @staticmethod
    def _hcl_string(s: str) -> str:
        return s.replace('\\', '\\\\').replace('"', '\\"').replace('${', '$${').replace('%{', '%%{')

    def generate_tfvars(self) -> str:
        """Generate Terraform tfvars format"""
        rec = self.get_recommendations()
        tp = rec['thread_pools']
        return f"""# Generated JVM configuration for {self.machine_type}
# Workload: {self.workload}, JDK {self.jdk}, GC: {self.gc_algorithm}
# Heap rule: min({rec['heap_breakdown']['percent']:g}% of RAM, RAM - headroom)
# Generated by jvm-flag-recommender.py

wildfly_machine_type = "{self.machine_type}"
wildfly_jdk_version = {self.jdk}
wildfly_heap_size_mb = {rec['heap_mb']}
wildfly_heap_size_gb = {rec['heap_mb'] // 1024}
wildfly_java_opts = "{self._hcl_string(self.generate_java_opts())}"
wildfly_io_worker_io_threads = {tp['io_worker_io_threads']}
wildfly_io_worker_task_max_threads = {tp['io_worker_task_max_threads']}
wildfly_ejb3_thread_pool_max_threads = {tp['ejb3_thread_pool_max_threads']}
wildfly_datasource_max_pool = {rec['datasource_pool_size']}
wildfly_datasource_min_pool = 0
wildfly_deployment_timeout_seconds = 600
"""

    def print_report(self):
        rec = self.get_recommendations()
        hb = rec['heap_breakdown']
        tp = rec['thread_pools']
        java_opts = self.generate_java_opts()

        print("\n" + "=" * 70)
        print("JVM CONFIGURATION RECOMMENDATIONS")
        print("=" * 70)

        print(f"\nMACHINE CONFIGURATION:")
        print(f"  Machine type: {rec['machine_type']}")
        print(f"  vCPU: {rec['vcpu']}")
        print(f"  Memory: {rec['memory_gb']:g}GB")
        print(f"  Workload: {rec['workload']}")
        print(f"  JDK: {rec['jdk']}")

        print(f"\nHEAP CONFIGURATION:")
        print(f"  Heap size (-Xmx): {rec['heap_mb']}MB ({rec['heap_mb'] / 1024:.1f}GB)")
        print(f"  Initial heap (-Xms): {rec['initial_heap_mb']}MB"
              f"{' (= -Xmx)' if self.xms == 'equal' else ' (1/4 of max, grows on demand; --xms equal to pin)'}")
        print(f"  Code cache: {rec['code_cache_mb']}MB")
        print(f"  Rule: heap = min({hb['percent']:g}% of RAM = {hb['target_mb']}MB, RAM - headroom = {hb['cap_mb']}MB), "
              f"rounded down; limited by {hb['limited_by']}")
        print(f"  Headroom: OS reserve {hb['os_reserve_mb']}MB + code cache {hb['code_cache_mb']}MB + metaspace "
              f"{hb['metaspace_mb']}MB + thread stacks ~{hb['thread_stacks_mb']}MB + GC native ~10% of heap")
        footprint = rec['heap_mb'] + hb['code_cache_mb'] + hb['metaspace_mb'] + hb['thread_stacks_mb'] + hb['gc_overhead_mb']
        print(f"  Estimated JVM footprint: ~{footprint}MB of {hb['ram_mb']}MB RAM")

        print(f"\nGC CONFIGURATION:")
        print(f"  Algorithm: {rec['gc_algorithm'].upper()}")
        print(f"  Flags: {rec['gc_flags']}")
        print(f"  Logging: {self.get_logging_flags()}")

        print(f"\nWILDFLY THREAD POOLS:")
        print(f"  io subsystem worker io-threads: {tp['io_worker_io_threads']} (WildFly default: 2 x vCPU)")
        print(f"  io subsystem worker task-max-threads: {tp['io_worker_task_max_threads']} "
              f"(max(8 x vCPU, 1.5 x datasource pool), capped at the default 16 x vCPU)")
        print(f"  ejb3 thread-pool max-threads: {tp['ejb3_thread_pool_max_threads']} (async, timers, remote EJB)")

        print(f"\nDATABASE CONFIGURATION:")
        print(f"  DataSource max-pool-size: {rec['datasource_pool_size']} (bounded by the database connection budget)")

        print(f"\nCOMPLETE JAVA_OPTS:")
        print(f"  {java_opts}")

        print(f"\nTERRAFORM VARIABLES:")
        for line in self.generate_tfvars().split('\n'):
            if line.strip():
                print(f"  {line}")

        print("\n" + "=" * 70)

    def validation_warnings(self) -> List[str]:
        warnings = list(self._baseline_notes)
        hb = self.heap_breakdown()
        footprint = hb['heap_mb'] + hb['code_cache_mb'] + hb['metaspace_mb'] + hb['thread_stacks_mb'] + hb['gc_overhead_mb']
        if footprint > hb['ram_mb'] - hb['os_reserve_mb']:
            warnings.append(f"WARNING: estimated JVM footprint {footprint}MB leaves less than the OS reserve "
                            f"({hb['os_reserve_mb']}MB) on a {hb['ram_mb']}MB machine.")
        if self.workload == 'peak-batch' and self.machine['memory_gb'] < 32:
            warnings.append(f"WARNING: Peak-batch workload on {self.machine['memory_gb']:g}GB machine. Consider 32GB+.")
        if self.gc_algorithm == 'zgc':
            if self.machine['memory_gb'] < 32:
                warnings.append(f"WARNING: ZGC on a {self.machine['memory_gb']:g}GB machine: ZGC needs heap headroom "
                                f"(live set well below -Xmx) to avoid allocation stalls; G1 is usually better on small heaps.")
            if self.jdk == 17:
                warnings.append("WARNING: JDK 17 ZGC is single-generation only (more CPU and headroom needed for high "
                                "allocation rates). Prefer JDK 21+ generational ZGC or G1.")
            if self.jdk == 25:
                warnings.append("NOTE: JDK 24+ ZGC is always generational; do not add -XX:+ZGenerational (obsolete, "
                                "warning on 24/25, the JVM refuses to start from JDK 26).")
        if self.baseline_opts is not None and self._baseline_code_cache_mb() and \
                self._baseline_code_cache_mb() < self.recommended_code_cache():
            warnings.append(f"NOTE: baseline ReservedCodeCacheSize={self._baseline_code_cache_mb()}m kept; "
                            f"{self.recommended_code_cache()}m is suggested for this machine size.")
        if self.heap_breakdown()['limited_by'].startswith('compressed-oops'):
            warnings.append("NOTE: heap capped at 31 GB: with G1 a heap of 32 GB or more disables compressed oops, "
                            "so anything below ~48 GB holds fewer objects than 31 GB.")
        if self.calculate_code_cache() > 2048:
            warnings.append("WARNING: ReservedCodeCacheSize above 2 GB is not allowed by HotSpot.")
        if '/' in self.gc_log_path or self.gc_log_path.startswith('.'):
            where = self.gc_log_path.rsplit('/', 1)[0] or '/'
            warnings.append(f"NOTE: the GC log directory '{where}' must exist and be writable, otherwise the JVM "
                            f"refuses to start (Invalid -Xlog option). Relative paths are resolved against the JVM "
                            f"working directory.")
        else:
            warnings.append(f"NOTE: GC log '{self.gc_log_path}' is relative to the JVM working directory; "
                            f"pass --gc-log-path with an absolute path in an existing, writable directory.")
        warnings.append("NOTE: with -Xlog:gc in JAVA_OPTS, WildFly's standalone.sh does not add its own GC log. "
                        "Alternative: drop the -Xlog flag and set GC_LOG=\"true\" in standalone.conf (logs to "
                        "$JBOSS_LOG_DIR/gc.log, directory created by the script).")
        return warnings

    def print_validation_warnings(self):
        warnings = self.validation_warnings()
        if warnings:
            print(f"\nVALIDATION WARNINGS:")
            for warning in warnings:
                print(f"  - {warning}")

    def print_comparison(self):
        print(f"\nCOMPARISON WITH EXAMPLE VENDOR BASELINE:")
        print(f"  Example baseline heap: 8192m")
        print(f"  Recommended heap: {self.calculate_heap_size()}m")
        print(f"  Example baseline code cache: 512m")
        print(f"  Recommended code cache: {self.calculate_code_cache()}m")
        print(f"  Example baseline GC algorithm: G1GC")
        print(f"  Recommended: {self.gc_algorithm.upper()}")


def main():
    parser = argparse.ArgumentParser(
        description='Recommend JVM flags (JDK 17/21/25) and WildFly pool sizes for a heavy-load application',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""heap rule:
  heap = min(P% of RAM, RAM - headroom), rounded down to 512 MB (256 MB below 4 GB), min 512 MB
  P = 50 standard, 60 peak-batch, 70 high-memory (override with --heap-percent)
  headroom = OS reserve max(1 GB, 10% RAM) + code cache + metaspace budget 512 MB
             + thread stacks (1 MB x estimated threads) + GC native overhead (10% of heap)
  e.g. 32 GB / standard -> 16 GB heap; 8 GB -> 4 GB heap

JDK-specific GC flags:
  --gc-algorithm zgc: JDK 17 -XX:+UseZGC (single-generation); JDK 21 -XX:+UseZGC -XX:+ZGenerational;
  JDK 25 -XX:+UseZGC (generational only; ZGenerational is obsolete since 24)""")
    parser.add_argument('--machine-type',
                        choices=list(JVMRecommender.MACHINES.keys()),
                        help='Predefined machine type (GCP names as examples)')
    parser.add_argument('--vcpu', type=int,
                        help='vCPU count (use with --memory-gb instead of --machine-type)')
    parser.add_argument('--memory-gb', type=float,
                        help='Machine memory in GB (use with --vcpu instead of --machine-type)')
    parser.add_argument('--workload', default='standard',
                        choices=['standard', 'peak-batch', 'high-memory'],
                        help='Workload type (peak-batch = period-end / batch-heavy windows); sets the heap %% of RAM')
    parser.add_argument('--gc-algorithm', default='g1gc',
                        choices=['g1gc', 'zgc'],
                        help='Garbage collection algorithm')
    parser.add_argument('--jdk', type=int, default=21, choices=list(JVMRecommender.SUPPORTED_JDKS),
                        help='Target JDK feature release; GC flags are emitted for it (default: %(default)s)')
    parser.add_argument('--heap-percent', type=float,
                        help='Override the heap share of RAM (10-85); still capped by the headroom')
    parser.add_argument('--xms', default='quarter', choices=['quarter', 'equal'],
                        help='-Xms as 1/4 of -Xmx (default) or equal to -Xmx (fixed-size heap)')
    parser.add_argument('--gc-log-path', default='gc.log',
                        help='GC log file for -Xlog (its directory must exist; default: %(default)s, '
                             'relative to the JVM working directory)')
    parser.add_argument('--baseline-opts',
                        help='Your baseline JVM flags to prepend (default: none). Heap sizes, GC selectors '
                             'and removed JDK 8 flags are dropped from it')
    parser.add_argument('--vendor-baseline', action='store_true',
                        help='Prepend the example vendor-mandated baseline '
                             '("-Xrs -server -XX:ReservedCodeCacheSize=512m -XX:-TieredCompilation"). '
                             'Only when your vendor requires it: -Xrs disables kill -3 dumps and shutdown hooks on SIGTERM')
    parser.add_argument('--output', help='Output file for Terraform-style variables (HCL)')
    parser.add_argument('--format', default='report',
                        choices=['report', 'tfvars', 'java-opts'],
                        help='Output format (tfvars = Terraform-style HCL variables)')

    args = parser.parse_args()

    use_custom = args.vcpu is not None or args.memory_gb is not None
    if use_custom and (args.vcpu is None or args.memory_gb is None):
        parser.error('--vcpu and --memory-gb must be given together')
    if not use_custom and not args.machine_type:
        parser.error('either --machine-type or --vcpu/--memory-gb is required')
    if args.vendor_baseline and args.baseline_opts is not None:
        parser.error('--vendor-baseline and --baseline-opts are mutually exclusive')

    machine_type = args.machine_type
    if use_custom:
        machine_type = f"custom-{args.vcpu}vcpu-{args.memory_gb:g}gb"

    try:
        recommender = JVMRecommender(machine_type, args.workload, args.gc_algorithm,
                                     vcpu=args.vcpu, memory_gb=args.memory_gb,
                                     baseline_opts=args.baseline_opts, jdk=args.jdk,
                                     heap_percent=args.heap_percent, gc_log_path=args.gc_log_path,
                                     xms=args.xms, vendor_baseline=args.vendor_baseline)

        if args.format == 'report':
            recommender.print_report()
            recommender.print_validation_warnings()
            if args.vendor_baseline:
                recommender.print_comparison()
        elif args.format == 'tfvars':
            print(recommender.generate_tfvars())
        elif args.format == 'java-opts':
            print(recommender.generate_java_opts())
            for w in recommender.validation_warnings():
                if not w.startswith('NOTE: GC log'):
                    print(w, file=sys.stderr)

        if args.output:
            with open(args.output, 'w') as f:
                f.write(recommender.generate_tfvars())
            print(f"\nConfiguration saved to {args.output}", file=sys.stderr if args.format != 'report' else sys.stdout)

    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
