FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# Provide the database through environment variables (QA_DB_*) or a mounted .db.json.
# Mount a volume at /data so accounts and saved queries survive restarts.
ENV QA_APP_DB=/data/app_data.sqlite3
VOLUME ["/data"]
EXPOSE 8501
CMD ["python", "-m", "streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0"]
