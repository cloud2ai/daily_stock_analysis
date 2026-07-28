# 联合启动 newsgrab（Google News 采集服务）

`MacroIntelAgent`（`AGENT_ARCH=multi` 的 `full` 编排模式下的宏观情报 Agent）依赖一个独立的
开源项目 [newsgrab](https://github.com/cloud2ai/newsgrab) 来采集多区域 Google News 内容。
newsgrab 完全独立部署、独立维护，DSA 不内置它、不依赖它也能正常运行——不配置时
`search_macro_news` 这个 agent tool 会自动禁用，不影响其余分析流程。

本文档说明如何用 Docker Compose 一键联合启动两个项目。

## 前置条件

1. newsgrab 已作为**兄弟目录**克隆在本仓库同级：

   ```
   some-dir/
     daily_stock_analysis/   <- 本仓库
     newsgrab/                <- git clone <newsgrab repo url>
   ```

   如果 newsgrab 放在别的位置，在 `.env` 里设置 `NEWSGRAB_COMPOSE_PATH` 指向它的
   `docker-compose.yml`（相对路径相对于 `docker/docker-compose.newsgrab.yml` 自身位置）。

2. Docker Compose v2.20+（支持 `include:` 顶层指令）。用 `docker compose version` 确认。

## 一键启动

```bash
docker compose -f docker/docker-compose.yml -f docker/docker-compose.newsgrab.yml up -d server
# 或同时启动定时分析进程：
docker compose -f docker/docker-compose.yml -f docker/docker-compose.newsgrab.yml up -d analyzer server
```

这一条命令会：
- 构建并启动 newsgrab 的 `playwright-service` + `collector-service`（复用 newsgrab 仓库自己的
  `docker-compose.yml`，未做任何修改）。
- 构建并启动 DSA 的 `server`（或 `analyzer`）。
- 把 DSA 的容器加入 newsgrab 的内部网络（`newsgrab-internal`），使其能直接通过容器名
  `http://collector-service:8000` 访问 newsgrab，**不需要对外发布端口，不需要手动配置代理**。
- 自动覆盖 `GOOGLE_NEWS_COLLECTOR_URL` 为容器内部地址——`.env` 里如果配了别的值（比如本地开发时
  手动跑的 `http://127.0.0.1:18101`），叠加这个文件后会以这里的为准。

停止：

```bash
docker compose -f docker/docker-compose.yml -f docker/docker-compose.newsgrab.yml down
```

## 已知限制

- newsgrab 自己的 `docker-compose.yml` 给两个服务写死了 `container_name`
  （`newsgrab-playwright-service`/`newsgrab-collector-service`）。如果你**同时**在 newsgrab
  仓库目录下单独跑过 `docker compose up`（容器仍在运行），再通过本文档的联合启动方式启动，会因为
  容器名冲突而失败。先 `docker compose down`（在 newsgrab 仓库目录下）停掉独立实例，再用联合方式
  启动即可；两者不需要同时运行。
- newsgrab 的代理配置（`HTTPS_PROXY`/`NO_PROXY` 等）如果需要，仍然按 newsgrab 自己仓库 README
  里的说明设置——联合启动只解决"一条命令起两个项目、内部网络怎么连通"的问题，不改变 newsgrab
  自身对代理环境的要求。

## 不用 Docker 的场景

本地直接跑 `python3 main.py --serve` / `uvicorn server:app`（不经过 `docker/docker-compose.yml`）
时，联合启动方式不适用；照常在 `.env` 里配置 `GOOGLE_NEWS_COLLECTOR_URL` 指向 newsgrab 实际暴露的
主机端口即可（newsgrab 默认不对外发布端口，需要自行加一个 `docker-compose.override.yml` 发布，或
参照 newsgrab 仓库自己的说明）。
