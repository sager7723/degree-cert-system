# 应用与数据设计

## 调用流程

Ubuntu 浏览器 → Waitress → Flask 路由与角色检查 → Registry 业务服务 → CMC 普通用户身份 → chain1 / degree_cert_v1 Go 合约。

Jinja 页面与 CSS/JavaScript 均在本地，不依赖 Node.js 或外部 CDN。网页账号按角色映射到第四步的机构身份。多个同角色网页账号在链上代表同一机构，本地审计记录具体网页账号。

| 角色 | 网页账号 | 链上组织 | 写权限 |
| --- | --- | --- | --- |
| 高校 | uni_admin | wx-org1.chainmaker.org | issueCertificate |
| 监管 | edu_admin | wx-org2.chainmaker.org | revokeCertificate |
| 查询 | query_user | wx-org3.chainmaker.org | 无 |

网页读取共用 org3 查询身份，私钥只由服务端 CMC 读取，网页表单不接收身份路径或 sudo 命令。

## 全文、摘要与有效性

SQLite 按 cert_id 保存全文，student_id 只建查询索引，同一学生可有多张证书。

规范全文包含 schema_version、cert_id、student_id、student_name、school_name、major、degree、graduation_date。协议固定 degree-cert/v1；文本去首尾空白并作 Unicode NFC 规范化，日期统一 YYYY-MM-DD。按键排序生成紧凑 JSON，UTF-8 编码后计算 SHA-256。

链上保存编号、摘要、有效/撤销状态、机构、交易 ID、时间和撤销原因。姓名、学号、院校等全文不写入链；演示撤销原因也不要放真实个人敏感信息。

导出 JSON 是规范全文。核验重新计算摘要并调用 verifyCertificate。摘要一致代表内容与存证一致；当前有效还要求链上状态为 valid。

## SQLite 表

| 表 | 内容 | 关键约束 |
| --- | --- | --- |
| users | 账号、口令哈希、角色、显示名 | username 唯一 |
| certificates | 全文、摘要、本地状态、最近确认的链记录 | cert_id 主键；学号可重复 |
| operations | 动作、账号、链参数、交易 ID、结果 | 表单标识和交易 ID 唯一；每证书最多一个待确认操作 |
| audit_log | 登录、核验、发起、确认与拒绝 | 关联网页账号和证书 |

审计日志是本地应用记录，不是链上审计合约。列表统计读取本地数据，详情与核验实时读链。外部工具修改链状态后统计可能尚未更新，应以实时链结果为准。

## 多条件查询与排序

三个角色均支持证书编号、学生姓名、学校名称、学号的完整匹配。姓名不作为唯一标识，不同学号的同名学生及其多张证书全部返回；学校查询包含该校全部匹配记录，包括已撤销记录。查询机构的工作台提供查询入口，未输入条件时不列出全库证书。高校和监管角色还可浏览全库、使用综合关键词搜索。

列表每页 20 条，有总数、上一页和下一页，没有总结果 200 条的截断。路由读取表单的 q 参数，兼容旧 search 参数。姓名与学校来自 SQLite 中已登记的全文；这不是对整条链上其他应用的证书进行全文搜索。详情和内容核验继续读取真实链记录。

排序使用本地已确认链记录 ledger_json.issued_at 中的签发交易 Unix 秒时间，按整数比较，默认从早到晚，也可切换从晚到早。撤销时间、导入时间和本地更新时间不改变签发排序。相同秒数按证书编号排序，尚无已确认签发时间的记录放在最后；这不是按区块内交易序号严格排序。列表显示 UTC+8 时间。本次更新没有修改数据库表结构或链上合约。

## 交易一致性

先把全文、参数、表单标识和交易 ID 提交到 SQLite，再调用链，网络等待时不占数据库写锁。重复表单返回原操作，不创建第二笔交易。

invoke 响应不直接作为业务成功。随后按交易 ID 查询已入块交易，核对链、合约、方法、全部参数、发送组织以及返回编号、哈希、交易 ID，全部通过后才标记 confirmed。

超时或结果不确定保留 pending；明确合约拒绝为 failed。查证按钮只读原交易；CMC 层另有唯一提交标记。若进程在登记后、发送前退出，会保留待确认记录，需要人工排查，不自动猜测成功或重发。

回执解析兼容普通错误文本和 Base64 result，沿用第五步验证过的 UNAUTHORIZED 等错误处理。

## 代码入口

1. backend/degreeapp/__init__.py：路由、登录、CSRF、角色与页面。
2. backend/degreeapp/domain.py：字段验证、规范 JSON、SHA-256。
3. backend/degreeapp/db.py：表结构、事务、数据转换。
4. backend/degreeapp/service.py：查询分页与排序、签发、撤销与待确认恢复。
5. backend/degreeapp/chain.py：CMC 调用、身份与回执绑定。
6. scripts/cmc_receipts.py：严格回执解析。
7. contracts/degreecert/：已在 Ubuntu 部署的 Go 合约。
8. backend/degreeapp/templates/、static/：页面、样式、交互。

本轮完成单张证书核心闭环、JSON 导出核验及本地审计。批量导入、版本历史、附件 PDF、生产账号管理和公开联网部署属于后续扩展；当前用于本机教学演示。
