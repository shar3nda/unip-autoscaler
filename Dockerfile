FROM python:3.8-slim-buster

USER root
RUN useradd -ms /bin/bash app
WORKDIR /home/app

RUN python3.8 -m pip install --upgrade pip wheel setuptools
COPY requirements.txt ./requirements.txt
RUN python3.8 -m pip install -r ./requirements.txt

COPY --chown=app unip_autoscaler /home/app/unip_autoscaler
USER app

CMD ["uvicorn", "unip_autoscaler.main:app", "--host", "0.0.0.0", "--port", "8080"]