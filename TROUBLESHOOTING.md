# TROUBLESHOOTING · 踩坑记录

> 原则：每个坑记录 **现象 → 根因 → 解法**，能定位到源码/文档的写明出处。
> 这份文档同时是两样东西：部署运维手册（照着避坑），和工程问题排查能力的存证。

## 1. Docker / 镜像

### 1.1 MinIO 镜像拉不到，多个镜像站无货 ⭐ 死局级

- **现象**：`minio/minio:RELEASE.2024-05-28T17-19-04Z` 拉取时 blob size validation 反复失败（下载的层大小与 manifest 声明不符），换华为云等多个镜像站均无货或同样失败，Docker Hub 登录后拉取仍 401。
- **根因**：代理节点损坏了 blob（对比正常节点下载同层大小不一致）；叠加 MinIO 侧对 Docker Hub 匿名拉取的封锁，形成死局。
- **解法**：**放弃 MinIO，改用 Milvus 官方 standalone_embed 轻量模式**——单容器内嵌 etcd + 本地存储（`ETCD_USE_EMBED=true`、`COMMON_STORAGETYPE=local`），从架构上移除对 MinIO 的依赖。BM25 / 混合检索功能不受影响。
- **教训**：被依赖卡死时，最贵的方案不是"再换个镜像源试一次"，而是审视这个依赖是否可以**从架构上消除**。

### 1.2 内嵌 etcd 启动 panic：`embedded etcd can not be used under distributed mode`

- **现象**：按 standalone_embed 模式配置后，Milvus 容器启动即 panic。
- **根因**：只设了 `ETCD_USE_EMBED=true`，**漏了 `DEPLOY_MODE=STANDALONE`**。Milvus 默认按分布式模式自检，内嵌 etcd 只允许在 standalone 模式下使用。触发条件在源码 `paramtable/service_param.go` 中定位。
- **解法**：补 `DEPLOY_MODE=STANDALONE` 环境变量。
- **教训**：官方文档的配置示例要**整块抄**，掐头去尾猜哪些"看起来可选"的变量可以省，是最贵的省钱方式。

### 1.3 v2.6.22 镜像内不含 `embedEtcd.yaml`

- **现象**：配了 `ETCD_CONFIG_PATH` 后容器找不到该文件。
- **解法**：自备 `embedEtcd.yaml` 挂载进 `/milvus/configs/`。

### 1.4 compose 文件残留已废弃的服务定义

- **现象**：`docker compose up -d` 报 minio 拉取失败，但实际上 Milvus 已改内嵌模式、根本不需要 minio。
- **根因**：改造时只改了 milvus 服务的配置，**没有删除 etcd/minio 的服务定义**；`up -d` 默认拉起文件里所有服务。
- **解法**：删除 etcd/minio 服务定义及其 volumes 声明。
- **教训**：架构降级时，"不用了"和"删干净"是两件事——残留定义随时会在下一次 `up -d` 时炸给你看。

## 2. 网络 / 代理

### 2.1 代理环境变量劫持 localhost 请求 ⭐ 高频复发

- **现象**：本机开着代理（HTTP_PROXY=127.0.0.1:xxxx）时——pymilvus 的 gRPC 连 `localhost:19530` 被代理劫持报 502；curl 连本地 MySQL/Milvus 假死；`docker compose` 拉镜像也走代理。
- **根因**：HTTP_PROXY/HTTPS_PROXY 对所有 HTTP 流量生效，**包括发往 localhost 的**；gRPC 底层同样走 HTTP/2 连接，一样被劫持。
- **解法**：跑任何连本地服务的进程前：
  ```bash
  export HTTP_PROXY= HTTPS_PROXY= http_proxy= https_proxy= \
         NO_PROXY=localhost,127.0.0.1 no_proxy=localhost,127.0.0.1
  ```
  curl 临时测试用 `--noproxy '*'`。
- **教训**："本地服务连不上"先查代理，再查服务——这个排查顺序能省一小时。

### 2.2 宿主机连不上容器网络别名

- **现象**：`redis://redis:6379` 在宿主机直连时解析失败。
- **根因**：`redis` 这种主机名只在 **docker compose 网络内部**可解析；宿主机上跑的进程不在网络里。
- **解法**：宿主机侧配置改 `redis://localhost:6379`（配合端口映射）。
- **教训**：同一个配置项，"容器里跑"和"宿主机跑"要写不同的地址——配置文件注释里标清楚。

## 3. 数据库

### 3.1 端口冲突：本机 3306 已被占用

- **现象**：MySQL 容器起不来或连不上。
- **根因**：本机早先装的 MySQL 服务占用了 3306。
- **解法**：compose 映射改 `3307:3306`，应用连接串同步改 3307。
- **教训**：端口映射改了 compose 还要**全局搜一遍连接串**，两处不同步的排查时间远大于改动时间。

### 3.2 `docker-entrypoint-initdb.d` 按文件名字母序执行，破坏外键依赖 ⭐ 改名翻车

- **现象**：项目改名后重建数据库，MySQL 初始化在 `ERROR 1824: Failed to open the referenced table 'low_confidence_questions'` 处失败。
- **根因**：initdb 目录的脚本**严格按文件名字母序执行**。原文件按章节号命名（ch04 建表、ch10 引用），字母序恰好等于依赖拓扑序；改成功能名后（classifier < retrieval），建表脚本排到了引用它的脚本之后，外键指向尚不存在的表。
- **解法**：全部 SQL 加数字前缀 `01-tools-ddl.sql` … `11-rag-staging-review.sql` 锁定执行顺序；全仓 9 处文件名引用（Makefile / 评估脚本 / conftest）同步更新。
- **教训**：① 批量重命名文件时，**凡是"按名字排序生效"的机制**（initdb 目录、加载顺序）都要先画依赖图再动手；② 初始化失败的容器不要直接重启——entrypoint 检测到数据目录非空会跳过 init 脚本，必须 `down -v` 清卷重来。

### 3.3 `.env` 迁移后数据库名残留旧值

- **现象**：服务启动报 database not found。
- **根因**：`.env` 从旧项目借用，`DATABASE_URL` 里的库名没跟着改。
- **解法**：迁移配置时用 `grep 旧名` 全仓扫描，而不是靠记忆逐个改。

## 4. 配置 / 依赖

### 4.1 示例配置里的模型名不存在

- **现象**：按 `.env.example` 填 `deepseek-v4-flash`，调用报模型不存在。
- **根因**：模板里的模型名是过时的/杜撰的。
- **解法**：先调上游 `/models` 接口实测可用模型列表（实测返回 `deepseek-flash`、`deepseek-v4-pro`），以实测为准。
- **教训**：**凡引用第三方资源的名字（模型名、镜像 tag、表名），先实测再写死**——文档会过时，接口不会。

### 4.2 思考链模型导致流式输出卡顿

- **现象**：开启 thinking 的一句回复 22s+，token 消耗 4 倍，流式体验不可用。
- **解法**：`CHAT_THINKING=disabled`。客服场景要的是首字延迟，不是深度推理。
- **教训**：模型能力开关要对着**场景 SLA** 选，不是默认全开。

### 4.3 uv 下载依赖超时

- **现象**：`uv sync` 下载包时超时中断。
- **解法**：`export UV_HTTP_TIMEOUT=180`。

### 4.4 Windows 环境无 make

- **现象**：Git Bash 下 `make` 命令不存在。
- **解法**：直接执行 Makefile 中对应 target 的底层命令（每个 target 就一两条命令）。

## 5. 排查方法论（从这些坑里沉淀的）

1. **先分清"连接问题"还是"配置问题"**：连不上一律先看代理和端口，再看用户名密码，最后才怀疑服务本身。
2. **报错指向源码**：panic 信息含糊时（如 distributed mode 提示），直接到上游源码 grep 触发条件，比搜博客快且准。
3. **改名/迁移类操作**：改完立刻跑一遍最小验证（起服务 → 建库 → 冒烟请求），不要攒到实验跑一半才炸。
4. **死局换思路**：同一个问题上第三次重试（换镜像、换节点、换登录态）仍失败，就该评估"这个依赖能不能去掉"——1.1 的解法就是这么来的。
