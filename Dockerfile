FROM python:3.12-slim

WORKDIR /srv
COPY requirements.txt ./requirements.txt
COPY web/requirements.txt ./web-requirements.txt
RUN pip install --no-cache-dir -r requirements.txt -r web-requirements.txt

COPY . .
ENV PYTHONPATH=/srv/core:/srv
EXPOSE 8000
CMD ["uvicorn", "web.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
