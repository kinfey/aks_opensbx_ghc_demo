# 基于 AKS OpenSandbox 的 Golden Order Copilot

[English](README.md) | **简体中文**


![arch](./imgs/arch.png)

这是一个客服与模拟点餐概念验证（POC）应用。网页前端调用内部 FastAPI 后端，
后端在 Azure Kubernetes Service（AKS）上，为**每个浏览器会话创建独立的
OpenSandbox Kata 虚拟机**，在其中运行 GitHub Copilot CLI。
智能体使用 `gpt-6-astra`、只读的麦当劳远程 MCP 服务，以及仅用于模拟点餐的本地工具。

> 本项目是非官方技术演示，不是麦当劳官方服务，也未获得麦当劳背书。
> 本地工具中的订单、菜单和价格均为模拟数据，不支持真实购买、支付、取消订单或领取优惠券。

本文记录截至 **2026-10-01** 已验证的实现和部署经验。Azure API、机型可用性、
模型使用权限、配额及功能审批状态，必须在你自己的订阅中重新确认。
所有部署名称和凭据都必须自行填写，不要复制其他环境的资源标识。

## 目录

- [1. 项目架构](#1-项目架构)
- [2. 技术栈与目录结构](#2-技术栈与目录结构)
- [3. 前置条件](#3-前置条件)
- [4. 配置](#4-配置)
- [5. 本地开发检查](#5-本地开发检查)
- [6. 首次部署到 Azure](#6-首次部署到-azure)
- [7. 验收与浏览器检查](#7-验收与浏览器检查)
- [8. 运行时、API 与 MCP 行为](#8-运行时api-与-mcp-行为)
- [9. 无损 Markdown 回复](#9-无损-markdown-回复)
- [10. 更新已有部署](#10-更新已有部署)
- [11. 排障与部署经验](#11-排障与部署经验)
- [12. 安全边界与 POC 限制](#12-安全边界与-poc-限制)
- [13. 运维与清理](#13-运维与清理)
- [14. 参考资料](#14-参考资料)

## 1. 项目架构

下图仅使用 Markdown 纯文本代码块，不需要额外的图形渲染器。
图中保留英文组件标识，便于与源码对应。

```text
Browser
   |
   | HTTPS / same-origin /api/*
   v
+---------------- Azure Container Apps environment -----------------+
| Public frontend                 Internal backend                  |
| Nginx :8080                     FastAPI :8000                     |
| HTML / CSS / JavaScript  ----->  Session orchestration              |
| Marked + DOMPurify       HTTPS   OpenSandbox Python SDK            |
| 0..2 replicas                   0..1 replica                       |
+------------------------------------------+------------------------+
                                           |
                    VNet: private HTTP + API key / secure access
                                           |
+------------------------- AKS ------------------------------------+
| Internal load balancers                                          |
|   Lifecycle API ---> OpenSandbox server ---> BatchSandbox CRDs     |
|   Gateway       ---> Ingress gateway ---> sandbox execd           |
|                          Controller reconciles sandbox workloads |
|                                                                  |
| One shared system/Kata pool                                      |
| 3 x Standard_D4s_v5 / Azure Linux / KataVmIsolation                |
|                                                                  |
|  +------------------- Per-session Kata VM --------------------+   |
|  | execd -> run_copilot.py -> GitHub Copilot CLI               |   |
|  |                            model: gpt-6-astra              |   |
|  |                            reasoning: high                |   |
|  |                            |                              |   |
|  |                            +--> local ordering MCP (stdio)|   |
|  |                                 simulation state only     |   |
|  +----------------------------+------------------------------+   |
|                               |                                  |
|              Controlled egress + Credential Vault injection       |
+-------------------------------+----------------------------------+
                                |
                    +-----------+----------------+
                    |                            |
                    v                            v
            GitHub Copilot APIs          https://mcp.mcd.cn
                                         allowlisted read-only MCP

Supporting Azure services
  Existing ACR ----------> frontend, backend, and sandbox images
  Key Vault -------------> backend secret references
  Managed identities ----> scoped ACR, vault, and network permissions
  Log Analytics <--------- AKS / Container Apps logs
  Application Insights <-- backend OpenTelemetry
```

**请求路径：**浏览器不直接访问 AKS 或内部后端。Nginx 通过 HTTPS 将 `/api/*`
代理到后端。后端创建或复用会话沙箱，等待 execd 就绪，配置出站凭据注入，
然后执行 Copilot 包装程序。回复以原始 Markdown 返回，由浏览器安全渲染。

**网络布局：**Bicep 模板使用 VNet `10.42.0.0/16`、AKS 子网 `10.42.0.0/22`、
Container Apps 子网 `10.42.4.0/23`、Pod CIDR `10.244.0.0/16` 和
Service CIDR `10.43.0.0/16`。部署前应检查地址空间是否重叠。
AKS API 是带授权 IP 白名单的公共端点；OpenSandbox 服务使用内部负载均衡器的私有 IP，
不会向公网暴露生命周期服务。

## 2. 技术栈与目录结构

| 层级 | 实现 |
|------|------|
| 基础设施 | Azure CLI 与资源组级 Bicep，不依赖 `azd` |
| AKS | Standard 集群、Free 控制平面层级、Azure CNI Overlay、Cilium、Azure RBAC |
| 节点池 | 单个名为 `kata` 的 `System` 池；三个 `Standard_D4s_v5` Azure Linux 节点；`KataVmIsolation` |
| 隔离 | `kata-vm-isolation` RuntimeClass、Kata 节点选择器、MSHV 来宾内核验证 |
| 沙箱平台 | OpenSandbox Helm 图表固定到提交 `3738975fc7b1da6875694f912b0422fe5d622064`；使用 `batchsandbox` |
| 沙箱镜像 | `opensandbox/code-interpreter:v1.1.0`；execd `v1.1.0`；egress `v1.1.7` |
| 智能体 | GitHub Copilot CLI 官方安装程序；修正后镜像中观察到 CLI 1.0.90；`gpt-6-astra`，高推理强度 |
| 后端 | Python 3.11+；容器使用 Python 3.12；FastAPI、Pydantic、Uvicorn、OpenSandbox SDK |
| MCP | 本地使用 Python FastMCP/stdin-stdout；远程使用 HTTP MCP；显式工具白名单 |
| 前端 | 原生 HTML5/CSS3/JavaScript；Node.js 22 构建资源；Nginx 1.29 |
| Markdown | Marked 18.0.14 和 DOMPurify 3.4.16，固定版本并由本地提供 |
| 验证 | pytest、Ruff、Bicep 编译/ARM 验证、Playwright 1.63.0 与 Microsoft Edge |
| 可观测性 | Log Analytics、Application Insights、Azure Monitor OpenTelemetry |

Python 依赖范围定义在 `src/backend/pyproject.toml`，并非完全锁定的依赖环境。
集成时使用的 SDK 为 0.1.16。官方 CLI 安装程序也未固定版本，因此重新构建时应检查
CLI 版本及 JSONL 行为。三个应用镜像均按 `linux/amd64` 构建。

```text
.
+-- .azure/deployment-plan.md       可选的本地部署记录，不公开发布
+-- .env.example                   配置模板；.env 被忽略
+-- infra/
|   +-- main.bicep                  网络、AKS、身份、密钥库、Container Apps
|   +-- main.parameters.example.json
+-- k8s/opensandbox/                Helm 参数、Kata 模板、内部 Service
+-- scripts/
|   +-- deploy-all.sh               完整部署入口
|   +-- prepare-azure.sh            订阅、区域、资源提供程序、功能状态
|   +-- deploy-infrastructure.sh    ARM 预检与两阶段资源部署
|   +-- build-images.sh             三个镜像的 ACR 构建
|   +-- install-opensandbox.sh      固定版本 Helm 安装与端点连接
|   +-- warm-sandbox-image.sh       三节点临时 Kata 镜像预热
|   +-- verify-kata.sh              ARM 节点池配置与运行时验证
|   +-- verify-kata-runtime.sh      仅 Kubernetes 的运行时、节点和内核检查
|   +-- verify-deployment.sh        HTTP/聊天冒烟检查与会话删除
+-- sandbox/
|   +-- Dockerfile                  Copilot CLI 与本地 MCP 运行环境
|   +-- run_copilot.py              CLI 执行、JSONL 解析、回复封装
|   +-- config/                    MCP 配置与智能体指令
|   +-- order_mcp/server.py         模拟菜单、计价和订单
+-- src/backend/app/               API、参数验证、会话和遥测
+-- src/backend/tests/             后端回归测试
+-- src/frontend/                  界面、Markdown 渲染、Nginx、浏览器测试
+-- tests/                         部署与 Copilot 输出回归测试
+-- README.md
+-- README.zh.md
```

## 3. 前置条件

- 已启用的 Azure 订阅和一个**现有资源组**。
- 位于 Bicep 目标资源组中的现有 ACR 与 Log Analytics 工作区。
  ACR 管理员账户必须保持禁用。
- 支持 Bicep 和 Container Apps 的 Azure CLI、Helm 3、kubectl、Git、OpenSSL、
  curl、Bash、Python 3.11+ 和 Node.js 22。AKS Entra 身份验证还可能需要
  `kubelogin`。当前浏览器测试配置要求安装 Microsoft Edge。
- 使用 ACR 构建时可不安装 Docker；本地检查或运行镜像时需要 Docker。
- 部署资源、注册资源提供程序、分配所需范围角色，以及管理相关 Kubernetes 资源的权限。
  拥有 Azure 资源创建权限不等于拥有 Kubernetes 访问权限。
- 具有 Copilot/模型使用权限的 GitHub 账户，以及具有 **Copilot Requests** 权限的
  细粒度令牌；远程服务还需要有效的麦当劳 MCP 令牌。
  本项目不需要 Azure OpenAI 资源或 Azure OpenAI API 密钥。
- 所选区域能够容纳三个 D4s_v5 节点，总计 12 vCPU、48 GiB 内存。
  为配置中的单节点升级扩容额外预留 4 vCPU，并计入订阅中其他工作负载占用的配额。

三个 AKS 节点、网络、镜像存储与构建、日志和模型调用都会产生费用。
Container Apps 缩容到零不会停止 AKS 节点计费。

## 4. 配置

在项目根目录执行：

```bash
cp .env.example .env
chmod 600 .env
```

在本地编辑 `.env`，替换**所有**环境相关值，包括示例 Container App 名称。
该文件会由 Bash 加载：包含空格或 Shell 特殊字符的值必须加引号，
并在加载前替换所有尖括号占位符。不要打印或提交此文件。

| 变量 | 含义 / 占位符示例 |
|------|-------------------|
| `AZURE_SUBSCRIPTION` | 你自己的已启用订阅名称或 ID |
| `AZURE_RESOURCE_GROUP` | `<your-existing-resource-group>` |
| `AZURE_LOCATION` | 必须与资源组一致；已验证部署使用 `swedencentral` |
| `RESOURCE_PREFIX` | 自定义小写字母数字前缀，长度 2-12 |
| `AKS_NAME` | `<your-new-aks-cluster-name>` |
| `ACR_NAME` | `<your-existing-registry-name>` |
| `LOG_ANALYTICS_WORKSPACE_NAME` | `<your-existing-workspace-name>` |
| `BACKEND_APP_NAME` | `<your-backend-container-app-name>` |
| `FRONTEND_APP_NAME` | `<your-frontend-container-app-name>` |
| `AKS_AUTHORIZED_IP_RANGES` | 部署客户端的公共 IPv4 CIDR，如 `<your-public-ip>/32`；多个值以逗号分隔 |
| `GITHUB_TOKEN` | GitHub 凭据，不要放进源码、命令或截图 |
| `MCD_MCP_TOKEN` | 远程 MCP 凭据 |
| `IMAGE_TAG` | 可选的明确构建标签；未设置时由 `deploy-all.sh` 生成 |
| `OPENSANDBOX_API_KEY` | 可选的固定生命周期 API 密钥；未设置时自动生成 |
| `OPENSANDBOX_SECURE_ACCESS_KEY` | 可选的固定网关签名密钥；未设置时自动生成 |

`deploy-all.sh` 自动加载 `.env`；单独运行的脚本需要已导出的环境变量。
分阶段部署时，请在**同一个 Shell** 中执行，确保基础设施和 Kubernetes 安装使用一致的密钥：

```bash
set -a
source .env
set +a
export IMAGE_TAG="${IMAGE_TAG:-$(date -u +%Y%m%d%H%M%S)}"
export OPENSANDBOX_API_KEY="${OPENSANDBOX_API_KEY:-$(openssl rand -hex 32)}"
export OPENSANDBOX_SECURE_ACCESS_KEY="${OPENSANDBOX_SECURE_ACCESS_KEY:-$(openssl rand -base64 32)}"
az login
az account set --subscription "$AZURE_SUBSCRIPTION"
```

若需要在另一个 Shell 中继续，请安全保存已生成的密钥，不要在安装中途重新生成。
不要为了验证而轮换线上密钥，应通过已授权的凭据管理流程复用现有值。
完整脚本不会把自动生成的值写回 `.env`。

软件包来源：

| 生态 | 来源 |
|------|------|
| Python / pip | `https://packagefeedproxy.microsoft.io/pypi/simple` |
| npm | `https://packagefeedproxy.microsoft.io/npm/`，配置在 `src/frontend/.npmrc` |
| Copilot CLI | 沙箱镜像内使用官方 `https://gh.io/copilot-install` 安装程序 |

npm 地址必须包含 `/npm/` 路径。实现时代理源中没有 `@github/copilot`，
因此 CLI 使用官方安装程序。

## 5. 本地开发检查

以下检查不会部署 Azure 资源，也不会调用模型：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --index-url https://packagefeedproxy.microsoft.io/pypi/simple \
  -e "src/backend[dev]"
ruff check src/backend sandbox tests
PYTHONPATH=src/backend python -m pytest tests src/backend/tests -q
node --check src/frontend/app.js
az bicep build --file infra/main.bicep --stdout > /dev/null

cd src/frontend
npm ci --ignore-scripts --no-audit --no-fund
npm test
cd ../..
```

`npm test` 构建本地浏览器依赖，并使用模拟 API 回复执行桌面端和移动端固定样例测试。
真实模型测试默认跳过，只有显式启用后才执行。Python 3 提供本地静态测试服务器，
同时需要已安装的 Edge。设置 `PYTHONPATH=src/backend` 还可避免项目移动后，
可编辑安装仍引用旧路径的问题。

本地 Bicep 编译器可能因为缺少 AKS API `2026-07-01` 的类型定义而报告 BCP081。
编译成功并不足够，仍需对完整模板执行实际的 Azure ARM 验证。

## 6. 首次部署到 Azure

此拓扑用于创建**新集群**，不是已有双节点池集群的原地迁移方案。
先检查自己的本地部署计划、模板、预计费用、配额、策略及角色范围。
不要仅为更新前端资源而执行基础设施变更。

### 6.1 选择完整或分阶段执行

完整部署入口：

```bash
./scripts/deploy-all.sh
```

它按以下顺序执行各阶段。也可以在完成前述配置和环境变量导出后逐条执行；
**不要连续执行两种路径**：

```bash
./scripts/prepare-azure.sh
./scripts/deploy-infrastructure.sh --validate-only
./scripts/build-images.sh
./scripts/deploy-infrastructure.sh
./scripts/install-opensandbox.sh
./scripts/verify-deployment.sh
```

最后一个脚本会调用真实模型，并请求使用本地菜单和远程优惠券工具。
执行前应确认模型额度，并授权访问所用远程账户。
发生错误时停止，定位原因后从失败阶段恢复，不要反复重建全部资源。

### 6.2 检查 Azure 上下文和资源提供程序

`prepare-azure.sh` 选择已配置的启用订阅，要求现有资源组的位置与配置一致，
并注册 App、ContainerRegistry、ContainerService、Insights、KeyVault、
ManagedIdentity、Network 和 OperationalInsights 资源提供程序。

脚本读取 `Microsoft.Network/AllowBringYourOwnPublicIpAddress` 的状态，
**仅用于诊断**，不会提交、取消或等待功能审批。
功能处于 `Pending`，既不能证明部署必然失败，也不能证明 Kata 能正常工作。

### 6.3 构建前验证完整模板

`deploy-infrastructure.sh --validate-only` 对完整目标工作负载执行 ARM 验证和 what-if。
它不会构建镜像或创建资源，但仍需要第 4 节中的配置、镜像标签和密钥变量。
包含敏感参数的临时文件使用 600 权限，并在退出时删除。

检查 what-if 中是否有意外修改或删除。最小示例模板验证成功，
不能替代对本项目完整模板的验证。

### 6.4 在 ACR 构建三个镜像

`build-images.sh` 会拒绝启用了管理员账户的注册表，并构建：

| 镜像 | 构建上下文 |
|------|------------|
| `mcd-copilot-api:$IMAGE_TAG` | `src/backend` |
| `mcd-copilot-web:$IMAGE_TAG` | `src/frontend` |
| `mcd-copilot-sandbox:$IMAGE_TAG` | `sandbox` |

构建发生在 ACR，不依赖客户端 Docker 守护进程。沙箱安装 Copilot CLI、
Python 虚拟环境、MCP 服务、包装程序和指令文件。
前端把依赖库及许可证打包到 Nginx 镜像，不从 CDN 加载脚本。

### 6.5 先部署基础资源，再部署工作负载

`deploy-infrastructure.sh` 再次验证后，执行两阶段部署：

| 阶段 | 资源 / 检查条件 |
|------|----------------|
| 基础资源 | VNet/子网、用户分配托管身份、Key Vault 及机密、Application Insights、Container Apps 环境、前置角色分配 |
| 角色传播 | 等待子网 Network Contributor、前后端 AcrPull，以及后端 Key Vault Secrets User |
| 工作负载 | 创建 AKS 和 Container Apps，再确认 kubelet 的 AcrPull 分配 |

不会重新创建复用的 ACR 和 Log Analytics 工作区。新的 Container Apps 环境接入 VNet，
使后端能够访问 AKS 私有端点。

AKS 使用 `Microsoft.ContainerService/managedClusters@2026-07-01`，
且只有一个包含三个 Azure Linux `Standard_D4s_v5` 节点的系统池，
运行时为 `KataVmIsolation`。共享池没有自定义污点，系统和控制平面 Pod 可正常调度。
沙箱 Pod 显式选择 AKS Kata 标签与 RuntimeClass。节点自动扩缩容关闭；
补丁和节点镜像升级已配置，并预留一个升级扩容节点。

脚本检查实际 ARM 节点池配置，随后运行临时 Kata Pod。
验收必须满足：三个 Ready 的 Kata 节点、存在 `kata-vm-isolation`，
且来宾内核名称包含 `mshv`。**不会降级为 `runc`。**

### 6.6 安装 OpenSandbox 并连接私有端点

`install-opensandbox.sh` 将固定的上游版本检出到 `.build/`，
获取 AKS 凭据后执行：

1. 要求存在 Kata RuntimeClass，创建 `opensandbox-system` 和 `opensandbox` 命名空间。
2. 创建 API/签名 Kubernetes Secret，不把真实凭据写入清单文件。
3. 应用包含 Kata 运行时、节点选择器和 `RuntimeDefault` seccomp 的 BatchSandbox 模板。
4. 通过 Helm 安装基础 CRD、控制器、入口网关和生命周期服务。
   由于脚本已创建命名空间，设置 `fastSandbox.namespaces.createSystem=false`。
5. 创建两个内部 LoadBalancer Service，等待私有 IP。
6. 配置服务端网关地址及共享安全访问密钥。
7. 预热沙箱镜像，然后把后端 `OPENSANDBOX_DOMAIN` 更新为私有生命周期端点。

控制器负责创建 BatchSandbox 工作负载，网关将 SDK 请求路由到 execd。
egress 组件执行沙箱默认拒绝的出站策略和凭据代理。
`allowed_host_paths=[]` 禁止配置宿主机路径存储挂载。

### 6.7 在请求路径之外预热镜像

约 2.8 GB 的沙箱镜像首次拉取曾耗时 **5 分 50 秒**。
即使调度成功，也会超过原先的生命周期创建超时。
安装器现在通过临时 Kata DaemonSet 在三个节点上预热，最多等待 10 分钟，
随后删除 DaemonSet。

节点替换或沙箱镜像大幅更新后，应使用已授权的 Kubernetes 上下文，
在开放用户流量前再次预热对应镜像：

```bash
bash scripts/warm-sandbox-image.sh "<registry>/mcd-copilot-sandbox:<tag>"
```

节点替换或镜像垃圾回收后，缓存不一定仍然存在。
仅增加浏览器等待时间不能解决冷启动拉取问题。

### 6.8 本地无法访问 AKS API 时

先检查客户端公网 IP 是否位于 `AKS_AUTHORIZED_IP_RANGES` 中，
不要将 API 开放给所有地址。可使用 Azure AKS Run Command 执行仅依赖 Kubernetes 的运行时检查：

```bash
az aks command invoke \
  --resource-group "$AZURE_RESOURCE_GROUP" \
  --name "$AKS_NAME" \
  --file scripts/verify-kata-runtime.sh \
  --command "bash verify-kata-runtime.sh" \
  --query '{id:id,exitCode:exitCode,logs:logs}'
```

如果仍在运行或尚未返回日志：

```bash
az aks command result \
  --resource-group "$AZURE_RESOURCE_GROUP" \
  --name "$AKS_NAME" \
  --command-id "<command-id>"
```

必须确认**远程命令**的 `exitCode` 为零，不能只看 Azure CLI 退出码。
此路径不能替代 ARM 节点池配置检查，也不会自动把本地安装脚本变为远程安装器。
远程 Helm 安装需要另行准备固定版本图表、参数、清单，并在合适环境中安全传递机密。
实际操作曾使用未压缩归档，因为 Run Command 环境中缺少 `gzip`。

Run Command 仍需要授权。安装集群级 CRD 和 Helm RBAC 时，
可能需要在**集群范围**授予 `Azure Kubernetes Service RBAC Cluster Admin`。
必须先获得明确批准，记录临时角色分配 ID，用完后只撤销该项分配。
不要静默提权，也不要遗留安装权限。

## 7. 验收与浏览器检查

### 基础设施与应用冒烟检查

```bash
./scripts/verify-kata.sh
./scripts/verify-deployment.sh
```

应用脚本会重复隔离检查，确认前端健康状态及代理菜单接口，发送真实聊天请求，
要求返回指定模型和沙箱 ID，并在删除会话时要求 `deleted: true`。
脚本虽然要求模型使用 MCP，但**不检查工具执行事件**；
模型文字不能单独证明工具确实被调用。
需要工具级验收时，应查看 Copilot 会话中成功的 `tool.execution_start` /
`tool.execution_complete` 事件，且不要公开令牌或账户数据。

获取自己的访问地址：

```bash
FRONTEND_URL="https://$(az containerapp show \
  --resource-group "$AZURE_RESOURCE_GROUP" \
  --name "$FRONTEND_APP_NAME" \
  --query properties.configuration.ingress.fqdn -o tsv)"
curl --fail --silent --show-error "$FRONTEND_URL/health"
curl --fail --silent --show-error "$FRONTEND_URL/api/menu"
```

### 固定样例与真实回复的区别

安装依赖后，在 `src/frontend` 目录执行：

```bash
FRONTEND_TEST_URL="$FRONTEND_URL" npm test
RUN_LIVE_CHAT=1 FRONTEND_TEST_URL="$FRONTEND_URL" npm run test:live
```

| 检查 | 能证明什么 |
|------|------------|
| 固定样例浏览器测试 | 八个桌面/移动场景，覆盖渲染、净化、用户纯文本和溢出；聊天 API 被模拟 |
| 真实浏览器测试 | 实际模型 -> 沙箱 -> execd -> API -> 浏览器，不模拟聊天 API |
| 真实断言 | 原始 Markdown 标记；`h2`、两列两行数据表格、`strong`、`pre code`；移动端无页面横向溢出 |
| 清理 | DELETE 返回正确会话 ID；创建过沙箱后要求 `deleted: true` |

真实测试会消耗模型额度，但不会请求工具调用或下单。
Markdown 修复过程中，24 个针对解析器和后端的测试通过；
上线后两次真实浏览器测试通过。这些是历史结果，不能替代你自己的部署验收。

前端 `/health` 只是 Nginx 响应；后端 `/ready` 只检查必需配置，
不会检查 OpenSandbox 或模型的实时连通性。两者都不能单独证明端到端可用。

## 8. 运行时、API 与 MCP 行为

### 会话与命令生命周期

1. 浏览器在 `sessionStorage` 中保存随机会话 ID。
2. FastAPI 验证提示词、购物车和语言，并按会话限流。
3. 管理器复用或创建沙箱，为其生成确定性的 UUID5 Copilot 会话 ID，并使用会话级请求锁。
4. 就绪条件包括沙箱 `RUNNING` 和 `sandbox.is_healthy()`。
   代理 CA 安装期间，即使 Pod Running，execd 仍可能尚未就绪。
5. 后端配置 Credential Vault，写入临时请求 JSON，
   并执行 `python3 /opt/mcd/run_copilot.py <request-file>`。
6. 已发布的 SDK 命令 API 接收 Shell 字符串，因此参数通过 `shlex.join` 序列化。
   临时请求文件在 `finally` 中删除。
7. 包装程序非交互调用 Copilot，提取最终助手消息，再返回下节介绍的单行 JSON 封装。
8. 空闲清理每 30 秒运行一次；显式删除和正常关闭也会清理受管理会话。
   创建后的初始化失败时会尝试删除沙箱。

包装程序使用的主要 CLI 参数：

| 参数 | 用途 |
|------|------|
| `-sp`、`-C /workspace`、`--session-id` | 非交互提示词、沙箱工作目录及稳定会话标识 |
| `--model gpt-6-astra --reasoning-effort high` | 模型及推理强度配置 |
| `--disable-builtin-mcps --additional-mcp-config @/opt/mcd/mcp-config.json` | 使用项目 MCP 配置，而不是内置 MCP 服务 |
| `--available-tools` 配合枚举的 MCP 工具，以及 `--allow-all-tools` | 允许受限可用工具集无人值守执行，不是开放全部 CLI 能力 |
| `--output-format json --stream off` | 返回结构化事件，而不是终端渲染后的回复 |
| `--secret-env-vars`、`--no-auto-update` | 保护指定环境变量，并避免运行时自行更新 |

| 设置 / 限制 | 当前默认值 |
|-------------|------------|
| 沙箱生命周期 | 1,800 秒 |
| 空闲阈值 | 900 秒 |
| 生命周期创建超时 | 120 秒 |
| SDK 请求超时 / 就绪等待预算 | 180 秒 |
| Copilot 子进程超时 | 135 秒 |
| Nginx 上游读取超时 | 180 秒 |
| 活跃会话上限 | 每个后端进程 25 个，不代表集群保证可承载的容量 |
| 限流 | 每会话每分钟 20 次聊天请求 |
| 输入 | 会话 ID 为 8-80 个允许字符；消息 1-2,000 字符；最多 30 条购物车记录 |
| 输出 | 解码后非空，最多 100,000 字符 |

这些超时作用于不同阶段，不是一个统一且有保证的总请求预算。
冷启动拉取、初始化和推理叠加后，仍可能超过 HTTP 链路的等待时间。

### API

| 方法和路径 | 行为 |
|------------|------|
| `GET /health` | 后端进程健康；前端另外有独立的 Nginx `/health` |
| `GET /ready` | 后端运行时机密配置检查，不会以 `/api/ready` 代理 |
| `GET /api/menu` | 本地模拟菜单 |
| `POST /api/chat` | 在该会话的 Kata 沙箱中执行一轮模型调用 |
| `DELETE /api/sessions/{session_id}` | 删除受管理沙箱，返回 `session_id` 与 `deleted` |

请求体示例：

```json
{
  "session_id": "demo-session-001",
  "message": "Show the simulation menu as a Markdown table.",
  "locale": "en",
  "cart": []
}
```

聊天成功时返回 `session_id`、`message`、`sandbox_id` 和 `model`。
语言可选 `zh-CN`、`zh-TW`、`en`，界面默认简体中文。
参数错误返回 422，限流返回 429，沙箱失败返回通用 502 响应。
详细诊断应查看已授权的服务日志。

### MCP 工具与订单状态

| 服务 | 传输方式 | 暴露的工具 |
|------|----------|------------|
| `mcd-order-sim` | 本地 stdio | `get_menu`、`calculate_order`、`create_order`、`get_order` |
| `mcd-mcp` | HTTP，`https://mcp.mcd.cn` | `available-coupons`、`query-my-coupons`、`campaign-calendar`、`query-my-account`、`query-nearby-stores`、`delivery-query-stores`、`query-meals`、`query-meal-detail`、`query-store-coupons`、`calculate-price` |

本地计价使用整数人民币分，数量范围为 1-20，并收取模拟的 300 分服务费。
`create_order` 会拒绝 `confirmed=false`；智能体指令要求先展示品项与总价，
获得明确确认后才能创建。这不是适用于真实交易、独立认证的审批流程。

模拟订单存放于会话沙箱内的 `/workspace/state/orders.json`，
沙箱删除后不会持久保存。页面购物车仅代表用户意图，不是官方库存或价格的证明。
官方优惠券、门店、餐品问题必须使用对应远程工具，缺失的必需标识不能猜测。

## 9. 无损 Markdown 回复

必须保证**整个回复链路**都正确：

```text
Copilot --output-format json --stream off
    |
    v
JSONL assistant.message.data.content + successful result
    |
    v
Runner stdout: {"message":"## Heading\n\n| A | B |\n..."}
    |          One physical line; newlines remain JSON-escaped
    v
execd command-log chunks
    |
    v
Backend joins chunks -> JSON decode -> original multiline Markdown
    |
    v
API JSON -> Marked GFM -> DOMPurify allowlist -> browser DOM
```

已修复两个上游问题：

- CLI 默认文本输出已经把 Markdown 表格转成 Unicode 终端边框，
  并丢掉标题、强调标记。现在包装程序读取 JSONL，选择最后一条符合条件的助手消息，
  忽略工具请求和过程说明事件，并要求存在成功完成的结果事件。
- execd 按行输出日志时会省略非空行末尾的换行符，直接拼接会压扁表格行和段落。
  现在使用单行 JSON 封装，把换行和代码缩进保留为转义内容，直到后端解码。

无效 JSON、失败或不完整的结果、缺失回复、错误封装和超长回复都会明确失败。
不要尝试从终端表格猜测还原 Markdown，也不要静默回退到未解析的 CLI 输出。

只有助手回复使用 Markdown；用户输入保持纯文本。原始 HTML 会转义，
DOMPurify 仅允许有限的格式、表格、链接元素和属性。
链接只接受 HTTP、HTTPS、mailto；外部网页链接添加 `noopener noreferrer`。
回复中的图片不渲染；宽表格与代码块支持横向滚动。
依赖库本地打包，CSP 不开启内联脚本或不安全的动态求值。

升级后请刷新并发送新消息。旧回复中已丢失的 Markdown 标记无法由前端补回。

## 10. 更新已有部署

只修改界面时，仅构建和更新 `mcd-copilot-web`。
修改包装程序协议时，必须**同时部署后端镜像及其沙箱镜像引用**。
旧包装程序输出纯文本，与新的 JSON 封装解码器不兼容。

仅更新前端时，在项目根目录加载已有配置后执行：

```bash
WEB_TAG="web-$(date -u +%Y%m%d%H%M%S)"
REGISTRY=$(az acr show --name "$ACR_NAME" --query loginServer -o tsv)
az acr build --registry "$ACR_NAME" --platform linux/amd64 \
  --image "mcd-copilot-web:$WEB_TAG" src/frontend
az containerapp update \
  --resource-group "$AZURE_RESOURCE_GROUP" \
  --name "$FRONTEND_APP_NAME" \
  --image "$REGISTRY/mcd-copilot-web:$WEB_TAG" \
  --output none
```

此路径使用下方修订检查命令时，将目标替换为 `FRONTEND_APP_NAME`，
随后执行线上浏览器检查。仅更新前端不应修改后端或 AKS。

加载已有配置，构建并验证修改后的代码，再执行：

```bash
UPDATE_TAG="reply-$(date -u +%Y%m%d%H%M%S)"
REGISTRY=$(az acr show --name "$ACR_NAME" --query loginServer -o tsv)
az acr build --registry "$ACR_NAME" --platform linux/amd64 \
  --image "mcd-copilot-sandbox:$UPDATE_TAG" sandbox
az acr build --registry "$ACR_NAME" --platform linux/amd64 \
  --image "mcd-copilot-api:$UPDATE_TAG" src/backend

az containerapp update \
  --resource-group "$AZURE_RESOURCE_GROUP" \
  --name "$BACKEND_APP_NAME" \
  --image "$REGISTRY/mcd-copilot-api:$UPDATE_TAG" \
  --set-env-vars "SANDBOX_IMAGE=$REGISTRY/mcd-copilot-sandbox:$UPDATE_TAG" \
  --output none
```

变更前记录上一组兼容镜像；必要时先预热较大的新沙箱镜像。
不要为应用修复盲目重跑基础设施部署，它可能把镜像引用和生命周期端点重置为模板值。
完整 `deploy-all.sh` 会重建全部镜像，并在后续阶段重新连接该端点。

更新命令返回时，新修订版可能尚未实际提供服务。检查：

```bash
az containerapp show \
  --resource-group "$AZURE_RESOURCE_GROUP" --name "$BACKEND_APP_NAME" \
  --query '{latest:properties.latestRevisionName,ready:properties.latestReadyRevisionName,image:properties.template.containers[0].image,sandbox:properties.template.containers[0].env[?name==`SANDBOX_IMAGE`].value}'
az containerapp revision list \
  --resource-group "$AZURE_RESOURCE_GROUP" --name "$BACKEND_APP_NAME" \
  --query '[?properties.active].{revision:name,health:properties.healthState,traffic:properties.trafficWeight}'
```

执行真实浏览器测试前，确认目标镜像组合正确、latest/ready 修订名称一致、
状态为 `Healthy`，并承接 100% 流量。CLI 连接中断时，
先检查 Azure 的实际状态，再决定是否重试修改。
回滚时在同一次更新中恢复兼容的后端和沙箱引用，再重复就绪与验收检查。
修订版替换期间，内存中的会话可能丢失。

## 11. 排障与部署经验

| 现象 | 诊断与处理 |
|------|------------|
| 功能长期 `Pending` | 查询状态，但以完整 ARM 验证、资源创建和运行时检查决定是否可用。不要反复注册或重建来“重置”订阅审批。 |
| Portal 搜不到功能 | 打开 **订阅 > 你的订阅 > 预览功能**，清除状态筛选，再按提供程序/名称筛选。并非所有功能都会显示，可用 `az feature show` 直接查询；资源提供程序注册是另一项操作。 |
| 旧 API/机型被拒绝 | 在已验证订阅中，API `2024-10-01` 拒绝该运行时组合，新 API 配合 D4s_v3 也不可用。成功部署使用 API `2026-07-01` + D4s_v5 + Azure Linux + `KataVmIsolation`，但这不是所有订阅均可用的保证。 |
| 沙箱长期 Pending / `POD_READY_TIMEOUT` | 从事件区分调度、镜像拉取、授权或资源不足。实际首个故障是近六分钟的拉取，不是功能审批；预热后重试。 |
| Pod Running 但 execd 不可用 | 代理 CA 初始化可能还在更新证书库。等待 `sandbox.is_healthy()`，不要使用固定睡眠代替。 |
| Helm 命名空间所有权冲突 | 命名空间由安装脚本创建，应按当前配置关闭基础图表的系统命名空间创建。 |
| 命令 API 拒绝参数数组 | 已发布 SDK 0.1.16 接收字符串，应使用现有 `shlex.join` 路径，不要直接照搬较新的上游源码接口。 |
| GitHub 可访问但 Copilot 未授权 | 凭据必须绑定到对应账户层级的确切 Copilot API 主机，而不只是 `api.github.com`；不要使用通配符凭据绑定。 |
| Markdown 仍显示终端边框 | 检查 `/api/chat` 原始输出和镜像兼容性，确认 JSONL 解析及单行封装。只改 CSS/Marked 无法恢复丢失的语法。 |
| 更新后立即看不到效果 | 旧修订版可能仍在服务；等待新修订真正 Ready/Healthy，并完成流量切换。 |
| 私有镜像或 Key Vault 引用失败 | 检查正确身份、精确的注册表/密钥库角色范围及传播状态，不要通过开启 ACR 管理员账户绕过。 |
| 本地 Docker 构建无法解析镜像代理 | 使用现有 ACR 构建路径并检查生成镜像；未经授权不要修改共享 Docker 守护进程配置。 |
| npm 代理返回 404 | 使用 `/npm/` 路径而不是裸域名，保留锁文件和配置的注册表。 |
| 浏览器测试触发 CSP unsafe-eval 错误 | 使用兼容 CSP 的定位器和断言，不要为了通过测试而降低生产 CSP。 |
| 无法直连 AKS API | 检查授权 CIDR，使用已批准的 Run Command 或可连通执行器；单独检查远程命令是否完成。 |

只读功能状态查询：

```bash
az feature show --namespace Microsoft.Network \
  --name AllowBringYourOwnPublicIpAddress \
  --query properties.state -o tsv
```

`FEATURE_REGISTRATION_TIMEOUT_SECONDS` 已不再使用。
验证失败必须停止，不能为了部署成功而静默取消 Kata 隔离。

## 12. 安全边界与 POC 限制

### 凭据与权限

真实业务凭据从 Key Vault 解析到可信后端，再交给 OpenSandbox Credential Vault。
工作负载虚拟机中的 `GH_TOKEN`、`COPILOT_GITHUB_TOKEN`、
`COPILOT_MCP_MCD_TOKEN` 仅包含占位符，不包含真实令牌。
凭据代理只向配置的 HTTPS 目标注入 bearer 请求头。

| 绑定 | 精确主机 | 方法 |
|------|----------|------|
| GitHub/Copilot | `api.github.com`、`api.githubcopilot.com`、`api.individual.githubcopilot.com`、`api.business.githubcopilot.com`、`api.enterprise.githubcopilot.com` | HTTPS/443 上的 GET、POST |
| 远程 MCP | `mcp.mcd.cn` | HTTPS/443 上的 GET、POST |

出站网络策略为了服务连通性包含选定的通配符域名，**凭据绑定不使用通配符**。
默认拒绝网络策略与凭据注入用途不同。后端及凭据代理基础设施仍属于可信边界。

| 身份 | 角色 | 范围 |
|------|------|------|
| AKS 控制平面 | Network Contributor | AKS 子网 |
| AKS kubelet | AcrPull | 现有注册表 |
| 后端 | AcrPull；Key Vault Secrets User | 分别限定到注册表和部署密钥库 |
| 前端 | AcrPull | 注册表 |
| 临时安装者，仅在批准后 | AKS RBAC Cluster Admin | 集群范围，使用后撤销 |

Key Vault 使用 RBAC、90 天软删除和清除保护；ACR 管理员账户禁用。
Kata 工作负载通过安全网关访问，而生命周期端点使用带 API 密钥的私有 HTTP。
这不意味着 VNet 内部具备端到端 TLS。

### 此 POC 尚未提供的能力

- 公共应用登录、浏览器会话 ID 的认证归属检查、按用户隔离的远程 MCP 凭据。
  会话 ID 不是授权边界；应限制为可信演示用户，并避免连接敏感的共享账户。
- 持久会话/订单存储或共享会话注册表。状态保存在内存中，因此后端最多一个副本；
  横向扩展需要重新设计。
- 生产可用性保证：三个节点共享一个池，未指定可用区，OpenSandbox 控制平面组件为单副本。
- 所有服务的私有端点：Key Vault 开启公共网络，AKS API 虽受 IP 限制但仍是公共端点。
- 真实商业审批、支付或财务控制。本地确认布尔值与模型指令只适合模拟场景。
- 完全可复现的构建：生产发布应固定 Python 依赖与 CLI 版本，并检查兼容性。

CLI 禁用内置 MCP 服务，只暴露指定 MCP 工具，不提供 Shell、文件写入或任意 URL 工具。
这些控制能降低风险，但不能把未提供应用认证的演示直接变为生产服务。

## 13. 运维与清理

通过 Portal 的**资源组 > 部署**查看 ARM 阶段状态，
通过 **AKS > 节点池**查看节点池创建进度，
通过 **Container App > 修订和副本**查看流量与健康状态。
Kubernetes 事件用于解释 Pod 启动问题；Container Apps 日志和 Application Insights
用于诊断 API 故障。不要公开原始提示词、账户查询结果、带令牌的输出或完整环境变量。

使用已授权的 Kubernetes 上下文：

```bash
kubectl get nodes -l kubernetes.azure.com/kata-vm-isolation=true
kubectl get deployments -n opensandbox-system
kubectl get batchsandboxes,pods,daemonsets -n opensandbox
kubectl get events -n opensandbox --sort-by=.lastTimestamp
```

通过应用删除已知测试会话；如果它仍由后端管理，应要求返回 `deleted: true`：

```bash
curl --fail --silent --show-error --request DELETE \
  "$FRONTEND_URL/api/sessions/<your-test-session-id>"
```

确认测试沙箱和预热 DaemonSet 已清理。TTL 是兜底机制，不能替代主动清理。
安装后不应遗留临时集群管理员分配；应根据记录的分配 ID 撤销，
不要批量删除某个主体的全部角色。

销毁环境前，先精确盘点本示例资源及依赖关系。
可以从 `workload=opensandbox-mcdonalds-copilot` 标签开始，但不能把标签当作唯一归属证据。
任何删除都必须先获得明确批准，保留现有资源组、不相关应用、共享 ACR 和共享
Log Analytics 工作区，不要直接删除整个资源组。
Key Vault 清除保护可能影响后续重建；删除资源不会重置功能注册状态。
本项目没有一键批量销毁脚本。

## 14. 参考资料

- [OpenSandbox 上游源码和图表](https://github.com/opensandbox-group/OpenSandbox)
- [GitHub Copilot CLI](https://github.com/github/copilot-cli)
- [AKS 文档](https://learn.microsoft.com/azure/aks/)
- [AKS Run Command](https://learn.microsoft.com/azure/aks/access-private-cluster)
- [Azure Container Apps 修订](https://learn.microsoft.com/azure/container-apps/revisions)
- [ACR 构建](https://learn.microsoft.com/azure/container-registry/container-registry-tutorial-quick-task)
- [Key Vault RBAC](https://learn.microsoft.com/azure/key-vault/general/rbac-guide)
- [Model Context Protocol](https://modelcontextprotocol.io/)
- [Marked](https://marked.js.org/) 和 [DOMPurify](https://github.com/cure53/DOMPurify)

`.azure/` 下的环境专属部署记录仅保存在本地，不包含在公开仓库中。
可在该目录保存自己的部署决策和验证证据；可复用的部署流程已整理在本文中。
