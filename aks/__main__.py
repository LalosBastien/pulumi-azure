import base64

import pulumi
import pulumi_azuread as azuread
import pulumi_random as random
import pulumi_tls as tls
from pulumi_azure_native import containerservice, network, resources
from pulumi_kubernetes.helm.v3 import Release, ReleaseArgs, RepositoryOptsArgs
from pulumi_kubernetes.apiextensions import CustomResource

##############
# Parameters #
##############
# Load configuration file
cfg = pulumi.Config()

# Optional parameters
resource_group_name = cfg.get("rg", "pulumi-py-aks")
k8s_cluster_name = cfg.get("kubernetesClusterName", "pulumi-py-aks")
k8s_version = cfg.get("kubernetesVersion", "1.26.3")
k8s_node_size = cfg.get("kubernetesNodeSize", "Standard_DS2_v2")
k8s_node_count = cfg.get_float("kubernetesNodeCount", 2)
dns_prefix = cfg.get("prefixForDns", "pulumi")
environment = cfg.get("environment", "dev")

# Required parameters
# mgmt_group_id = cfg.require("mgmtGroupId")


############
# Generate #
############
# Generate an SSH key
ssh_key = tls.PrivateKey("ssh-key", algorithm="RSA", rsa_bits=4096)

# Generate random password
password = random.RandomPassword("password", length=20, special=True)

############
# Azure AD #
############
# Create an AD service principal
ad_app = azuread.Application("aks", display_name="aks")
ad_sp = azuread.ServicePrincipal("aksSp", application_id=ad_app.application_id)

# Create the Service Principal Password
ad_sp_password = azuread.ServicePrincipalPassword(
    "aksSpPassword", service_principal_id=ad_sp.id, end_date="2099-01-01T00:00:00Z"
)

##################
# Resource Group #
##################
# Create an Azure Resource Group
resource_group = resources.ResourceGroup(resource_group_name)


##############
# Networking #
##############
# Create an Azure Virtual Network
virtual_network = network.VirtualNetwork(
    "virtual_network",
    address_space=network.AddressSpaceArgs(
        address_prefixes=["10.0.0.0/16"],
    ),
    resource_group_name=resource_group.name,
    # Workaround to avoid deletion-recreation when external subnets are attached
    # https://github.com/pulumi/pulumi/issues/2800
    opts=pulumi.ResourceOptions(ignore_changes=["subnets"]),
)

# Create three subnets in the virtual network
subnet1 = network.Subnet(
    "subnet-1",
    address_prefix="10.0.0.0/22",
    resource_group_name=resource_group.name,
    virtual_network_name=virtual_network.name,
)
subnet2 = network.Subnet(
    "subnet-2",
    address_prefix="10.0.4.0/22",
    resource_group_name=resource_group.name,
    virtual_network_name=virtual_network.name,
)
subnet3 = network.Subnet(
    "subnet-3",
    address_prefix="10.0.8.0/22",
    resource_group_name=resource_group.name,
    virtual_network_name=virtual_network.name,
)


##############
# Kubernetes #
##############
# Create an Azure Kubernetes Service cluster
managed_cluster = containerservice.ManagedCluster(
    k8s_cluster_name,
    resource_group_name=resource_group.name,
    # aad_profile=containerservice.ManagedClusterAADProfileArgs(
    #     enable_azure_rbac=False,
    #     managed=True,
    #     admin_group_object_ids=[mgmt_group_id],
    # ),
    # Use multiple agent/node pools to distribute nodes across subnets
    agent_pool_profiles=[
        containerservice.ManagedClusterAgentPoolProfileArgs(
            availability_zones=[
                # "1",
                "2",
                # "3",
            ],
            count=k8s_node_count,
            enable_node_public_ip=False,
            mode="System",
            name="agentpool1",
            os_type="Linux",
            node_labels={},
            os_disk_size_gb=30,
            type="VirtualMachineScaleSets",
            vm_size=k8s_node_size,
            # Change next line for additional node pools to distribute across subnets
            vnet_subnet_id=subnet1.id,
        ),
        containerservice.ManagedClusterAgentPoolProfileArgs(
            availability_zones=[
                # "1",
                "2",
                # "3",
            ],
            count=3,
            enable_node_public_ip=False,
            mode="System",
            name="agentpool2",
            os_type="Linux",
            node_labels={},
            os_disk_size_gb=30,
            type="VirtualMachineScaleSets",
            vm_size=k8s_node_size,
            # Change next line for additional node pools to distribute across subnets
            vnet_subnet_id=subnet2.id,
        ),
    ],
    # Change authorized_ip_ranges to limit access to API server
    # Changing enable_private_cluster requires alternate access to API server (VPN or similar)
    api_server_access_profile=containerservice.ManagedClusterAPIServerAccessProfileArgs(
        authorized_ip_ranges=["0.0.0.0/0"], enable_private_cluster=False
    ),
    dns_prefix=dns_prefix,
    enable_rbac=True,
    identity=containerservice.ManagedClusterIdentityArgs(
        type=containerservice.ResourceIdentityType.SYSTEM_ASSIGNED,
    ),
    kubernetes_version=k8s_version,
    linux_profile=containerservice.ContainerServiceLinuxProfileArgs(
        admin_username="azureuser",
        ssh=containerservice.ContainerServiceSshConfigurationArgs(
            public_keys=[
                containerservice.ContainerServiceSshPublicKeyArgs(
                    key_data=ssh_key.public_key_openssh,
                )
            ],
        ),
    ),
    network_profile=containerservice.ContainerServiceNetworkProfileArgs(
        network_plugin="azure",
        network_policy="azure",
        service_cidr="10.96.0.0/16",
        dns_service_ip="10.96.0.10",
    ),
    service_principal_profile={
        "client_id": ad_app.application_id,
        "secret": ad_sp_password.value,
    },
)

# Build a Kubeconfig to access the cluster
creds = containerservice.list_managed_cluster_user_credentials_output(
    resource_group_name=resource_group.name,
    resource_name=managed_cluster.name,
)
encoded = creds.kubeconfigs[0].value
k8sconfig = encoded.apply(lambda enc: base64.b64decode(enc).decode())

###############
# CertManager #
###############

# Install a cert manager.
cert_manager = Release(
    "cert-manager",
    ReleaseArgs(
        chart="cert-manager",
        version="1.12.4",
        namespace="cert-manager",
        create_namespace=True,
        repository_opts=RepositoryOptsArgs(
            repo="https://charts.jetstack.io",
        ),
        value_yaml_files=[pulumi.FileAsset("./helm/cert-manager/values.yaml")],
    ),
    kubeconfig=k8sconfig,
    opts=pulumi.ResourceOptions(
        depends_on=[managed_cluster],
    ),
)

# Create a cluster issuer for letsencrypt certificates.
issuer_staging = CustomResource('cluster-issuer-staging',
    api_version='cert-manager.io/v1',
    kind='ClusterIssuer',
    metadata={
        'name': 'letsencrypt-issuer-staging',
    },
    spec={
        "acme": {
            "email": "admin@equin0x.net",
            "server": "https://acme-staging-v02.api.letsencrypt.org/directory",
            "privateKeySecretRef": {
                "name": "letsencrypt-issuer-staging-account-key",
            },
            "solvers": [
                {
                    "http01": {
                        "ingress": {
                            "ingressClassName": "nginx"
                        }
                    }
                }
            ],
        }
    },
    opts=pulumi.ResourceOptions(
        depends_on=[cert_manager],
    ),
)

issuer_prod = CustomResource('cluster-issuer-prod',
    api_version='cert-manager.io/v1',
    kind='ClusterIssuer',
    metadata={
        'name': 'letsencrypt-issuer-prod',
    },
    spec={
        "acme": {
            "email": "admin@equin0x.net",
            "server": "https://acme-v02.api.letsencrypt.org/directory",
            "privateKeySecretRef": {
                "name": "letsencrypt-issuer-prod-account-key",
            },
            "solvers": [
                {
                    "http01": {
                        "ingress": {
                            "ingressClassName": "nginx"
                        }
                    }
                }
            ],
        }
    },
    opts=pulumi.ResourceOptions(
        depends_on=[cert_manager],
    ),
)

########
# Helm #
########
nginx_ingress = Release(
    "nginx-ingress",
    ReleaseArgs(
        chart="nginx-ingress",
        version="0.18.1",
        namespace="nginx-ingress",
        create_namespace=True,
        repository_opts=RepositoryOptsArgs(
            repo="https://helm.nginx.com/stable",
        ),
        value_yaml_files=[pulumi.FileAsset("./helm/nginx-ingress/values.yaml")],
    ),
    kubeconfig=k8sconfig,
    opts=pulumi.ResourceOptions(
        depends_on=[managed_cluster],
    ),
)

privatebin = Release(
    "privatebin",
    ReleaseArgs(
        chart="privatebin",
        version="0.19.0",
        namespace="privatebin",
        create_namespace=True,
        repository_opts=RepositoryOptsArgs(
            repo="https://privatebin.github.io/helm-chart/",
        ),
        value_yaml_files=[pulumi.FileAsset(f"./helm/privatebin/values.{environment}.yaml")],
    ),
    kubeconfig=k8sconfig,
    opts=pulumi.ResourceOptions(
        depends_on=[issuer_prod],
    ),
)

argocd = Release(
    "argocd",
    ReleaseArgs(
        chart="argo-cd",
        version="5.45.0",
        namespace="argocd",
        create_namespace=True,
        repository_opts=RepositoryOptsArgs(
            repo="https://argoproj.github.io/argo-helm",
        ),
        value_yaml_files=[pulumi.FileAsset(f"./helm/argocd/values.{environment}.yaml")],
    ),
    kubeconfig=k8sconfig,
    opts=pulumi.ResourceOptions(
        depends_on=[issuer_prod],
    ),
)

# Export some values for use elsewhere
pulumi.export("password", password.result)
pulumi.export("aksSpPassword", ad_sp_password.value)
pulumi.export("ssh", ssh_key)
pulumi.export("rgname", resource_group.name)
pulumi.export("vnetName", virtual_network.name)
pulumi.export("clusterName", managed_cluster.name)
pulumi.export("kubeconfig", k8sconfig)
