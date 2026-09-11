#!/usr/bin/env python3
"""
Generate patched deployment manifests for kubectl diff preview.

Usage:
  kubectl get deployment -n <namespace> svc1 svc2 ... -o yaml > /tmp/current.yaml
  python3 generate-patched-manifests.py /tmp/current.yaml /tmp/patched.yaml
  kubectl diff -n <namespace> -f /tmp/patched.yaml

Requires PyYAML. Only the container whose name equals the Deployment name is patched.
Edit the CONFIG section below before running.
"""

import yaml
import copy
import sys

# ── Configure per project ──────────────────────────────────────────────────
CPU_REQUESTS = {
    # 'orders-service':    '100m',
    # 'billing-service':   '100m',
    # 'shop-liquibase':    '50m',
    # 'catalog-service':   '100m',
    # 'pricing-service':   '100m',
    # 'shop-front':        '50m',
}

MEM_REQUESTS = {
    # 'catalog-service': '1536Mi',
}

LIVENESS_JAVA = {
    'httpGet': {'path': '/actuator/health', 'port': 8080},
    'initialDelaySeconds': 60,
    'periodSeconds': 30,
    'failureThreshold': 3,
    'timeoutSeconds': 5
}
READINESS_JAVA = {
    'httpGet': {'path': '/actuator/health', 'port': 8080},
    'initialDelaySeconds': 30,
    'periodSeconds': 10,
    'failureThreshold': 3,
    'timeoutSeconds': 5
}
# Frontend health path: never "/" behind mod_auth_openidc (OIDC redirect) — see SKILL.md Fix B.
# Port: 8080 when the frontend runs non-root (Fix E), else 80.
LIVENESS_FRONT = {
    'httpGet': {'path': '/health-check', 'port': 8080},
    'initialDelaySeconds': 30,
    'periodSeconds': 30,
    'failureThreshold': 3,
    'timeoutSeconds': 5
}
READINESS_FRONT = {
    'httpGet': {'path': '/health-check', 'port': 8080},
    'initialDelaySeconds': 15,
    'periodSeconds': 10,
    'failureThreshold': 3,
    'timeoutSeconds': 5
}
# ──────────────────────────────────────────────────────────────────────────


def main():
    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} <input.yaml> <output.yaml>")
        sys.exit(1)

    with open(sys.argv[1]) as f:
        data = yaml.safe_load(f)

    patched = copy.deepcopy(data)
    docs = []

    items = patched.get('items', [patched])

    for dep in items:
        name = dep['metadata']['name']

        # Strip server-side-only fields that cause kubectl diff issues
        meta = dep['metadata']
        for field in ['managedFields', 'resourceVersion', 'uid', 'creationTimestamp', 'generation']:
            meta.pop(field, None)
        dep.pop('status', None)

        for c in dep['spec']['template']['spec']['containers']:
            if c['name'] == name:
                # CPU request
                if name in CPU_REQUESTS:
                    c.setdefault('resources', {}).setdefault('requests', {})['cpu'] = CPU_REQUESTS[name]

                # Memory request
                if name in MEM_REQUESTS:
                    c.setdefault('resources', {}).setdefault('requests', {})['memory'] = MEM_REQUESTS[name]

                # Probes — only add if not already configured
                is_front = 'front' in name
                if not c.get('livenessProbe'):
                    c['livenessProbe'] = LIVENESS_FRONT if is_front else LIVENESS_JAVA
                if not c.get('readinessProbe'):
                    c['readinessProbe'] = READINESS_FRONT if is_front else READINESS_JAVA

        docs.append({
            'apiVersion': 'apps/v1',
            'kind': 'Deployment',
            'metadata': dep['metadata'],
            'spec': dep['spec']
        })

    with open(sys.argv[2], 'w') as f:
        yaml.dump_all(docs, f, default_flow_style=False)

    print(f"Patched {len(docs)} deployments → {sys.argv[2]}")
    print(f"Now run: kubectl diff -n <namespace> -f {sys.argv[2]}")


if __name__ == '__main__':
    main()
