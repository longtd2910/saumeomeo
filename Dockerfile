FROM python:3.9.18-slim-bullseye

WORKDIR /app

ENV LANG C.UTF-8
ENV LC_ALL C.UTF-8

RUN pip install discord PyNaCl yt_dlp
RUN apt-get update && apt-get install -y ffmpeg

COPY . /app/

CMD ["python", "run.py"]