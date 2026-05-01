# 生产配置模板

生产模板：

```text
config/production.example.yaml
```

该文件不是开箱即用配置，需要按现场环境修改。

## 相机目录

```yaml
cameras:
  - id: 1
    name: A面相机
    watch_dir: D:/MVS/data/MV-CU120-10UC (DA5594458)
  - id: 2
    name: B面相机
    watch_dir: D:/MVS/data/MV-CU120-10UC (DA4792752)
```

生产部署时建议确认：

- 相机软件是否稳定落盘
- 单图模式文件扩展名是否为 `.jpg/.jpeg/.png/.bmp`
- 多光源模式是否由 burst 模块写入 JSON manifest
- 目录权限是否允许 Python 进程读取
- 相机命名是否能推断同一袋体的 `bag_id`

## 模型配置

```yaml
models:
  primary:
    backend: multilight_torch
    weights_path: artifacts/models/multilight_yolo_feature_fusion.torchscript.pt
    encrypted_path:
    key_path:
    device: 0
    imgsz: 640
    conf_thres: 0.3
    iou_thres: 0.3
    light_order: [backlight, darkfield, polarized]
    primary_light: backlight
    input_format: blchw
    output_format: auto
    output_normalized: false
    class_names: [anomaly]
  patch:
    backend: ultralytics
    weights_path: artifacts/models/yolo11_patch_best.pt
    device: 0
    imgsz: 640
    conf_thres: 0.2
    iou_thres: 0.3
```

`backend: multilight_torch` 表示 primary detector 接收三张图组成的一个样本，一次模型调用输出检测结果。`input_format: blchw` 对应 `[B, 3, 3, H, W]`；如导出模型使用通道堆叠输入，可改为 `input_format: bchw`，对应 `[B, 9, H, W]`。

两种权重加载方式：

| 方式 | 配置 |
| --- | --- |
| 明文权重 | `weights_path` |
| 加密权重 | `encrypted_path + key_path` |

如果同时配置，优先使用 `weights_path`。

## 多光源组包

```yaml
multilight:
  enabled: true
  light_order: [backlight, darkfield, polarized]
  primary_light: backlight
  manifest_suffixes: [".json", ".manifest"]
```

启用后，watch 目录只消费 manifest，不再把三张原始图分别送检。manifest 示例：

```json
{
  "bag_id": "bag_0001",
  "camera_id": 1,
  "lights": {
    "backlight": "bag_0001_cam1_backlight.jpg",
    "darkfield": "bag_0001_cam1_darkfield.jpg",
    "polarized": "bag_0001_cam1_polarized.jpg"
  }
}
```

## PLC 配置

```yaml
plc:
  backend: modbus
  enabled: true
  port: COM4
  baudrate: 115200
  parity: N
  stopbits: 1
  bytesize: 8
  timeout: 0.1
  registers:
    cam1: 100
    cam2: 102
    alert: 104
    bag: 106
  alert_pulse_seconds: 0.2
  ack_timeout_ms: 150
  max_retries: 2
  retry_interval_ms: 50
```

部署前应和 PLC 工程师确认：

- 串口名和权限
- RTU 通信参数
- 寄存器地址
- `accept/reject` 写入值约定
- Ack 超时阈值是否符合现场节拍

## 存储与诊断

```yaml
storage:
  backend: sqlite
  sqlite_path: artifacts/inspection.db

runtime:
  backup_dir: artifacts/backups
  result_dir: artifacts/results
  upload_dir: artifacts/uploads
```

生产环境建议：

- 将 `artifacts` 放在容量充足的磁盘
- 定期归档或清理结果图
- 为 SQLite 和日志做备份
- 在长时间运行前评估磁盘增长速度

## 上线前检查清单

| 检查项 | 说明 |
| --- | --- |
| Demo 流程 | `make smoke` 通过 |
| 单图推理 | `inspect` 能加载真实权重 |
| 双相机关联 | 一袋两张图能正常 accept |
| 缺陷判退 | 任一相机缺陷能立即 reject |
| 超时策略 | 单侧缺失会按策略输出 |
| PLC Ack | 正常、重试、失败路径均验证 |
| 留档 | SQLite 中结果可查询 |
| Web 看板 | 指标和实时图像正常刷新 |
