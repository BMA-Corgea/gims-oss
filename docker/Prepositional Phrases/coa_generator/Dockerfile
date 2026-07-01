FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Accept dependencies as a build argument
ARG DEPENDENCIES=""

# Install dependencies if provided
ARG CACHEBUST=1
RUN if [ ! -z "$DEPENDENCIES" ]; then pip install --no-cache-dir $DEPENDENCIES; fi

# Copy entrypoint script (JSON-array form handles spaces)
COPY ["docker/Prepositional Phrases/coa_generator/entrypoint.py","/app/"]

# Copy utils into /app/utils
COPY ["utils","/app/utils"]

# At runtime, entrypoint.py gets executed
ENTRYPOINT ["python","entrypoint.py"]