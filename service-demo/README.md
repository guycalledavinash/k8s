# Nginx Deployment

This directory contains a three-replica Nginx Deployment and its ClusterIP Service.

## Health checks, resources, and rolling updates

The readiness probe checks HTTP `/` on port 80 and keeps a Pod out of Service traffic until it is ready. The liveness probe checks the same endpoint and restarts the container if it repeatedly fails.

Resource requests reserve the CPU and memory Kubernetes uses when placing each Pod. Limits cap the container's resource use.

The rolling update strategy allows one extra Pod during an update (`maxSurge: 1`) and keeps all three desired replicas available (`maxUnavailable: 0`). Kubernetes gradually replaces old Pods after new ones become ready.

Apply the Deployment and check its rollout and Pods:

```bash
kubectl apply -f deployment.yml
kubectl rollout status deployment/nginx-deployment
kubectl get pods
kubectl describe deployment nginx-deployment
```
