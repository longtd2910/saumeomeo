FROM python:3.10-slim

WORKDIR /app

ENV LANG C.UTF-8
ENV LC_ALL C.UTF-8

RUN pip install discord
RUN pip install PyNaCl
RUN pip install -U yt_dlp
RUN pip install python-dotenv
RUN pip install asyncpg

RUN apt-get update && apt-get install -y ffmpeg nodejs npm && rm -rf /var/lib/apt/lists/*

COPY . /app/

CMD ["python", "run.py"]