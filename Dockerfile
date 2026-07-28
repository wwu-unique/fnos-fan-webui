FROM python:3.13-alpine

WORKDIR /app
COPY fan_webui.py /app/fan_webui.py

RUN mkdir -p /data \
    && addgroup -S fanctl \
    && adduser -S -G fanctl fanctl

EXPOSE 8080

# The process runs as root because the host hwmon PWM file is mounted read/write.
# Do not make this image privileged by itself: that is an explicit deployment choice.
CMD ["python3", "/app/fan_webui.py"]

LABEL org.opencontainers.image.title="fnos-fan-webui" \
      org.opencontainers.image.description="Temperature-driven fan control dashboard for validated fnOS QU-605 hardware" \
      org.opencontainers.image.licenses="MIT"
