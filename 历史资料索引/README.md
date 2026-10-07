# 历史资料与迁移索引

来源仓库：https://github.com/Tabris-Ayanami/its-transtar-agent
来源分支：fix/local-clone-validation-20261002
来源完整提交：由根目录迁移来源.json记录（bc1f456c）。

原仓库完整文件索引.zip包含tracked-files.tsv：逐文件路径、原Git blob SHA1、Git对象字节数、LFS SHA256和实体字节数，以及included/original_repository标记。Git对象大小不是LFS实体大小；引用对象哈希不代表新仓库已携带对应历史内容。

已迁移文件哈希.zip记录导出时4758个源文件的SHA256。迁移后根README新增新仓库入口，原README保存为docs/原仓库README历史说明.md；.gitignore及本文是交付整理新增，不冒充原提交字节。新增ZIP哈希见迁移来源.json。

未迁移原件包括智能体实验中的大型旧场景数组、成果/实测档案及旧便携大包、各目录历史ZIP/7z和完整厂商分发副本。运行所需程序和南京案例保留。最新9场原始实验另外压缩封存到最新闭环证据目录，不受上述历史排除影响。

获取其他旧档案时，在有原仓库权限的独立目录检出所列原提交，并按对应路径执行Git LFS下载。保留历史版本和失败状态，不把索引文件当作原始数据。
