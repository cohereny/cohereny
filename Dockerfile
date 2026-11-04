FROM python:3.14-slim AS app

WORKDIR /app

COPY pyproject.toml ./
RUN pip install --no-cache-dir .

# Install pilot packages so workflows can run in-process via the API.
# Installed separately after the main package to allow independent layer caching.
COPY pilots/study/ ./pilots/study/
RUN pip install --no-cache-dir ./pilots/study/

COPY pilots/banking/ ./pilots/banking/
RUN pip install --no-cache-dir ./pilots/banking/

# The four zones of ADR-030, and this list is the reason the dev deployment was
# down from 24.08. to 28.08.: `kernel/` and `contexts/` were created by CHY-101
# and never added here. The image built, `docker compose up -d` returned 0, and
# the container then crash-looped on `ModuleNotFoundError: No module named
# 'kernel'` — a deploy that reported success and delivered nothing.
#
# Keep in step with `[tool.hatch.build] packages` in pyproject.toml. A test
# holds the two lists together (tests/test_image_contents.py) — before it, the
# only thing connecting them was somebody remembering.
COPY api/ ./api/
COPY cli/ ./cli/
COPY router/ ./router/
COPY contexts/ ./contexts/
COPY kernel/ ./kernel/
COPY core/ ./core/
COPY migrations/ ./migrations/

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]

# --- The quickstart (CHY-189) ---------------------------------------------------
#
# deploy/quickstart/compose.yaml builds this stage. One image serves the API,
# the migrations, the development issuer and the seed: the issuer and the seed
# need kernel/ and the packages above, and a second image would be a second
# list of what to copy. Nothing here is bind-mounted, because the CI runs the
# quickstart against the host's Docker daemon, where a path in the job's
# checkout does not exist.
FROM app AS quickstart
COPY scripts/dev_issuer.py ./scripts/dev_issuer.py
COPY deploy/quickstart/ ./deploy/quickstart/

# The production image. Last, so that a plain `docker build` — and every
# deploy job — builds exactly the stage above the quickstart and nothing of it.
FROM app
