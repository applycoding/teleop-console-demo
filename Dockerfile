FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY teleop/ teleop/
COPY web/ web/
RUN mkdir -p recordings
EXPOSE 8000
CMD ["python", "-m", "teleop.server"]
