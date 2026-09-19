# 项目决策与排障记录

## 2026-09-19 run_ui.py 启动失败三连修复

### 问题 1：ModuleNotFoundError: vnpy_ctptqtts.gateway.tqsdk_mdapi_only
- **根因**：`vnpy_ctptqtts` 是 meson-python editable 安装，导入由 site-packages 的
  `_vnpy_ctptqtts_editable_loader.py` 拦截，只认 `vnpy_ctp/api/meson-info/intro-install_plan.json`
  里登记的文件。新增的 `tqsdk_mdapi_only.py` 不在 `meson.build` 的 `python_files` 清单里，永远导入不到。
- **修复**：`vnpy_ctptqtts/meson.build` 的 python_files 增加
  `['vnpy_ctptqtts/gateway/tqsdk_mdapi_only.py', 'vnpy_ctptqtts/gateway']`，然后重建（见问题 3 的脚本）。
- **规则**：以后给 vnpy_ctptqtts（及其他 meson editable 包 vnpy_ctptq / vnpy_ttstq / vnpy_ctptesttq）
  新增 .py 文件，必须同步登记进对应 meson.build 并重建，否则磁盘上存在也导入不到。

### 问题 2：common/ 下裸导入导致包方式导入失败
- `common/strategy_spread.py` 的 `from file_dir import ...`、`from time_check import ...`
  改为 `from common.file_dir import ...`、`from common.time_check import ...`。
- `common/file_dir.py` 的 `from trader.utility import ...` 改为 `from vnpy.trader.utility import ...`（旧版 vnpy 残留路径）。
- **规则**：common/ 是包（有 __init__.py），内部互相引用一律用 `from common.xxx import`，禁止裸导入。

### 问题 3：meson 重建在中文 Windows + PYTHONUTF8=1 下崩溃
- **根因**：本机全局设置 `PYTHONUTF8=1`，meson 的 Popen_safe 因此用 UTF-8 严格解码子进程输出；
  而 `lib.exe /?` 等 MSVC 工具输出 GBK 中文帮助 → UnicodeDecodeError → reader 线程死掉 →
  detect_static_linker 抛 AttributeError → editable loader 的自动重建永远失败。
- **修复**：用 `testcode/_rebuild_ctptqtts.bat` 重建（先清掉 PYTHONUTF8/PYTHONIOENCODING，
  再 call vcvars64.bat，最后 meson compile）。一次性重建成功后，日常导入走 "no work to do" 不再触发该 bug。
- **注意**：仅当 meson.build 变更后才需要重跑该 bat；git-bash 环境下测 DLL 加载会因 PATH 污染出现
  "不是有效的 Win32 应用程序" 假报错，以 PyCharm/正常终端运行为准。

### 问题 4：gm SDK 与 protobuf 7.35 不兼容（Descriptors cannot be created directly）
- **根因**：环境里 protobuf 被升到 7.35.0，gm 3.0.180 的 _pb2.py 是旧 protoc 生成的；
  且 protobuf 6.x+ 已移除纯 Python 实现，报错提示的方案 2（PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=python）不可行。
- **修复**：`pip install protobuf==3.20.3`。blackboxprotobuf 1.0.1 声明要 3.10.0（与 gm >=3.12.2 冲突），
  实测 3.20.3 下 import 正常。
- **规则**：此环境禁止随意升级 protobuf；升级 gm SDK 前先确认其对 protobuf 的版本约束。
