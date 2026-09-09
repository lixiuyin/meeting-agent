# meeting-agent Helm Chart

Deploys the Meeting-Agent full-stack application (backend + frontend) to Kubernetes.

## Quick Start

```bash
# Install with required secrets
AUTH_HASH="$(openssl passwd -apr1)"
helm install meeting-agent ./deploy/helm/meeting-agent \
  --namespace meeting-agent --create-namespace \
  --set backend.createSecret=true \
  --set-string backend.secrets.API_KEY="$(python -c "import secrets; print(secrets.token_hex(32))")" \
  --set-string backend.secrets.PRINCIPAL_PEPPER="$(python -c "import secrets; print(secrets.token_hex(32))")" \
  --set-string backend.secrets.LLM_API_KEY="$YOUR_LLM_KEY" \
  --set-string backend.secrets.ASSEMBLYAI_API_KEY="$YOUR_ASR_KEY" \
  --set-string backend.secrets.FRONTEND_AUTH_USER="meeting-agent" \
  --set-string backend.secrets.FRONTEND_AUTH_PASSWORD_HASH="$AUTH_HASH" \
  --set-string backend.config.CORS_ORIGINS="https://meeting-agent.example.com" \
  --set ingress.enabled=true \
  --set ingress.tls[0].hosts[0]=meeting-agent.example.com \
  --set ingress.tls[0].secretName=meeting-agent-tls
```

## Production Checklist

- [ ] **TLS configured** — `ingress.tls` is required. The chart will refuse to render without it (unless `ingress.tlsDisabled=true`).
- [ ] **Secrets injected** — Never commit secrets to values files. Use `--set`, overlay files, or External Secrets Operator.
- [ ] **Images pinned by digest** — Set both `backend.image.digest` and `frontend.image.digest` to the tested release digests. Digest values override tags.
- [ ] **PRINCIPAL_PEPPER set** — Required in production for user-id isolation. Generate with:
  ```bash
  python -c "import secrets; print(secrets.token_hex(32))"
  ```
- [ ] **Identity migration checked** — If rotating an existing deployment's
  backend credential, set `backend.config.PRINCIPAL_ID` only to the owner ID
  already present in SQLite. Omitting it derives a new owner from the new key;
  inventing a value does not migrate existing records.
- [ ] **Frontend caller authentication set** — Configure `FRONTEND_AUTH_USER` and `FRONTEND_AUTH_PASSWORD_HASH`, or place an OIDC gateway before the ingress.
- [ ] **Single replica** — Multi-worker mode is not supported (circuit breaker / rate-limiter state is process-local). Use `backend.replicaCount=1`.
- [ ] **NetworkPolicy reviewed** — It is enabled by default; allow the ingress-controller namespace when it is separate.
- [ ] **StorageClass specified** — Set `persistence.storageClassName` to your cluster's StorageClass (empty string uses the default provisioner).

## Configuration

| Parameter | Description | Default |
|-----------|-------------|---------|
| `backend.replicaCount` | Backend pods (must be 1) | `1` |
| `backend.image.repository` | Backend image | `ghcr.io/lixiuyin/meeting-agent-backend` |
| `backend.image.digest` | Immutable backend image digest (preferred; overrides tag) | `""` |
| `backend.secretName` | K8s Secret for env vars | `meeting-agent-secrets` |
| `backend.createSecret` | Create `secretName` from validated `backend.secrets`; enable only for install-time injection | `false` |
| `backend.config.CORS_ORIGINS` | Allowed browser origins | `https://meeting-agent.example.com` |
| `backend.config.PRINCIPAL_ID` | Optional existing owner pinned across API-key rotation; not an account or RBAC system | unset |
| `frontend.replicaCount` | Frontend pods | `1` |
| `frontend.image.digest` | Immutable frontend image digest (preferred; overrides tag) | `""` |
| `ingress.enabled` | Enable ingress | `false` |
| `ingress.tlsDisabled` | Skip TLS validation (dev only) | `false` |
| `ingress.tls` | TLS configuration | `[]` (must be set) |
| `persistence.enabled` | Enable PVC | `true` |
| `persistence.size` | PVC size | `10Gi` |
| `persistence.storageClassName` | StorageClass (set explicitly) | `""` |
| `networkPolicy.enabled` | Enable NetworkPolicy | `true` |

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
