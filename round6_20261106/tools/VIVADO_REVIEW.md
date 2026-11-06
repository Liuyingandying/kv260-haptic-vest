# 在Vivado中打开已冻结M2源码

先完整解压源码，再双击上一级 `OPEN_M2_VIVADO.cmd`。推荐Vivado2025.2。

入口先使用环境变量 `VIVADO_BAT`，否则通过PATH查找 `vivado.bat`。不假设安装盘符；需要时在PowerShell中先设置实际路径，再启动：

```powershell
$env:VIVADO_BAT = '<实际Vivado安装目录>\bin\vivado.bat'
.\OPEN_M2_VIVADO.cmd
```

首次打开将在 `build/m2_review.cache/` 中复制发布源码快照，创建：

```text
build/m2_review.cache/project/kv260_m2_review.xpr
```

`.cache`目录由仓库现有忽略规则排除。日志在其 `launch/` 子目录；现有BD脚本写出的evidence也只进入快照目录，不写发布源码。器件为 `xck26-sfvc784-2LV-c`，顶层为 `kv260_unified_board_top`。全部发布RTL、两份XDC、PS配置和BD封装会加入审阅工程，M2 pre-opt hook仅设置为工程属性，不执行。

已有该XPR时只打开，不覆盖或重新生成。首次中途失败留下快照时，入口停止并提示检查日志，不自动删除旧目录或重试。Vivado若已有其他工程打开，入口不会关闭它。

在Sources中展开顶层阅读RTL；Constraints中可查看H12/SCL、B10/SDA约束；IP Integrator中可打开BD。生成BD输出文件不代表综合、实现、实板加载或功能验收通过。入口不调用 `launch_runs`、综合、布局布线、Bitstream生成或任何硬件管理命令。

本次仅完成Tcl `info complete`、依赖路径和入口静态检查。**首次在新机器实际创建GUI审阅工程尚未执行**，也没有重新综合或加载板卡。此XPR用于阅读冻结M2，不能替代对应已验Bitstream、XSA和历史构建证据。
