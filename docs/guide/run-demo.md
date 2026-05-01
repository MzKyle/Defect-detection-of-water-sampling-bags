# 启动 Demo

## 1. 生成演示样本

```bash
python -m waterbag_inspection seed-demo --output-root demo_data --clean
```

生成结果：

| 文件 | 场景 |
| --- | --- |
| `bag_0001_cam1_good.jpg` + `bag_0001_cam2_good.jpg` | 双相机正常，袋体放行 |
| `bag_0002_cam1_defect_primary.jpg` + `bag_0002_cam2_good.jpg` | 一次检测命中缺陷，袋体判退 |
| `bag_0003_cam1_defect_primary.jpg` + `bag_0003_cam2_good.jpg` | 重复缺陷场景 |
| `bag_0004_cam1_good.jpg` + `bag_0004_cam2_micro_patch.jpg` | 一次检测通过，二次网格复检命中微小缺陷 |

## 2. 启动 Web 服务

推荐入口：

```bash
python app.py
```

等价 CLI 入口：

```bash
python -m waterbag_inspection serve --config config/demo.yaml
```

默认地址：

```text
http://127.0.0.1:5000
```

## 3. 观察页面

Web 页面会展示：

- 最近检测图片
- 当前袋体状态：正常 / 异常 / 待确认 / 超时
- Stage 1 / Stage 2 检测框数量
- PLC Ack 是否成功
- Ack attempts / latency
- Timeout、Ack retry、Stale frame、PLC failure 等故障信号
- 历史结果表和聚合指标

## 4. 单图烟测

```bash
python -m waterbag_inspection inspect \
  --config config/demo.yaml \
  --camera-id 1 \
  --image demo_data/camera1/bag_0003_cam1_defect_primary.jpg \
  --reset-history
```

输出为单次 `InspectionResult` 的摘要 JSON，适合排查某张图如何被 pipeline 判断。

## 5. 历史回放

```bash
python -m waterbag_inspection replay \
  --config config/demo.yaml \
  --source-root demo_data \
  --limit 4 \
  --reset-history
```

回放会按 `bag_id -> camera_id -> filename` 排序构造 `FramePacket`，复用和在线运行一致的 pipeline。

## 6. Makefile 快捷命令

```bash
make install-demo
make seed-demo
make serve-demo
make replay-demo
make inject-faults
make smoke
make test
```

其中 `make smoke` 会自动生成 demo 数据并执行一次短回放。

## 运行产物

| 路径 | 内容 |
| --- | --- |
| `demo_data/camera1/` | A 面相机输入目录 |
| `demo_data/camera2/` | B 面相机输入目录 |
| `artifacts/backups/` | 原始输入图备份 |
| `artifacts/results/` | 绘制检测框后的结果图 |
| `artifacts/inspection.db` | SQLite 检测结果 |
| `artifacts/repeat_history.json` | 重复缺陷历史 |
