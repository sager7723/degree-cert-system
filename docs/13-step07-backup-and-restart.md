# 第七步：备份成果与整机重启验收

截至本步，用户已反馈第六步全部操作可用，说明 Ubuntu 上三角色登录、签发、核验、导出、撤销和网页进程重启后的数据保存均已通过。当前进入系统交付收尾，不再重复合约部署。

## 一、安装备份工具

将 degree-cert-system-step07.zip 拖入 Ubuntu 的 /home/wsc/Downloads，然后执行：

```bash
cd /home/wsc/degree-cert-system
unzip -o /home/wsc/Downloads/degree-cert-system-step07.zip -d /home/wsc/degree-cert-system
```

本包只增加备份工具、测试及说明，不覆盖网页模板和样式，保持退出按钮在左栏底部的选择。

## 二、停止网页，备份应用

先完成当前页面操作，确认“交易记录”没有待确认交易。记下第六步从网页签发、撤销的那张证书编号，重启后用它作核对。

在运行网页的终端按 Ctrl+C。然后执行：

```bash
cd /home/wsc/degree-cert-system
bash scripts/08-backup-application.sh
```

不用 sudo。备份期间保持网页停止，不修改项目文件；链节点可以继续运行。脚本不发送交易，不更改证书状态，也不停止节点。

预期输出包括：SQLite 快照完整性与外键校验通过、压缩包逐文件 SHA-256 校验通过、数据库各表记录数量、备份路径与 SHA-256。

备份位置：

```text
/home/wsc/degree-cert-backups/application-日期时间-随机后缀/application.tar.gz
```

每次生成新的目录，不覆盖先前备份。报告位于项目的 reports/stage07-日期时间-随机后缀/report.txt，终端会显示完整路径。

备份包含应用源代码、离线依赖、合约源码与构建产物、角色配置、应用身份、账号、交易登记和经过 SQLite backup API 生成的数据库快照。数据库已有 WAL 日志中的已提交数据也会进入快照。

不包含 .venv、历史 reports、CMC 临时工作日志；更不包含 /home/wsc/chainmaker-go 中的节点账本、CMC 二进制、节点管理员身份或 Docker 镜像。**它是应用备份，不能单独恢复整个区块链环境。**

生成的压缩包包含账号和身份密钥，请只保存到自己的设备，不发送到聊天。反馈 report.txt 即可。可以把整个 application-日期时间-随机后缀文件夹复制到 Windows，以同时保留压缩包和校验文件。保留 Ubuntu 原件。

## 三、保存完整虚拟机副本

为了今后能恢复“链 + Docker VM + CMC + 应用”，还需要保存整个虚拟机环境：

1. 应用备份成功后，从 Ubuntu 系统菜单正常关机。
2. 等待 VMware 显示虚拟机已关闭电源。不要使用挂起状态，也不要强制断电。
3. 找到这个 Ubuntu 虚拟机的 .vmx 配置文件所在文件夹。可以在 VMware 中选中虚拟机查看配置文件位置。
4. 在 Windows 中，把完整虚拟机文件夹复制到有足够空间的备份位置，名称可用 Ubuntu-degree-cert-working-20260928。应包含所有虚拟磁盘及相关文件，不能只复制 .vmx 或单个磁盘分片。若磁盘在其他目录，还需一并保存；位置不明确时先核对再复制。
5. 继续使用原虚拟机，保留副本作为恢复用。不要同时启动原件和副本开展业务。

VMware 快照可以方便回退，但不能代替独立的完整副本。应用备份和整机副本应在这一轮停止业务后保存，避免记录时间点不一致。

## 四、启动原虚拟机并恢复系统

启动原来的 Ubuntu，登录 wsc，打开终端：

```bash
cd /home/wsc/degree-cert-system
sudo -v
bash scripts/03-start-and-query.sh
```

输入 Ubuntu 密码时终端没有字符显示是正常现象。只对 sudo -v 输入密码，不要给整条 bash 命令加 sudo。

第三步脚本会检查并恢复四个已有发布节点。四个 RPC 已运行时跳过重复启动；若只运行了部分节点，会停止并留下报告。发生这种情况反馈报告，不要反复启动、删除容器或清空链数据。

确认脚本结果码为 0、真实区块查询成功、四个 RPC 与 Go 引擎端口都在监听后，执行：

```bash
.venv/bin/python -B backend/app.py doctor
```

doctor 必须显示真实合约 health 可用，再执行：

```bash
bash scripts/07-start-web.sh
```

第三步旧提示中提到“下一步部署合约”是前期检查时的文字；当前合约已经部署，应直接进行上面的 doctor 和网页启动。不要重跑第四、第五步或删除已有状态。

## 五、重启后的验收

在 Ubuntu 浏览器打开 http://127.0.0.1:8080，核对：

- 三个原账号仍可登录，密码不变。
- A：DEMO_20260927083846_c03f4591_A，原内容一致，状态已撤销。
- B：DEMO_20260927083846_c03f4591_B，原内容一致，状态有效。
- 第六步网页新建证书仍在；撤销状态、签发与撤销交易 ID 与关机前一致。
- 对应账号的交易记录、日志仍在。

这轮先做查询和核验即可，不需要重新签发相同证书。完成后反馈第七步备份报告，以及整机重启后 doctor 和上述检查的结果。若启动失败，同时反馈新第三步 report.txt。

## 六、然后开始录屏

整机重启验收通过后，按 docs/14-recording-rehearsal.md 进行一次演练，再正式录屏。录屏素材齐全后，整理应用设计、数据设计和代码解析的授课 PPT。
