# Legacy Assets

这里保留的是重构前的实验性脚本和旧版页面，方便回溯原始思路，但不再作为主运行入口。

当前约定：

- `legacy/scripts/`：早期单文件脚本，例如裁剪、一次性测试、MySQL 版本逻辑
- `legacy/web_pages/`：旧版页面模板
- `legacy/state/`：历史状态文件样例

如果后续仍要复用其中某段逻辑，建议按下面方式处理：

1. 先确认逻辑是否仍和当前主流程兼容。
2. 把可复用部分迁移到 `waterbag_inspection/` 或 `tools/`。
3. 不再继续往 legacy 目录里叠加新功能。
