FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir fastapi uvicorn jinja2 python-multipart itsdangerous

COPY app /app/app
COPY DOKUMENTATION.md /app/DOKUMENTATION.md

EXPOSE 8500

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8500"]