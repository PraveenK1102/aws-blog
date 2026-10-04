# Kubernetes manifests — Ask FastAPI backend (Step 2, local only)

First Kubernetes configuration for the existing Ask backend. **Manifest
preparation only** — no EKS, no AWS resources, no Ingress/ALB, no HPA.

These manifests run the **same** container as the local Docker path
(`../lambdas/ask/Dockerfile.local`: plain uvicorn on port 8080, no Lambda Web
Adapter). The production **Lambda** deployment (`../lambdas/ask/Dockerfile` + CI)
is untouched.

## Files
| File | Kind | Purpose |
|---|---|---|
| `ask-deployment.yaml` | Deployment | Runs N replicas of the `multitenant-ask:local` container (port 8080). |
| `ask-service.yaml` | Service (ClusterIP) | Stable in-cluster address that load-balances across the Pods. |

## Local usage
```bash
# 1. Build the image (context = ../lambdas)
cd ../lambdas
docker build -f ask/Dockerfile.local -t multitenant-ask:local .

# 2. (kind only) load the local image into the cluster
#    kind load docker-image multitenant-ask:local
#    (minikube: `minikube image load multitenant-ask:local`)

# 3. Apply
kubectl apply -f ask-deployment.yaml -f ask-service.yaml

# 4. Watch the Pods become Ready
kubectl get pods -l app=ask -w

# 5. Reach /health through the ClusterIP Service (port-forward)
kubectl port-forward svc/ask 8080:8080
curl http://localhost:8080/health      # -> {"ok":true}
```

## What this does NOT do
- No real Groq / Qdrant / Bedrock / DynamoDB / AWS calls (only startup env vars
  are set; `/health` needs no backend).
- No readiness/liveness probes yet.
- No Ingress, LoadBalancer, or ALB.
- No HPA / autoscaling.
- Not deployed to EKS or any cluster in AWS.

## Concepts this step introduces
- **Pod** — the smallest deployable unit; wraps one (or few) containers.
- **Deployment** — declares the desired state ("run N identical Pods of this
  image") and keeps it true (restarts/reschedules Pods as needed).
- **Replica** — one of those N identical Pods; `replicas: 2` here.
- **Service** — a stable name + virtual IP in front of a changing set of Pods.
- **ClusterIP** — a Service type reachable only inside the cluster.
- **selector / labels** — the Service finds its Pods by matching `app: ask`;
  labels are the glue between Deployment, Pods, and Service.
