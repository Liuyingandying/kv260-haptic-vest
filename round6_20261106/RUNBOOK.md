# 冻结版本的复现与停止

## 本轮边界

发布/备份阶段不连接串口，不启动相机，不执行AXI或I²C。板端示范已经结束并精确读回8片FULL_OFF，用户随后收起设备。发布代码不携带已消费的现场授权、boot ID、nonce、root凭据或自动上电脚本。

## 离线检查

在仓库根目录运行：

    python -B round6_20261106/tests/run_tests.py
    python -B round6_20261106/tests/run_tests.py --suite rtl
    python -B round6_20261106/native/build_arm64.py --verify-only
    python -B round6_20261106/tools/prepare_runtime.py --out NEW_LOCAL_DIRECTORY

最后一步仅创建33文件的原样部署树及READY.json，不启动任何板端程序。源码树中额外的测试/文档/构建源码不能直接当部署树使用：原监督器严格拒绝未声明文件。

## Vivado与原生构建

OPEN_M2_VIVADO.cmd调用Vivado2025.2，脚本在build下创建审阅工程，包含实际XDC、PS配置和RTL。新电脑运行前设置VIVADO_BAT或将vivado.bat加入PATH。本轮不运行此工具，也不生成新固件；参见tools/VIVADO_REVIEW.md。

C++源码分别位于native/bounded60、native/continuous600；禁止用continuous600源码解释bounded60 ELF。按native/README.md准备SDK_ROOT、CXX、SYSROOT后编译到新目录。保留已验收ELF，重新编译产物不能自动替换哈希绑定的部署文件。MMIO库源码及构建脚本分别位于runtime_control/native和iic_c1/native。

## 下次现场运行

1. 恢复原接线，首先保持马达V+断开。原M2部署包只恢复PL，不写QSPI或永久启动配置；加载、健康检查和当前设计ID需现场重新确认。
2. SDK v1.10.37安装目录由原监督器明确绑定为/home/ubuntu/gemini_live_20261026T135635Z/sdk；实际库和XML分别校验SHA256。路径不等价于ABI/设备恢复证明；新机器换路径必须审查并同步更新Host命令绑定。
3. Python板端仅依赖标准库、自研.so和原生C++，不需运行NumPy/OpenCV。Windows串口上传工具依赖pyserial。显式设置KV260_UART_PORT或KV260_UART_SERIAL；不自动登录、不存密码、不抢占已打开的终端。
4. 用scripts/deploy_files.py上传prepare_runtime生成的tree到全新用户目录，获得本次真实部署收据；不得将旧收据伪装成本次部署。使用生成的READY.json和收据通过tools/run_m2_stage.py的--check-only门禁后，方可显式--run-once。
5. M2模式参数为--matrix-mode --matrix-cell-limit 128 --output-mask 0xF0F0 --level-cap 1。60秒使用--duration-s 60 --renew-digital-window；600秒使用独立--extended-session-s 600及continuous ELF。实体测试另需--supervised-matrix和本次boot/out/时长绑定的现场授权文件；默认仍为V+断开。授权schema见runtime_night/matrix_bench.py和supervised_single.py，不提供自动填真的模板。
6. 使用tools/run_m2_stage.py保留成功演示的按当前boot分离尝试标记修复；其余历史结果检查、全局worker锁、健康检查和授权条件完整保留。相同boot重试必须显式指定--allow-returned-restart且上次已完整返回，MMIO状态未知时禁止重试。
7. 正常停止由父监督器先撤销控制/关闭输出，再等待SDK。运行目录创建STOP_REQUESTED可人工请求停止；独立V+切断能力始终由现场人员掌握。I²C或未知MMIO故障不得靠循环重试恢复。

## 完成标准

进程退出0不能代替物理震动。分别保留相机帧、PL读回、I²C计数、PCA FULL_OFF、用户实际观察。SDK清理仍为UNKNOWN；正式曝光帧龄映射仍为BLOCKED；联调的安全关断/续约间隔不等于无间断PWM验收。当前支持有界60/600秒试验，不宣称已完成无人值守永久服务。
