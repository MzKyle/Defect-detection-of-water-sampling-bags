CMAKE ?= cmake
PYTHON ?= python
BUILD_DIR ?= build/cpp_backend
CONFIG ?= config/cpp_backend/demo.ini
DATA ?= config/waterbag.yaml
DEVICE ?= 0

.PHONY: configure-cpp build-cpp run-cpp-demo run-cpp-once run-cpp-watch serve-dashboard sync-results test smoke install-train train-yolov8 train-yolo11 benchmark-models export-onnx python-check clean-cpp

configure-cpp:
	$(CMAKE) -S cpp_backend -B $(BUILD_DIR)

build-cpp: configure-cpp
	$(CMAKE) --build $(BUILD_DIR) -j

run-cpp-demo: build-cpp
	./$(BUILD_DIR)/waterbag_cpp_demo

run-cpp-once: build-cpp
	./$(BUILD_DIR)/waterbag_cpp_service --config $(CONFIG) --once

run-cpp-watch: build-cpp
	./$(BUILD_DIR)/waterbag_cpp_service --config $(CONFIG) --watch

serve-dashboard:
	$(PYTHON) -m waterbag_inspection serve --config $(CONFIG)

sync-results:
	$(PYTHON) -m waterbag_inspection sync-results --config $(CONFIG)

test: build-cpp
	ctest --test-dir $(BUILD_DIR) --output-on-failure

smoke: run-cpp-once

install-train:
	$(PYTHON) -m pip install -r requirements.txt

train-yolov8:
	$(PYTHON) train_v8.py --data $(DATA) --device $(DEVICE)

train-yolo11:
	$(PYTHON) train_yolo11.py --data $(DATA) --device $(DEVICE)

benchmark-models:
	$(PYTHON) benchmark_ultralytics_models.py \
		--models runs/train/yolov8_waterbag/weights/best.pt runs/train/yolo11_waterbag/weights/best.pt \
		--data $(DATA) \
		--device $(DEVICE) \
		--output artifacts/model_benchmarks.csv \
		--json-output artifacts/model_benchmarks.json

export-onnx:
	$(PYTHON) export_ultralytics_onnx.py \
		--weights runs/train/yolov8_waterbag/weights/best.pt \
		--output artifacts/models/yolov8_waterbag.onnx \
		--device $(DEVICE) \
		--dynamic \
		--simplify

python-check:
	$(PYTHON) -m compileall waterbag_inspection train_ultralytics.py train_v8.py train_yolo11.py benchmark_ultralytics_models.py export_ultralytics_onnx.py predict_twostage_multilight.py benchmark_twostage_multilight.py

clean-cpp:
	$(CMAKE) -E rm -rf $(BUILD_DIR)
