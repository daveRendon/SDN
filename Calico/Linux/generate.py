#!/usr/bin/python2
import sys
import os.path

# Inputs:
#
#   - Cluster CIDR, likely a /16
#
# Outputs:
#   - 1 manifest file:
#       calico.yaml

MANIFEST_TEMPLATES = {
    "calico.yaml": """# Calico Version v2.5.0
# https://docs.projectcalico.org/v2.5/releases#v2.5.0
# This manifest includes the following component versions:
#   calico/node:v2.5.0
#   calico/cni:v1.10.0

# This ConfigMap is used to configure a self-hosted Calico installation.
kind: ConfigMap
apiVersion: v1
metadata:
  name: calico-config
  namespace: kube-system
data:
  # The CNI network configuration to install on each node.
  cni_network_config: |-
    {
        "name": "k8s-pod-network",
        "cniVersion": "0.1.0",
        "type": "calico",
        "log_level": "debug",
        "datastore_type": "kubernetes",
        "nodename": "__KUBERNETES_NODE_NAME__",
        "mtu": 1500,
        "ipam": {
            "type": "host-local",
            "subnet": "usePodCidr"
        },
        "policy": {
            "type": "k8s",
            "k8s_auth_token": "__SERVICEACCOUNT_TOKEN__"
        },
        "kubernetes": {
            "k8s_api_root": "https://__KUBERNETES_SERVICE_HOST__:__KUBERNETES_SERVICE_PORT__",
            "kubeconfig": "__KUBECONFIG_FILEPATH__"
        }
    }

---

# This manifest installs the calico/node container, as well
# as the Calico CNI plugins and network config on
# each master and worker node in a Kubernetes cluster.
kind: DaemonSet
apiVersion: extensions/v1beta1
metadata:
  name: calico-node
  namespace: kube-system
  labels:
    k8s-app: calico-node
spec:
  selector:
    matchLabels:
      k8s-app: calico-node
  template:
    metadata:
      labels:
        k8s-app: calico-node
      annotations:
        # This, along with the CriticalAddonsOnly toleration below,
        # marks the pod as a critical add-on, ensuring it gets
        # priority scheduling and that its resources are reserved
        # if it ever gets evicted.
        scheduler.alpha.kubernetes.io/critical-pod: ''
    spec:
      hostNetwork: true
      serviceAccountName: calico-node
      tolerations:
        # Allow the pod to run on the master.  This is required for
        # the master to communicate with pods.
        - key: node-role.kubernetes.io/master
          effect: NoSchedule
        # Mark the pod as a critical add-on for rescheduling.
        - key: "CriticalAddonsOnly"
          operator: "Exists"
      containers:
        # Runs calico/node container on each Kubernetes node.  This
        # container programs network policy and routes on each
        # host.
        - name: calico-node
          image: quay.io/calico/node:v2.5.0
          env:
            # Use Kubernetes API as the backing datastore.
            - name: DATASTORE_TYPE
              value: "kubernetes"
            # Kube Config
            - name: KUBECONFIG
              value: "/etc/kubernetes/config"
            # Enable felix info logging.
            - name: FELIX_LOGSEVERITYSCREEN
              value: "info"
            # Don't enable BGP.
            - name: CALICO_NETWORKING_BACKEND
              value: "none"
            # Cluster type to identify the deployment type
            - name: CLUSTER_TYPE
              value: "k8s"
            # Disable file logging so `kubectl logs` works.
            - name: CALICO_DISABLE_FILE_LOGGING
              value: "true"
            # Set Felix endpoint to host default action to ACCEPT.
            - name: FELIX_DEFAULTENDPOINTTOHOSTACTION
              value: "ACCEPT"
            # Disable IPV6 on Kubernetes.
            - name: FELIX_IPV6SUPPORT
              value: "false"
            # Wait for the datastore.
            - name: WAIT_FOR_DATASTORE
              value: "true"
            # The Calico IPv4 pool to use.  This should match `--cluster-cidr`
            - name: CALICO_IPV4POOL_CIDR
              value: "$CLUSTER"
            # Enable IPIP
            - name: CALICO_IPV4POOL_IPIP
              value: "always"
            # Set based on the k8s node name.
            - name: NODENAME
              valueFrom:
                fieldRef:
                  fieldPath: spec.nodeName
            # No IP address needed.
            - name: IP
              value: ""
            - name: FELIX_HEALTHENABLED
              value: "true"
          securityContext:
            privileged: true
          resources:
            requests:
              cpu: 250m
          livenessProbe:
            httpGet:
              path: /liveness
              port: 9099
            periodSeconds: 10
            initialDelaySeconds: 10
            failureThreshold: 6
          readinessProbe:
            httpGet:
              path: /readiness
              port: 9099
            periodSeconds: 10
          volumeMounts:
            - mountPath: /lib/modules
              name: lib-modules
              readOnly: true
            - mountPath: /var/run/calico
              name: var-run-calico
              readOnly: false
            - mountPath: /etc/kubernetes
              name: etc-kubernetes
              readOnly: false
        # This container installs the Calico CNI binaries
        # and CNI network config file on each node.
        - name: install-cni
          image: quay.io/calico/cni:v1.10.0
          command: ["/install-cni.sh"]
          env:
            # The CNI network config to install on each node.
            - name: CNI_NETWORK_CONFIG
              valueFrom:
                configMapKeyRef:
                  name: calico-config
                  key: cni_network_config
            # Set the hostname based on the k8s node name.
            - name: KUBERNETES_NODE_NAME
              valueFrom:
                fieldRef:
                  fieldPath: spec.nodeName
          volumeMounts:
            - mountPath: /host/opt/cni/bin
              name: cni-bin-dir
            - mountPath: /host/etc/cni/net.d
              name: cni-net-dir
      volumes:
        # Used by calico/node.
        - name: lib-modules
          hostPath:
            path: /lib/modules
        - name: var-run-calico
          hostPath:
            path: /var/run/calico
        - name: etc-kubernetes
          hostPath:
            path: $HOME/kube/kubelet
        # Used to install CNI.
        - name: cni-bin-dir
          hostPath:
            path: /opt/cni/bin
        - name: cni-net-dir
          hostPath:
            path: /etc/cni/net.d

# Create all the CustomResourceDefinitions needed for
# Calico policy-only mode.
---

apiVersion: apiextensions.k8s.io/v1beta1
description: Calico Global Felix Configuration
kind: CustomResourceDefinition
metadata:
   name: globalfelixconfigs.crd.projectcalico.org
spec:
  scope: Cluster
  group: crd.projectcalico.org
  version: v1
  names:
    kind: GlobalFelixConfig
    plural: globalfelixconfigs
    singular: globalfelixconfig

---

apiVersion: apiextensions.k8s.io/v1beta1
description: Calico Global BGP Configuration
kind: CustomResourceDefinition
metadata:
  name: globalbgpconfigs.crd.projectcalico.org
spec:
  scope: Cluster
  group: crd.projectcalico.org
  version: v1
  names:
    kind: GlobalBGPConfig
    plural: globalbgpconfigs
    singular: globalbgpconfig

---

apiVersion: apiextensions.k8s.io/v1beta1
description: Calico IP Pools
kind: CustomResourceDefinition
metadata:
  name: ippools.crd.projectcalico.org
spec:
  scope: Cluster
  group: crd.projectcalico.org
  version: v1
  names:
    kind: IPPool
    plural: ippools
    singular: ippool

---

apiVersion: apiextensions.k8s.io/v1beta1
description: Calico Global Network Policies
kind: CustomResourceDefinition
metadata:
  name: globalnetworkpolicies.crd.projectcalico.org
spec:
  scope: Cluster
  group: crd.projectcalico.org
  version: v1
  names:
    kind: GlobalNetworkPolicy
    plural: globalnetworkpolicies
    singular: globalnetworkpolicy

---

apiVersion: v1
kind: ServiceAccount
metadata:
  name: calico-node
  namespace: kube-system
""",
}

HOME = os.path.expanduser("~")

def main(cluster_cidr):
    for filename, content in MANIFEST_TEMPLATES.iteritems():
        print "Generating %s ..." % filename,
        with open(filename, "w") as manifest:
            new_content = content.replace(
                "$HOME", HOME).replace(
                "$CLUSTER", cluster_cidr
            )
            manifest.write(new_content)
        print "done."

if len(sys.argv) != 2:
    print "usage: generate.py [full cluster CIDR]"
    print "   ex: generate.py 192.168.0.0/16"
    sys.exit(1)

print "User home directory:", HOME
print "Generating manifests in local directory..."
main(*sys.argv[1:])
print "All done."
