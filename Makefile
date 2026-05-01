PYTHON ?= python
DATA ?= config/waterbag.yaml
DEVICE ?= 0
DOCS_PORT ?= 5173

.PHONY: install-demo install-full serve-demo serve-docs seed-demo replay-demo inject-faults inject-timeout inject-ack-retry inject-out-of-order train-yolov8 train-yolo11 benchmark-models test smoke

install-demo:
	$(PYTHON) -m pip install -r requirements-demo.txt

install-full:
	$(PYTHON) -m pip install -r requirements.txt

serve-demo:
	$(PYTHON) -m waterbag_inspection serve --config config/demo.yaml

serve-docs:
	$(PYTHON) -m http.server $(DOCS_PORT) -d docs

seed-demo:
	$(PYTHON) -m waterbag_inspection seed-demo --output-root demo_data --clean

replay-demo:
	$(PYTHON) -m waterbag_inspection replay --config config/demo.yaml --source-root demo_data --reset-history

inject-faults:
	$(PYTHON) -m waterbag_inspection inject-faults --config config/demo.yaml --scenario all --output-root artifacts/fault_injection --clean

inject-timeout:
	$(PYTHON) -m waterbag_inspection inject-faults --config config/demo.yaml --scenario timeout --output-root artifacts/fault_injection --clean

inject-ack-retry:
	$(PYTHON) -m waterbag_inspection inject-faults --config config/demo.yaml --scenario ack-retry --output-root artifacts/fault_injection --clean

inject-out-of-order:
	$(PYTHON) -m waterbag_inspection inject-faults --config config/demo.yaml --scenario out-of-order --output-root artifacts/fault_injection --clean

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

test:
	$(PYTHON) -m pytest -q tests

smoke:
	$(PYTHON) -m waterbag_inspection seed-demo --output-root demo_data --clean
	$(PYTHON) -m waterbag_inspection replay --config config/demo.yaml --source-root demo_data --limit 3 --reset-history
