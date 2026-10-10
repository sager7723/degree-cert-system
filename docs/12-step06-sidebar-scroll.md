# 第六步补丁 2：只修复左侧栏滚动

按当前要求，退出按钮保持原来左侧栏底部的位置和图标样式。本次只修复侧栏超出窗口时不能上下滚动的问题。右上角不增加按钮。

本包可以直接覆盖原第六步版本，也可以覆盖已经安装的 fix1，恢复原按钮位置。使用本包即可，不用先安装 fix1。

## 同步到 Ubuntu

1. 把 degree-cert-system-step06-fix2.zip 拖入 Ubuntu 的 /home/wsc/Downloads。
2. 在运行网页的终端按 Ctrl+C 停止网页服务。
3. 执行：

```bash
cd /home/wsc/degree-cert-system
unzip -o /home/wsc/Downloads/degree-cert-system-step06-fix2.zip -d /home/wsc/degree-cert-system
bash scripts/07-start-web.sh
```

4. Ubuntu 浏览器打开 http://127.0.0.1:8080，按 Ctrl+Shift+R 强制刷新。
5. 登录后把鼠标移到深色左侧栏内，向下滚动到底部。账号旁的小箭头图标就是退出按钮，悬停显示“退出登录”。点击后可使用另一个角色的账号密码登录。

侧栏内容未超出窗口时，不需要滚动。修复的是桌面固定侧栏，窄屏原有布局保持不变。

补丁只包含页面模板、样式和说明，不改账号、证书数据库、身份文件或链上合约，不需要 sudo、重装依赖或重新部署。
