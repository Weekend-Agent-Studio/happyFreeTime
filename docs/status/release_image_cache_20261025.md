# 正式镜像依赖缓存整理

_Current，2026-10-25。正式镜像构建准备，不代表公网发布。_

用户确认 80a595c 路线补丁通过服务器严格冒烟；此时通过只读文件挂载生效，运行镜像尚未包含该补丁。后续需正常构建 api/web、移除临时 overlay 后复验，不将镜像原标签当作修正版本。

原 Dockerfile 先 COPY 业务源码，再在其子阶段安装 CPU PyTorch/BGE 依赖；release 标签也位于依赖安装之前。源码或 release 变化都会使大依赖层失去缓存。现在基础 dependencies 阶段仅依赖基础镜像和 requirements.txt，rag_dependencies 阶段仅增加系统/requirements-rag.txt 依赖。runtime 保持原业务文件白名单，rag 从 runtime 复制业务目录、独立复制模型资产；最终两种目标均保留非 root 用户、启动命令和 revision 标签。

首次改变阶段结构仍可能重新安装依赖；后续缓存复用依赖相同构建器及未清理的缓存，不保证任意环境都快速构建。本机没有 Docker，未实际构建镜像。部署定向测试 8 项通过，git diff --check 通过；首次测试有 2 项被临时目录权限阻断，在可访问临时目录的环境重跑后通过。服务器构建与正式容器冒烟仍待执行。
