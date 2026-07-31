CMAKE ?= cmake
PYTHON ?= python
BUILD_DIR ?= build/cpp_backend
CONFIG ?= config/cpp_backend/demo.ini
VERIFY_CONFIG ?= config/cpp_backend/verify.ini
VERIFY_DIR ?= build/verify
VERIFY_BUILD_DIR ?= $(VERIFY_DIR)/cpp_backend_build
WHEELHOUSE_DIR ?= build/wheelhouse
VERIFY_DOCKER ?= auto
DOCKER_IMAGE ?= waterbag-inspection-verify:local
HARDWARE_CONFIG ?= config/cpp_backend/hardware_hik_mvs_modbus.ini
DATA ?= config/waterbag.yaml
DEVICE ?= 0

.PHONY: configure-cpp build-cpp run-cpp-demo run-cpp-once run-cpp-watch hardware-check run-hardware-watch serve-dashboard sync-results test smoke install-train train-yolov8 train-yolo11 benchmark-models export-onnx python-boundary-check dashboard-smoke python-check verify verify-clean verify-demo verify-cpp verify-mock-once verify-python verify-wheelhouse verify-dashboard-smoke verify-installed-dashboard verify-docker verify-docker-run clean-cpp

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

hardware-check: build-cpp
	./$(BUILD_DIR)/waterbag_cpp_service --config $(HARDWARE_CONFIG) --check-hardware

run-hardware-watch: build-cpp
	./$(BUILD_DIR)/waterbag_cpp_service --config $(HARDWARE_CONFIG) --watch

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

python-boundary-check:
	$(PYTHON) scripts/check_python_realtime_boundary.py

dashboard-smoke:
	$(PYTHON) scripts/smoke_dashboard.py --config $(CONFIG)

python-check:
	$(PYTHON) -m compileall waterbag_inspection train_ultralytics.py train_v8.py train_yolo11.py benchmark_ultralytics_models.py export_ultralytics_onnx.py predict_twostage_multilight.py benchmark_twostage_multilight.py
	$(PYTHON) scripts/check_python_realtime_boundary.py
	$(PYTHON) scripts/smoke_dashboard.py --config $(CONFIG)

verify: verify-clean verify-demo verify-cpp verify-mock-once verify-python verify-wheelhouse verify-dashboard-smoke verify-installed-dashboard verify-docker

verify-clean:
	$(CMAKE) -E remove_directory $(VERIFY_DIR)
	$(CMAKE) -E remove_directory build/generated_demo
	$(CMAKE) -E remove_directory $(WHEELHOUSE_DIR)
	$(CMAKE) -E make_directory $(VERIFY_DIR)

verify-demo:
	$(PYTHON) scripts/generate_demo_data.py --output build/generated_demo

verify-cpp:
	$(CMAKE) -S cpp_backend -B $(VERIFY_BUILD_DIR) -DCMAKE_BUILD_TYPE=RelWithDebInfo
	$(CMAKE) --build $(VERIFY_BUILD_DIR) -j
	ctest --test-dir $(VERIFY_BUILD_DIR) --output-on-failure

verify-mock-once:
	./$(VERIFY_BUILD_DIR)/waterbag_cpp_service --config $(VERIFY_CONFIG) --once
	$(PYTHON) scripts/assert_mock_results.py --jsonl $(VERIFY_DIR)/cpp_backend/results.jsonl

verify-python:
	$(PYTHON) -m compileall waterbag_inspection train_ultralytics.py train_v8.py train_yolo11.py benchmark_ultralytics_models.py export_ultralytics_onnx.py predict_twostage_multilight.py benchmark_twostage_multilight.py
	$(PYTHON) scripts/check_python_realtime_boundary.py

verify-wheelhouse:
	$(PYTHON) -m venv $(VERIFY_DIR)/build-venv
	$(VERIFY_DIR)/build-venv/bin/python -m pip install --require-hashes -r requirements/build.lock
	$(VERIFY_DIR)/build-venv/bin/python -m pip download --only-binary=:all: --require-hashes -r requirements/dashboard.lock --dest $(WHEELHOUSE_DIR)
	$(VERIFY_DIR)/build-venv/bin/python -m build --wheel --no-isolation --outdir $(WHEELHOUSE_DIR)

verify-dashboard-smoke:
	$(VERIFY_DIR)/build-venv/bin/python -m pip install --no-index --find-links $(WHEELHOUSE_DIR) Flask
	$(VERIFY_DIR)/build-venv/bin/python scripts/smoke_dashboard.py --config $(VERIFY_CONFIG)

verify-installed-dashboard:
	$(PYTHON) -m venv $(VERIFY_DIR)/install-venv
	$(VERIFY_DIR)/install-venv/bin/python -m pip install --no-index --find-links $(WHEELHOUSE_DIR) waterbag-inspection-dashboard
	$(VERIFY_DIR)/install-venv/bin/python scripts/smoke_installed_dashboard.py --config $(abspath $(VERIFY_CONFIG)) --cli $(abspath $(VERIFY_DIR)/install-venv/bin/waterbag-inspection)

verify-docker:
	@if [ "$(VERIFY_DOCKER)" = "0" ]; then \
		echo "docker verification disabled"; \
	elif ! command -v docker >/dev/null 2>&1; then \
		if [ "$(VERIFY_DOCKER)" = "auto" ]; then echo "docker not found; skipping docker verification"; else echo "docker not found"; exit 1; fi; \
	else \
		$(MAKE) verify-docker-run; \
	fi

verify-docker-run:
	$(CMAKE) -E remove_directory $(VERIFY_DIR)/docker
	$(CMAKE) -E make_directory $(VERIFY_DIR)/docker/cpp_backend
	docker build -t $(DOCKER_IMAGE) .
	docker run --rm --user $$(id -u):$$(id -g) -v $(abspath $(VERIFY_DIR)/docker):/app/build/verify $(DOCKER_IMAGE)
	$(PYTHON) scripts/assert_mock_results.py --jsonl $(VERIFY_DIR)/docker/cpp_backend/results.jsonl

clean-cpp:
	$(CMAKE) -E remove_directory $(BUILD_DIR)
