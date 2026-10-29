# Regional scheduler extender POC

This is a small, opt-in scheduling experiment for the upstream [e-commerce microservices sample](https://github.com/venkataravuri/e-commerce-microservices-sample). The upstream app contains Store UI, products, cart, users, and search services deployed with Kustomize under the `e-commerce` namespace. This directory does not copy those manifests or images.

## Architecture and compatibility

The POC runs the stock `kube-scheduler` v1.35.9 with a separate profile named `regional-poc-scheduler` and a small HTTP scheduler extender. It uses the stable `kubescheduler.config.k8s.io/v1` configuration API and targets Kubernetes 1.35.x (matching the scheduler binary to the control-plane minor version is recommended).

The stock scheduler performs its normal filtering first, including node readiness, taints/tolerations, affinity, volume constraints, and CPU/memory requests. The extender then filters nodes below a simulated available-capacity threshold and returns a weighted 0–10 priority score. The scheduler still performs final binding. Only pods with `spec.schedulerName: regional-poc-scheduler` use this path; default-scheduled workloads are unaffected.

Signals are labels you set manually for this local POC, not live telemetry:

| Signal | Key | Meaning |
| --- | --- | --- |
| Pod region | annotation `placement.demo/region` | Region the workload is serving |
| Node region | label `placement.demo/region` | Simulated node region |
| Regional demand | label `placement.demo/demand` | Demand from 0–100; matching-region locality is weighted by this value |
| Network latency | label `placement.demo/latency-ms` | Simulated latency; lower is better, normalized against 200 ms |
| Available capacity | label `placement.demo/available-capacity` | Simulated free-capacity percentage; filters below 10 and scores higher values better |

The default score weights are locality 3, latency 2, capacity 2. They and the minimum capacity/latency bound are environment-configurable on the extender Deployment. The formula is the weighted average of the three 0–100 component scores, scaled and rounded to 0–10. Missing demand/latency contributes zero; missing/invalid capacity is filtered out.

## Prerequisites

- A disposable Kubernetes 1.35.x cluster with at least two schedulable worker nodes.
- `kubectl` pointed at that local cluster, plus Docker and a local-cluster image load command (`minikube image load` or `kind load docker-image`).
- Python 3.11+ to run the unit tests (the extender itself uses only the Python standard library).

Do not install this experimental scheduler in a production or shared cluster. The scheduler and extender are single replicas, and extender traffic is plain HTTP inside the cluster. `ignorable: false` deliberately fails closed if the extender is unavailable, so selected pods can remain Pending.

## Build and install

Run from the repository root. Build and load the image into the local cluster:

```sh
docker build -t regional-scheduler-extender:dev "Custom Scheduler/extender"
# Choose the command for your local cluster:
minikube image load regional-scheduler-extender:dev
# or: kind load docker-image regional-scheduler-extender:dev
```

Install the config and scheduler resources:

```sh
kubectl create namespace custom-scheduler --dry-run=client -o yaml | kubectl apply -f -
kubectl -n custom-scheduler create configmap regional-scheduler-config \
  --from-file=scheduler-config.yaml="Custom Scheduler/scheduler-config.yaml" \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -f "Custom Scheduler/cust-scheduler.yaml"
kubectl -n custom-scheduler rollout status deployment/regional-scheduler
kubectl -n custom-scheduler rollout status deployment/regional-scheduler-extender
```

The scheduler uses the standard `system:kube-scheduler` and `system:volume-scheduler` cluster roles, bound only to its dedicated service account. The extender has no Kubernetes API token. This is still cluster-scoped scheduling authority; review the RBAC and never apply it to a shared production cluster.

## Repeatable scheduling demonstration

1. List two worker nodes and give them simulated signals (replace node names as appropriate):

```sh
kubectl get nodes
kubectl label node worker-a placement.demo/region=us-east placement.demo/demand=30 placement.demo/latency-ms=150 placement.demo/available-capacity=80 --overwrite
kubectl label node worker-b placement.demo/region=us-west placement.demo/demand=60 placement.demo/latency-ms=20 placement.demo/available-capacity=80 --overwrite
```

2. Apply a baseline pod and a custom-scheduled pod with identical resource requests:

```sh
kubectl apply -f - <<'YAML'
apiVersion: v1
kind: Pod
metadata:
  name: placement-baseline
  namespace: default
spec:
  schedulerName: default-scheduler
  containers:
    - name: pause
      image: registry.k8s.io/pause:3.10
      resources:
        requests:
          cpu: 100m
          memory: 64Mi
---
apiVersion: v1
kind: Pod
metadata:
  name: placement-custom
  namespace: default
  annotations:
    placement.demo/region: us-east
spec:
  schedulerName: regional-poc-scheduler
  containers:
    - name: pause
      image: registry.k8s.io/pause:3.10
      resources:
        requests:
          cpu: 100m
          memory: 64Mi
YAML
kubectl get pods placement-baseline placement-custom -o wide
kubectl describe pod placement-custom
kubectl -n custom-scheduler logs deployment/regional-scheduler-extender
```

The baseline is handled by the cluster's default scheduler. The custom pod is filtered/scored by the extender; its logs show candidate scores, and its Pod event and `spec.nodeName` show the actual decision. These are placement observations, not latency benchmarks.

3. Change the simulated east demand and recreate only the custom pod. With the defaults above, east starts below west (score about 4 vs 5); raising east demand to 80 makes east rank above west (about 6 vs 5):

```sh
kubectl label node worker-a placement.demo/demand=80 --overwrite
kubectl delete pod placement-custom
# Re-apply the custom pod manifest above, then inspect its score log and nodeName again.
kubectl get pod placement-custom -o wide
kubectl -n custom-scheduler logs deployment/regional-scheduler-extender
```

4. Verify hard resource constraints remain authoritative by changing a custom pod's requests to something no node can satisfy (for example, `cpu: "1000"`) and recreating it. It must remain Pending with a default scheduler `Insufficient cpu` event; because infeasible nodes never reach the extender, a high simulated score cannot select them. The extender's separate minimum-capacity filter also removes a candidate below the configured threshold.

To opt an upstream workload in after deploying the upstream Kustomize app, patch its existing `users-deployment` Pod template (verified upstream name) to set both the scheduler profile and region annotation. This changes only that selected Deployment:

```sh
kubectl patch deployment users-deployment -n e-commerce --type=merge \
  -p '{"spec":{"template":{"metadata":{"annotations":{"placement.demo/region":"us-east"}},"spec":{"schedulerName":"regional-poc-scheduler"}}}}'
kubectl rollout status deployment/users-deployment -n e-commerce
kubectl get pods -n e-commerce -l app=users-deployment -o wide
kubectl describe pod -n e-commerce -l app=users-deployment
```

The upstream deployment currently omits CPU/memory requests; for meaningful resource-fit behavior, add realistic requests through your own overlay before using it for the experiment. Do not edit the upstream repository as part of this POC.

## Tests and validation

Run the scoring and filtering tests from this directory:

```sh
python -m unittest -v extender/test_scheduler.py
python -m py_compile extender/server.py extender/test_scheduler.py
```

No AWS credentials, network calls, or live metrics are needed for these tests. For manifest validation, use `kubeconform -kubernetes-version 1.35.9 Custom Scheduler/cust-scheduler.yaml` when available, and validate `scheduler-config.yaml` against the v1.35 kube-scheduler configuration schema. The local POC does not deploy or measure application latency.

## Cleanup

Delete the demo pods, revert the upstream Deployment template to `schedulerName: default-scheduler` and remove its region annotation if you patched it. Remove simulated node labels, then remove the POC:

```sh
kubectl delete pod placement-baseline placement-custom --ignore-not-found
kubectl label node worker-a placement.demo/region- placement.demo/demand- placement.demo/latency-ms- placement.demo/available-capacity-
kubectl label node worker-b placement.demo/region- placement.demo/demand- placement.demo/latency-ms- placement.demo/available-capacity-
kubectl delete -f "Custom Scheduler/cust-scheduler.yaml"
```
