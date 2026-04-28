# 演示流程

## 目标

演示流程用于在没有真实相机、PLC 和模型权重的情况下，展示完整工业视觉闭环。

## 步骤

### 1. 安装依赖

```bash
python -m pip install -r requirements-demo.txt
```

### 2. 生成样本

```bash
python -m waterbag_inspection seed-demo --output-root demo_data --clean
```

### 3. 启动服务

```bash
python app.py
```

### 4. 打开看板

```text
http://127.0.0.1:5000
```

### 5. 观察四类样本

| 样本 | 预期 |
| --- | --- |
| `bag_0001` | 双相机正常，最终 accept |
| `bag_0002` | A 面缺陷，立即 reject |
| `bag_0003` | 重复缺陷提示 |
| `bag_0004` | B 面 Stage 2 微小缺陷，reject |

## CLI 演示版本

不打开 Web 也可以回放：

```bash
python -m waterbag_inspection replay \
  --config configs/demo.yaml \
  --source-root demo_data \
  --reset-history
```

## 面试展示建议

推荐演示顺序：

1. 打开 Web 页面，说明这是实时观测面
2. 展示 `configs/demo.yaml`，说明相机、模型、PLC 都是配置化
3. 运行 `seed-demo`，解释四类样本
4. 运行 replay 或上传图片，观察 Web 变化
5. 展示 `/api/results/metrics` 或页面指标
6. 打开 `fault_injection` 章节，说明异常路径也可复现
