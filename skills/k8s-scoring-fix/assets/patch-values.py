#!/usr/bin/env python3
"""
Patch helm values-*.yaml for k8s scoring fixes:
- Reduce CPU requests
- Enable liveness/readiness probes
- Optionally adjust memory requests

Usage:
  python3 patch-values.py <values-file.yaml> [--dry-run]

Edit the SERVICE_CPU_REQUESTS dict below before running.
"""

import re
import sys
import argparse

# ── Configure per project ──────────────────────────────────────────────────
# Map service name → new CPU request
SERVICE_CPU_REQUESTS = {
    # Java Spring Boot services
    # 'orders-service':    '100m',
    # 'billing-service':   '100m',
    # 'catalog-service':   '100m',
    # 'pricing-service':   '100m',
    # 'shop-liquibase':    '50m',
    # Frontend (nginx/angular) — names containing 'front' are treated as the app: block
    # 'shop-front':        '50m',
}

# Optional memory request changes: service → new memory request
SERVICE_MEM_REQUESTS = {
    # 'catalog-service': '1536Mi',
}

# Probe config for Java Spring Boot (4-space indent, inside apis[])
LIVENESS_JAVA = """\
    livenessProbe:
      httpGet:
        path: /actuator/health
        port: 8080
      initialDelaySeconds: 60
      periodSeconds: 30
      failureThreshold: 3
      timeoutSeconds: 5"""

READINESS_JAVA = """\
    readinessProbe:
      httpGet:
        path: /actuator/health
        port: 8080
      initialDelaySeconds: 30
      periodSeconds: 10
      failureThreshold: 3
      timeoutSeconds: 5"""

# Probe config for frontend (2-space indent, inside app:)
# Frontend health path: never "/" behind mod_auth_openidc (OIDC redirect) — see SKILL.md Fix B.
# Port: 8080 when the frontend runs non-root (Fix E), else 80.
LIVENESS_FRONT = """\
  livenessProbe:
    httpGet:
      path: /health-check
      port: 8080
    initialDelaySeconds: 30
    periodSeconds: 30
    failureThreshold: 3
    timeoutSeconds: 5"""

READINESS_FRONT = """\
  readinessProbe:
    httpGet:
      path: /health-check
      port: 8080
    initialDelaySeconds: 15
    periodSeconds: 10
    failureThreshold: 3
    timeoutSeconds: 5"""

# ──────────────────────────────────────────────────────────────────────────


def patch_service_cpu(text, service_name, cpu_old, cpu_new, mem_old=None, mem_new=None):
    """Patch cpu request (and optionally memory request) inside a named service block."""
    pattern = re.compile(
        r'(- name: ' + re.escape(service_name) + r'.*?'
        r'requests:\s*\n(?:\s+cpu:.*\n)?(?:\s+memory:.*\n)?)',
        re.DOTALL
    )
    def replace_block(m):
        block = m.group(0)
        block = re.sub(r'(\s+cpu:\s*)' + re.escape(cpu_old), r'\g<1>' + cpu_new, block, count=1)
        if mem_old and mem_new:
            block = re.sub(r'(\s+memory:\s*)' + re.escape(mem_old), r'\g<1>' + mem_new, block, count=1)
        return block
    return pattern.sub(replace_block, text)


def patch_app_cpu(text, cpu_old, cpu_new):
    """Patch cpu request in the app: block (frontend)."""
    pattern = re.compile(r'(name: .*?front.*?requests:\s*\n(?:\s+cpu:.*\n)?)', re.DOTALL)
    def replace_block(m):
        block = m.group(0)
        block = re.sub(r'(\s+cpu:\s*)' + re.escape(cpu_old), r'\g<1>' + cpu_new, block, count=1)
        return block
    return pattern.sub(replace_block, text)


def main():
    parser = argparse.ArgumentParser(description='Patch helm values for k8s scoring fixes')
    parser.add_argument('values_file', help='Path to values-*.yaml file')
    parser.add_argument('--dry-run', action='store_true', help='Print diff without writing')
    args = parser.parse_args()

    with open(args.values_file, 'r') as f:
        content = f.read()

    original = content

    # Apply CPU request patches
    for service, new_cpu in SERVICE_CPU_REQUESTS.items():
        mem_new = SERVICE_MEM_REQUESTS.get(service)
        mem_match = re.search(r'- name: ' + re.escape(service) + r'.*?requests:.*?memory:\s*(\S+)', content, re.DOTALL) if mem_new else None
        mem_old = mem_match.group(1) if mem_match else None
        if 'front' in service:
            # need to know old value — find it
            old_match = re.search(r'name: ' + re.escape(service) + r'.*?requests:.*?cpu:\s*(\S+)', content, re.DOTALL)
            if old_match:
                content = patch_app_cpu(content, old_match.group(1), new_cpu)
        else:
            old_match = re.search(r'- name: ' + re.escape(service) + r'.*?requests:.*?cpu:\s*(\S+)', content, re.DOTALL)
            if old_match:
                old_cpu = old_match.group(1)
                content = patch_service_cpu(content, service, old_cpu, new_cpu, mem_old, mem_new)

    # Apply probe patches
    content = content.replace('    livenessProbe: { }', LIVENESS_JAVA)
    content = content.replace('    readinessProbe: { }', READINESS_JAVA)
    content = content.replace('  livenessProbe: { }', LIVENESS_FRONT)
    content = content.replace('  readinessProbe: { }', READINESS_FRONT)

    if args.dry_run:
        import difflib
        diff = difflib.unified_diff(original.splitlines(), content.splitlines(),
                                    fromfile='original', tofile='patched', lineterm='')
        print('\n'.join(list(diff)))
    else:
        with open(args.values_file, 'w') as f:
            f.write(content)
        print(f"Patched: {args.values_file}")


if __name__ == '__main__':
    main()
