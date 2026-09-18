# Kubernetes Secret example

These manifests show a safer baseline for mounting database credentials into a
Kubernetes Deployment. They are still examples: do not commit real production
secrets to Git.

## Production checklist

- Replace the `CHANGE_ME_*` placeholders in `secret.yml` before applying, or
  preferably generate this Secret from a secret manager using External Secrets,
  Sealed Secrets, SOPS, or your platform's managed secret integration.
- Enable encryption at rest for Kubernetes Secrets in the cluster.
- Restrict Secret access with RBAC so only the app workload and authorized
  operators can read `sample-python-db-credentials`.
- Rotate credentials regularly and roll pods after each rotation.
- Pin application images by digest in production after validating the image.
- Keep at least three replicas when using the included PodDisruptionBudget,
  so voluntary disruptions can maintain two available pods.

## Health checks and graceful termination

- The Deployment uses an HTTP startup probe on `/` every 5 seconds and permits
  up to 60 seconds for the application to start. Kubernetes does not run the
  liveness or readiness probes until startup succeeds, avoiding restarts for a
  healthy application that is still initializing.
- After startup, readiness checks `/` every 5 seconds so an unhealthy pod is
  removed from Service endpoints promptly. Liveness checks the same endpoint
  every 20 seconds and restarts the container only after three consecutive
  failures, reducing restarts caused by brief transient failures.
- Pods receive a 30-second termination grace period. The `preStop` hook waits
  5 seconds before Kubernetes sends SIGTERM, allowing endpoint removal to
  propagate and in-flight requests to drain during a rolling update or
  voluntary disruption.

## Apply

```sh
kubectl apply -f .
```

## Verify the mounted secret

```sh
POD_NAME=$(kubectl get pods -l app.kubernetes.io/name=sample-python -o jsonpath='{.items[0].metadata.name}')
kubectl exec -it "$POD_NAME" -- /bin/sh
ls -l /opt/secrets
cat /opt/secrets/db-username
```
