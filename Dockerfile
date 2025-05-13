FROM rust:latest AS builder
WORKDIR /usr/src/librespot
RUN apt update
RUN apt install -y libasound-dev pkg-config
RUN cargo install librespot

FROM python:3

WORKDIR /usr/src/app

COPY requirements.txt ./
RUN apt update
RUN apt install -y ffmpeg opus-tools
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
COPY --from=builder /usr/local/cargo/bin/librespot /bin/librespot

CMD [ "python", "./bot.py" ]
