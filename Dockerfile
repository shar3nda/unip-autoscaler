FROM python:3.12-slim-bookworm

USER root
RUN useradd -ms /bin/bash app
WORKDIR /home/app

RUN python3.12 -m pip install --upgrade pip wheel setuptools
COPY requirements.txt ./requirements.txt
RUN python3.12 -m pip install -r ./requirements.txt

COPY --chown=app src /home/app/src
USER app

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8080"]