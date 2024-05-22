## Minikube Setup

It is important to know that other than minikube, there should be a driver installed which is responsible for managing the virtual machines or containers that runs the Kubernetes cluster. It handles the creation, configuration and management of the infrastructure where the Kubernetes cluster will be deployed. 

Let’s use Docker driver because of its lightweight nature and its compatibility with Kubernetes

Atleast 4 GB of RAM is needed hence I choose `t2.medium` EC2 instance

1. Install Docker

```
sudo apt update 

curl -fsSL https://get.docker.com -o get-docker.sh 

sudo sh get-docker.sh 

sudo usermod -aG docker $USER && newgrp docker
```

2. Install Minikube dependencies

```
sudo apt install -y curl wget apt-transport-https 
```

3. Download minikube binary

```
wget https://storage.googleapis.com/minikube/releases/latest/minikube-linux-amd64 
```

4. Once the binary is downloaded, copy it to the path /usr/local/bin and set the executable permissions on it by executing the below commands: 

```
sudo cp minikube-linux-amd64 /usr/local/bin/minikube 

sudo chmod +x /usr/local/bin/minikube 
```

5. Verify the minikube version

```
minikube version 
```

6. Install kubectl, which is the primary command-line tool for managing and controlling Kubernetes clusters. 

```
curl -LO https://storage.googleapis.com/kubernetes-release/release/`curl -s https://storage.googleapis.com/kubernetes-release/release/stable.txt`/bin/linux/amd64/kubectl 
```

7. Once kubectl is downloaded, set the executable permissions on the kubectl binary and move it to the path /usr/local/bin by executing the below commands

```
chmod +x kubectl 

sudo mv kubectl /usr/local/bin/ 
```

8. Verify

```
kubectl version 
```

9. Now use docker as base to run minikube, so start minikube with docker driver 

```
minikube start –driver=docker 
```

10. Check the status

```
minikube status 
```

11. Verify the Kubernetes version, node status and cluster info 

```
kubectl cluster-info 
```
```
kubectl get nodes 
```

You're welcome! Good luck kubin'
