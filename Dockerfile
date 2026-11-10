# Imagen única para coordinador y worker (issue #24): el Compose decide el
# rol vía `command:`. El indexador no tiene imagen propia: es un proceso hijo
# que el coordinador lanza con `multiprocessing` dentro de su mismo
# contenedor (PROJECT_STATEMENT §14), no un quinto servicio.
FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src

RUN pip install --no-cache-dir "celery[redis]>=5.4,<6" "boto3>=1.34,<2" \
    && pip install --no-cache-dir -e .

CMD ["python", "-m", "loghunter.coordinador"]
