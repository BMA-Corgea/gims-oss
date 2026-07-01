FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Accept dependencies as a build argument
ARG DEPENDENCIES=""

# Install dependencies if provided
RUN if [ ! -z "$DEPENDENCIES" ]; then pip install --no-cache-dir $DEPENDENCIES; fi

# Copy only entrypoint; parser code is mounted at runtime
COPY entrypoint.py /app/

ENTRYPOINT ["python", "entrypoint.py"]