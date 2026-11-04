FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY harness harness
COPY contract contract
COPY catalog catalog
COPY fixtures fixtures
COPY web web
ENV STORE=postgres
EXPOSE 8000
# Wait for the dev Moodle's token, sync the catalog into Postgres, then serve the API.
CMD ["sh", "-c", "until [ -s \"$MOODLE_TOKEN_FILE\" ]; do echo waiting for Moodle token; sleep 5; done; until python -m harness sync-db --mode live --site \"$MOODLE_URL\" --shelves fixtures/shelves.yaml; do echo retrying sync; sleep 10; done; exec uvicorn harness.api:app --host 0.0.0.0 --port 8000"]
