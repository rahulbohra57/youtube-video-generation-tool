FROM python:3.10

WORKDIR /app

RUN apt-get update && apt-get install -y fonts-dejavu-core fonts-indic && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]