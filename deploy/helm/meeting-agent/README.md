# meeting-agent Helm Chart

Deploys the Meeting-Agent full-stack application (backend + frontend) to Kubernetes.

## Quick Start

```bash
# Install with required secrets
helm install meeting-agent ./deploy/helm/meeting-agent \
  --namespace meeting-agent --create-namespace \
  --set backend.secrets.API_KEY=$(python -c "import secrets; print(secrets.token_hex(32))") \
  --set backend.secrets.LLM_API_KEY="$YOUR_LLM_KEY" \
  --set backend.secrets.ASSEMBLYAI_API_KEY="$YOUR_ASR_KEY" \
  --set ingress.tls[0].hosts[0]=meeting-agent.example.com \
  --set ingress.tls[0].secretName=meeting-agent-tls
```

## Production Checklist

- [ ] **TLS configured** — `ingress.tls` is required. The chart will refuse to render without it (unless `ingress.tlsDisabled=true`).
- [ ] **Secrets injected** — Never commit secrets to values files. Use `--set`, overlay files, or External Secrets Operator.
- [ ] **PRINCIPAL_PEPPER set** — Required in production for user-id isolation. Generate with:
  ```bash
  python -c "import secrets; print(secrets.token_hex(32))"
  ```
- [ ] **Single replica** — Multi-worker mode is not supported (circuit breaker / rate-limiter state is process-local). Use `backend.replicaCount=1`.
- [ ] **NetworkPolicy enabled** — Set `networkPolicy.enabled=true` in multi-tenant clusters.
- [ ] **StorageClass specified** — Set `persistence.storageClassName` to your cluster's StorageClass (empty string uses the default provisioner).

## Configuration

| Parameter | Description | Default |
|-----------|-------------|---------|
| `backend.replicaCount` | Backend pods (must be 1) | `1` |
| `backend.image.repository` | Backend image | `ghcr.io/lixiuyin/meeting-agent-backend` |
| `backend.secretName` | K8s Secret for env vars | `meeting-agent-secrets` |
| `frontend.replicaCount` | Frontend pods | `1` |
| `ingress.enabled` | Enable ingress | `true` |
| `ingress.tlsDisabled` | Skip TLS validation (dev only) | `false` |
| `ingress.tls` | TLS configuration | `[]` (must be set) |
| `persistence.enabled` | Enable PVC | `true` |
| `persistence.size` | PVC size | `10Gi` |
| `persistence.storageClassName` | StorageClass (set explicitly) | `""` |
| `networkPolicy.enabled` | Enable NetworkPolicy | `false` |
| `autoscaling.enabled` | Enable HPA | `false` |

## TLS Setup

Create a TLS secret before installing:

```bash
# With cert-manager
kubectl apply -f - <<EOF
apiVersion: cert-manager.io/v1
kind: Certificate
metadata:
  name: meeting-agent-tls
spec:
  secretName: meeting-agent-tls
  issuerRef:
    name: letsencrypt-prod
    kind: ClusterIssuer
  dnsNames:
    - meeting-agent.example.com
EOF

# Or manually
kubectl create secret tls meeting-agent-tls \
  --cert=path/to/tls.crt \
  --key=path/to/tls.key
```

Then reference it in the ingress:

```yaml
ingress:
  tls:
    - hosts:
        - meeting-agent.example.com
      secretName: meeting-agent-tls
```
