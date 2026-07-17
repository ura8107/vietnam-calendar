FROM python:3.12.12-slim
WORKDIR /app
RUN pip install --no-cache-dir uv==0.9.11
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/migrations migrations
COPY backend/alembic.ini ./
COPY backend/src src
COPY evals evals
RUN uv sync --frozen --no-dev
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
CMD ["uvicorn", "vietnam_calendar.api:app", "--host", "0.0.0.0", "--port", "8000"]
