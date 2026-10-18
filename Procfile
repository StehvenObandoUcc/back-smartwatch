web: python -m uvicorn app.main:create_app --factory --host 0.0.0.0 --port $PORT
worker: python -m arq app.worker.main.WorkerSettings
release: python -m alembic upgrade head
