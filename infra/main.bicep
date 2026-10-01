targetScope = 'resourceGroup'

@description('Azure region. Must match the existing resource group.')
param location string = resourceGroup().location

@minLength(2)
@maxLength(12)
@description('Lowercase alphanumeric resource prefix.')
param resourcePrefix string

@description('AKS cluster name supplied at deployment time.')
param aksName string

@description('Existing Azure Container Registry name.')
param acrName string

@description('Existing Log Analytics workspace name.')
param logAnalyticsWorkspaceName string

@description('Backend Container App name supplied at deployment time.')
param backendAppName string

@description('Frontend Container App name supplied at deployment time.')
param frontendAppName string

@description('Create AKS and Container Apps after prerequisite RBAC assignments propagate.')
param deployWorkloads bool = true

@description('Backend image, including registry and tag.')
param backendImage string

@description('Frontend image, including registry and tag.')
param frontendImage string

@description('Custom OpenSandbox image, including registry and tag.')
param sandboxImage string

@description('OpenSandbox lifecycle endpoint. Updated after Helm installation.')
param opensandboxDomain string = 'http://127.0.0.1:18080'

@secure()
param githubToken string

@secure()
param mcdMcpToken string

@secure()
param opensandboxApiKey string

@secure()
param opensandboxSecureAccessKey string

@description('CIDRs allowed to reach the AKS API server.')
param aksAuthorizedIpRanges array

param tags object = {
  workload: 'opensandbox-mcdonalds-copilot'
  environment: 'poc'
  managedBy: 'bicep'
}

var token = uniqueString(subscription().id, resourceGroup().id, resourcePrefix)
var vnetName = '${resourcePrefix}-vnet-${token}'
var containerAppsEnvironmentName = '${resourcePrefix}-cae-${token}'
var keyVaultName = take(replace('${resourcePrefix}kv${token}', '-', ''), 24)
var appInsightsName = '${resourcePrefix}-appi-${token}'
var aksIdentityName = '${resourcePrefix}-aks-id-${token}'
var backendIdentityName = '${resourcePrefix}-backend-id-${token}'
var frontendIdentityName = '${resourcePrefix}-frontend-id-${token}'
var acrPullRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '7f951dda-4ed3-4680-a7ca-43fe172d538d'
)
var keyVaultSecretsUserRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '4633458b-17de-408a-b874-0445c86b69e6'
)
var networkContributorRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '4d97b98b-1d4f-4787-a291-c67834d212e7'
)

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: acrName
}

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' existing = {
  name: logAnalyticsWorkspaceName
}

resource vnet 'Microsoft.Network/virtualNetworks@2024-05-01' = {
  name: vnetName
  location: location
  tags: tags
  properties: {
    addressSpace: {
      addressPrefixes: [
        '10.42.0.0/16'
      ]
    }
    subnets: [
      {
        name: 'aks'
        properties: {
          addressPrefix: '10.42.0.0/22'
          privateEndpointNetworkPolicies: 'Disabled'
        }
      }
      {
        name: 'container-apps'
        properties: {
          addressPrefix: '10.42.4.0/23'
          delegations: [
            {
              name: 'MicrosoftAppEnvironments'
              properties: {
                serviceName: 'Microsoft.App/environments'
              }
            }
          ]
        }
      }
    ]
  }
}

resource backendIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: backendIdentityName
  location: location
  tags: tags
}

resource aksIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: aksIdentityName
  location: location
  tags: tags
}

resource frontendIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: frontendIdentityName
  location: location
  tags: tags
}

resource aksSubnet 'Microsoft.Network/virtualNetworks/subnets@2024-05-01' existing = {
  parent: vnet
  name: 'aks'
}

resource aksNetworkAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(aksSubnet.id, aksIdentity.id, 'network-contributor')
  scope: aksSubnet
  properties: {
    roleDefinitionId: networkContributorRoleId
    principalId: aksIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: keyVaultName
  location: location
  tags: tags
  properties: {
    tenantId: subscription().tenantId
    sku: {
      family: 'A'
      name: 'standard'
    }
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 90
    enablePurgeProtection: true
    publicNetworkAccess: 'Enabled'
  }
}

resource githubTokenSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'github-token'
  properties: {
    value: githubToken
  }
}

resource mcdMcpTokenSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'mcd-mcp-token'
  properties: {
    value: mcdMcpToken
  }
}

resource opensandboxApiKeySecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'opensandbox-api-key'
  properties: {
    value: opensandboxApiKey
  }
}

resource opensandboxSecureAccessSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'opensandbox-secure-access-key'
  properties: {
    value: opensandboxSecureAccessKey
  }
}

resource backendVaultAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, backendIdentity.id, 'secrets-user')
  scope: keyVault
  properties: {
    roleDefinitionId: keyVaultSecretsUserRoleId
    principalId: backendIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource backendAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acr.id, backendIdentity.id, 'acrpull')
  scope: acr
  properties: {
    roleDefinitionId: acrPullRoleId
    principalId: backendIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource frontendAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acr.id, frontendIdentity.id, 'acrpull')
  scope: acr
  properties: {
    roleDefinitionId: acrPullRoleId
    principalId: frontendIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: appInsightsName
  location: location
  kind: 'web'
  tags: tags
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logAnalytics.id
    IngestionMode: 'LogAnalytics'
  }
}

resource aks 'Microsoft.ContainerService/managedClusters@2026-07-01' = if (deployWorkloads) {
  name: aksName
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${aksIdentity.id}': {}
    }
  }
  sku: {
    name: 'Base'
    tier: 'Free'
  }
  properties: {
    dnsPrefix: '${resourcePrefix}-${token}'
    enableRBAC: true
    disableLocalAccounts: true
    aadProfile: {
      managed: true
      enableAzureRBAC: true
    }
    agentPoolProfiles: [
      {
        name: 'kata'
        count: 3
        vmSize: 'Standard_D4s_v5'
        osType: 'Linux'
        osSKU: 'AzureLinux'
        workloadRuntime: 'KataVmIsolation'
        mode: 'System'
        nodeLabels: {
          'opensandbox.io/isolation': 'kata'
        }
        type: 'VirtualMachineScaleSets'
        vnetSubnetID: vnet.properties.subnets[0].id
        enableAutoScaling: false
        maxPods: 50
        upgradeSettings: {
          maxSurge: '1'
        }
      }
    ]
    apiServerAccessProfile: {
      enablePrivateCluster: false
      authorizedIPRanges: aksAuthorizedIpRanges
    }
    autoUpgradeProfile: {
      upgradeChannel: 'patch'
      nodeOSUpgradeChannel: 'NodeImage'
    }
    addonProfiles: {
      azurepolicy: {
        enabled: true
      }
      omsagent: {
        enabled: true
        config: {
          logAnalyticsWorkspaceResourceID: logAnalytics.id
          useAADAuth: 'true'
        }
      }
    }
    oidcIssuerProfile: {
      enabled: true
    }
    securityProfile: {
      workloadIdentity: {
        enabled: true
      }
      imageCleaner: {
        enabled: true
        intervalHours: 168
      }
    }
    networkProfile: {
      networkPlugin: 'azure'
      networkPluginMode: 'overlay'
      networkDataplane: 'cilium'
      networkPolicy: 'cilium'
      loadBalancerSku: 'standard'
      outboundType: 'loadBalancer'
      serviceCidr: '10.43.0.0/16'
      dnsServiceIP: '10.43.0.10'
      podCidr: '10.244.0.0/16'
      ipFamilies: [
        'IPv4'
      ]
    }
  }
  dependsOn: [
    aksNetworkAccess
  ]
}

resource aksAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (deployWorkloads) {
  name: guid(acr.id, aks!.id, 'kubelet-acrpull')
  scope: acr
  properties: {
    roleDefinitionId: acrPullRoleId
    principalId: aks!.properties.identityProfile.kubeletidentity.objectId
    principalType: 'ServicePrincipal'
  }
}

resource containerAppsEnvironment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: containerAppsEnvironmentName
  location: location
  tags: tags
  properties: {
    vnetConfiguration: {
      infrastructureSubnetId: vnet.properties.subnets[1].id
      internal: false
    }
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalytics.properties.customerId
        sharedKey: logAnalytics.listKeys().primarySharedKey
      }
    }
    zoneRedundant: false
  }
}

resource backend 'Microsoft.App/containerApps@2024-03-01' = if (deployWorkloads) {
  name: backendAppName
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${backendIdentity.id}': {}
    }
  }
  properties: {
    managedEnvironmentId: containerAppsEnvironment.id
    configuration: {
      activeRevisionsMode: 'Single'
      registries: [
        {
          server: acr.properties.loginServer
          identity: backendIdentity.id
        }
      ]
      ingress: {
        external: false
        targetPort: 8000
        transport: 'http'
        allowInsecure: false
        traffic: [
          {
            latestRevision: true
            weight: 100
          }
        ]
      }
      secrets: [
        {
          name: 'github-token'
          keyVaultUrl: githubTokenSecret.properties.secretUri
          identity: backendIdentity.id
        }
        {
          name: 'mcd-mcp-token'
          keyVaultUrl: mcdMcpTokenSecret.properties.secretUri
          identity: backendIdentity.id
        }
        {
          name: 'opensandbox-api-key'
          keyVaultUrl: opensandboxApiKeySecret.properties.secretUri
          identity: backendIdentity.id
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'api'
          image: backendImage
          env: [
            {
              name: 'ENVIRONMENT'
              value: 'production'
            }
            {
              name: 'OPENSANDBOX_DOMAIN'
              value: opensandboxDomain
            }
            {
              name: 'SANDBOX_IMAGE'
              value: sandboxImage
            }
            {
              name: 'COPILOT_MODEL'
              value: 'gpt-6-astra'
            }
            {
              name: 'COPILOT_REASONING_EFFORT'
              value: 'high'
            }
            {
              name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
              value: appInsights.properties.ConnectionString
            }
            {
              name: 'GITHUB_TOKEN'
              secretRef: 'github-token'
            }
            {
              name: 'MCD_MCP_TOKEN'
              secretRef: 'mcd-mcp-token'
            }
            {
              name: 'OPENSANDBOX_API_KEY'
              secretRef: 'opensandbox-api-key'
            }
          ]
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          probes: [
            {
              type: 'Liveness'
              httpGet: {
                path: '/health'
                port: 8000
                scheme: 'HTTP'
              }
              initialDelaySeconds: 10
              periodSeconds: 20
            }
            {
              type: 'Readiness'
              httpGet: {
                path: '/ready'
                port: 8000
                scheme: 'HTTP'
              }
              initialDelaySeconds: 5
              periodSeconds: 10
            }
          ]
        }
      ]
      scale: {
        minReplicas: 0
        maxReplicas: 1
        rules: [
          {
            name: 'http'
            http: {
              metadata: {
                concurrentRequests: '10'
              }
            }
          }
        ]
      }
    }
  }
  dependsOn: [
    backendVaultAccess
    backendAcrPull
  ]
}

resource frontend 'Microsoft.App/containerApps@2024-03-01' = if (deployWorkloads) {
  name: frontendAppName
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${frontendIdentity.id}': {}
    }
  }
  properties: {
    managedEnvironmentId: containerAppsEnvironment.id
    configuration: {
      activeRevisionsMode: 'Single'
      registries: [
        {
          server: acr.properties.loginServer
          identity: frontendIdentity.id
        }
      ]
      ingress: {
        external: true
        targetPort: 8080
        transport: 'http'
        allowInsecure: false
        traffic: [
          {
            latestRevision: true
            weight: 100
          }
        ]
      }
    }
    template: {
      containers: [
        {
          name: 'web'
          image: frontendImage
          env: [
            {
              name: 'BACKEND_SCHEME'
              value: 'https'
            }
            {
              name: 'BACKEND_HOST'
              value: backend!.properties.configuration.ingress.fqdn
            }
          ]
          resources: {
            cpu: json('0.25')
            memory: '0.5Gi'
          }
          probes: [
            {
              type: 'Liveness'
              httpGet: {
                path: '/health'
                port: 8080
                scheme: 'HTTP'
              }
              initialDelaySeconds: 5
              periodSeconds: 20
            }
          ]
        }
      ]
      scale: {
        minReplicas: 0
        maxReplicas: 2
        rules: [
          {
            name: 'http'
            http: {
              metadata: {
                concurrentRequests: '50'
              }
            }
          }
        ]
      }
    }
  }
  dependsOn: [
    frontendAcrPull
  ]
}

output aksName string = deployWorkloads ? aks!.name : ''
output acrLoginServer string = acr.properties.loginServer
output aksIdentityPrincipalId string = aksIdentity.properties.principalId
output aksSubnetId string = aksSubnet.id
output backendIdentityPrincipalId string = backendIdentity.properties.principalId
output frontendIdentityPrincipalId string = frontendIdentity.properties.principalId
output backendName string = deployWorkloads ? backend!.name : ''
output backendFqdn string = deployWorkloads ? backend!.properties.configuration.ingress.fqdn : ''
output frontendName string = deployWorkloads ? frontend!.name : ''
output frontendUrl string = deployWorkloads ? 'https://${frontend!.properties.configuration.ingress.fqdn}' : ''
output keyVaultName string = keyVault.name
output containerAppsEnvironmentName string = containerAppsEnvironment.name
output sandboxImage string = sandboxImage
