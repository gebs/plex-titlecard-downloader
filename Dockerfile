# syntax=docker/dockerfile:1

FROM python:3.8-slim-buster as base
WORKDIR /app
COPY requirement.txt requirement.txt
RUN pip3 install -r requirement.txt

COPY . .

RUN apt update && apt install tzdata -y
ENV TZ="Europe/Zurich"

FROM base as debug
RUN pip3 install ptvsd

WORKDIR /app
CMD python3 -m ptvsd --host 0.0.0.0 --port 5678 --wait plex_title_card_finder.py


FROM base as prod

CMD ["python3","plex_title_card_finder.py"]