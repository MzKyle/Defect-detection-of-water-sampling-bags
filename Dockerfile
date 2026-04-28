FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV WATERBAG_CONFIG=configs/demo.yaml

WORKDIR /app

COPY requirements-demo.txt ./
RUN pip install --no-cache-dir -r requirements-demo.txt

COPY . .

EXPOSE 5000

CMD ["python", "-m", "waterbag_inspection", "serve", "--config", "configs/demo.yaml"]
