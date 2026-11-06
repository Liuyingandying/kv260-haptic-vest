# Gemini ARM64 原生源码闭包

这里保存两套独立构建输入，不包含 Orbbec SDK、真实相机帧、设备序列号、账号或凭据。它们是采集和几何处理程序；PS/PL 提交、输出许可和停止流程仍由上级目录的已封存运行包管理。不要直接启动 ELF 来代替监督器。

| 目录 | 对应的历史 ARM64 ELF SHA256 | 用途 |
|---|---|---|
| `bounded60/` | `27c22c7271c573683bcd4f352bbd259b0907dbcb4c69f233ac7b8d53b7b6b270` | 已验收8/60秒有界采集源码；本次一分钟演示使用这一版 |
| `continuous600/` | `6eb4aacce1a488b7aee6ac383030322ee2fdab2f42cb6eb828fe9c750c949cd9` | 独立600秒连续采集候选源码；与有界版本分别保存 |
| `shared/` | 几何核心源码见 `SOURCE_ORIGIN.json` | 两版共同使用的冻结 Gemini V2 C++ 几何核心 |

`gemini_geometry.h` 是原工程真实接口名，并不存在应当补造的 `gemini_geometry.hpp`。每个 variant 内的同名 `.h` 保持原始本地 include 解析；其内容与共享接口相同。

## 源码来源

`SOURCE_ORIGIN.json` 逐文件记录相对来源、原始SHA256和对应构建清单，不依赖个人目录。

- 有界构建清单记录的9项源输入全部逐字节匹配。后来改变的 `gemini_live_compact.cpp` 与 `latest_seqpacket.hpp` 从不可变的预硬件检查点ZIP恢复；没有用连续候选覆盖它们。
- 共享几何接口和有界 variant 的 `.h` 额外与同一不可变ZIP核对。原始构建清单没有列出 `.h`，这里明确补齐此证据边界。
- 连续版保存它自己的 CPP、队列头文件和 `capture_run_policy.hpp`，并补齐递归自研 includes。历史连续构建复用的几何对象SHA也单独记录。
- 构建所需全部自研源码/头文件均在本目录。系统C/C++头文件和外部 Orbbec 头文件/库由编译环境提供。

仅验证源码闭包，不需要SDK，也不会调用编译器：

```sh
python3 -B build_arm64.py --verify-only
```

## ARM64构建

构建入口只读取三个环境配置：

- `SDK_ROOT`：合法获得的 **Orbbec SDK 1.10.37 AArch64** SDK目录，具有 `include/libobsensor/ObSensor.hpp` 和 `lib/libOrbbecSDK.so`。库必须匹配清单中已核验的SDK SHA256。
- `CXX`：GNU兼容的 AArch64/Linux C++ 编译器路径或命令名。入口通过 `-dumpmachine` 拒绝 x86 或裸机目标。
- `SYSROOT`：交叉构建时提供 Ubuntu24 AArch64 sysroot。需包含匹配的目标C++头文件、glibc和libstdc++库，不得指向Windows或x86用户目录。直接在ARM64 Ubuntu上使用本机 `g++` 时可以省略。

Linux交叉构建示例，路径按自己的环境填写：

```sh
export SDK_ROOT=/opt/orbbec-1.10.37/SDK
export CXX=aarch64-linux-gnu-g++
export SYSROOT=/opt/sysroots/ubuntu24-aarch64
python3 -B build_arm64.py --variant bounded60
python3 -B build_arm64.py --variant continuous600
```

Windows PowerShell交叉构建同样使用环境变量，不要求特定盘符或用户名：

```powershell
$env:SDK_ROOT = '<AArch64 SDK目录>'
$env:CXX = '<aarch64-linux-gnu-g++.exe的实际路径>'
$env:SYSROOT = '<Ubuntu24 AArch64 sysroot目录>'
python -B build_arm64.py --variant bounded60
```

每次在全新 `build/<variant>_<UTC>/` 中输出 ELF、共享验证器、命令和stdout/stderr；不会覆盖旧产物，也不会运行输出程序或SDK。采用C++17、`-O3 -fno-fast-math -ffp-contract=off -pthread`，共享几何对象保留原来的 `-fno-exceptions -fno-rtti -fPIC`。

这次只完成源码来源、include闭包及构建入口的静态验证，**没有执行新的交叉编译或硬件验证**。不同编译器、sysroot、链接器和构建路径可能改变ELF字节；即使源码匹配，也不能预先承诺重建SHA等于历史ELF。新产物不得直接覆盖已验版本，必须另行回归后由监督器部署。

## 运行边界

SDK/XML由现有隔离运行环境提供；本目录不自动安装、复制或执行它们。未知采集帧龄仍不能声称生产 `FRESH` 通过；SDK清理状态仍独立记录。程序生成的设备身份和原始深度数据属于运行证据，不是源码交付内容。
