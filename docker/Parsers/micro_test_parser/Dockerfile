FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Let runner_env.py handle dependency logic — we copy only the entrypoint now
COPY entrypoint.py /app/

# Dependencies and script mounting will be handled at runtime
ENTRYPOINT ["python", "entrypoint.py"]